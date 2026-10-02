"""공통 승인 모델, UI 응답 저장, 엔진 재개 및 재시작 계약을 검증한다."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from llm.core.interactions import InteractionOption, InteractionRequest, InteractionResponse, approval_request
from llm.llm import LargeLanguageModel, ServiceConfig
from llm.services.runtime.tools import ToolPolicy, ToolApprovalRequired
from tests.llm import test_long_running
from tests.llm import test_graph_checkpoints
from tests.llm.test_graph_engine import straight
from tests.llm.test_loop import chunk, call


class InteractionModelTests(unittest.TestCase):
    def test_roundtrip_metadata_and_no_implicit_recommended_approval(self):
        request = replace(approval_request("파일 수정", risk="high"),
                          priority="urgent", recommended_option_id="approve").bind("loop", "tool:1:a")
        restored = InteractionRequest.from_dict(request.to_dict())
        self.assertEqual(restored, request)
        self.assertEqual(restored.status, "pending")
        response = restored.respond("deny")
        self.assertIs(InteractionResponse.from_dict(response.to_dict()).decision(restored), False)

    def test_changed_request_expiry_and_unknown_choice_are_rejected(self):
        request = approval_request("review")
        response = request.respond("approve")
        for changed in (replace(request, revision=2), replace(request, action={"tool": "other"})):
            with self.assertRaisesRegex(ValueError, "changed"):
                response.decision(changed)
        expired = replace(request, expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        with self.assertRaisesRegex(ValueError, "pending"):
            expired.respond("approve")
        with self.assertRaisesRegex(ValueError, "Unknown"):
            request.respond("missing")

    def test_input_schema_and_deny_cannot_replace_decision(self):
        request = replace(approval_request("검토"), kind="confirmation", input_schema={
            "type": "object", "properties": {"answer": {"type": "string"}},
            "required": ["answer"], "additionalProperties": False}).bind(
                "graph", "node", decision_key="approved", input_key="state")
        self.assertEqual(request.respond("approve", value={"answer": "yes"}).decision(request),
                         {"approved": True, "state": {"answer": "yes"}})
        with self.assertRaises(Exception):
            request.respond("approve", value={"other": 1})
        with self.assertRaisesRegex(ValueError, "Denial"):
            request.respond("deny", value={"answer": "yes"})
        self.assertEqual(request.respond("deny").decision(request), {"approved": False})

    def test_invalid_options_schema_and_tool_input_rejected(self):
        with self.assertRaises(ValueError):
            InteractionRequest("bad", (InteractionOption("x", "one"), InteractionOption("x", "two")))
        with self.assertRaises(ValueError):
            InteractionRequest("bad", (InteractionOption("x", "one"),), recommended_option_id="missing")
        with self.assertRaises(ValueError):
            replace(approval_request("review"), input_schema={"$ref": "https://example.com/schema"})
        with self.assertRaises(ValueError):
            ToolApprovalRequired(request=replace(approval_request("review"), input_schema={"type": "object"}))


class InteractionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    setup_app = test_long_running.LongRunningTests.setup_app

    async def paused(self, **options):
        self.effects, self.events = [], []
        async def act(arguments):
            self.effects.append(arguments)
        async def authorize(call):
            request = replace(approval_request("검토", source={"run_id": "untrusted"}), **options)
            raise ToolApprovalRequired(request=request)
        app, session, model = await self.setup_app(act, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')], [chunk('done', finish='stop')]],
            ToolPolicy(authorize=authorize))
        def observe(run, event):
            if event.interaction is not None:
                checkpoint = app.run_repository.checkpoint(run, event.interaction.binding['checkpoint'])
                persisted = checkpoint['records'][event.interaction.binding['key']]['interaction']
                self.events.append((event.interaction, persisted))
        app._manager(session._snapshot).on_event = observe
        run = await (await session.run.submit('go', engine='loop')).wait()
        self.assertEqual(run.data.status, 'paused', run.data.error)
        self.assertEqual(self.effects, [])
        return app, session, run

    async def test_response_is_durable_idempotent_and_resume_is_explicit(self):
        app, session, run = await self.paused()
        request, = await run.ainteractions(pending_only=True)
        self.assertEqual(self.events, [(request, request.to_dict())])
        self.assertEqual(request.source['run_id'], run.id)
        self.assertEqual(request.action['tool'], 'act')
        self.assertEqual(request.action['arguments'], {})
        self.assertEqual(request.action['policy']['revision'], '1')
        response = request.respond('approve')
        saved = await run.arespond(response)
        self.assertEqual(await run.arespond(request.respond('approve')), saved)
        self.assertEqual(await run.ainteractions(pending_only=True), [])
        self.assertEqual(await run.ainteraction_responses(), [saved])
        self.assertEqual(self.effects, [])
        self.assertEqual(run.data.status, 'paused')
        with self.assertRaisesRegex(ValueError, 'differently'):
            await run.arespond(request.respond('deny'))
        with self.assertRaisesRegex(Exception, 'conflicts'):
            await session.run.resume(run.id, engine='loop', decisions={request.binding['key']: False})
        resumed = await (await session.run.resume(run.id, engine='loop')).wait()
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(self.effects, [{}])
        with self.assertRaisesRegex(ValueError, 'resume request'):
            await run.arespond(response)

    async def test_saved_denial_prevents_tool_effect(self):
        app, session, run = await self.paused()
        request, = await run.ainteractions()
        await run.arespond(request.respond('deny'))
        denied = await (await session.run.resume(run.id, engine='loop')).wait()
        self.assertEqual(denied.data.status, 'failed')
        self.assertEqual(self.effects, [])

    async def test_response_survives_backend_restart(self):
        app, session, run = await self.paused()
        request, = await run.ainteractions()
        saved = await run.arespond(request.respond('approve'))
        root, project_id, session_id, run_id = session.project.paths.root.parent.parent, session.project.id, session.id, run.id
        engine = app.engines.resolve('loop')
        policy = app._manager(session._snapshot).tool_policy
        component = app.project_manager.components.get('tools')
        await app.shutdown()
        reopened = LargeLanguageModel(root, engines={'loop': engine}, components=[component],
                                      services=ServiceConfig(tool_policy=policy))
        self.addAsyncCleanup(reopened.shutdown)
        session = (await reopened.projects.aload(project_id)).sessions.load(session_id)
        run = await session.run.aload(run_id)
        self.assertEqual(await run.ainteraction_responses(), [saved])
        resumed = await (await session.run.resume(run_id, engine='loop')).wait()
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(self.effects, [{}])

    async def test_different_run_stale_fingerprint_and_policy_change_are_rejected(self):
        app, session, run = await self.paused()
        request, = await run.ainteractions()
        with self.assertRaisesRegex(ValueError, 'belong'):
            await run.arespond(approval_request('other').respond('approve'))
        with self.assertRaisesRegex(ValueError, 'changed'):
            await run.arespond(replace(request, action={'tool': 'other'}).respond('approve'))
        await run.arespond(request.respond('approve'))
        manager = app._manager(session._snapshot)
        manager.tool_policy = replace(manager.tool_policy, revision='2')
        with self.assertRaisesRegex(Exception, 'changed'):
            await session.run.resume(run.id, engine='loop')
        self.assertEqual(self.effects, [])

    async def test_expired_request_never_resumes_via_raw_decisions(self):
        app, session, run = await self.paused(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        request, = await run.ainteractions()
        self.assertEqual(await run.ainteractions(pending_only=True), [])
        with self.assertRaisesRegex(Exception, 'pending'):
            await session.run.resume(run.id, engine='loop', decisions={request.binding['key']: True})
        self.assertEqual(self.effects, [])

    async def test_concurrent_conflicting_answers_accept_only_one(self):
        app, session, run = await self.paused()
        request, = await run.ainteractions()
        results = await asyncio.gather(run.arespond(request.respond('approve')),
                                       run.arespond(request.respond('deny')), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, InteractionResponse) for r in results), 1)
        self.assertEqual(len(await run.ainteraction_responses()), 1)

    async def test_expiry_is_checked_again_when_queued_resume_starts(self):
        from llm.engines.base import BaseEngine
        app, session, run = await self.paused(expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        request, = await run.ainteractions()
        await run.arespond(request.respond('approve'))
        started, release = asyncio.Event(), asyncio.Event()
        async def wait(context):
            started.set()
            await release.wait()
        app.engines.register('wait', BaseEngine(action=wait))
        await session.run.submit('block', engine='wait')
        await asyncio.wait_for(started.wait(), 10)
        queued = await session.run.resume(run.id, engine='loop')
        with patch('llm.core.interactions.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime.now(timezone.utc) + timedelta(hours=2)
            release.set()
            failed = await queued.wait()
        self.assertEqual(failed.data.status, 'failed')
        self.assertIn('expired', failed.data.error)
        self.assertEqual(self.effects, [])

    async def test_native_decisions_use_common_receipts_and_reject_integer_approval(self):
        app, session, run = await self.paused()
        request, = await run.ainteractions()
        with self.assertRaises(Exception):
            await session.run.resume(run.id, engine='loop', decisions={request.binding['key']: 1})
        resumed = await (await session.run.resume(run.id, engine='loop', decisions={request.binding['key']: True})).wait()
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        response, = await run.ainteraction_responses()
        self.assertEqual(response.request_id, request.id)
        self.assertEqual(response.option_id, 'approve')

    async def test_resume_uses_the_backends_response_repository(self):
        app, session, run = await self.paused()
        request, = await run.ainteractions()
        response = request.respond('approve')
        # 사용자 정의 저장소가 제공한 응답도 조회와 실행에 똑같이 사용해야 한다.
        with patch.object(app.run_repository, 'interaction_responses', return_value=[response]) as read:
            self.assertEqual(await run.ainteraction_responses(), [response])
            resumed = await (await session.run.resume(run.id, engine='loop')).wait()
        self.assertGreaterEqual(read.call_count, 2)
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(self.effects, [{}])


class GraphInteractionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_graph_checkpoints.GraphCheckpointTests.asyncSetUp
    setup_graph = test_graph_checkpoints.GraphCheckpointTests.setup_graph
    run_graph = test_graph_checkpoints.GraphCheckpointTests.run_graph

    async def setup_tool_graph(self, authorize):
        from llm.components.tools import Tool, ToolRegistry
        from llm.components.workflows import WorkflowComponent, WorkflowGraph
        from llm.engines.graph import GraphEngine
        from llm.engines.graph.tool import ToolNode
        from tests.llm.support.runtime_tools import RuntimeTools
        self.effects, self.authorizations = [], []
        async def act(arguments):
            self.effects.append(arguments)
        async def checked(call):
            self.authorizations.append(call)
            return await authorize(call)
        self.app = LargeLanguageModel(self.root, components=[WorkflowComponent(), RuntimeTools(
            ToolRegistry((Tool('act', 'action', {'type': 'object'}, act),)))],
            engines={'graph': GraphEngine('flow', handlers={'tool': ToolNode()})},
            services=ServiceConfig(tool_policy=ToolPolicy(authorize=checked)))
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=['tools', 'workflows'])
        await self.project.components.tools.aenable('act')
        graph = (WorkflowGraph(entry='action').node('action', 'tool', tool='act', pause_before=True)
                 .node('end', 'end').connect('action', 'end').to_dict())
        await self.project.components.workflows.acreate(graph, identifier='flow')
        self.session = await self.project.sessions.acreate()

    async def answer_and_resume(self, run, option='approve'):
        request, = await run.ainteractions(pending_only=True)
        await run.arespond(request.respond(option))
        return await (await self.session.run.resume(run.id, engine='graph')).wait()

    async def test_workflow_confirmation_does_not_override_host_tool_denial(self):
        async def deny(call):
            return False
        await self.setup_tool_graph(deny)
        paused = await self.run_graph()
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        request, = await paused.ainteractions()
        self.assertEqual(request.kind, 'confirmation')
        self.assertEqual(self.authorizations, [])
        denied = await self.answer_and_resume(paused)
        self.assertEqual(denied.data.status, 'failed', denied.data.error)
        self.assertEqual(denied.data.error_code, 'tool_denied')
        self.assertEqual(len(self.authorizations), 1)
        self.assertEqual(self.effects, [])

    async def test_workflow_confirmation_then_separate_tool_approval(self):
        async def ask(call):
            raise ToolApprovalRequired('Tool permission')
        await self.setup_tool_graph(ask)
        confirmation = await self.run_graph()
        paused = await self.answer_and_resume(confirmation)
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        request, = await paused.ainteractions(pending_only=True)
        self.assertEqual(request.kind, 'approval')
        self.assertEqual(request.action['tool'], 'act')
        self.assertEqual(self.effects, [])
        self.assertEqual(len(self.authorizations), 1)
        completed = await self.answer_and_resume(paused)
        self.assertEqual(completed.data.status, 'completed', completed.data.error)
        self.assertEqual(self.effects, [{}])
        self.assertEqual(len(self.authorizations), 1)
        counts = self.app.observability.snapshot()['tools']
        self.assertEqual((counts['requests'], counts['approval_required'], counts['executions']), (1, 1, 1))

    async def test_denied_confirmation_never_reaches_tool_authorization(self):
        async def allow(call):
            return True
        await self.setup_tool_graph(allow)
        denied = await self.answer_and_resume(await self.run_graph(), 'deny')
        self.assertEqual(denied.data.status, 'failed')
        self.assertEqual(self.authorizations, [])
        self.assertEqual(self.effects, [])

    async def test_graph_confirmation_uses_same_response_api(self):
        effects = []
        async def work(node):
            effects.append(node.state['review'])
            return {}
        await self.setup_graph(straight(pause_before=True, resume_schema={
            'type': 'object', 'properties': {'review': {'type': 'string'}},
            'additionalProperties': False}), work)
        run = await self.run_graph()
        request, = await run.ainteractions()
        self.assertEqual(request.kind, 'confirmation')
        await run.arespond(request.respond('approve', value={'review': 'accepted'}))
        self.assertEqual(effects, [])
        resumed = await (await self.session.run.resume(run.id, engine='graph')).wait()
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(effects, ['accepted'])
