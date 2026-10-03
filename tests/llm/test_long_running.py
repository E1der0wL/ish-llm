"""장시간 실행의 재개·조건부 Tool 재시도 계약을 실제 서비스 경로로 검증한다."""

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tests.llm.support.runtime_tools import RuntimeTools
from llm.llm import LargeLanguageModel, ProjectConfig, LoopEngine, Tool, ToolRegistry, ToolComponent, ServiceConfig
from llm.services.runtime.tools import ToolPolicy, ToolExecutionError, ToolApprovalRequired
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class LongRunningTests(unittest.IsolatedAsyncioTestCase):
    async def test_usage_projection_reuses_unchanged_runs_and_reloads_replacements(self):
        async def act(arguments):
            pass
        app, session, _ = await self.setup_app(act, [[chunk('done', finish='stop')]])
        run = await (await session.run.submit('go', engine='loop')).wait()
        repository = app.run_repository
        first = repository.completion_history(session.data)
        with patch.object(repository, 'load', side_effect=AssertionError('no full Run load')):
            with patch('llm.services.infrastructure.storage.read_domain_record', side_effect=AssertionError('cached')):
                self.assertEqual(repository.completion_history(session.data), first)
        updated = run.data
        updated.metadata['completions'][0]['reserved_tokens'] = 777
        repository.save(updated)
        self.assertEqual(repository.completion_history(session.data)[0]['reserved_tokens'], 777)

    async def test_prior_uncertain_retry_never_becomes_no_effect_receipt(self):
        calls = []
        async def act(arguments):
            calls.append(1)
            raise ToolExecutionError('retry', effect='uncertain' if len(calls) == 1 else 'none', retryable=True)
        app, session, _ = await self.setup_app(act, [[chunk(calls=[call('{}', name='act')], finish='tool_calls')]],
            ToolPolicy(operation_key=lambda call: 'work', retry_safe_tools=('act',), max_retries=1, retry_delay=0))
        failed = await (await session.run.submit('go', engine='loop')).wait()
        self.assertEqual((await session.run.aoperation('work'))['status'], 'started')
        with self.assertRaisesRegex(Exception, 'uncertain Tool'):
            await session.run.resume(failed.id, engine='loop')
    async def test_resume_after_backend_restart_reuses_completed_effect(self):
        effects = []
        async def act(arguments):
            effects.append(1)
        app, session, model = await self.setup_app(act, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')],
            [ConnectionError('offline')], [chunk('done', finish='stop')]])
        failed = await (await session.run.submit('go', engine='loop')).wait()
        project = session.project
        root, project_id, session_id = project.data.paths.root.parent.parent, project.id, session.id
        await app.shutdown()
        reopened = LargeLanguageModel(root, components=[RuntimeTools(ToolRegistry((
            Tool('act', 'action', {'type': 'object'}, act),)))], engines={'loop': LoopEngine(completion_fn=model)})
        self.addAsyncCleanup(reopened.shutdown)
        session = (await reopened.projects.aload(project_id)).sessions.load(session_id)
        resumed = await (await session.run.resume(failed.id, engine='loop')).wait()
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(effects, [1])
        self.assertEqual(len(model.requests), 3)

    async def test_partial_provider_response_is_never_automatically_retried(self):
        class Busy(Exception):
            status_code = 503
        async def act(arguments):
            pass
        app, session, model = await self.setup_app(act, [[chunk('partial'), Busy('offline')]])
        await session.project.aconfigure_policies({'provider_retry': {'max_retries': 2, 'delay_seconds': 0}})
        run = await (await session.run.submit('go', engine='loop')).wait()
        self.assertEqual(run.data.status, 'failed')
        self.assertEqual(len(model.requests), 1)

    async def test_partial_tool_arguments_are_not_automatically_retried(self):
        class Busy(Exception):
            status_code = 503
        async def act(arguments):
            self.fail('Incomplete Tool call must not execute')
        app, session, model = await self.setup_app(act, [[chunk(calls=[call('{', name='act')]), Busy('offline')]])
        await session.project.aconfigure_policies({'provider_retry': {'max_retries': 2, 'delay_seconds': 0}})
        run = await (await session.run.submit('go', engine='loop')).wait()
        self.assertEqual(run.data.status, 'failed')
        self.assertEqual(len(model.requests), 1)

    async def test_tool_handler_cannot_report_effect_as_approval_waiting(self):
        async def act(arguments):
            raise ToolApprovalRequired('effect may already have happened')
        app, session, _ = await self.setup_app(act, [[chunk(calls=[call('{}', name='act')], finish='tool_calls')]])
        run = await (await session.run.submit('go', engine='loop')).wait()
        self.assertEqual(run.data.status, 'failed')
        with self.assertRaisesRegex(Exception, 'uncertain Tool'):
            await session.run.resume(run.id, engine='loop')

    async def test_run_page_loads_only_selected_records_and_invalidates_catalog(self):
        from llm.services.query import Query
        async def act(arguments):
            pass
        app, session, _ = await self.setup_app(act, [[chunk('one', finish='stop')], [chunk('two', finish='stop')]])
        first = await (await session.run.submit('one', engine='loop')).wait()
        second = await (await session.run.submit('two', engine='loop')).wait()
        repository = app.run_repository
        with patch.object(repository, 'load', wraps=repository.load) as read:
            page = await session.run.alist(query=Query(limit=1, descending=True))
            self.assertEqual([item.id for item in page], [second.id])
            self.assertEqual(read.call_count, 1)
        record = first.data
        from llm.core.models import RunStatus
        record.status = RunStatus.FAILED
        repository.save(record)
        self.assertEqual([item.id for item in await session.run.alist(query=Query(status='failed'))], [first.id])

    async def test_graph_tool_approval_waits_and_executes_once(self):
        from llm.engines.graph import GraphEngine
        from llm.engines.graph.tool import ToolNode
        from llm.components.workflows import WorkflowComponent, WorkflowGraph
        effects = []
        async def authorize(call):
            raise ToolApprovalRequired('Review Tool')
        async def act(arguments):
            effects.append(1)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        app = LargeLanguageModel(Path(temporary.name),
            components=[RuntimeTools(ToolRegistry((Tool('act', 'action', {'type': 'object'}, act),))), WorkflowComponent()],
            engines={'graph': GraphEngine(handlers={'tool': ToolNode()})},
            services=ServiceConfig(tool_policy=ToolPolicy(authorize=authorize)))
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate(components=['tools', 'workflows'])
        project.components.tools.enable('act')
        graph = WorkflowGraph(entry='action').node('action', 'tool', tool='act').node('end', 'end').connect('action', 'end').to_dict()
        await project.components.workflows.acreate(graph, identifier='flow')
        session = await project.sessions.acreate()
        paused = await (await session.run.submit('go', engine='graph', engine_options={"workflow": "flow"})).wait()
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        self.assertEqual(effects, [])
        resumed = await (await session.run.resume(paused.id, engine='graph', decisions={'["action"]': {'approved': True}})).wait()
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(effects, [1])

    async def test_token_quota_reserves_unknown_usage_and_project_call_limit(self):
        async def act(arguments):
            return 'ok'
        app, session, model = await self.setup_app(act, [[chunk(calls=[call('{}', name='act')], finish='tool_calls')]])
        app.policy_resolver.token_counters['fixed'] = lambda request: 10
        project = app.projects.load(session.data.project_id)
        config = project.data.config
        config.completion['max_tokens'] = 5
        config.configure_policies({'completion': {'counter': 'fixed'}, 'usage': {'max_tokens': 20, 'project_max_calls': 1}})
        await project.asave(config=config)
        failed = await (await session.run.submit('go', engine='loop')).wait()
        self.assertEqual((await failed.aresult()).error_code, 'usage_limit')
        other = await project.sessions.acreate()
        failed = await (await other.run.submit('go', engine='loop')).wait()
        self.assertEqual((await failed.aresult()).error_code, 'usage_limit')
        self.assertEqual(len(model.requests), 1)

    async def test_output_reconciliation_and_ui_snapshot(self):
        async def act(arguments):
            pass
        app, session, _ = await self.setup_app(act, [[chunk('answer', finish='stop')]])
        run = await (await session.run.submit('go', engine='loop')).wait()
        await session.run.shutdown()
        store = app.project_manager.sessions.conversations(session.data)
        # 출력 저널 기록 뒤 Conversation 쓰기가 누락된 장애를 재현한다.
        store._append({'type': 'message.delta', 'id': run.data.assistant_message_id,
                       'text': 'incomplete', 'operation': 'replace'})
        self.assertTrue(await run.areconcile_output())
        self.assertEqual((await run.aresponse()).content, 'answer')
        self.assertFalse(await run.areconcile_output())
        view = await run.aview()
        self.assertEqual(view.run.status, 'completed')
        self.assertGreater(view.cursor, 0)

    async def test_committed_deletion_cleanup_can_be_completed(self):
        async def act(arguments):
            pass
        app, session, _ = await self.setup_app(act, [])
        project_id = session.data.project_id
        with patch('llm.services.infrastructure.storage.shutil.rmtree', side_effect=OSError('disk unavailable')):
            with self.assertWarnsRegex(RuntimeWarning, "cleanup"):
                session.delete(permanent=True)
        self.assertFalse(session.paths.root.exists())
        recovered = await app.projects.arecover_deletions()
        self.assertEqual(recovered['sessions'][project_id], [])
        self.assertEqual(list(app.project_manager.ownership.transactions.journal.iterdir()), [])

    async def test_retention_preview_apply_and_protected_runtime(self):
        async def act(arguments):
            pass
        app, session, _ = await self.setup_app(act, [[chunk('done', finish='stop')]])
        project = app.projects.load(session.data.project_id)
        await project.aconfigure_policies({'retention': {'unit': 'session', 'max_bytes': 1}})
        await (await session.run.submit('go', engine='loop')).wait()
        self.assertEqual((await project.aretention()).candidates, [])
        await session.run.shutdown()
        plan = await project.aretention()
        self.assertEqual([r['session_id'] for r in plan.candidates], [session.id])
        await project.aretention(apply=True, expected_version=plan.version)
        self.assertEqual(await project.sessions.alist(), [])

    async def test_provider_retry_has_separate_usage_records(self):
        class Busy(Exception):
            status_code = 503
        async def act(arguments):
            pass
        app, session, model = await self.setup_app(act, [[Busy('busy')], [chunk('done', finish='stop')]])
        project = app.projects.load(session.data.project_id)
        await project.aconfigure_policies({'provider_retry': {'max_retries': 1, 'delay_seconds': 0}})
        run = await (await session.run.submit('go', engine='loop')).wait()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(len((await run.aresult()).completions), 2)

    async def test_durable_approval_resumes_without_repeating_model(self):
        effects = []
        async def authorize(call):
            raise ToolApprovalRequired('Approve action')
        async def act(arguments):
            effects.append(1)
        app, session, model = await self.setup_app(act, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')], [chunk('done', finish='stop')]],
            ToolPolicy(authorize=authorize))
        paused = await (await session.run.submit('go', engine='loop')).wait()
        self.assertEqual(paused.data.status, 'paused')
        self.assertEqual(effects, [])
        with self.assertRaisesRegex(Exception, 'decisions required'):
            await session.run.resume(paused.id, engine='loop')
        resumed = await (await session.run.resume(paused.id, engine='loop', decisions={'tool:1:call_1': True})).wait()
        self.assertEqual(resumed.data.status, 'completed')
        self.assertEqual(effects, [1])
        self.assertEqual(len(model.requests), 2)

    async def test_usage_admission_stops_before_second_call(self):
        async def act(arguments):
            return None
        app, session, model = await self.setup_app(act, [[chunk(calls=[call('{}', name='act')], finish='tool_calls')]])
        project = app.projects.load(session.data.project_id)
        await project.aconfigure_policies({'usage': {'max_calls': 1}})
        failed = await (await session.run.submit('go', engine='loop')).wait()
        self.assertEqual((await failed.aresult()).error_code, 'usage_limit')
        self.assertEqual(len(model.requests), 1)

    async def test_component_edit_conflict(self):
        async def act(arguments):
            pass
        app, session, _ = await self.setup_app(act, [])
        tools = app.projects.load(session.data.project_id).components.tools
        snapshot = await tools.asnapshot()
        await tools.aconfigure({'enabled': []}, expected_version=snapshot['version'])
        with self.assertRaisesRegex(ValueError, 'edit_conflict'):
            await tools.aconfigure({'enabled': ['act']}, expected_version=snapshot['version'])

    async def setup_app(self, handler, responses, policy=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        model = ScriptedCompletion(*responses)
        tools = ToolRegistry((Tool("act", "action", {"type": "object"}, handler),))
        app = LargeLanguageModel(Path(temporary.name), components=[RuntimeTools(tools)],
            engines={"loop": LoopEngine(completion_fn=model)}, services=ServiceConfig(tool_policy=policy or ToolPolicy()))
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate("resume", config=ProjectConfig(completion={"model": "test/model"}), components=["tools"])
        project.components.tools.enable("act")
        session = await project.sessions.acreate()
        return app, session, model

    async def test_resume_reuses_none_result_and_does_not_repeat_completion(self):
        effects = []
        async def act(arguments):
            effects.append(arguments)
        app, session, model = await self.setup_app(act, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')],
            [ConnectionError('network')], [chunk('done', finish='stop')]])
        failed = await (await session.run.submit('go', engine='loop')).wait()
        self.assertEqual(failed.data.status, 'failed')
        resumed = await (await session.run.resume(failed.id, engine='loop')).wait()
        self.assertEqual(resumed.data.status, 'completed')
        self.assertEqual(effects, [{}])
        self.assertEqual(len(model.requests), 3)
        self.assertEqual(model.requests[-1]['messages'][-1]['content'], 'null')
        reused = [step for step in await resumed.steps.alist() if step.metadata.get('reused')]
        self.assertEqual(len(reused), 1)
        self.assertEqual(reused[0].metadata['source_run_id'], failed.id)
        self.assertIsNone(reused[0].output.data)
        original = app.project_manager.results.tool_result(session.project.data, session.id, resumed.id, 'call_1', steps=app.step_manager)
        self.assertEqual(original['content'], 'null')

    async def test_no_effect_operation_can_resume_without_resetting_uncertain_receipts(self):
        calls = []
        async def act(arguments):
            calls.append(1)
            if len(calls) == 1:
                raise ToolExecutionError('not started', effect='none', retryable=True)
            return None
        app, session, model = await self.setup_app(act, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')], [chunk('done', finish='stop')]],
            ToolPolicy(operation_key=lambda call: 'same-work'))
        failed = await (await session.run.submit('go', engine='loop')).wait()
        self.assertEqual((await session.run.aoperation('same-work'))['status'], 'not_applied')
        resumed = await (await session.run.resume(failed.id, engine='loop')).wait()
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        receipt = await session.run.aoperation('same-work')
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(len(receipt['attempts']), 1)
        self.assertEqual(len(model.requests), 2)

    async def test_unknown_tool_effect_requires_explicit_retry(self):
        effects = []
        async def act(arguments):
            effects.append(1)
            raise RuntimeError('lost receipt')
        app, session, model = await self.setup_app(act, [[chunk(calls=[call('{}', name='act')], finish='tool_calls')]])
        failed = await (await session.run.submit('go', engine='loop')).wait()
        with self.assertRaisesRegex(Exception, 'uncertain Tool'):
            await session.run.resume(failed.id, engine='loop')
        self.assertEqual(effects, [1])

    async def test_only_explicit_safe_failures_retry(self):
        for safe in (True, False):
            effects = []
            async def act(arguments):
                effects.append(1)
                if len(effects) == 1:
                    raise ToolExecutionError('temporary', effect='none' if safe else 'uncertain', retryable=True)
                return 'ok'
            app, session, model = await self.setup_app(act, [
                [chunk(calls=[call('{}', name='act')], finish='tool_calls')], [chunk('done', finish='stop')]],
                ToolPolicy(max_retries=2))
            run = await (await session.run.submit('go', engine='loop')).wait()
            self.assertEqual(run.data.status, 'completed' if safe else 'failed')
            self.assertEqual(len(effects), 2 if safe else 1)
            await app.shutdown()
