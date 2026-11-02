"""Remembered Project rules, issuance/revocation races and existing resume receipts."""

import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

from llm.components.tools import ToolClassification, ToolContract, ToolEffect, ToolIsolation
from llm.core.policies import ApprovalPolicy
from llm.services.runtime.approval import ApprovalEvaluator, SchemaApprovalEvaluator
from llm.services.runtime.interactions import InteractionRepository
from llm.services.runtime.tools import ToolRuntime
from tests.llm import test_long_running
from tests.llm.test_loop import chunk, call


CONTRACT = ToolContract(revision='test-1', effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE,
                       approval_required=True, operation_key_required=False, retry_fields=())


def remember(*, scope='session', identifier='remember-write', implementation='schema', config=None):
    return {
        'id': identifier, 'category': 'file.write', 'max_risk': 20, 'scope': scope,
        'condition': {'implementation': implementation, 'revision': 'test-1', 'config': config if config is not None else {
            'type': 'object', 'required': ['action'], 'properties': {'action': {
                'type': 'object', 'required': ['tool', 'arguments'], 'properties': {
                    'tool': {'const': 'act'}, 'arguments': {'const': {}}}}}}},
    }


class GateEvaluator(SchemaApprovalEvaluator):
    def __init__(self):
        super().__init__(revision='test-1')
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.request = None

    async def evaluate(self, request, config):
        self.request = request
        self.started.set()
        await self.release.wait()
        return super().evaluate(request, config)


class RememberedApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def setup(self, *, evaluator=None):
        self.effects = []
        self.classification = {'scheme': 'test-v1', 'risk': 20, 'deny': False}

        async def act(arguments):
            self.effects.append(deepcopy(arguments))
            return 'effect completed'

        async def authorize(call):
            return not self.classification['deny']
        runtime = ToolRuntime(
            classify=lambda call: ToolClassification('file.write', self.classification['scheme'], self.classification['risk']),
            authorize=authorize,
        )
        self.app, self.session, self.model = await test_long_running.LongRunningTests.setup_app(
            self, act, [], runtime, tool_contract=CONTRACT,
            policies={'approval': {'enabled': True, 'risk_scheme': 'test-v1', 'rules': []}},
        )
        self.project = self.session.project
        self.app.project_manager.approval_evaluators['schema'] = evaluator or SchemaApprovalEvaluator(revision='test-1')

    async def pause(self, session=None, *, arguments='{}'):
        session = session or self.session
        self.model.responses.append([chunk(calls=[call(arguments, name='act')], finish='tool_calls')])
        run, = await (await session.run.submit('perform action', recipients=['loop'])).wait(timeout=15)
        self.assertEqual(run.data.status, 'paused', run.data.error)
        return run

    async def finish(self, run, session=None):
        session = session or self.session
        self.model.responses.append([chunk('done', finish='stop')])
        completed, = await (await session.run.resume(run.id, agent_id='loop')).wait(timeout=15)
        self.assertEqual(completed.data.status, 'completed', completed.data.error)
        return completed

    async def seed(self, *, scope='session', evaluator=None):
        await self.setup(evaluator=evaluator)
        first = await self.pause()
        request, = await first.ainteractions()
        await first.arespond(request.respond('approve'), remember=remember(scope=scope), request_key='remember-1')
        await self.finish(first)
        rule, = await self.project.approvals.alist()
        return rule

    async def blocked(self, gate):
        pending = asyncio.create_task(self.pause())
        self.addAsyncCleanup(self.drain, pending, gate)
        await asyncio.wait_for(gate.started.wait(), 15)
        return pending

    async def drain(self, pending, gate):
        gate.release.set()
        await asyncio.gather(pending, return_exceptions=True)

    async def test_once_creates_no_rule_and_remembered_policy_requires_explicit_resume(self):
        await self.setup()
        run = await self.pause()
        request, = await run.ainteractions()
        await run.arespond(request.respond('approve'))
        self.assertEqual(await self.project.approvals.alist(), [])
        self.assertEqual(self.effects, [])
        await self.finish(run)
        later = await self.pause()
        request, = await later.ainteractions()
        await later.arespond(request.respond('approve'), remember=remember())
        self.assertEqual(len(self.effects), 1)
        await self.finish(later)
        automatic = await self.pause()
        response, = await automatic.ainteraction_responses()
        self.assertEqual(response.actor, 'policy')
        self.assertEqual(response.policy_id, 'remember-write')
        issued_rule, = automatic.data.metadata['policies']['approval']['rules']
        self.assertEqual(issued_rule['id'], response.policy_id)
        self.assertEqual(issued_rule['remembered']['condition']['revision'], 'test-1')
        self.assertEqual(issued_rule['remembered']['revision'], 1)
        self.assertEqual(len(self.effects), 2)
        await self.finish(automatic)
        self.assertEqual(len(self.effects), 3)

    async def test_session_scope_and_arguments_do_not_widen(self):
        await self.seed()
        other = await self.project.sessions.acreate(agent_ids=['loop'])
        run = await self.pause(other)
        self.assertEqual(await run.ainteraction_responses(), [])
        run = await self.pause(arguments='{"different":true}')
        self.assertEqual(await run.ainteraction_responses(), [])
        run = await self.pause()
        self.assertEqual(len(await run.ainteraction_responses()), 1)

    async def test_project_scope_allows_another_session_but_not_another_project(self):
        await self.seed(scope='project')
        other = await self.project.sessions.acreate(agent_ids=['loop'])
        self.assertEqual(len(await (await self.pause(other)).ainteraction_responses()), 1)
        project = await self.app.projects.acreate(components=['tools'], config={'policies': {
            'approval': {'enabled': True, 'risk_scheme': 'test-v1', 'rules': []}}})
        await project.components.tools.aenable('act')
        await project.agents.acreate({'completion': {'model': 'test/model'}, 'engine': 'loop', 'tools': ['act']}, identifier='loop')
        session = await project.sessions.acreate(agent_ids=['loop'])
        self.assertEqual(await (await self.pause(session)).ainteraction_responses(), [])

    async def test_unknown_risk_scheme_mismatch_and_excess_risk_still_ask(self):
        await self.seed()
        for scheme, risk in [('test-v1', None), ('different', 1), ('test-v1', 21)]:
            self.classification.update(scheme=scheme, risk=risk)
            with self.subTest(scheme=scheme, risk=risk):
                self.assertEqual(await (await self.pause()).ainteraction_responses(), [])

    async def test_revoke_during_evaluation_prevents_issuance(self):
        gate = GateEvaluator()
        rule = await self.seed(evaluator=gate)
        pending = await self.blocked(gate)
        revoked = await self.project.approvals.arevoke(rule['id'], expected_revision=rule['remembered']['revision'])
        self.assertIsNotNone(revoked['remembered']['revoked_at'])
        self.assertEqual(revoked, await self.project.approvals.arevoke(rule['id'], expected_revision=revoked['remembered']['revision']))
        gate.release.set()
        run = await pending
        self.assertEqual(await run.ainteraction_responses(), [])
        self.assertEqual(len(self.effects), 1)
        self.assertIsNone(run.data.metadata['policies']['approval']['rules'][0]['remembered']['revoked_at'])

    async def test_update_during_evaluation_cannot_reuse_the_old_decision(self):
        gate = GateEvaluator()
        rule = await self.seed(evaluator=gate)
        pending = await self.blocked(gate)
        changed = await self.project.approvals.aupdate(rule['id'], {'max_risk': 30}, expected_revision=rule['remembered']['revision'])
        self.assertNotEqual(rule['remembered']['revision'], changed['remembered']['revision'])
        gate.release.set()
        self.assertEqual(await (await pending).ainteraction_responses(), [])
        self.assertEqual(len(await (await self.pause()).ainteraction_responses()), 1)
        with self.assertRaisesRegex(ValueError, 'revision changed'):
            await self.project.approvals.arevoke(rule['id'], expected_revision=rule['remembered']['revision'])

    async def test_evaluator_change_and_false_result_do_not_approve(self):
        gate = GateEvaluator()
        await self.seed(evaluator=gate)
        pending = await self.blocked(gate)
        gate.revision = 'changed'
        gate.release.set()
        self.assertEqual(await (await pending).ainteraction_responses(), [])
        self.assertEqual(await (await self.pause()).ainteraction_responses(), [])

    async def test_revocation_after_issuance_does_not_cancel_existing_response(self):
        rule = await self.seed()
        run = await self.pause()
        response, = await run.ainteraction_responses()
        revoked = await self.project.approvals.arevoke(rule['id'], expected_revision=rule['remembered']['revision'])
        self.assertEqual(await run.ainteraction_responses(), [response])
        await self.finish(run)
        self.assertEqual(len(self.effects), 2)
        self.assertEqual(await (await self.pause()).ainteraction_responses(), [])
        with self.assertRaisesRegex(ValueError, 'reactivated'):
            await self.project.approvals.aupdate(rule['id'], {'max_risk': 30}, expected_revision=revoked['remembered']['revision'])

    async def test_cancelled_request_cannot_be_approved_after_evaluation(self):
        gate = GateEvaluator()
        await self.seed(evaluator=gate)
        pending = await self.blocked(gate)
        run = await self.session.run.aload(gate.request.source['run_id'])
        await run.acancel_interaction(gate.request)
        gate.release.set()
        self.assertEqual(await (await pending).ainteraction_responses(), [])
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            await self.session.run.resume(run.id, agent_id='loop')
        self.assertEqual(len(self.effects), 1)

    async def test_concurrent_cancel_and_resume_have_one_winner(self):
        await self.seed()
        run = await self.pause()
        request, = await run.ainteractions()
        self.model.responses.append([chunk('done', finish='stop')])
        cancel, resume = await asyncio.gather(
            run.acancel_interaction(request), self.session.run.resume(run.id, agent_id='loop'),
            return_exceptions=True,
        )
        if isinstance(resume, BaseException):
            self.assertNotIsInstance(cancel, BaseException)
            self.assertEqual(len(self.effects), 1)
        else:
            self.assertIsInstance(cancel, ValueError)
            completed, = await resume.wait(timeout=15)
            self.assertEqual(completed.data.status, 'completed', completed.data.error)
            self.assertEqual(len(self.effects), 2)

    async def test_user_response_during_evaluation_is_not_overwritten(self):
        gate = GateEvaluator()
        await self.seed(evaluator=gate)
        pending = await self.blocked(gate)
        run = await self.session.run.aload(gate.request.source['run_id'])
        response = await run.arespond(gate.request.respond('deny'))
        gate.release.set()
        self.assertEqual(await (await pending).ainteraction_responses(), [response])

    async def test_response_and_rule_roll_back_together_and_client_key_cannot_widen(self):
        await self.setup()
        run = await self.pause()
        request, = await run.ainteractions()
        response = request.respond('approve')
        with patch.object(InteractionRepository, 'respond', side_effect=OSError('injected response failure')):
            with self.assertRaises(OSError):
                await run.arespond(response, remember=remember(), request_key='click')
        self.assertEqual(await self.project.approvals.alist(), [])
        self.assertEqual(await run.ainteraction_responses(), [])
        await run.arespond(response, remember=remember(), request_key='click')
        self.assertEqual(await run.arespond(response, remember=remember(), request_key='click'), response)
        self.assertEqual(len(await self.project.approvals.alist()), 1)
        with self.assertRaisesRegex(ValueError, 'different content'):
            await run.arespond(response, remember=remember(scope='project'), request_key='click')
        self.assertEqual(self.effects, [])

    async def test_config_cannot_forge_remove_or_edit_managed_rules(self):
        await self.seed()
        for change in ('remove', 'edit', 'revive'):
            config = self.project.data.config.to_dict()
            rules = config['policies']['approval']['rules']
            if change == 'remove':
                rules.clear()
            elif change == 'edit':
                rules[0]['max_risk'] = 999
            else:
                rules[0]['remembered']['revision'] += 1
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'approval APIs'):
                await self.project.asave(config=config)
        with self.assertRaisesRegex(ValueError, 'approval APIs'):
            await self.app.projects.acreate(components=[], config=self.project.data.config)

    async def test_technical_deny_and_changed_classification_still_block_resume(self):
        await self.seed()
        for change in ('deny', 'risk'):
            self.classification.update(deny=False, risk=20)
            run = await self.pause()
            self.classification[change] = True if change == 'deny' else 21
            resumed, = await (await self.session.run.resume(run.id, agent_id='loop')).wait(timeout=15)
            self.assertEqual(resumed.data.status, 'failed')
        self.assertEqual(len(self.effects), 1)

    async def test_clone_does_not_inherit_remembered_authority(self):
        await self.seed(scope='project')
        await self.session.run.shutdown()
        clone = await self.project.aclone()
        self.assertEqual(await clone.approvals.alist(), [])
        session = await clone.sessions.acreate(agent_ids=['loop'])
        self.assertEqual(await (await self.pause(session)).ainteraction_responses(), [])

    async def test_reopen_preserves_rules_responses_and_revocation(self):
        from llm.llm import LargeLanguageModel, BackendServices
        rule = await self.seed()
        run = await self.pause()
        response, = await run.ainteraction_responses()
        original = self.app
        await original.shutdown()
        self.app = LargeLanguageModel(
            original.workspace,
            components=[original.project_manager.components.get(name)
                        for name in original.project_manager.components.names()],
            engines={name: original.engines.get(name) for name in original.engines.names()},
            services=BackendServices(tool_runtime=original.services.tool_runtime,
                approval_evaluators={'schema': SchemaApprovalEvaluator(revision='test-1')}),
        )
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.aload(self.project.id)
        self.session = await self.project.sessions.aload(self.session.id)
        self.assertEqual(await self.project.approvals.alist(), [rule])
        reopened = await self.session.run.aload(run.id)
        self.assertEqual(await reopened.ainteraction_responses(), [response])
        await self.project.approvals.arevoke(rule['id'], expected_revision=rule['remembered']['revision'])
        await self.finish(reopened)
        self.assertEqual(len(self.effects), 2)
        self.assertEqual(await (await self.pause()).ainteraction_responses(), [])

    async def test_missing_evaluator_does_not_prevent_revocation(self):
        rule = await self.seed()
        self.app.project_manager.approval_evaluators.clear()
        self.assertEqual(await (await self.pause()).ainteraction_responses(), [])
        revoked = await self.project.approvals.arevoke(rule['id'], expected_revision=rule['remembered']['revision'])
        self.assertIsNotNone(revoked['remembered']['revoked_at'])

    async def test_evaluator_failure_false_and_nonboolean_never_approve(self):
        class Evaluator(SchemaApprovalEvaluator):
            def evaluate(self, request, config):
                if isinstance(self.result, Exception):
                    raise self.result
                return self.result
        evaluator = Evaluator(revision='test-1')
        await self.seed(evaluator=evaluator)
        for result in (False, 'yes', RuntimeError('failed evaluator')):
            evaluator.result = result
            with self.subTest(result=result):
                self.assertEqual(await (await self.pause()).ainteraction_responses(), [])
        self.assertEqual(len(self.effects), 1)

    async def test_rule_added_after_admission_cannot_approve_current_run(self):
        gate = GateEvaluator()
        await self.seed(evaluator=gate)
        pending = await self.blocked(gate)
        await self.project.aconfigure_policies({'approval': {'rules': [
            *self.project.data.config.policies['approval']['rules'],
            {'id': 'later-rule', 'category': 'file.write', 'max_risk': 99}]}})
        rule, = await self.project.approvals.alist()
        await self.project.approvals.arevoke(rule['id'], expected_revision=rule['remembered']['revision'])
        gate.release.set()
        self.assertEqual(await (await pending).ainteraction_responses(), [])

    async def test_rule_changed_then_restored_does_not_reuse_inflight_evaluation(self):
        gate = GateEvaluator()
        rule = await self.seed(evaluator=gate)
        pending = await self.blocked(gate)
        changed = await self.project.approvals.aupdate(rule['id'], {'max_risk': 30}, expected_revision=rule['remembered']['revision'])
        restored = await self.project.approvals.aupdate(rule['id'], {'max_risk': 20}, expected_revision=changed['remembered']['revision'])
        self.assertEqual(restored['remembered']['revision'], rule['remembered']['revision'] + 2)
        gate.release.set()
        self.assertEqual(await (await pending).ainteraction_responses(), [])
        self.assertEqual(len(await (await self.pause()).ainteraction_responses()), 1)

    async def test_execution_limit_change_still_rejects_resume(self):
        await self.seed()
        run = await self.pause()
        await self.project.aconfigure_policies({'tools': {'max_calls': 1}})
        with self.assertRaisesRegex(ValueError, 'changed'):
            await self.session.run.resume(run.id, agent_id='loop')
        self.assertEqual(len(self.effects), 1)

    async def test_graph_tool_uses_same_remember_and_revocation_contract(self):
        from llm.llm import GraphEngine, ToolNode, WorkflowComponent, WorkflowGraph
        await self.setup()
        self.app.engines.register('graph', GraphEngine(handlers={'tool': ToolNode()}))
        self.app.project_manager.components.register(WorkflowComponent())
        await self.project.asave(components=['tools', 'workflows'])
        graph = (WorkflowGraph(entry='act').node('act', 'tool', tool='act', arguments={})
                 .node('end', 'end').connect('act', 'end').to_dict())
        await self.project.components.workflows.acreate(graph, identifier='flow')
        await self.project.agents.acreate(
            {'engine': 'graph', 'engine_options': {'workflow': 'flow'}}, identifier='graph-agent')
        session = await self.project.sessions.acreate(agent_ids=['graph-agent'])
        first, = await (await session.run.submit('act', recipients=['graph-agent'])).wait()
        self.assertEqual(first.data.status, 'paused', first.data.error)
        request, = await first.ainteractions()
        await first.arespond(request.respond('approve'), remember=remember())
        done, = await (await session.run.resume(first.id, agent_id='graph-agent')).wait()
        self.assertEqual(done.data.status, 'completed', done.data.error)
        later, = await (await session.run.submit('act', recipients=['graph-agent'])).wait()
        response, = await later.ainteraction_responses()
        self.assertEqual(response.actor, 'policy')
        rule, = await self.project.approvals.alist()
        await self.project.approvals.arevoke(rule['id'], expected_revision=rule['remembered']['revision'])
        done, = await (await session.run.resume(later.id, agent_id='graph-agent')).wait()
        self.assertEqual(done.data.status, 'completed', done.data.error)
        self.assertEqual(len(self.effects), 2)

    async def test_custom_child_validates_its_own_unknown_to_parent_option(self):
        class CustomEvaluator(ApprovalEvaluator):
            def describe_config(self):
                return {'type': 'object', 'additionalProperties': False, 'required': ['company_marker'],
                        'properties': {'company_marker': {'type': 'string', 'const': 'authorized'}}}
            def evaluate(self, request, config):
                return config['company_marker'] == 'authorized'
        await self.setup(evaluator=CustomEvaluator(revision='test-1'))
        run = await self.pause()
        request, = await run.ainteractions()
        with self.assertRaises(Exception):
            await run.arespond(request.respond('approve'), remember=remember(config={'wrong': True}))
        self.assertEqual(await self.project.approvals.alist(), [])
        await run.arespond(request.respond('approve'), remember=remember(config={'company_marker': 'authorized'}))
        await self.finish(run)
        self.assertEqual(len(await (await self.pause()).ainteraction_responses()), 1)


class ApprovalSchemaTests(unittest.TestCase):
    def test_missing_policy_does_not_materialize_remembered_values(self):
        from llm.core.policies import normalize_policies
        self.assertEqual(normalize_policies({}), {})
        explicit = {'approval': {'rules': [{'id': 'read', 'category': 'read', 'max_risk': 1}], 'risk_scheme': 'test'}}
        self.assertEqual(normalize_policies(explicit), explicit)
        self.assertNotIn('remembered', ApprovalPolicy.from_dict(explicit['approval']).rules[0].to_dict())
        self.assertNotIn('remembered', ApprovalPolicy.from_dict(explicit['approval']).to_dict()['rules'][0])

    def test_execution_projection_excludes_only_approval_issuance(self):
        from llm.core.configuration import bind_execution_config
        value = {'policies': {'approval': {'enabled': True}, 'tools': {'max_calls': 2},
                 'run': {'timeout_seconds': 10}}, 'parameters': {'opaque': True}, 'data': {'x': 1}}
        expected = deepcopy(value)
        del expected['policies']['approval']
        self.assertEqual(bind_execution_config(value), expected)
        self.assertIn('approval', value['policies'])
