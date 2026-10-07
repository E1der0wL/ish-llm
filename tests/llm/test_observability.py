"""누적 fact 집계와 authoritative gauge 합성·승인 재개 중복·privacy 경계."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llm.components.tools import Tool
from llm.core.models import Run, Session, Project
from llm.core.interactions import approval_request
from llm.engines.base import BaseEngine, EngineContext
from llm.services.infrastructure.observability import Observability, ObservabilityView
from llm.services.runtime.tools import ToolExecutor, ToolPolicy, ToolExecutionScope, ToolExecutionError, ToolApprovalRequired, ToolInvocationError
from llm.services.runtime.events import EventSubscriptions
from llm.providers.calls import ProviderCalls
from tests.llm import test_tool_packages as package_tests
from tests.llm.test_tool_workers import source


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    setup_runtime = package_tests.PackageRuntimeTests.setup_runtime

    async def execute(self, handler, *, scope=None, observer=None, decision=None):
        observer = observer or Observability()
        scope = scope or ToolExecutionScope(ToolPolicy())
        context = SimpleNamespace(project=SimpleNamespace(id="p"), session=SimpleNamespace(id="s"),
                                  run=SimpleNamespace(id="r", input_message_id="m"), tool_scope=scope)
        tool = Tool("act", "fixture", {"type": "object"}, handler)
        with observer.scope():
            async for _ in ToolExecutor().execute(tool, {}, result={}, context=context, decision=decision):
                pass
        return observer.snapshot()

    async def test_retry_counts_physical_attempts_once(self):
        attempts = 0
        async def handler(arguments):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ToolExecutionError("private", effect="none", retryable=True)
            return "private result"
        snapshot = await self.execute(handler, scope=ToolExecutionScope(ToolPolicy(), retry={"max_retries": 1}))
        self.assertEqual({k: snapshot["tools"][k] for k in ("requests", "executions", "retries", "failed")},
                         {"requests": 1, "executions": 2, "retries": 1, "failed": 0})
        self.assertNotIn("private", json.dumps(snapshot))

    async def test_operation_reuse_never_executes_handler(self):
        class Operations:
            async def claim(self, call):
                return {"reused": True, "result": "private receipt"}
        async def handler(arguments):
            self.fail("Receipt reuse must not execute")
        value = await self.execute(handler, scope=ToolExecutionScope(
            ToolPolicy(operation_key=lambda _: "business"), operations=Operations()))
        self.assertEqual((value["tools"]["requests"], value["tools"]["reused"], value["tools"]["executions"]), (1, 1, 0))

    async def test_actual_approval_resume_request_is_counted_once(self):
        async def ask(call):
            raise ToolApprovalRequired()
        project, session, _ = await self.setup_runtime(source('return None', decorator='@tool(approval_required=True)'),
                                                       policy=ToolPolicy(authorize=ask))
        app = project.app
        paused = await (await session.run.submit("private prompt", engine="loop")).wait()
        pending = app.observability.snapshot()
        self.assertEqual(pending["tools"]["requests"], 1)
        self.assertEqual(pending["tools"]["executions"], 0)
        request, = await paused.ainteractions()
        await paused.arespond(request.respond("approve"))
        resumed = await (await session.run.resume(paused.id, engine="loop")).wait()
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        value = app.observability.snapshot()
        self.assertEqual({k: value["tools"][k] for k in ("requests", "approval_required", "executions", "failed")},
                         {"requests": 1, "approval_required": 1, "executions": 1, "failed": 0})
        self.assertEqual(value["runs"]["started"], 2)
        self.assertEqual(value["runs"]["paused"], 1)
        self.assertEqual(value["runs"]["completed"], 1)
        self.assertNotIn("private prompt", json.dumps(value))

    async def test_worker_crash_is_one_tool_failure(self):
        project, session, _ = await self.setup_runtime(source('os._exit(9)', 'import os'))
        run = await (await session.run.submit("private prompt", engine="loop")).wait()
        self.assertEqual(run.data.error_code, 'tool_worker_failed')
        value = project.app.observability.snapshot()
        self.assertEqual(value['workers']['failed'], 1)
        self.assertEqual(value['tools']['failed'], 1)
        self.assertEqual(value['runs']['failed'], 1)

    async def test_sync_prepare_uses_the_same_host_observer(self):
        project, _, _ = await self.setup_runtime(source('return None'))
        before = project.app.observability.snapshot()['workers']['spawned']
        # 공개 동기 API를 스레드에서 사용하는 Host도 async prepare와 동일하게 관찰한다.
        await asyncio.to_thread(project.components.tools.prepare, 'act')
        self.assertEqual(project.app.observability.snapshot()['workers']['spawned'], before + 1)

    async def test_gauges_come_from_existing_owners_and_snapshot_is_detached(self):
        observer, calls, events = Observability(), ProviderCalls(), EventSubscriptions()
        runtime = {"active_runs": 2, "queued_requests": 3, "unfinished_work": 0}
        view = ObservabilityView(observer, lambda: dict(runtime), calls, events)
        subscription = events.subscribe(lambda _: None)
        await calls.acquire()
        await events.publish('engine', 'not persisted')
        value = view.snapshot()
        self.assertEqual(value['providers']['active'], calls.stats['active'])
        self.assertEqual(value['events']['delivered'], subscription.stats['delivered'])
        self.assertEqual(value['runtime'], runtime)
        self.assertNotIn('active', observer.snapshot()['providers'])
        self.assertNotIn('events', observer.snapshot())
        calls.release()
        runtime['active_runs'] = 0
        value['recent'].append({"modified": True})
        self.assertEqual(view.snapshot()['providers']['active'], 0)
        self.assertEqual(view.snapshot()['runtime']['active_runs'], 0)
        self.assertEqual(view.snapshot()['recent'], [])
        await events.close()

    async def test_event_totals_survive_subscription_retirement_without_retaining_entries(self):
        observer, events = Observability(), EventSubscriptions()
        view = ObservabilityView(observer, lambda: {}, ProviderCalls(), events)
        async def broken(value):
            raise ValueError("observation failure")
        good = events.subscribe(lambda value: None)
        failed = events.subscribe(broken, delivery="queued")
        await events.publish("engine", "value")
        await events.flush()
        before = view.snapshot()["events"]
        self.assertEqual(before, {"delivered": 1, "dropped": 0, "failures": 1, "timed_out": 0, "pending": 0})
        await good.aclose()
        await failed.aclose()
        await events.close()
        await events.close()
        self.assertEqual(len(events._subscriptions), 0)
        self.assertEqual(view.snapshot()["events"], before)
        self.assertEqual(good.stats["delivered"], 1)
        self.assertEqual(failed.stats["failures"], 1)
        self.assertNotIn("events", observer.snapshot())

    async def test_same_arguments_at_different_checkpoint_keys_are_distinct_requests(self):
        observer = Observability()
        context = SimpleNamespace(project=SimpleNamespace(id='p'), session=SimpleNamespace(id='s'),
            run=SimpleNamespace(id='r', input_message_id='m', metadata={'resume': {'decisions': {'a': True, 'b': True}}}),
            tool_scope=ToolExecutionScope(ToolPolicy()),
            checkpoint={'records': {
                'a': {'status': 'waiting', 'interaction': approval_request('Tool',
                    action={'tool': 'act', 'arguments': {}, 'contract': None,
                            'classification': {'category': 'tool.execute', 'risk_scheme': None, 'risk': None},
                            'policy': ToolExecutionScope(ToolPolicy()).binding()}).bind('custom', 'a').to_dict()},
                'b': {'status': 'waiting', 'interaction': approval_request('Pause',
                    action={'node_id': 'b'}).bind('custom', 'b').to_dict()}}})
        async def handler(arguments):
            return None
        tool = Tool('act', 'fixture', {'type': 'object'}, handler)
        # a의 Tool 승인 요청은 이미 관찰했다. b는 단순 Graph pause_before였으므로 새 Tool 요청이다.
        observer.record('tools', 'requests')
        with observer.scope():
            for key in ('a', 'b'):
                async for _ in ToolExecutor().execute(tool, {}, result={}, context=context,
                                                     request_key=key):
                    pass
        self.assertEqual(observer.snapshot()['tools']['requests'], 2)
        self.assertEqual(observer.snapshot()['tools']['executions'], 2)

    async def test_sink_failure_never_changes_tool_or_run(self):
        def fail(event):
            raise RuntimeError('sink failure')
        observer = Observability(sink=fail)
        async def handler(arguments):
            return None
        value = await self.execute(handler, observer=observer)
        self.assertEqual(value['tools']['completed'], 1)
        project, session, _ = await self.setup_runtime(source('return None'))
        project.app._observability._sink = fail
        run = await (await session.run.submit('prompt', engine='loop')).wait()
        self.assertEqual(run.data.status, 'completed', run.data.error)

    async def test_provider_common_boundary_counts_retries_and_streaming(self):
        from llm.providers.requests import invoke, ProviderError
        from llm.providers.litellm import stream_completion
        observer, attempts = Observability(), 0
        async def call(**kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ProviderError('provider_rate_limit')
            return {"private result": True}
        with observer.scope():
            await invoke('aembedding', {"input": ["private prompt"]}, call, {"max_attempts": 2})
            async for _ in stream_completion({}, completion_fn=lambda **_: iter([{"private response": True}])):
                pass
        value = observer.snapshot()
        self.assertEqual((value['providers']['calls'], value['providers']['retries'], value['providers']['failed']), (3, 1, 1))
        self.assertNotIn('private', json.dumps(value))

    def test_bounded_thread_safe_aggregate_and_allowlisted_facts(self):
        observer = Observability()
        def emit(_):
            observer.record('tools', 'executions', retry=True, prompt='private-secret', arguments='private-secret')
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(emit, range(1000)))
        value = observer.snapshot()
        self.assertEqual(value['tools']['executions'], 1000)
        self.assertEqual(value['tools']['retries'], 1000)
        self.assertEqual(len(value['recent']), 256)
        self.assertNotIn('private-secret', json.dumps(value))

    def test_extension_codes_and_labels_are_not_silently_folded_or_truncated(self):
        observer = Observability()
        name = "host-extension-" * 20
        for i in range(140):
            observer.record("tools", "failed", name=name, code="extension_" + "x" * 100 + str(i))
        value = observer.snapshot()
        self.assertEqual(len(value["failures_by_code"]), 140)
        self.assertNotIn("other", value["failures_by_code"])
        self.assertEqual(value["recent"][-1]["name"], name)


class ToolInvocationTests(unittest.IsolatedAsyncioTestCase):
    def context(self, *, records=None, decisions=None, scope=None):
        return EngineContext(SimpleNamespace(id='p'), SimpleNamespace(id='s'),
            SimpleNamespace(id='resume', input_message_id='m', metadata={'resume': {'decisions': decisions or {}}}),
            (), checkpoint={'records': records or {}}, tool_scope=scope or ToolExecutionScope(ToolPolicy()))

    def waiting(self, key, *, tool='act', arguments=None, graph=False):
        request = approval_request('Tool', action={'tool': tool, 'arguments': arguments or {}, 'contract': None,
            'classification': {'category': 'tool.execute', 'risk_scheme': None, 'risk': None},
            'policy': ToolExecutionScope(ToolPolicy()).binding()})
        return {'status': 'waiting', 'interaction': request.bind('custom', key,
            decision_key='approved' if graph else None).to_dict()}

    async def test_custom_engine_helper_reuses_approval_across_fresh_scope_and_retry(self):
        observer = Observability()
        attempts = []
        async def ask(call):
            raise ToolApprovalRequired()
        async def handler(arguments):
            attempts.append(1)
            if len(attempts) == 1:
                raise ToolExecutionError('retry', effect='none', retryable=True)
            return None
        tool = Tool('act', 'fixture', {'type': 'object'}, handler)
        class CustomEngine(BaseEngine):
            async def execute(self, context):
                from contextlib import aclosing
                async with aclosing(self.execute_tool(context, tool, {}, result={},
                                                     checkpoint_key='node/iteration:3')) as events:
                    async for event in events:
                        yield event
        engine = CustomEngine()
        with observer.scope():
            with self.assertRaises(ToolApprovalRequired) as pending:
                async for _ in engine.execute(self.context(scope=ToolExecutionScope(ToolPolicy(authorize=ask), retry={"max_retries": 1}))):
                    pass
            key = 'node/iteration:3'
            waiting = {'status': 'waiting', 'interaction': pending.exception.request.bind('custom', key).to_dict()}
            fresh = self.context(records={key: waiting}, decisions={key: True},
                                 scope=ToolExecutionScope(ToolPolicy(authorize=ask), retry={"max_retries": 1}))
            async for _ in engine.execute(fresh):
                pass
        tools = observer.snapshot()['tools']
        self.assertEqual([tools[k] for k in ('requests', 'approval_required', 'executions', 'retries')], [1, 1, 2, 1])

    async def test_missing_wrong_or_conflicting_binding_fails_before_execution(self):
        from unittest.mock import AsyncMock
        handler = AsyncMock()
        tool = Tool('act', 'fixture', {'type': 'object'}, handler)
        cases = [(None, True, {}), ('typo', True, {}), ('a', False, {}), ('a', None, {}),
                 ('a', True, {'changed': True})]
        for key, decision, arguments in cases:
            with self.subTest(key=key, decision=decision, arguments=arguments):
                observer = Observability()
                context = self.context(records={'a': self.waiting('a'), 'b': self.waiting('b')},
                                       decisions={'a': True, 'b': True})
                with observer.scope(), self.assertRaises(ToolInvocationError), \
                        patch('llm.components.tools.process.invoke_worker') as worker:
                    async for _ in ToolExecutor().execute(tool, arguments, result={}, context=context,
                                                         request_key=key, decision=decision):
                        self.fail('Invalid invocation must not start a Step')
                worker.assert_not_called()
                self.assertEqual(observer.snapshot()['tools']['requests'], 0)
        handler.assert_not_awaited()

    async def test_helper_keeps_scoped_keys_and_resolves_graph_choice_values(self):
        executed = []
        async def handler(arguments):
            executed.append(arguments)
        tool = Tool('act', 'fixture', {'type': 'object'}, handler)
        keys = ['["parent","loop",0,"tool"]', '["parent","loop",1,"tool"]']
        records = {key: self.waiting(key, graph=True) for key in keys}
        context = self.context(records=records, decisions={key: {'approved': True} for key in keys})
        for key in keys:
            async for _ in context.execute_tool(tool, {}, checkpoint_key=key, result={}):
                pass
        self.assertEqual(len(executed), 2)
        self.assertEqual(set(records), set(keys))

    async def test_denial_reuses_request_without_execution(self):
        from unittest.mock import AsyncMock
        observer, handler = Observability(), AsyncMock()
        tool = Tool('act', 'fixture', {'type': 'object'}, handler)
        context = self.context(records={'a': self.waiting('a')}, decisions={'a': False})
        with observer.scope():
            observer.record('tools', 'requests')
            with self.assertRaisesRegex(Exception, 'denied'):
                async for _ in context.execute_tool(tool, {}, checkpoint_key='a', result={}):
                    pass
        self.assertEqual(observer.snapshot()['tools']['requests'], 1)
        self.assertEqual(observer.snapshot()['tools']['executions'], 0)
        handler.assert_not_awaited()

    async def test_plain_host_decision_without_checkpoint_remains_supported(self):
        async def handler(arguments):
            return None
        tool = Tool('act', 'fixture', {'type': 'object'}, handler)
        async for _ in ToolExecutor().execute(tool, {}, result={}, context=self.context(), decision=True):
            pass

    async def test_checkpoint_confirmation_cannot_be_forwarded_as_tool_approval(self):
        from dataclasses import replace
        from unittest.mock import AsyncMock
        handler = AsyncMock()
        tool = Tool('act', 'fixture', {'type': 'object'}, handler)
        pause = replace(approval_request('Continue', category='workflow.continue', action={'node': 'b'}),
                        kind='confirmation').bind('graph', 'b', decision_key='approved').to_dict()
        for record in ({'status': 'waiting', 'interaction': pause},
                       {'status': 'started', 'decision': {'approved': True}}):
            for decision in (True, False):
                with self.subTest(record=record, decision=decision):
                    context = self.context(records={'b': record}, decisions={'b': {'approved': True}})
                    with self.assertRaisesRegex(ToolInvocationError, 'not a durable Tool approval'):
                        async for _ in ToolExecutor().execute(tool, {}, result={}, context=context,
                                                             decision=decision, request_key='b'):
                            self.fail('Invalid decision must fail before Step/effect')
        handler.assert_not_awaited()

    async def test_helper_ignores_confirmation_and_runs_host_authorization(self):
        from dataclasses import replace
        from unittest.mock import AsyncMock
        handler, authorize = AsyncMock(), AsyncMock(return_value=False)
        tool = Tool('act', 'fixture', {'type': 'object'}, handler)
        pause = replace(approval_request('Continue'), kind='confirmation').bind(
            'graph', 'b', decision_key='approved').to_dict()
        context = self.context(records={'b': {'status': 'waiting', 'interaction': pause}},
            decisions={'b': {'approved': True}}, scope=ToolExecutionScope(ToolPolicy(authorize=authorize)))
        with self.assertRaisesRegex(Exception, 'denied'):
            async for _ in context.execute_tool(tool, {}, checkpoint_key='b', result={}):
                pass
        authorize.assert_awaited_once()
        handler.assert_not_awaited()


class BoundaryTests(unittest.TestCase):
    def test_project_source_execution_only_in_child_and_engine_uses_executor(self):
        import ast
        root = Path(__file__).resolve().parents[2] / 'llm'
        for relative in ('components/tools/packages.py', 'components/tools/process.py'):
            tree = ast.parse((root / relative).read_text())
            self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                                 and node.func.id in ('exec', 'eval') for node in ast.walk(tree)))
        for path in (root / 'engines').rglob('*.py'):
            tree = ast.parse(path.read_text())
            self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                                 and node.func.attr == 'handler' for node in ast.walk(tree)), str(path))

    def test_sdk_execution_is_confined_to_provider_boundary(self):
        import ast
        root = Path(__file__).resolve().parents[2] / 'llm'
        for directory in ('components', 'engines', 'services'):
            for path in (root / directory).rglob('*.py'):
                tree = ast.parse(path.read_text())
                # SDK를 얻어 token_counter를 사용하는 것은 모델 호출이 아니다.
                self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                                     and node.func.attr in ('completion', 'acompletion', 'aembedding', 'arerank')
                                     and (isinstance(node.func.value, ast.Name) and node.func.value.id == 'sdk'
                                          or isinstance(node.func.value, ast.Call) and isinstance(node.func.value.func, ast.Name)
                                          and node.func.value.func.id == 'litellm_sdk')
                                     for node in ast.walk(tree)), str(path))
        client = ast.parse((root / 'components/rag/_client.py').read_text())
        self.assertTrue(any(isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
                            and isinstance(node.value.func, ast.Name) and node.value.func.id == 'invoke'
                            for node in ast.walk(client)))

    def test_components_do_not_own_telemetry(self):
        import ast
        root = Path(__file__).resolve().parents[2] / 'llm/components'
        for path in root.rglob('*.py'):
            if path == root / 'tools/process.py':
                continue  # 지정된 worker boundary
            tree = ast.parse(path.read_text())
            self.assertFalse(any(isinstance(node, ast.ImportFrom) and node.module
                                 and node.module.endswith('infrastructure.observability')
                                 for node in ast.walk(tree)), str(path))
