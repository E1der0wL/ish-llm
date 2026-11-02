"""Explicit unknown grants and Application-provided numeric assessment lifecycle."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

from jsonschema import ValidationError

from llm.core.policies import ApprovalPolicy, normalize_policies
from llm.core.interactions import approval_request
from llm.services.runtime.interaction_records import InteractionRecord
from llm.services.runtime.interactions import InteractionRepository
from tests.llm import test_remembered_approvals as fixtures


def unknown_rule(**kwargs):
    value = fixtures.remember(**kwargs)
    del value['max_risk']
    value['basis'] = 'explicit_unknown'
    return value


class AssessmentTests(unittest.IsolatedAsyncioTestCase):
    setup = fixtures.RememberedApprovalTests.setup
    pause = fixtures.RememberedApprovalTests.pause
    finish = fixtures.RememberedApprovalTests.finish
    blocked = fixtures.RememberedApprovalTests.blocked
    drain = fixtures.RememberedApprovalTests.drain

    async def remember_unknown(self, *, evaluator=None, scheme=None):
        await self.setup(evaluator=evaluator)
        self.classification.update(scheme=scheme, risk=None)
        run = await self.pause()
        request, = await run.ainteractions()
        await run.arespond(request.respond('approve'), remember=unknown_rule())
        await self.finish(run)
        rule, = await self.project.approvals.alist()
        return rule

    async def test_unknown_grant_keeps_unknown_and_requires_explicit_resume(self):
        rule = await self.remember_unknown()
        self.assertNotIn('max_risk', rule)
        run = await self.pause()
        request, = await run.ainteractions()
        response, = await run.ainteraction_responses()
        self.assertIsNone(request.risk)
        self.assertIsNone(request.risk_scheme)
        self.assertEqual(response.actor, 'policy')
        self.assertEqual(len(self.effects), 1)
        await self.finish(run)
        self.assertEqual(len(self.effects), 2)

    async def test_unknown_rule_never_applies_to_known_risk_or_other_scope(self):
        await self.remember_unknown()
        self.classification.update(scheme='test-v1', risk=0)
        self.assertEqual(await (await self.pause()).ainteraction_responses(), [])
        self.classification.update(scheme=None, risk=None)
        session = await self.project.sessions.acreate(agent_ids=['loop'])
        self.assertEqual(await (await self.pause(session)).ainteraction_responses(), [])

    async def test_once_unknown_approval_creates_no_rule(self):
        await self.setup()
        self.classification.update(risk=None, scheme=None)
        run = await self.pause()
        request, = await run.ainteractions()
        await run.arespond(request.respond('approve'))
        self.assertEqual(await self.project.approvals.alist(), [])
        await self.finish(run)
        self.assertEqual(await (await self.pause()).ainteraction_responses(), [])

    async def test_unknown_allowance_needs_no_scheme_but_keeps_condition_and_basis(self):
        await self.setup()
        config = (await self.project.aget_data()).config.to_dict()
        del config['policies']['approval']['risk_scheme']
        await self.project.asave(config=config)
        self.classification.update(risk=None, scheme=None)
        first = await self.pause()
        request, = await first.ainteractions()
        await first.arespond(request.respond('approve'), remember=unknown_rule())
        await self.finish(first)
        rule, = await self.project.approvals.alist()
        for changes in ({'max_risk': 100}, {'basis': 'threshold'}):
            with self.assertRaises(ValidationError):
                await self.project.approvals.aupdate(rule['id'], changes, expected_revision=1)
        match = await self.pause()
        self.assertEqual((await match.ainteraction_responses())[0].actor, 'policy')
        await self.finish(match)
        changed = await self.pause(arguments='{"path":"elsewhere"}')
        self.assertEqual(await changed.ainteraction_responses(), [])

    async def test_unknown_grant_revocation_wins_over_inflight_evaluation(self):
        gate = fixtures.GateEvaluator()
        rule = await self.remember_unknown(evaluator=gate)
        task = await self.blocked(gate)
        await self.project.approvals.arevoke(rule['id'], expected_revision=rule['remembered']['revision'])
        gate.release.set()
        self.assertEqual(await (await task).ainteraction_responses(), [])

    async def test_reclassification_does_not_approve_or_resume(self):
        await self.setup()
        await self.project.aconfigure_policies({'approval': {'rules': [
            {'id': 'low', 'category': 'file.write', 'max_risk': 20}]}})
        self.classification['risk'] = None
        run = await self.pause()
        original, = await run.ainteractions()
        changed = await run.areclassify(original, risk=10, risk_scheme='test-v1')
        self.assertNotEqual(changed.id, original.id)
        self.assertEqual(changed.revision, original.revision + 1)
        self.assertEqual(changed.action, original.action)
        self.assertEqual(changed.binding, original.binding)
        self.assertEqual(await run.ainteraction_responses(), [])
        self.assertEqual(self.effects, [])
        policy, = await run.apply_approval()
        self.assertEqual(policy.actor, 'policy')
        self.assertEqual(policy.request_id, changed.id)
        self.assertEqual(self.effects, [])
        await self.finish(run)
        self.assertEqual(len(self.effects), 1)

    async def test_reclassification_invalidates_prior_approval_but_preserves_evidence(self):
        await self.setup()
        run = await self.pause()
        original, = await run.ainteractions()
        response = await run.arespond(original.respond('approve'))
        changed = await run.areclassify(original, risk=50, risk_scheme='another-explicit-scheme')
        self.assertEqual(await run.ainteraction_responses(), [])
        self.assertEqual(InteractionRepository().control(run.data, original).parsed_response, response)
        with self.assertRaises(ValueError):
            await run.arespond(response)
        with self.assertRaises(ValueError):
            await self.session.run.resume(run.id, agent_id='loop')
        await run.arespond(changed.respond('approve'))
        await self.finish(run)
        self.assertEqual(len(self.effects), 1)

    async def test_same_assessment_retains_request_and_response(self):
        await self.setup()
        run = await self.pause()
        request, = await run.ainteractions()
        response = await run.arespond(request.respond('approve'))
        self.assertEqual(await run.areclassify(request, risk=20, risk_scheme='test-v1'), request)
        self.assertEqual(await run.ainteraction_responses(), [response])

    async def test_invalid_values_cancelled_requests_and_stale_snapshots_fail(self):
        await self.setup()
        run = await self.pause()
        request, = await run.ainteractions()
        for risk, scheme in [(None, 'test-v1'), (True, 'test-v1'), (1.0, 'test-v1'),
                             (-1, 'test-v1'), (3, None), (3, ''), ('3', 'test-v1')]:
            with self.subTest(risk=risk, scheme=scheme):
                with self.assertRaises(ValueError):
                    await run.areclassify(request, risk=risk, risk_scheme=scheme)
                self.assertEqual(await run.ainteractions(), [request])
        changed = await run.areclassify(request, risk=21, risk_scheme='test-v1')
        with self.assertRaises(ValueError):
            await run.areclassify(request, risk=22, risk_scheme='test-v1')
        await run.acancel_interaction(changed)
        with self.assertRaises(ValueError):
            await run.areclassify(changed, risk=23, risk_scheme='test-v1')

    async def test_storage_failure_rolls_back_reclassification(self):
        await self.setup()
        run = await self.pause()
        request, = await run.ainteractions()
        save = InteractionRepository._save
        def fail(repository, run, subject, value):
            if subject.id == request.id:
                raise OSError('injected link failure')
            return save(repository, run, subject, value)
        before = InteractionRepository().snapshot(run.data)
        with patch.object(InteractionRepository, '_save', fail):
            with self.assertRaises(OSError):
                await run.areclassify(request, risk=1, risk_scheme='test-v1')
        self.assertEqual(InteractionRepository().snapshot(run.data), before)
        self.assertEqual(await run.ainteractions(), [request])

    async def test_reclassification_prevents_inflight_policy_issuance(self):
        gate = fixtures.GateEvaluator()
        await self.remember_unknown(evaluator=gate)
        task = await self.blocked(gate)
        run = await self.session.run.aload(gate.request.source['run_id'])
        changed = await run.areclassify(gate.request, risk=5, risk_scheme='test-v1')
        gate.release.set()
        await task
        self.assertEqual(await run.ainteractions(), [changed])
        self.assertEqual(await run.ainteraction_responses(), [])
        self.assertEqual(await run.apply_approval(), [])

    async def test_reclassification_and_resume_have_one_winner(self):
        await self.setup()
        run = await self.pause()
        request, = await run.ainteractions()
        await run.arespond(request.respond('approve'))
        self.model.responses.append([fixtures.chunk('done', finish='stop')])
        changed, resumed = await asyncio.gather(
            run.areclassify(request, risk=21, risk_scheme='test-v1'),
            self.session.run.resume(run.id, agent_id='loop'), return_exceptions=True)
        if isinstance(resumed, BaseException):
            self.assertNotIsInstance(changed, BaseException)
            self.assertEqual(self.effects, [])
        else:
            self.assertIsInstance(changed, ValueError)
            done, = await resumed.wait(timeout=15)
            self.assertEqual(done.data.status, 'completed', done.data.error)
            self.assertEqual(len(self.effects), 1)

    async def test_reclassification_never_bypasses_baseline_classification_or_host_deny(self):
        await self.setup()
        for alteration in ('risk', 'deny'):
            self.model.responses.clear()
            self.classification.update(risk=20, deny=False)
            run = await self.pause()
            request, = await run.ainteractions()
            changed = await run.areclassify(request, risk=0, risk_scheme='test-v1')
            await run.arespond(changed.respond('approve'))
            self.classification[alteration] = 21 if alteration == 'risk' else True
            self.model.responses.append([fixtures.chunk('done', finish='stop')])
            done, = await (await self.session.run.resume(run.id, agent_id='loop')).wait(timeout=15)
            self.assertEqual(done.data.status, 'failed')
            self.assertEqual(self.effects, [])

    async def test_reclassification_does_not_weaken_host_revision_or_project_constraints(self):
        await self.setup()
        run = await self.pause()
        request, = await run.ainteractions()
        changed = await run.areclassify(request, risk=1, risk_scheme='test-v1')
        await run.arespond(changed.respond('approve'))
        manager = self.app._manager(self.session._snapshot)
        manager.tool_runtime = replace(manager.tool_runtime, revision='changed-runtime')
        with self.assertRaises(ValueError):
            await self.session.run.resume(run.id, agent_id='loop')
        self.assertEqual(self.effects, [])

    async def test_changed_rules_do_not_enter_admitted_policy_on_explicit_application(self):
        await self.setup()
        self.classification['risk'] = None
        run = await self.pause()
        request, = await run.ainteractions()
        await run.areclassify(request, risk=1, risk_scheme='test-v1')
        await self.project.aconfigure_policies({'approval': {'rules': [
            {'id': 'added-later', 'category': 'file.write', 'max_risk': 20}]}})
        self.assertEqual(await run.apply_approval(), [])
        self.assertEqual(await run.ainteraction_responses(), [])

    async def test_explicit_application_race_does_not_overwrite_user_or_cancel(self):
        gate = fixtures.GateEvaluator()
        await self.remember_unknown(evaluator=gate)
        # The first automatic evaluation is prevented by a false condition. Later
        # explicit evaluation still uses the identical persisted rule/condition.
        gate.release.set()
        with patch.object(gate, 'evaluate', return_value=False):
            run = await self.pause()
        request, = await run.ainteractions()
        gate.release.clear()
        gate.started.clear()
        task = asyncio.create_task(run.apply_approval())
        self.addAsyncCleanup(self.drain, task, gate)
        await asyncio.wait_for(gate.started.wait(), 15)
        response = await run.arespond(request.respond('deny'))
        gate.release.set()
        self.assertEqual(await task, [])
        self.assertEqual(await run.ainteraction_responses(), [response])

    async def test_reclassification_chain_survives_reopen_and_renewal(self):
        from llm.llm import LargeLanguageModel, BackendServices
        await self.setup()
        run = await self.pause()
        request, = await run.ainteractions()
        first = await run.areclassify(request, risk=1, risk_scheme='one')
        second = await run.areclassify(first, risk=2, risk_scheme='two')
        await run.acancel_interaction(second)
        latest = await run.arenew_interaction(second)
        self.assertEqual((latest.risk, latest.risk_scheme), (2, 'two'))
        original = self.app
        await original.shutdown()
        self.app = LargeLanguageModel(original.workspace,
            components=[original.project_manager.components.get(name) for name in original.project_manager.components.names()],
            engines={name: original.engines.get(name) for name in original.engines.names()},
            services=BackendServices(tool_runtime=original.services.tool_runtime))
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.aload(self.project.id)
        self.session = await self.project.sessions.aload(self.session.id)
        reopened = await self.session.run.aload(run.id)
        self.assertEqual(await reopened.ainteractions(), [latest])
        await reopened.arespond(latest.respond('approve'))
        await self.finish(reopened)
        self.assertEqual(len(self.effects), 1)

    async def test_nested_graph_reclassification_preserves_parent_child_binding(self):
        from llm.llm import GraphEngine, ToolNode, WorkflowComponent, WorkflowGraph
        await self.setup()
        self.app.engines.register('graph', GraphEngine(handlers={'tool': ToolNode()}))
        self.app.project_manager.components.register(WorkflowComponent())
        await self.project.asave(components=['tools', 'workflows'])
        leaf = (WorkflowGraph(entry='act').node('act', 'tool', tool='act', arguments={})
                .node('end', 'end').connect('act', 'end').to_dict())
        root = (WorkflowGraph(entry='child').node('child', 'workflow', workflow='leaf')
                .node('end', 'end').connect('child', 'end').to_dict())
        await self.project.components.workflows.acreate(leaf, identifier='leaf')
        await self.project.components.workflows.acreate(root, identifier='root')
        await self.project.agents.acreate({'engine': 'graph', 'engine_options': {'workflow': 'root'}}, identifier='graph-agent')
        session = await self.project.sessions.acreate(agent_ids=['graph-agent'])
        run, = await (await session.run.submit('act', recipients=['graph-agent'])).wait(timeout=15)
        self.assertEqual(run.data.status, 'paused', run.data.error)
        request, = await run.ainteractions()
        changed = await run.areclassify(request, risk=3, risk_scheme='test-v1')
        await run.arespond(changed.respond('approve'))
        done, = await (await session.run.resume(run.id, agent_id='graph-agent')).wait(timeout=15)
        self.assertEqual(done.data.status, 'completed', done.data.error)
        self.assertEqual(len(self.effects), 1)


class DelegatedAssessmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_reclassification_reuses_completed_nested_effect_and_model_calls(self):
        from tests.llm import test_agent_delegation
        from tests.llm.support.runtime_tools import TEST_CONTRACT
        from llm.components.tools import Tool, ToolRegistry
        effects = []
        async def act(arguments):
            effects.append(arguments['value'])
        schema = {'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value']}
        registry = ToolRegistry((Tool('first', 'first', schema, act, contract=TEST_CONTRACT),
                                Tool('second', 'second', schema, act, contract=fixtures.CONTRACT)))
        app, session, parent, child = await test_agent_delegation.AgentDelegationTests.setup(
            self, registry=registry, child_tools=registry.names(), policies={'tools': {'max_calls': 3}},
            child_responses=[
                [fixtures.chunk(calls=[fixtures.call('{"value":1}', name='first')], finish='tool_calls')],
                [fixtures.chunk(calls=[fixtures.call('{"value":2}', name='second', call_id='two')], finish='tool_calls')],
                [fixtures.chunk('done', finish='stop')]])
        run, = await (await session.run.submit('act', recipients=['parent-agent'])).wait(timeout=15)
        self.assertEqual(run.data.status, 'paused', run.data.error)
        self.assertEqual(effects, [1])
        request, = await run.ainteractions()
        changed = await run.areclassify(request, risk=10, risk_scheme='app-explicit')
        await run.arespond(changed.respond('approve'))
        done, = await (await session.run.resume(run.id, agent_id='parent-agent')).wait(timeout=15)
        self.assertEqual(done.data.status, 'completed', done.data.error)
        self.assertEqual(effects, [1, 2])
        self.assertEqual((len(parent.requests), len(child.requests)), (2, 3))


class AssessmentRecordTests(unittest.TestCase):
    def test_reclassification_envelope_cannot_change_action_or_weaken_renewal(self):
        from types import SimpleNamespace
        request = approval_request('review', risk=20, risk_scheme='test-v1')
        successor = request.reclassified('b' * 32, created_at=request.created_at, risk=10, risk_scheme='test-v1')
        record = InteractionRecord.initial(SimpleNamespace(id='run'), request)
        saved = record.reclassified_record(successor)
        InteractionRecord.from_dict(saved).validate_request(request)
        malformed = deepcopy(saved)
        malformed['replacement']['request']['action'] = {'injected': True}
        with self.assertRaises(ValueError):
            InteractionRecord.from_dict(malformed).validate_request(request)
        with self.assertRaises(ValueError):
            InteractionRecord.from_dict(record.renewed_record(successor)).validate_request(request)

    def test_threshold_schema_and_unknown_conditions_are_separate(self):
        self.assertEqual(normalize_policies({}), {})
        with self.assertRaises(ValueError):
            ApprovalPolicy.from_dict({'enabled': True, 'rules': [
                {'id': 'x', 'category': 'write', 'basis': 'explicit_unknown'}]})

    def test_reclassification_rejects_confirmation_and_uncertain_retry(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from types import SimpleNamespace
        with TemporaryDirectory() as directory:
            run = SimpleNamespace(id='run', paths=SimpleNamespace(state=Path(directory)))
            repository = InteractionRepository()
            for request in (replace(approval_request('review'), kind='confirmation'),
                            approval_request('retry', category=repository.RETRY_CATEGORY)):
                repository.initialize(run, request)
                with self.assertRaises(ValueError):
                    repository.reclassify(run, request, risk=0, risk_scheme='app')
                self.assertFalse(repository.control(run, request).has_replacement)
