"""Public summary requests, durable chunk cursors and original Tool source ownership."""

from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from llm.components.base import Component
from llm.components.data import DataComponent
from llm.components.memory import MemoryComponent
from llm.components.mcp import MCPComponent
from llm.components.tools import Tool, ToolContract, ToolRegistry, ToolClassification
from llm.llm import LargeLanguageModel, LoopEngine
from tests.llm.configuration_fixtures import memory_settings, configure_engine
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from llm.components.tools import ToolEffect, ToolIsolation


def answer(text="done"):
    return [chunk(text, finish="stop")]


class SourceTools(Component):
    name = directory = "source"
    capabilities = ("tools",)

    def __init__(self):
        self.calls = 0
        self.content = "한글 source " * 100

    def resolve(self, project, capability):
        async def read(arguments):
            self.calls += 1
            return self.content
        return ToolRegistry((Tool("source_read", "Read a large result", {"type": "object", "properties": {},
            "additionalProperties": False}, read, contract=ToolContract(revision="1", effect=ToolEffect.READ_ONLY, isolation=ToolIsolation.NONE, approval_required=False, operation_key_required=False, retry_fields=())),))


class LargeDataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.requests = []
        self.memory_component = MemoryComponent(completion_adapter=LiteLLMCompletionAdapter(completion_fn=self.auxiliary))
        self.source = SourceTools()
        self.serial = 0
        await self.open()
        self.project = await self.app.projects.acreate(components=["memory", "data", "source"])
        self.memory = await self.project.components.aget("memory")
        self.files = await self.project.components.aget("data")
        await self.files.acreate({"title": "Explicit result storage"}, identifier="results")
        self.session = await self.project.sessions.acreate()

    def auxiliary(self, **request):
        self.requests.append(json.loads(request["messages"][1]["content"]))
        yield from answer('{"summary":"valid summary"}')

    async def open(self):
        self.app = LargeLanguageModel(self.root, components=[self.memory_component, DataComponent(), self.source], engines={})
        self.addAsyncCleanup(self.app.shutdown)

    async def run_loop(self, text="next", model=None):
        model = model or ScriptedCompletion(answer())
        name = "loop" + str(self.serial)
        self.serial += 1
        self.app.engines.register(name, LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model)))
        await configure_engine(self.project, name, completion={"model": "test/main"})
        from llm.components.tools.resolver import ComponentToolResolver
        manager = self.app.project_manager
        def selected_tools():
            with manager.ownership.scope():
                project = manager.access.require(self.project.data)
                return list(ComponentToolResolver(manager.components,
                    data_factory=manager.component, agents=manager.agents,
                    engines=self.app.engines).resolve_tools(project).names())
        await self.project.agents.acreate({"engine": name,
            "tools": [*(await asyncio.to_thread(selected_tools)), "memory_tool_result"]}, identifier=name)
        run = await (await self.session.run.submit(text, agent_id=name)).wait(timeout=20)
        return run, model

    async def history(self):
        await self.run_loop("source " * 90)
        await self.run_loop("recent")

    def options(self, **values):
        return {"method": "summary", "keep_turns": 0, "summary_chars": 40,
            "model_input_chars": 300, "max_summary_calls": 1, "prompt": "Summarize",
            "completion": {"model": "test/summary"}, **values}

    async def request(self, **values):
        view = await self.memory.aget_context(self.session.id)
        return await self.memory.arequest_compaction(self.session.id, self.options(**values), expected_revision=view["revision"])

    async def tool_config(self, mode="summary", **values):
        await self.memory.aconfigure(memory_settings({"processing": {
            "priority": 0, "tool_result_mode": mode, "tool_data": {"component": "data", "identifier": "results"},
            "tool_result_chars": 100, "tool_summary_prompt": "Summarize reference data",
            "completion": {"model": "test/summary"}, "summary_chars": 40,
            "model_input_chars": 300, "max_summary_calls": 40, **values}}))

    async def test_compaction_state_operations_read_each_record_once(self):
        from llm.components.memory.compaction import Compactions
        await self.history()
        original, reads = Compactions.read, []
        def read(storage):
            reads.append(storage.path)
            return original(storage)
        with patch.object(Compactions, "read", read):
            view = await self.memory.aget_context(self.session.id)
            self.assertEqual(len(reads), 1)
            reads.clear()
            pending = await self.memory.arequest_compaction(self.session.id, self.options(),
                                                            expected_revision=view["revision"])
            self.assertEqual(len(reads), 1)
            view = await self.memory.aget_context(self.session.id)
            reads.clear()
            cancelled = await self.memory.acancel_compaction(self.session.id, expected_revision=view["revision"])
            self.assertEqual(len(reads), 1)
            self.assertEqual(cancelled["request"]["id"], pending["id"])
            self.assertEqual(cancelled["request"]["status"], "cancelled")
            reads.clear()
            await self.memory.adelete_compaction(self.session.id, expected_revision=cancelled["revision"])
            self.assertEqual(len(reads), 1)

        await self.tool_config(mode="reference")
        run, _ = await self.run_loop(model=ScriptedCompletion(
            [chunk(calls=[call('{}', name='source_read')], finish="tool_calls")], answer()))
        self.assertEqual(run.data.status, "completed", run.data.error)
        reads.clear()
        with patch.object(Compactions, "read", read):
            rows = await self.memory.alist_tool_summaries(self.session.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(reads), 1)

    async def test_fragments_resume_same_request_after_reopen_without_repeating_source(self):
        await self.history()
        pending = await self.request()
        run, model = await self.run_loop()
        self.assertEqual(run.data.status, "completed", run.data.error)
        view = await self.memory.aget_context(self.session.id)
        self.assertEqual(view["request"]["status"], "partial")
        self.assertIn("source source", str(model.requests))
        saved = view["request"]["checkpoint"]
        await self.app.shutdown()
        await self.open()
        self.project = await self.app.projects.aload(self.project.id)
        self.session = await self.project.sessions.aload(self.session.id)
        self.memory = await self.project.components.aget("memory")
        # An ordinary Run must not automatically continue a partial request.
        await self.run_loop()
        self.assertEqual(len(self.requests), 1)
        for _ in range(20):
            view = await self.memory.aget_context(self.session.id)
            if view["request"]["status"] == "applied":
                break
            await self.memory.aresume_compaction(self.session.id, expected_revision=view["revision"])
            run, model = await self.run_loop()
            self.assertEqual(run.data.status, "completed", run.data.error)
        view = await self.memory.aget_context(self.session.id)
        self.assertEqual(view["request"]["id"], pending["id"])
        self.assertEqual(view["request"]["status"], "applied")
        self.assertEqual(self.requests[1]["messages"][0]["offset"], saved["offset"])
        parts = [request["messages"][0] for request in self.requests]
        text = "".join(p["content"] for p in parts)
        self.assertEqual(len(text), parts[-1]["total_chars"])
        self.assertEqual([p["offset"] for p in parts], [sum(len(v["content"]) for v in parts[:i]) for i in range(len(parts))])
        self.assertTrue(all(len(json.dumps(r, ensure_ascii=False)) <= 300 for r in self.requests))
        self.assertIn("Compacted prior conversation", str(model.requests))

    async def test_missing_budgets_rejected_and_pending_cancel_is_cas(self):
        await self.history()
        view = await self.memory.aget_context(self.session.id)
        for key in ("model_input_chars", "max_summary_calls"):
            options = self.options()
            del options[key]
            with self.assertRaises(Exception):
                await self.memory.arequest_compaction(self.session.id, options, expected_revision=view["revision"])
        await self.request()
        with self.assertRaises(ValueError):
            await self.memory.acancel_compaction(self.session.id, expected_revision=view["revision"])
        view = await self.memory.aget_context(self.session.id)
        cancelled = await self.memory.acancel_compaction(self.session.id, expected_revision=view["revision"])
        self.assertEqual(cancelled["request"]["status"], "cancelled")
        await self.run_loop()
        self.assertEqual(self.requests, [])
        with self.assertRaises(ValueError):
            await self.memory.aresume_compaction(self.session.id,
                expected_revision=(await self.memory.aget_context(self.session.id))["revision"])

    async def test_failure_after_chunk_keeps_checkpoint_and_resume_does_not_repeat_it(self):
        await self.history()
        attempts = []
        def failing(**request):
            payload = json.loads(request["messages"][1]["content"])
            attempts.append(payload["messages"][0]["offset"])
            if len(attempts) == 2:
                raise ConnectionError("second fragment fails")
            yield from self.auxiliary(**request)
        self.memory_component.completion_adapter = LiteLLMCompletionAdapter(completion_fn=failing)
        await self.request(max_summary_calls=30)
        await self.run_loop()
        view = await self.memory.aget_context(self.session.id)
        self.assertEqual(view["request"]["status"], "failed")
        self.assertEqual(view["request"]["checkpoint"]["completed_chunks"], 1)
        await self.memory.aresume_compaction(self.session.id, expected_revision=view["revision"])
        await self.run_loop()
        self.assertEqual(attempts[1], attempts[2])
        self.assertNotIn(0, attempts[1:])
        self.assertEqual((await self.memory.aget_context(self.session.id))["request"]["status"], "applied")

    async def test_cancel_during_model_call_rejects_late_result_and_keeps_original(self):
        await self.history()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def slow(**request):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("test did not release summary call")
            yield from answer('{"summary":"late result"}')
        self.memory_component.completion_adapter = LiteLLMCompletionAdapter(completion_fn=slow)
        await self.request(max_summary_calls=30)
        task = asyncio.create_task(self.run_loop())
        self.assertTrue(await asyncio.to_thread(entered.wait, 5))
        view = await self.memory.aget_context(self.session.id)
        await self.memory.acancel_compaction(self.session.id, expected_revision=view["revision"])
        release.set()
        run, model = await task
        self.assertEqual(run.data.status, "completed", run.data.error)
        self.assertNotIn("late result", str(model.requests))
        self.assertEqual((await self.memory.aget_context(self.session.id))["request"]["status"], "cancelled")

    async def test_publication_write_failure_keeps_completed_cursor_but_no_applied_summary(self):
        await self.history()
        await self.request(max_summary_calls=30)
        from llm.components.memory.compaction import Compactions
        write, failed = Compactions.write, []
        def fail_once(storage, state):
            if state.get("request", {}).get("status") == "applied" and not failed:
                failed.append(True)
                raise OSError("disk write failed")
            return write(storage, state)
        with patch.object(Compactions, "write", fail_once):
            run, _ = await self.run_loop()
        self.assertEqual(run.data.status, "failed")
        view = await self.memory.aget_context(self.session.id)
        checkpoint = view["request"]["checkpoint"]
        self.assertEqual(checkpoint["offset"], checkpoint["total_chars"])
        calls = len(self.requests)
        await self.memory.aresume_compaction(self.session.id, expected_revision=view["revision"])
        run, _ = await self.run_loop()
        self.assertEqual(run.data.status, "completed", run.data.error)
        self.assertEqual(len(self.requests), calls)

    async def test_tool_summary_reuses_original_on_run_resume_and_protects_data(self):
        await self.tool_config()
        model = ScriptedCompletion([chunk(calls=[call('{}', name='source_read')], finish='tool_calls')],
            [ConnectionError("main model disconnected")], answer())
        run, _ = await self.run_loop(model=model)
        self.assertEqual(run.data.status, "failed")
        self.assertEqual(self.source.calls, 1)
        saved = (await self.memory.alist_tool_summaries(self.session.id))[0]
        self.assertEqual(saved["request"]["status"], "applied")
        count = len(self.requests)
        resumed = await (await self.session.run.resume(run.id, agent_id=run.data.agent_id)).wait(timeout=20)
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertEqual((self.source.calls, len(self.requests)), (1, count))
        identifier, ref = saved["request"]["id"], saved["request"]["reference"]
        text = await self.memory.aread_source(self.session.id, identifier, expected_revision=ref["revision"], offset=0, limit=15)
        self.assertIn("한글 source", text["text"])
        with self.assertRaises(ValueError):
            await self.files.adelete("results")
        snapshot = await self.files.asnapshot("results")
        with self.assertRaises(ValueError):
            await self.files.adelete_file("results", ref["path"], expected_revision=snapshot["version"])
        other = await self.project.sessions.acreate()
        with self.assertRaises(KeyError):
            await self.memory.aread_source(other.id, identifier, expected_revision=ref["revision"], offset=0, limit=15)
        await self.memory.adelete_tool_summary(self.session.id, identifier, expected_revision=saved["revision"])
        await self.files.adelete_file("results", ref["path"], expected_revision=snapshot["version"])

    async def test_reference_mode_has_no_model_call_and_stale_file_is_rejected(self):
        await self.tool_config(mode="reference")
        model = ScriptedCompletion([chunk(calls=[call('{}', name='source_read')], finish='tool_calls')], answer())
        run, _ = await self.run_loop(model=model)
        self.assertEqual(run.data.status, "completed", run.data.error)
        self.assertEqual(self.requests, [])
        result = json.loads(next(m['content'] for m in model.requests[-1]['messages'] if m['role'] == 'tool'))
        self.assertEqual(result['reference']['tool'], 'memory_read_source')
        saved = (await self.memory.alist_tool_summaries(self.session.id))[0]
        reference = saved['request']['reference']
        snapshot = await self.files.asnapshot('results')
        await self.files.awrite_file('results', reference['path'], 'edited', expected_revision=snapshot['version'])
        with self.assertRaisesRegex(ValueError, 'stale'):
            await self.memory.aread_source(self.session.id, saved['request']['id'],
                expected_revision=reference['revision'], offset=0, limit=10)

    async def test_mcp_approval_effect_once_then_summary(self):
        effects = []
        @asynccontextmanager
        async def connector(definition):
            async def invoke(arguments):
                effects.append(True)
                return {"content": [{"type": "text", "text": "remote " * 200}]}
            yield ToolRegistry((Tool("remote", "external", {"type": "object", "properties": {}}, invoke,
                contract=ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=True, operation_key_required=False, retry_fields=()),
                classification=ToolClassification("remote.call", "test", 1)),))
        await self.app.shutdown()
        self.app = LargeLanguageModel(self.root, components=[self.memory_component, DataComponent(), self.source,
            MCPComponent(connector=connector)], engines={})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=['memory', 'data', 'mcp'], config={"policies": {
            "approval": {"enabled": True, "risk_scheme": "test", "rules": [{"id": "allow", "category": "remote.call", "max_risk": 1}]}}})
        self.memory, self.files = self.project.components.memory, self.project.components.data
        self.session = await self.project.sessions.acreate()
        await self.files.acreate({}, identifier='results')
        await self.tool_config()
        mcp = self.project.components.mcp
        await mcp.acreate({'transport': 'stdio', 'command': 'fake'}, identifier='server')
        await mcp.aprepare('server')
        await mcp.aconfigure({'config': {'tools': {'remote': {'server': 'server', 'tool': 'remote'}}}})
        model = ScriptedCompletion([chunk(calls=[call('{}', name='remote')], finish='tool_calls')], answer())
        paused, _ = await self.run_loop(model=model)
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        self.assertEqual((effects, self.requests), ([], []))
        self.assertEqual([r.actor for r in await paused.ainteraction_responses()], ['policy'])
        done = await (await self.session.run.resume(paused.id, agent_id=paused.data.agent_id)).wait(timeout=20)
        self.assertEqual(done.data.status, 'completed', done.data.error)
        self.assertEqual(effects, [True])
        self.assertGreater(len(self.requests), 1)
        self.assertIn('memory_read_source', str(model.requests[-1]['messages']))

    async def test_partial_tool_summary_resumes_only_by_request_without_replaying_tool(self):
        self.source.content = "fragment " * 30
        await self.tool_config(max_summary_calls=1)
        model = ScriptedCompletion([chunk(calls=[call('{}', name='source_read')], finish='tool_calls')],
                                   [ConnectionError("stop after partial summary")])
        run, _ = await self.run_loop(model=model)
        saved = (await self.memory.alist_tool_summaries(self.session.id))[0]
        identifier = saved['request']['id']
        self.assertEqual(saved['request']['status'], 'partial')
        with self.assertRaises(ValueError):
            await self.memory.adelete_tool_summary(self.session.id, identifier, expected_revision=saved['revision'])
        count = len(self.requests)
        model.responses.append([ConnectionError('still unavailable')])
        run = await (await self.session.run.resume(run.id, agent_id=run.data.agent_id)).wait(timeout=20)
        self.assertEqual(len(self.requests), count)  # ordinary Run resume does not requeue summary work
        for _ in range(10):
            saved = (await self.memory.alist_tool_summaries(self.session.id))[0]
            if saved['request']['status'] == 'applied':
                break
            await self.memory.aresume_tool_summary(self.session.id, identifier, expected_revision=saved['revision'])
            model.responses.append([ConnectionError('keep Run resumable')])
            run = await (await self.session.run.resume(run.id, agent_id=run.data.agent_id)).wait(timeout=20)
        saved = (await self.memory.alist_tool_summaries(self.session.id))[0]
        self.assertEqual(saved['request']['status'], 'applied')
        model.responses.append(answer())
        done = await (await self.session.run.resume(run.id, agent_id=run.data.agent_id)).wait(timeout=20)
        self.assertEqual(done.data.status, 'completed', done.data.error)
        self.assertEqual(self.source.calls, 1)
        parts = [value['messages'][0] for value in self.requests]
        self.assertEqual(''.join(value['content'] for value in parts), self.source.content)

    async def test_run_interrupt_preserves_first_chunk_and_explicit_resume_continues(self):
        await self.history()
        await self.request(max_summary_calls=30)
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        attempts = []
        def model(**request):
            attempts.append(json.loads(request['messages'][1]['content'])['messages'][0]['offset'])
            if len(attempts) == 2:
                entered.set()
                try:
                    if not release.wait(10):
                        raise TimeoutError('fixture not released')
                    yield from answer('{"summary":"interrupted result"}')
                finally:
                    closed.set()
            else:
                yield from self.auxiliary(**request)
        self.memory_component.completion_adapter = LiteLLMCompletionAdapter(completion_fn=model)
        task = asyncio.create_task(self.run_loop())
        self.assertTrue(await asyncio.to_thread(entered.wait, 5))
        await self.session.run.interrupt()
        release.set()
        self.assertTrue(await asyncio.to_thread(closed.wait, 5))
        run, main = await task
        self.assertEqual(run.data.status, 'interrupted')
        self.assertEqual(main.requests, [])
        view = await self.memory.aget_context(self.session.id)
        self.assertEqual(view['request']['checkpoint']['completed_chunks'], 1)
        self.assertEqual(view['request']['status'], 'interrupted')
        await self.memory.aresume_compaction(self.session.id, expected_revision=view['revision'])
        done = await (await self.session.run.resume(run.id, agent_id=run.data.agent_id)).wait(timeout=20)
        self.assertEqual(done.data.status, 'completed', done.data.error)
        self.assertEqual(attempts[1], attempts[2])
        self.assertEqual((await self.memory.aget_context(self.session.id))['request']['status'], 'applied')

    async def test_checkpoint_corruption_and_options_change_cannot_reuse_summary(self):
        await self.history()
        await self.request()
        await self.run_loop()
        from llm.components.memory.compaction import Compactions
        from llm.services.infrastructure.storage import atomic_json
        storage = Compactions(self.memory, self.session.id, None)
        state = storage.read()
        corrupted = json.loads(json.dumps(state))
        corrupted['request']['checkpoint']['summary'] = 'forged'
        atomic_json(storage.path, corrupted)
        with self.assertRaisesRegex(ValueError, 'integrity'):
            await self.memory.aget_context(self.session.id)
        atomic_json(storage.path, state)
        # A valid owner-written option change must still reject reuse of the old cursor.
        state['request']['options']['prompt'] = 'a different instruction'
        with self.app.project_manager.ownership.scope():
            storage.write(state)
        view = await self.memory.aget_context(self.session.id)
        await self.memory.aresume_compaction(self.session.id, expected_revision=view['revision'])
        count = len(self.requests)
        await self.run_loop()
        self.assertEqual(len(self.requests), count)
        self.assertEqual((await self.memory.aget_context(self.session.id))['request']['status'], 'failed')

    async def test_original_and_reference_are_rolled_back_together_on_storage_failure(self):
        await self.tool_config()
        from llm.components.memory.compaction import Compactions
        original = Compactions.write
        def fail(storage, state):
            if 'reference' in state.get('request', {}):
                raise OSError('reference storage unavailable')
            return original(storage, state)
        model = ScriptedCompletion([chunk(calls=[call('{}', name='source_read')], finish='tool_calls')], answer())
        with patch.object(Compactions, 'write', fail):
            run, _ = await self.run_loop(model=model)
        self.assertEqual(run.data.status, 'failed')
        self.assertNotIn('files', (await self.files.asnapshot('results'))['data'])
        self.assertEqual(await self.memory.alist_tool_summaries(self.session.id), [])
        done = await (await self.session.run.resume(run.id, agent_id=run.data.agent_id)).wait(timeout=20)
        self.assertEqual(done.data.status, 'completed', done.data.error)
        self.assertEqual(self.source.calls, 1)

    async def test_read_source_tool_returns_exact_slice_without_recursive_transformation(self):
        await self.tool_config(mode='reference')
        requests = []
        def main(**request):
            requests.append(request)
            if len(requests) == 1:
                yield chunk(calls=[call('{}', name='source_read')], finish='tool_calls')
            elif len(requests) == 2:
                reference = json.loads(request['messages'][-1]['content'])['reference']
                yield chunk(calls=[call(json.dumps({'identifier': reference['id'], 'expected_revision': reference['revision'],
                    'offset': 5, 'limit': 150}), name='memory_read_source', call_id='slice')], finish='tool_calls')
            else:
                yield from answer()
        run, _ = await self.run_loop(model=main)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        value = json.loads(requests[-1]['messages'][-1]['content'])
        self.assertEqual(value['text'], self.source.content[5:155])
        self.assertEqual(len(await self.memory.alist_tool_summaries(self.session.id)), 1)
        self.assertEqual(self.requests, [])

    async def test_compaction_references_release_only_after_cancel_or_completion(self):
        await self.history()
        pending = await self.request()
        source_id = pending['sources'][0]['id']
        await self.session.run.shutdown()
        plan = await self.project.aretention_plan('run')
        self.assertTrue(any(source_id in row['message_ids'] and 'recent_or_referenced' in row['reasons']
                            for row in plan.protected))
        view = await self.memory.aget_context(self.session.id)
        with self.assertRaises(ValueError):
            await self.memory.adelete_compaction(self.session.id, expected_revision=view['revision'])
        cancelled = await self.memory.acancel_compaction(self.session.id, expected_revision=view['revision'])
        await self.memory.adelete_compaction(self.session.id, expected_revision=cancelled['revision'])
        self.assertTrue(any(source_id in row['message_ids'] for row in (await self.project.aretention_plan('run')).candidates))
        await self.session.adelete_turn(source_id)

    async def test_logically_deleted_source_invalidates_pending_compaction(self):
        await self.history()
        pending = await self.request(max_summary_calls=30)
        await self.session.run.shutdown()
        await self.session.adelete_turn(pending['sources'][0]['id'])
        run, _ = await self.run_loop()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(self.requests, [])
        self.assertEqual((await self.memory.aget_context(self.session.id))['request']['status'], 'stale')

    async def test_large_result_features_require_explicit_settings(self):
        self.assertEqual(await self.memory.aget_config(), {})
        for config in ({'tool_result_mode': 'reference'},
                       {'tool_result_mode': 'summary', 'priority': 0, 'tool_data': {'component': 'data', 'identifier': 'results'},
                        'tool_result_chars': 100}):
            with self.assertRaises(ValueError):
                await self.memory.aconfigure(memory_settings({'processing': config}))
        run, main = await self.run_loop()
        names = [tool['function']['name'] for tool in main.requests[0].get('tools', [])]
        self.assertNotIn('memory_read_source', names)
        self.assertEqual(self.requests, [])

    async def test_purged_session_references_can_be_cancelled_and_released_but_not_resumed(self):
        await self.history()
        await self.request()
        await self.tool_config(max_summary_calls=1)
        model = ScriptedCompletion([chunk(calls=[call('{}', name='source_read')], finish='tool_calls')], answer())
        run, _ = await self.run_loop(model=model)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        await self.session.run.shutdown()
        with self.assertRaisesRegex(ValueError, 'referenced'):
            await self.session.adelete(permanent=True)
        # Normal deletion preserves live references. Inject external source loss
        # to retain the independent cancellation/release/Resume failure scenario.
        import shutil
        shutil.rmtree(self.session.paths.root)
        view = await self.memory.aget_context(self.session.id)
        self.assertEqual(view['request']['status'], 'partial')
        with self.assertRaisesRegex(ValueError, 'Session was removed'):
            await self.memory.aresume_compaction(self.session.id, expected_revision=view['revision'])
        view = await self.memory.acancel_compaction(self.session.id, expected_revision=view['revision'])
        await self.memory.adelete_compaction(self.session.id, expected_revision=view['revision'])
        saved = (await self.memory.alist_tool_summaries(self.session.id))[0]
        identifier = saved['request']['id']
        self.assertEqual(saved['request']['status'], 'partial')
        with self.assertRaisesRegex(ValueError, 'Session was removed'):
            await self.memory.aresume_tool_summary(self.session.id, identifier, expected_revision=saved['revision'])
        with self.assertRaises(ValueError):
            await self.files.adelete('results')
        saved = await self.memory.acancel_tool_summary(self.session.id, identifier, expected_revision=saved['revision'])
        await self.memory.adelete_tool_summary(self.session.id, identifier, expected_revision=saved['revision'])
        await self.files.adelete('results')

    async def test_new_request_can_shrink_a_previous_applied_summary(self):
        await self.history()
        await self.request(max_summary_calls=30)
        await self.run_loop()
        self.assertEqual((await self.memory.aget_context(self.session.id))['request']['status'], 'applied')
        def smaller(**request):
            payload = json.loads(request['messages'][1]['content'])
            self.assertEqual(payload['previous_summary'], 'valid summary')
            yield from answer('{"summary":"small"}')
        self.memory_component.completion_adapter = LiteLLMCompletionAdapter(completion_fn=smaller)
        await self.request(summary_chars=5, model_input_chars=1000, max_summary_calls=30)
        run, model = await self.run_loop()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        saved = (await self.memory.aget_context(self.session.id))['request']
        self.assertEqual(saved['status'], 'applied')
        self.assertEqual(saved['checkpoint']['summary'], 'small')
