"""Hub matching choices and public approval persistence/continuation contracts."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from hub.backend.interactions.approval_flow import ApprovalFlow
from hub.backend.interactions.approval_management import ApprovalAction
from hub.backend.interactions.automatic import AutomaticApprovals
from hub.backend.interactions.command_range import CommandChain, CommandRangeApproval
from hub.backend.interactions.remembered import (
    ApprovalMatch, ApprovalSelection, RememberScope, ToolApprovalConditions, approval_choices,
)
from hub.backend.runtime import HubRuntime
from hub.config.approval_presets import ApprovalPresets
from llm.components.tools import Tool, ToolRegistry, ToolClassification
from llm.core.approval import ApprovalOrigin, ApprovalScope, RememberedApproval
from llm.core.interactions import approval_request
from llm.core.models import ProjectConfig
from llm.core.policies import ApprovalRule
from llm.llm import LargeLanguageModel, BackendServices, LoopEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.services.runtime.interactions import InteractionRepository
from llm.services.runtime.tools import ToolRuntime
from tests.llm.support.runtime_tools import RuntimeTools, TEST_CONTRACT
from tests.llm.test_loop import ScriptedCompletion, chunk, call


def action(command='git status --short | head -n 30'):
    return {'tool': 'shell_execute', 'arguments': {'command': command, 'cwd': '/project',
            'env': {'LANG': 'C'}}, 'contract': {'revision': 'environment-1'}, 'policy': {}}


class ApprovalConditionTests(unittest.TestCase):
    def test_range_matches_each_argv_prefix_and_retains_connectors_and_environment(self):
        original = action()
        condition = CommandRangeApproval.condition(original, (('git', 'status'), ('head',)))
        evaluator = CommandRangeApproval()
        for command in ('git status -sb | head -n 5', 'git status | head',
                        'git   "status" --short|head -n 10'):
            with self.subTest(command=command):
                self.assertTrue(evaluator.evaluate(approval_request('Review', action=action(command)), condition.config))
        for command in ('git log | head', 'git status | headless',
                        'git status && head', 'git status | head | cat',
                        'git status > output.txt | head', 'git status $(pwd) | head',
                        'git status $FLAGS | head', 'git status * | head',
                        'git status | head &', 'git status; head', 'git status "a;b" | head',
                        'git status | head\necho extra', 'git status | head "unterminated'):
            with self.subTest(command=command):
                self.assertFalse(evaluator.evaluate(approval_request('Review', action=action(command)), condition.config))
        for key, value in [('cwd', '/elsewhere'), ('env', {'LANG': 'other'}), ('timeout_seconds', 2)]:
            changed = action()
            changed['arguments'][key] = value
            self.assertFalse(evaluator.evaluate(approval_request('Review', action=changed), condition.config))
        for key, value in [('tool', 'other'), ('contract', {'revision': 'environment-2'}), ('policy', {'changed': True})]:
            changed = {**original, key: value}
            self.assertFalse(evaluator.evaluate(approval_request('Review', action=changed), condition.config))

    def test_exact_and_range_keep_boolean_numeric_values_distinct(self):
        original = action('git status')
        original['policy'] = {'flag': True}
        changed = deepcopy(original)
        changed['policy']['flag'] = 1
        request = approval_request('Review', action=changed)
        for condition in (ToolApprovalConditions.exact_condition(original),
                          CommandRangeApproval.condition(original, (('git', 'status'),))):
            evaluator = ToolApprovalConditions.evaluators()[condition.implementation]
            self.assertFalse(evaluator.evaluate(request, condition.config))

    def test_exact_matches_whole_arguments_and_range_requires_a_reviewable_chain(self):
        original = action()
        condition = ToolApprovalConditions.exact_condition(original)
        evaluator = ToolApprovalConditions.evaluators()[condition.implementation]
        self.assertTrue(evaluator.evaluate(approval_request('Review', action=original), condition.config))
        self.assertFalse(evaluator.evaluate(approval_request('Review', action=action('git status | head')), condition.config))
        for command in ('', 'A=1 git status', 'if true; then echo yes; fi', 'git status &&',
                        'git status ||| head', 'git status `pwd`', 'git status !previous'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                CommandChain.parse(command)
        with self.assertRaises(ValueError):
            CommandRangeApproval.condition(original, (('git', 'log'), ('head',)))
        with self.assertRaises(ValueError):
            CommandChain.prefixes('git status | head')

    def test_choices_preserve_questions_denials_and_uncertain_effect_approvals(self):
        request = approval_request('Review', action=action(), category='tool.execute')
        choices = approval_choices(request, str)
        self.assertEqual([c.match for c in choices], [None, ApprovalMatch.EXACT, ApprovalMatch.COMMAND_RANGE, None])
        self.assertEqual([c.option_id for c in choices], ['approve', 'approve', 'approve', 'deny'])
        for changed in (replace(request, kind='confirmation'),
                        replace(request, category=InteractionRepository.RETRY_CATEGORY)):
            self.assertEqual(len(approval_choices(changed, str)), 2)
            with self.assertRaises(ValueError):
                ToolApprovalConditions.remember(changed, ApprovalSelection(RememberScope.SESSION, ApprovalMatch.EXACT))
        complex_request = replace(request, action=action('git status > output.txt'))
        self.assertEqual([c.match for c in approval_choices(complex_request, str)], [None, ApprovalMatch.EXACT, None])

    def test_selection_retains_unknown_and_receipt_identity_and_fixed_environment(self):
        request = approval_request('Review', action=action(), category='tool.execute')
        choice = ApprovalSelection(RememberScope.SESSION, ApprovalMatch.EXACT)
        rule = ToolApprovalConditions.remember(request, choice)
        self.assertEqual(rule['basis'], ApprovalRule.EXPLICIT_UNKNOWN)
        self.assertNotIn('max_risk', rule)
        self.assertEqual(rule, ToolApprovalConditions.remember(request, choice))
        known = ToolApprovalConditions.remember(replace(request, risk=20, risk_scheme='app'), choice)
        self.assertEqual(known['max_risk'], 20)
        self.assertNotIn('basis', known)
        for field in ('cwd', 'env'):
            changed = dict(request.action['arguments'])
            changed.pop(field)
            with self.assertRaises(ValueError):
                ToolApprovalConditions.replace_arguments(request.action, changed)

    def test_presets_never_transport_approval_proof_but_preserve_target_rules(self):
        request = approval_request('Review', action=action())
        evidence = RememberedApproval(1, ApprovalScope('project', 'session'),
            ToolApprovalConditions.exact_condition(request.action),
            ApprovalOrigin('run', request.id, request.revision, request.fingerprint, request.created_at), None)
        rule = ApprovalRule('remembered', 'tool.execute', remembered=evidence, basis=ApprovalRule.EXPLICIT_UNKNOWN)
        current = ProjectConfig(policies={'approval': {'enabled': True, 'risk_scheme': 'app', 'rules': [rule.to_dict()]}})
        portable = ApprovalPresets.portable(current)
        self.assertEqual(portable.policies['approval']['rules'], [])
        self.assertEqual(len(current.policies['approval']['rules']), 1)
        with self.assertRaises(ValueError):
            ApprovalPresets.validate(current)
        applied = ApprovalPresets.apply(portable, current)
        self.assertEqual(applied.policies['approval']['rules'], [rule.to_dict()])


class HubApprovalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.effects = []
        self.provider = ScriptedCompletion()
        async def effect(arguments):
            self.effects.append(deepcopy(arguments))
            return 'performed once'
        tool = Tool('shell_execute', 'Test action; never executes a shell',
            {'type': 'object', 'required': ['command'], 'properties': {'command': {'type': 'string'}}},
            effect, contract=replace(TEST_CONTRACT, approval_required=True))
        self.app = LargeLanguageModel(root, components=[RuntimeTools(ToolRegistry((tool,)))],
            engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=self.provider))},
            services=BackendServices(approval_evaluators=ToolApprovalConditions.evaluators(),
                tool_runtime=ToolRuntime(classify=lambda call: ToolClassification('tool.execute', 'app', None))))
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate('Approvals', components=['tools'],
            config=ProjectConfig(policies={'approval': {'enabled': True, 'risk_scheme': 'app', 'rules': []}}))
        await self.project.components.tools.aenable('shell_execute')
        await self.project.agents.acreate({'completion': {'model': 'test/model'}, 'engine': 'loop',
                                          'tools': ['shell_execute']}, identifier='assistant')
        self.session = await self.project.sessions.acreate('Session', agent_ids=['assistant'])
        # Exercise Hub entry points with real public services, without unrelated
        # startup assets, UI polling or additional model calls.
        self.runtime = HubRuntime.__new__(HubRuntime)
        self.runtime.backend, self.runtime.project = self.app, self.project
        self.runtime.sessions = {self.session.id: self.session}
        self.runtime.builtin_tools = None
        self.runtime.snapshot_reader = NS(invalidate=Mock())
        self.runtime.dirty = asyncio.Event()
        self.runtime.approval_flow = ApprovalFlow(self.runtime)

    async def pause(self, command='git status --short', session=None):
        session = session or self.session
        self.provider.responses.append([chunk(calls=[call(json.dumps({'command': command}), name='shell_execute')], finish='tool_calls')])
        run, = await (await session.run.submit('Inspect the project', recipients=['assistant'])).wait(timeout=20)
        self.assertEqual(run.data.status, 'paused', run.data.error)
        request, = await run.ainteractions()
        return run, request

    async def answer(self, run, request, selection=None, session=None):
        session = session or self.session
        self.provider.responses.append([chunk('done', finish='stop')])
        before = len(self.effects)
        ack = []
        await self.runtime.answer_question(self.project.id, session.id, run.id, request.to_dict(),
            'approve', None, selection, acknowledged=lambda: ack.append(len(self.effects)))
        await session.run.wait_idle()
        self.assertEqual(ack, [before])
        self.assertEqual(len(self.effects), before + 1)

    async def test_remembered_session_rule_and_automatic_resume_reuse_saved_completion(self):
        run, request = await self.pause()
        await self.answer(run, request, ApprovalSelection(RememberScope.SESSION, ApprovalMatch.COMMAND_RANGE, (('git', 'status'),)))
        rule, = await self.project.approvals.alist()
        self.assertEqual(rule['remembered']['scope']['session_id'], self.session.id)
        self.assertEqual(rule['basis'], 'explicit_unknown')
        later, _ = await self.pause('git status --porcelain')
        response, = await later.ainteraction_responses()
        self.assertEqual(response.actor, 'policy')
        self.assertEqual(len(self.effects), 1)
        self.provider.responses.append([chunk('done again', finish='stop')])
        auto = AutomaticApprovals(self.runtime)
        self.addAsyncCleanup(auto.close)
        auto.observe(later.data)
        auto.observe(later.data)
        await asyncio.gather(*auto.tasks)
        await self.session.run.wait_idle()
        self.assertEqual(len(self.effects), 2)
        self.assertEqual(len(self.provider.requests), 4)
        self.assertFalse(auto.tasks)
        changed, _ = await self.pause('git log')
        self.assertEqual(await changed.ainteraction_responses(), [])
        other = await self.project.sessions.acreate('Other', agent_ids=['assistant'])
        foreign, _ = await self.pause(session=other)
        self.assertEqual(await foreign.ainteraction_responses(), [])

    async def test_project_rule_applies_to_another_session_and_revoke_preserves_issued_decision(self):
        run, request = await self.pause()
        await self.answer(run, request, ApprovalSelection(RememberScope.PROJECT, ApprovalMatch.EXACT))
        rule, = await self.project.approvals.alist()
        other = await self.project.sessions.acreate('Other', agent_ids=['assistant'])
        issued, _ = await self.pause(session=other)
        self.assertEqual(len(await issued.ainteraction_responses()), 1)
        catalog = await self.runtime.manage_approvals(self.project.id, ApprovalAction.REVOKE,
            {'id': rule['id'], 'revision': rule['remembered']['revision'], 'include_interactions': True})
        self.assertIsNotNone(catalog.rules[0].remembered.revoked_at)
        self.assertEqual(len(await issued.ainteraction_responses()), 1)
        with self.assertRaises(ValueError):
            await self.runtime.manage_approvals(self.project.id, ApprovalAction.UPDATE,
                {'id': rule['id'], 'revision': rule['remembered']['revision'], 'arguments': {'command': 'git log'}})
        later, _ = await self.pause()
        self.assertEqual(await later.ainteraction_responses(), [])

    async def test_management_reclassification_is_not_approval_and_cancel_keeps_rule(self):
        run, request = await self.pause()
        await self.answer(run, request, ApprovalSelection(RememberScope.PROJECT, ApprovalMatch.EXACT))
        later, request = await self.pause('git log')
        payload = {'session_id': self.session.id, 'run_id': later.id, 'request': request.to_dict(),
                   'include_interactions': True, 'risk': 10, 'risk_scheme': 'app'}
        catalog = await self.runtime.manage_approvals(self.project.id, ApprovalAction.RECLASSIFY, payload)
        changed, = await later.ainteractions()
        self.assertNotEqual(changed.id, request.id)
        self.assertEqual(changed.risk, 10)
        self.assertEqual(await later.ainteraction_responses(), [])
        self.assertFalse(catalog.resumed)
        self.assertEqual(len(self.effects), 1)
        await self.runtime.manage_approvals(self.project.id, ApprovalAction.CANCEL, {**payload, 'request': changed.to_dict()})
        view, = await later.ainteraction_views()
        self.assertFalse(view.can_respond)
        self.assertEqual(len(await self.project.approvals.alist()), 1)

    async def test_receipt_and_concurrent_resume_do_not_duplicate_effects(self):
        run, request = await self.pause()
        selection = ApprovalSelection(RememberScope.SESSION, ApprovalMatch.EXACT)
        options = dict(remember=ToolApprovalConditions.remember(request, selection),
                       request_key=ToolApprovalConditions.request_key(request))
        first = await run.arespond(request.respond('approve'), **options)
        self.assertEqual(first, await run.arespond(request.respond('approve'), **options))
        self.provider.responses.append([chunk('done', finish='stop')])
        async def resume():
            async with self.runtime.approval_flow.lock(self.session.id, run.id):
                return await self.runtime.approval_flow.resume_approved(self.session, run)
        results = await asyncio.gather(resume(), resume())
        await self.session.run.wait_idle()
        self.assertEqual(results.count(True), 1)
        self.assertEqual(len(self.effects), 1)
        self.assertEqual(len(await self.project.approvals.alist()), 1)

    async def test_rule_prefix_update_uses_revision_and_keeps_environment(self):
        run, request = await self.pause()
        await self.answer(run, request, ApprovalSelection(RememberScope.SESSION, ApprovalMatch.COMMAND_RANGE, (('git', 'status'),)))
        rule, = await self.project.approvals.alist()
        payload = {'id': rule['id'], 'revision': rule['remembered']['revision'], 'prefixes': 'git status --short'}
        catalog = await self.runtime.manage_approvals(self.project.id, ApprovalAction.UPDATE, payload)
        self.assertGreater(catalog.rules[0].remembered.revision, rule['remembered']['revision'])
        with self.assertRaises(ValueError):
            await self.runtime.manage_approvals(self.project.id, ApprovalAction.UPDATE, payload)
        later, _ = await self.pause('git status --porcelain')
        self.assertEqual(await later.ainteraction_responses(), [])

    async def test_apply_run_policy_explicitly_resumes_only_after_user_risk_assessment(self):
        config = (await self.project.aget_data()).config
        config.configure_policies({'approval': {'rules': [ApprovalRule('low', 'tool.execute', max_risk=20).to_dict()]}})
        await self.project.asave(config=config)
        run, request = await self.pause()
        payload = {'session_id': self.session.id, 'run_id': run.id, 'request': request.to_dict(),
                   'include_interactions': True, 'risk': 10, 'risk_scheme': 'app'}
        before = await self.runtime.manage_approvals(self.project.id, ApprovalAction.APPLY, payload)
        self.assertFalse(before.resumed)
        self.assertEqual(self.effects, [])
        await self.runtime.manage_approvals(self.project.id, ApprovalAction.RECLASSIFY, payload)
        self.provider.responses.append([chunk('done', finish='stop')])
        after = await self.runtime.manage_approvals(self.project.id, ApprovalAction.APPLY, payload)
        await self.session.run.wait_idle()
        self.assertTrue(after.resumed)
        self.assertEqual(len(self.effects), 1)

    async def test_manager_can_resume_an_existing_user_approval_without_replacing_it(self):
        run, request = await self.pause()
        response = await run.arespond(request.respond('approve'))
        self.assertEqual(self.effects, [])
        self.provider.responses.append([chunk('done', finish='stop')])
        catalog = await self.runtime.manage_approvals(self.project.id, ApprovalAction.APPLY,
            {'session_id': self.session.id, 'run_id': run.id, 'include_interactions': True})
        await self.session.run.wait_idle()
        self.assertTrue(catalog.resumed)
        self.assertEqual(await run.ainteraction_responses(), [response])
        self.assertEqual(len(self.effects), 1)
