"""장시간 실행의 재개·조건부 Tool 재시도 계약을 실제 서비스 경로로 검증한다."""

from llm.engines.loop import LoopEngine

from llm.providers.litellm import LiteLLMCompletionAdapter

import tempfile
import asyncio
import threading
import json
import unittest
from unittest.mock import patch
from pathlib import Path

from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.configuration_fixtures import configure_engine
from llm.llm import LargeLanguageModel, ProjectConfig, LoopEngine, Tool, ToolRegistry, ToolComponent, BackendServices
from llm.services.runtime.tools import ToolRuntime, ToolExecutionError, ToolApprovalRequired
from llm.services.infrastructure.storage import atomic_domain_json, record as domain_record
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from tests.llm.support.runtime_tools import TEST_CONTRACT


class LongRunningTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_retry_evidence_rejects_resume_without_repeating_effect(self):
        with tempfile.TemporaryDirectory() as root:
            effect = Path(root) / "effect.txt"
            async def act(arguments):
                with effect.open("a") as stream:
                    stream.write("effect\n")
                raise RuntimeError("receipt not completed")
            app, session, model = await self.setup_app(
                act, [[chunk(calls=[call('{}', name='act')], finish='tool_calls')]])
            failed = await (await session.run.submit("go", agent_id="loop")).wait()
            self.assertEqual(failed.data.status, "failed")
            self.assertEqual(effect.read_text(), "effect\n")
            candidates = []
            for path in (failed.data.paths.state / "checkpoints/loop/records").glob("*.json"):
                record = json.loads(path.read_text())
                if record["value"]["status"] == "started":
                    candidates.append((path, record))
            self.assertEqual(len(candidates), 1)
            path, record = candidates[0]
            for missing in ("container", "requires_retry"):
                value = {**record, "value": {name: data for name, data in record["value"].items() if name != missing}}
                path.write_text(json.dumps(value))
                before = path.read_bytes()
                with self.subTest(missing=missing):
                    # The Engine still rejects the malformed effect proof. The
                    # public path rejects a changed source record at the storage
                    # revision guard before interpreting retry/control evidence.
                    checkpoint = {"header": {"format": LoopEngine.CHECKPOINT_FORMAT},
                                  "records": {record["key"]: value["value"]}}
                    with self.assertRaisesRegex(ValueError, "explicit container and retry evidence"):
                        LoopEngine().validate_resume(checkpoint, retry_nodes=[record["key"]])
                    with self.assertRaisesRegex(ValueError, "Checkpoint record identity or revision mismatch"):
                        await session.run.resume(failed.id, agent_id="loop", retry_nodes=[record["key"]])
                    self.assertEqual([run.id for run in await session.run.alist()], [failed.id])
                    self.assertEqual(len(model.requests), 1)
                    self.assertEqual(effect.read_text(), "effect\n")
                    self.assertEqual(path.read_bytes(), before)

    async def test_resume_partial_output_is_opt_in_and_completed_tools_are_reused(self):
        for include in (False, True):
            with self.subTest(partial_output=include):
                effects = []
                async def act(arguments):
                    effects.append(1)
                    return 'completed effect'
                app, session, model = await self.setup_app(act, [
                    [chunk(calls=[call('{}', name='act')], finish='tool_calls')],
                    [chunk('unfinished reasoning text'), ConnectionError('offline')],
                    [chunk('final answer', finish='stop')]])
                failed = await (await session.run.submit('go', agent_id='loop')).wait()
                self.assertEqual(failed.data.status, 'failed')
                original = await failed.aoutputs()
                resumed = await (await session.run.resume(failed.id, agent_id='loop', partial_output=include)).wait()
                self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
                self.assertEqual(effects, [1])
                self.assertEqual(len(model.requests), 3)
                text = str(model.requests[-1]['messages'])
                self.assertEqual('unfinished reasoning text' in text, include)
                self.assertIn('completed effect', text)
                self.assertEqual(await failed.aoutputs(), original)

    async def test_user_interrupt_then_resume_includes_partial_output(self):
        started, release, closed = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        class InterruptedCompletion:
            def __init__(self):
                self.requests = []
            def __call__(self, **request):
                self.requests.append(request)
                if len(self.requests) == 1:
                    yield chunk('user-visible partial')
                    started.set()
                    try:
                        release.wait(10)
                    finally:
                        closed.set()
                else:
                    yield chunk('finished', finish='stop')
        async def act(arguments):
            self.fail('No Tool should execute')
        app, session, _ = await self.setup_app(act, [])
        model = InterruptedCompletion()
        app.engines.register('partial', LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model)))
        await configure_engine(session.project, 'partial', completion={'model': 'test/model'})
        await session.project.agents.acreate({'engine': 'partial', 'tools': ['act']}, identifier='partial')
        handle = await session.run.submit('go', agent_id='partial')
        self.assertTrue(await asyncio.to_thread(started.wait, 5))
        await session.run.interrupt()
        interrupted = await handle.wait()
        self.assertEqual(interrupted.data.status, 'interrupted')
        release.set()
        self.assertTrue(await asyncio.to_thread(closed.wait, 5))
        resumed = await (await session.run.resume(interrupted.id, agent_id='partial', partial_output=True)).wait()
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertIn('user-visible partial', str(model.requests[-1]['messages']))

    async def test_usage_projection_reuses_unchanged_runs_and_reloads_replacements(self):
        async def act(arguments):
            pass
        app, session, _ = await self.setup_app(act, [[chunk('done', finish='stop')]])
        run = await (await session.run.submit('go', agent_id='loop')).wait()
        repository = app.run_repository
        first = repository.completion_history(session.data)
        with patch.object(repository, 'load', side_effect=AssertionError('no full Run load')):
            with patch('llm.services.infrastructure.storage.read_domain_record', side_effect=AssertionError('cached')):
                self.assertEqual(repository.completion_history(session.data), first)
        updated = run.data
        updated.metadata['completions'][0]['reserved_tokens'] = 777
        # Replace the source to test cache invalidation, bypassing normal immutable receipts.
        atomic_domain_json(updated.paths.root / 'run.json', domain_record(updated))
        self.assertEqual(repository.completion_history(session.data)[0]['reserved_tokens'], 777)

    async def test_prior_uncertain_retry_never_becomes_no_effect_receipt(self):
        calls = []
        async def act(arguments):
            calls.append(1)
            raise ToolExecutionError('retry', effect='uncertain' if len(calls) == 1 else 'none', retryable=True)
        app, session, _ = await self.setup_app(act, [[chunk(calls=[call('{}', name='act')], finish='tool_calls')]],
            ToolRuntime(operation_key=lambda call: 'work', retry_safe_tools=('act',)),
            policies={"tool_retry": {"max_retries": 1, "delay_seconds": 0}})
        failed = await (await session.run.submit('go', agent_id='loop')).wait()
        self.assertEqual((await session.run.aoperation('work'))['status'], 'started')
        with self.assertRaisesRegex(Exception, 'uncertain Tool'):
            await session.run.resume(failed.id, agent_id='loop')
    async def test_resume_after_backend_restart_reuses_completed_effect(self):
        effects = []
        async def act(arguments):
            effects.append(1)
        app, session, model = await self.setup_app(act, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')],
            [ConnectionError('offline')], [chunk('done', finish='stop')]])
        failed = await (await session.run.submit('go', agent_id='loop')).wait()
        project = session.project
        root, project_id, session_id = project.data.paths.root.parent.parent, project.id, session.id
        await app.shutdown()
        reopened = LargeLanguageModel(root, components=[RuntimeTools(ToolRegistry((
            Tool('act', 'action', {'type': 'object'}, act, contract=TEST_CONTRACT),)))], engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})
        self.addAsyncCleanup(reopened.shutdown)
        session = (await reopened.projects.aload(project_id)).sessions.load(session_id)
        resumed = await (await session.run.resume(failed.id, agent_id='loop')).wait()
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(effects, [1])
        self.assertEqual(len(model.requests), 3)

    async def test_partial_provider_response_is_never_automatically_retried(self):
        class Busy(Exception):
            status_code = 503
        async def act(arguments):
            pass
        app, session, model = await self.setup_app(act, [[chunk('partial'), Busy('offline')]])
        await configure_engine(session.project, 'loop', provider={'max_attempts': 3, 'delay_seconds': 0})
        run = await (await session.run.submit('go', agent_id='loop')).wait()
        self.assertEqual(run.data.status, 'failed')
        self.assertEqual(len(model.requests), 1)

    async def test_partial_tool_arguments_are_not_automatically_retried(self):
        class Busy(Exception):
            status_code = 503
        async def act(arguments):
            self.fail('Incomplete Tool call must not execute')
        app, session, model = await self.setup_app(act, [[chunk(calls=[call('{', name='act')]), Busy('offline')]])
        await configure_engine(session.project, 'loop', provider={'max_attempts': 3, 'delay_seconds': 0})
        run = await (await session.run.submit('go', agent_id='loop')).wait()
        self.assertEqual(run.data.status, 'failed')
        self.assertEqual(len(model.requests), 1)

    async def test_tool_handler_cannot_report_effect_as_approval_waiting(self):
        async def act(arguments):
            raise ToolApprovalRequired('effect may already have happened')
        app, session, _ = await self.setup_app(act, [[chunk(calls=[call('{}', name='act')], finish='tool_calls')]])
        run = await (await session.run.submit('go', agent_id='loop')).wait()
        self.assertEqual(run.data.status, 'failed')
        with self.assertRaisesRegex(Exception, 'uncertain Tool'):
            await session.run.resume(run.id, agent_id='loop')

    async def test_run_page_loads_only_selected_records_and_invalidates_catalog(self):
        from llm.services.query import Query
        async def act(arguments):
            pass
        app, session, _ = await self.setup_app(act, [[chunk('one', finish='stop')], [chunk('two', finish='stop')]])
        first = await (await session.run.submit('one', agent_id='loop')).wait()
        second = await (await session.run.submit('two', agent_id='loop')).wait()
        repository = app.run_repository
        with patch.object(repository, 'load', wraps=repository.load) as read:
            page = await session.run.alist(query=Query(limit=1, descending=True))
            self.assertEqual([item.id for item in page], [second.id])
            self.assertEqual(read.call_count, 1)
        record = first.data
        from llm.core.models import RunStatus
        record.status = RunStatus.FAILED
        atomic_domain_json(record.paths.root / 'run.json', domain_record(record))
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
            components=[RuntimeTools(ToolRegistry((Tool('act', 'action', {'type': 'object'}, act, contract=TEST_CONTRACT),))), WorkflowComponent()],
            engines={'graph': GraphEngine(handlers={'tool': ToolNode()})},
            services=BackendServices(tool_runtime=ToolRuntime(authorize=authorize)))
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate(components=['tools', 'workflows'])
        project.components.tools.enable('act')
        graph = WorkflowGraph(entry='action').node('action', 'tool', tool='act').node('end', 'end').connect('action', 'end').to_dict()
        await project.components.workflows.acreate(graph, identifier='flow')
        await project.agents.acreate({'engine': 'graph', 'engine_options': {'workflow': 'flow'}}, identifier='graph')
        session = await project.sessions.acreate()
        paused = await (await session.run.submit('go', agent_id='graph')).wait()
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        self.assertEqual(effects, [])
        resumed = await (await session.run.resume(paused.id, agent_id='graph', decisions={'["action"]': {'approved': True}})).wait()
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(effects, [1])

    async def test_token_quota_reserves_unknown_usage_and_project_call_limit(self):
        async def act(arguments):
            return 'ok'
        app, session, model = await self.setup_app(act, [[chunk(calls=[call('{}', name='act')], finish='tool_calls')]])
        app.policy_resolver.token_counters['fixed'] = lambda request: 10
        project = app.projects.load(session.data.project_id)
        config = project.data.config
        config.parameters["engines"]["loop"]["config"]["completion"]['max_tokens'] = 5
        config.configure_policies({'usage': {'counter': 'fixed', 'max_tokens': 20, 'project_max_calls': 1}})
        await project.asave(config=config)
        failed = await (await session.run.submit('go', agent_id='loop')).wait()
        self.assertEqual((await failed.aresult()).error_code, 'usage_limit')
        other = await project.sessions.acreate()
        failed = await (await other.run.submit('go', agent_id='loop')).wait()
        self.assertEqual((await failed.aresult()).error_code, 'usage_limit')
        self.assertEqual(len(model.requests), 1)

    async def test_output_reconciliation_and_ui_snapshot(self):
        async def act(arguments):
            pass
        app, session, _ = await self.setup_app(act, [[chunk('answer', finish='stop')]])
        run = await (await session.run.submit('go', agent_id='loop')).wait()
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
        from hub.backend.project.history_retention import HistoryRetention, HistoryRetentionPolicy
        retention = HistoryRetention(project, HistoryRetentionPolicy(unit='session', max_bytes=1))
        await (await session.run.submit('go', agent_id='loop')).wait()
        self.assertEqual((await retention.preview()).candidates, [])
        await session.run.shutdown()
        plan = await retention.preview()
        self.assertEqual([r['session_id'] for r in plan.candidates], [session.id])
        await retention.apply(plan)
        self.assertEqual(await project.sessions.alist(), [])

    async def test_provider_retry_has_separate_usage_records(self):
        class Busy(Exception):
            status_code = 503
        async def act(arguments):
            pass
        app, session, model = await self.setup_app(act, [[Busy('busy')], [chunk('done', finish='stop')]])
        project = app.projects.load(session.data.project_id)
        await configure_engine(project, 'loop', provider={'max_attempts': 2, 'delay_seconds': 0})
        run = await (await session.run.submit('go', agent_id='loop')).wait()
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
            ToolRuntime(authorize=authorize))
        paused = await (await session.run.submit('go', agent_id='loop')).wait()
        self.assertEqual(paused.data.status, 'paused')
        self.assertEqual(effects, [])
        with self.assertRaisesRegex(Exception, 'decisions required'):
            await session.run.resume(paused.id, agent_id='loop')
        resumed = await (await session.run.resume(paused.id, agent_id='loop', decisions={'tool:1:call_1': True})).wait()
        self.assertEqual(resumed.data.status, 'completed')
        self.assertEqual(effects, [1])
        self.assertEqual(len(model.requests), 2)

    async def test_usage_admission_stops_before_second_call(self):
        async def act(arguments):
            return None
        app, session, model = await self.setup_app(act, [[chunk(calls=[call('{}', name='act')], finish='tool_calls')]])
        project = app.projects.load(session.data.project_id)
        await project.aconfigure_policies({'usage': {'max_calls': 1}})
        failed = await (await session.run.submit('go', agent_id='loop')).wait()
        self.assertEqual((await failed.aresult()).error_code, 'usage_limit')
        self.assertEqual(len(model.requests), 1)

    async def test_component_edit_conflict(self):
        async def act(arguments):
            pass
        app, session, _ = await self.setup_app(act, [])
        tools = app.projects.load(session.data.project_id).components.tools
        snapshot = await tools.asnapshot()
        await tools.aconfigure({'config': {'enabled': []}}, expected_version=snapshot['version'])
        with self.assertRaisesRegex(ValueError, 'edit_conflict'):
            await tools.aconfigure({'config': {'enabled': ['act']}}, expected_version=snapshot['version'])

    async def setup_app(self, handler, responses, runtime=None, *, policies=None, tool_schema=None, tool_contract=TEST_CONTRACT):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        model = ScriptedCompletion(*responses)
        tools = ToolRegistry((Tool("act", "action", tool_schema or {"type": "object"}, handler, contract=tool_contract),))
        app = LargeLanguageModel(Path(temporary.name), components=[RuntimeTools(tools)],
            engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))}, services=BackendServices(tool_runtime=runtime or ToolRuntime()))
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate("resume", config=ProjectConfig(policies=policies or {}, parameters={"engines": {"loop": {'config': {'completion': {'model': 'test/model'}}}}}), components=["tools"])
        project.components.tools.enable("act")
        await project.agents.acreate({"engine": "loop", "tools": ["act"]}, identifier="loop")
        session = await project.sessions.acreate()
        return app, session, model

    async def test_resume_reuses_none_result_and_does_not_repeat_completion(self):
        effects = []
        async def act(arguments):
            effects.append(arguments)
        app, session, model = await self.setup_app(act, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')],
            [ConnectionError('network')], [chunk('done', finish='stop')]])
        failed = await (await session.run.submit('go', agent_id='loop')).wait()
        self.assertEqual(failed.data.status, 'failed')
        resumed = await (await session.run.resume(failed.id, agent_id='loop')).wait()
        self.assertEqual(resumed.data.status, 'completed')
        self.assertEqual(effects, [{}])
        self.assertEqual(len(model.requests), 3)
        self.assertEqual(model.requests[-1]['messages'][-1]['content'], 'null')
        reused = [step for step in await resumed.steps.alist() if step.metadata.get('reused')]
        self.assertEqual(len(reused), 1)
        self.assertEqual(reused[0].metadata['source_run_id'], failed.id)
        self.assertIsNone(reused[0].output.data)
        original = app.project_manager.results.tool_result(session.project.data, session.id, resumed.id, reused[0].id, steps=app.step_manager)
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
            ToolRuntime(operation_key=lambda call: 'same-work'))
        failed = await (await session.run.submit('go', agent_id='loop')).wait()
        self.assertEqual((await session.run.aoperation('same-work'))['status'], 'not_applied')
        resumed = await (await session.run.resume(failed.id, agent_id='loop')).wait()
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
        failed = await (await session.run.submit('go', agent_id='loop')).wait()
        with self.assertRaisesRegex(Exception, 'uncertain Tool'):
            await session.run.resume(failed.id, agent_id='loop')
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
                policies={"tool_retry": {"max_retries": 2}})
            run = await (await session.run.submit('go', agent_id='loop')).wait()
            self.assertEqual(run.data.status, 'completed' if safe else 'failed')
            self.assertEqual(len(effects), 2 if safe else 1)
            await app.shutdown()
