"""Memory 중심의 장기 문맥 처리, Session 격리, 원본 보존과 오류/재시작 경계를 검증한다."""

import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest

from tests.llm.configuration_fixtures import memory_processing
from llm.components.memory import MemoryComponent, MemoryConflictError
from llm.components.base import Component
from llm.components.processing import CompletionSession
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.components.agents import AgentComponent
from llm.core.models import RunStatus
from llm.engines.graph.agent import AgentNode
from llm.engines.graph import GraphEngine
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from llm.services.configuration import ServiceConfig
from llm.services.history.context import CompletionPolicy
from tests.llm.test_loop import ScriptedCompletion, call, chunk


def answer(text="done"):
    return [chunk(text), chunk(finish="stop")]


class Auxiliary:
    def __init__(self):
        self.requests = []
        self.candidates = []

    def __call__(self, **request):
        self.requests.append(request)
        instruction = request["messages"][0]["content"]
        payload = json.loads(request["messages"][1]["content"])
        if instruction.startswith("Summarize"):
            value = {"summary": "누적 요약: " + " ".join(m["content"][:10] for m in payload["messages"])}
        else:
            value = {"memories": self.candidates}
        yield from answer(json.dumps(value, ensure_ascii=False))


class MemoryProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_work_compaction_preserves_original_tool_results(self):
        def summarize(**request):
            yield from answer(json.dumps({'summary': 'Completed lookups; continue remaining work.'}))
        self.component.completion_fn = summarize
        await self.configure(summarize=True, summary_after_chars=1, active_keep_iterations=1)
        await self.memory.acreate({'content': 'source data ' * 30}, identifier='source')
        model = ScriptedCompletion(*[
            [chunk(calls=[call('{"identifier":"source"}', name='memory_get', call_id=f'lookup_{i}')], finish='tool_calls')]
            for i in range(3)], answer())
        run, _ = await self.run_loop(model=model)
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        messages = model.requests[-1]['messages']
        self.assertIn('Completed work reference', messages[0]['content'])
        self.assertEqual(len([m for m in messages if m['role'] == 'tool']), 1)
        self.assertEqual(len([s for s in await run.steps.alist() if s.kind == 'tool']), 3)
        self.assertEqual((await self.session.aconversation())[0].content, 'request')

    async def test_compressed_result_can_be_read_from_own_session(self):
        await self.memory.acreate({'content': 'large source ' * 50}, identifier='source')
        first, _ = await self.run_loop(model=ScriptedCompletion(
            [chunk(calls=[call('{"identifier":"source"}', name='memory_get')], finish='tool_calls')], answer()))
        query = json.dumps({'run_id': first.id, 'tool_call_id': 'call_1', 'offset': 0, 'limit': 50})
        second, model = await self.run_loop(model=ScriptedCompletion(
            [chunk(calls=[call(query, name='memory_tool_result')], finish='tool_calls')], answer()))
        self.assertEqual(second.data.status, RunStatus.COMPLETED, second.data.error)
        result = json.loads(model.requests[-1]['messages'][-1]['content'])
        self.assertEqual(len(result['content']), 50)
        self.assertGreater(result['total_chars'], 50)

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.aux = Auxiliary()
        self.component = MemoryComponent(completion_fn=self.aux)
        self.app = LargeLanguageModel(self.root / "workspace", components=[self.component], engines={})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("Long work", components=["memory"])
        self.memory = await self.project.components.aget("memory")
        await self.configure(recall=True)
        self.session = await self.project.sessions.acreate()
        self.serial = 0

    async def configure(self, **options):
        await self.memory.aconfigure({"tool_write_status": "candidate", "processing": memory_processing({"recall": False, "completion": {"model": "test/aux"}, **options})})

    async def run_loop(self, prompt="request", *, model=None, session=None):
        model = model or ScriptedCompletion(answer())
        name = "loop" + str(self.serial)
        self.serial += 1
        self.app.engines.register(name, LoopEngine(completion_fn=model, completion_kwargs={"model": "test/main"}))
        run = await (await (session or self.session).run.submit(prompt, engine=name)).wait(timeout=20)
        return run, model

    async def build_history(self):
        for prompt in ("first requirement", "second decision", "third work"):
            run, _ = await self.run_loop(prompt)
            self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)

    async def test_automatic_recall_budget_and_session_scope(self):
        other = await self.project.sessions.acreate()
        for identifier, data in (("project", {"content": "Python project"}),
                                 ("mine", {"content": "Python mine", "scope": "session", "session_id": self.session.id}),
                                 ("other", {"content": "Python other", "scope": "session", "session_id": other.id}),
                                 ("candidate", {"content": "Python candidate", "status": "candidate"})):
            await self.memory.acreate(data, identifier=identifier)
        run, model = await self.run_loop("Python")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        sent = model.requests[0]["messages"][-1]["content"]
        self.assertIn("Python project", sent)
        self.assertIn("Python mine", sent)
        self.assertNotIn("Python other", sent)
        self.assertNotIn("Python candidate", sent)
        self.assertEqual(self.aux.requests, [])
        self.assertEqual((await run.aresponse()).content, "done")
        messages = await self.session.aconversation()
        self.assertEqual(messages[0].content, "Python")
        self.assertNotIn("Reference data", messages[0].content)
        await self.memory.aconfigure({"processing": {"context_chars": 1}})
        _, model = await self.run_loop("Python", session=other)
        self.assertEqual(model.requests[0]["messages"][-1]["content"], "Python")

    async def test_session_scope_crud_and_model_tools_cannot_cross_sessions(self):
        other = await self.project.sessions.acreate()
        await self.memory.acreate({"content": "private", "scope": "session", "session_id": other.id}, identifier="private")
        for operation in (lambda: self.memory.aload("private", session_id=self.session.id),
                          lambda: self.memory.aupdate("private", {"content": "wrong"}, expected_revision=1, session_id=self.session.id),
                          lambda: self.memory.adelete("private", expected_revision=1, session_id=self.session.id),
                          lambda: self.memory.ahistory("private", session_id=self.session.id)):
            with self.assertRaises(FileNotFoundError):
                await operation()
        self.assertEqual(await self.memory.alist(), {})
        model = ScriptedCompletion([chunk(calls=[call('{"identifier":"private"}', name="memory_get")]), chunk(finish="tool_calls")])
        run, _ = await self.run_loop(model=model)
        self.assertEqual(run.data.status, RunStatus.FAILED)
        model = ScriptedCompletion([chunk(calls=[call('{"content":"mine","scope":"session"}', name="memory_create")]),
                                    chunk(finish="tool_calls")], answer())
        run, _ = await self.run_loop(model=model)
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        record = next(iter((await self.memory.alist(session_id=self.session.id)).values()))
        self.assertEqual(record["session_id"], self.session.id)
        self.assertEqual(record["status"], "candidate")
        with self.assertRaises(ValueError):
            await self.memory.aupdate(record["id"], {"scope": "project"}, expected_revision=1, session_id=self.session.id)

    async def test_incremental_summary_keeps_recent_turns_and_source_history(self):
        await self.configure(summarize=True, keep_turns=1, summary_after_chars=1)
        await self.build_history()
        summary = await self.memory.asummary(self.session.id)
        self.assertEqual(summary["metadata"]["coverage_count"], 2)
        self.assertEqual(len(self.aux.requests), 1)
        run, model = await self.run_loop("fourth request")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        summary = await self.memory.asummary(self.session.id)
        self.assertEqual(summary["revision"], 2)
        self.assertEqual(summary["metadata"]["coverage_count"], 4)
        payload = json.loads(self.aux.requests[-1]["messages"][1]["content"])
        self.assertEqual([m["content"] for m in payload["messages"]], ["second decision", "done"])
        self.assertTrue(payload["previous_summary"])
        self.assertEqual(model.requests[0]["messages"][0]["content"], "third work")
        self.assertEqual(len(await self.session.aconversation()), 8)
        step = next(s for s in await run.steps.alist() if s.name == "Memory summarize")
        self.assertEqual(summary["source"]["session_id"], self.session.id)
        self.assertEqual(summary["source"]["step_id"], step.id)
        self.assertNotIn("coverage", summary["metadata"])
        self.assertTrue(any(s.kind == "memory" and s.output.visibility == "internal" for s in await run.steps.alist()))
        self.assertEqual((await run.aresult()).output.text, "done")

    async def test_summary_batch_never_leaves_orphan_assistant(self):
        await self.configure(summarize=True, keep_turns=1, summary_after_chars=1, model_input_chars=7)
        await self.build_history()
        summary = await self.memory.asummary(self.session.id)
        self.assertIsNone(summary)
        self.assertEqual(self.aux.requests, [])
        run, model = await self.run_loop('next')
        self.assertEqual(run.data.status, RunStatus.COMPLETED)
        self.assertEqual(model.requests[0]['messages'][0]['content'], 'first requirement')

    async def test_active_summary_batches_cover_only_complete_exchanges(self):
        from types import SimpleNamespace
        from llm.components.memory.processing import MemorySession
        from llm.components.processing import CompletionRequest, CompletionMessage
        session = MemorySession(SimpleNamespace(data=None), SimpleNamespace(run=SimpleNamespace(input_message_id='input')))
        session.config = {'active_keep_iterations': 1, 'summary_after_chars': 1,
                          'summary_chars': 100, 'model_input_chars': 540, 'max_summary_calls': 2}
        submitted = []
        async def summarize(instruction, payload, result):
            self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False)), 540)
            submitted.extend(c['id'] for m in payload['exchanges'] for c in m.get('tool_calls', []))
            result['summary'] = 'completed'
            if False:
                yield
        session._model = summarize
        messages = [CompletionMessage({'role': 'user', 'content': 'work'}, 'input')]
        for index, size in enumerate((120, 120, 1000, 120)):
            messages.extend([CompletionMessage({'role': 'assistant', 'content': None,
                'tool_calls': [{'id': str(index), 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}]}),
                CompletionMessage({'role': 'tool', 'tool_call_id': str(index), 'content': 'x' * size})])
        request = CompletionRequest({}, messages, 5)
        async for _ in session._compact_active(request, {}, {}):
            pass
        self.assertEqual(submitted, ['0', '1'])
        self.assertEqual(request.compacted_tool_calls, ('0', '1'))
        self.assertEqual([m.value.get('tool_call_id') for m in request.messages if m.value['role'] == 'tool'], ['2', '3'])
        self.assertEqual(request.messages[2].value['content'], 'x' * 1000)

    async def test_restart_reuses_summary_and_does_not_import_other_session_history(self):
        await self.configure(summarize=True, keep_turns=1, summary_after_chars=1)
        await self.build_history()
        await self.app.shutdown()
        self.app = LargeLanguageModel(self.root / "workspace", components=[self.component], engines={})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.aload(self.project.id)
        self.session = await self.project.sessions.aload(self.session.id)
        self.memory = await self.project.components.aget("memory")
        run, _ = await self.run_loop("after restart")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        payload = json.loads(self.aux.requests[-1]["messages"][1]["content"])
        self.assertEqual(payload["messages"][0]["content"], "second decision")
        other = await self.project.sessions.acreate()
        _, model = await self.run_loop("other session", session=other)
        self.assertEqual(model.requests[0]["messages"], [{"role": "user", "content": "other session"}])

    async def test_tool_previews_preserve_raw_steps_and_distinct_references(self):
        await self.configure(compress_tools=True, tool_result_chars=40)
        await self.memory.acreate({"content": "x" * 10000}, identifier="large")
        model = ScriptedCompletion([chunk(calls=[call('{"identifier":"large"}', name="memory_get"),
                                                call('{"identifier":"large"}', name="memory_get", index=1, call_id="call_2")]),
                                    chunk(finish="tool_calls")], answer())
        run, _ = await self.run_loop(model=model)
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        tool_messages = [m for m in model.requests[-1]["messages"] if m["role"] == "tool"]
        for message in tool_messages:
            preview = json.loads(message["content"])
            self.assertTrue(preview["truncated"])
            self.assertEqual(preview["source"]["tool_call_id"], message["tool_call_id"])
            self.assertLess(len(message["content"]), 600)
        steps = [s for s in await run.steps.alist() if s.kind == "tool"]
        self.assertEqual(len(steps[0].output.data["content"]), 10000)
        self.assertEqual(steps[0].output.data, steps[1].output.data)

    async def test_extraction_candidates_dedup_proposals_and_expiry(self):
        await self.configure(extract=True)
        await self.memory.acreate({"content": "Old Python choice"}, identifier="old")
        self.aux.candidates = [{"content": "New Python choice", "replaces": [{"id": "old", "revision": 1}]}]
        run, _ = await self.run_loop("new Python choice")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        records = await self.memory.alist(session_id=self.session.id)
        candidate = next(r for r in records.values() if r["status"] == "candidate")
        self.assertEqual(candidate["session_id"], self.session.id)
        self.assertEqual(candidate["source"]["run_id"], run.id)
        self.assertEqual(len((await self.memory.areview(session_id=self.session.id))["proposals"]), 1)
        self.assertEqual((await self.memory.aload("old"))["revision"], 1)
        await self.run_loop("new Python choice")
        self.assertEqual(len(await self.memory.alist(session_id=self.session.id)), 2)
        await self.memory.adelete(candidate["id"], expected_revision=1, session_id=self.session.id)
        await self.run_loop("new Python choice")
        self.assertEqual(len(await self.memory.alist(session_id=self.session.id)), 1)
        await self.memory.acreate({"content": "Old Python choice"}, identifier="duplicate")
        await self.memory.acreate({"content": "expired unique", "expires_at": "2000-01-01T00:00:00+00:00"}, identifier="expired")
        review = await self.memory.areview()
        self.assertEqual(review["duplicates"], [["duplicate", "old"]])
        self.assertEqual(review["expired"], ["expired"])
        self.assertEqual(await self.memory.asearch("expired"), [])

    async def test_invalid_extraction_is_atomic_before_any_candidate(self):
        await self.configure(extract=True)
        self.aux.candidates = [{"content": "valid"}, {"content": "bad", "replaces": [{"id": "unknown", "revision": 1}]}]
        run, _ = await self.run_loop()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(await self.memory.alist(session_id=self.session.id), {})

    async def test_aux_failure_continue_keeps_answer_and_records_fallback_step(self):
        await self.configure(extract=True, failure_mode="continue")
        self.aux.candidates = [{"content": ""}]
        run, _ = await self.run_loop()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual((await run.aresponse()).content, "done")
        step = next(s for s in await run.steps.alist() if s.name == "Memory extract")
        self.assertEqual(str(step.status), "completed")
        self.assertTrue(step.metadata["degraded"])
        self.assertEqual(await self.memory.alist(session_id=self.session.id), {})

    async def test_scope_and_configuration_checked_after_model_preparation(self):
        snapshot = await self.memory._async_call(self.memory._processing_snapshot, self.session.id)
        await self.memory.aconfigure({"processing": {"context_chars": 2}})
        with self.assertRaises(MemoryConflictError):
            await self.memory._async_call(self.memory._publish_candidates, snapshot, [], session_id=self.session.id, source={})
        snapshot = await self.memory._async_call(self.memory._processing_snapshot, self.session.id)
        await self.project.components.aremove("memory", permanent=True)
        await self.project.components.aselect(["memory"])
        with self.assertRaises(MemoryConflictError):
            await self.memory._async_call(self.memory._publish_candidates, snapshot, [], session_id=self.session.id, source={})

    async def test_memory_token_budget_and_final_request_budget(self):
        self.component.token_counter = lambda text: len(text)
        await self.memory.acreate({"content": "budget memory"})
        await self.memory.aconfigure({"processing": {"context_tokens": 1}})
        run, model = await self.run_loop("budget")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(model.requests[0]["messages"][-1]["content"], "budget")
        await self.app.shutdown()
        self.app = LargeLanguageModel(self.root / "workspace", components=[self.component], engines={},
            services=ServiceConfig(token_counters={"test": lambda request: sum(
                len(m.get("content") or "") for m in request["messages"])}))
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.aload(self.project.id)
        await self.project.aconfigure_policies({"completion": {"max_tokens": 10, "counter": "test"}})
        self.memory = await self.project.components.aget("memory")
        await self.configure(recall=True)
        await self.memory.aconfigure({})
        self.session = await self.project.sessions.acreate()
        run, model = await self.run_loop("budget")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(model.requests[0]["messages"][-1]["content"], "budget")

    async def test_graph_agent_uses_processor_without_reusing_session_summary(self):
        for component in (AgentComponent(), WorkflowComponent()):
            self.app.project_manager.components.register(component)
        await self.project.components.aselect(["memory", "agents", "workflows"])
        await self.memory.acreate({"content": "Python guidelines"})
        await self.configure(recall=True, summarize=True, extract=True)
        agents = await self.project.components.aget("agents")
        await agents.acreate({"engine": "loop", "purpose": "Python", "completion": {"model": "test/model"}}, identifier="reader")
        graph = (WorkflowGraph(entry="read", inputs={"request": "/prompt"})
                 .node("read", "agent", agent="reader", inputs={"request": "/request"})
                 .node("end", "end").connect("read", "end"))
        await (await self.project.components.aget("workflows")).acreate(graph.to_dict(), identifier="g")
        model = ScriptedCompletion(answer())
        self.app.engines.register("graph", GraphEngine("g", handlers={"agent": AgentNode(engines={"loop": LoopEngine(completion_fn=model)})}))
        run = await (await self.session.run.submit("Python", engine="graph")).wait(timeout=20)
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertIn("Python guidelines", model.requests[0]["messages"][-1]["content"])
        self.assertEqual(self.aux.requests, [])
        step = next(s for s in await run.steps.alist() if s.name == "Memory recall")
        self.assertIn("agent_id", step.metadata)

    async def test_common_processor_is_not_memory_specific(self):
        class Processor:
            name, priority, close_timeout = "custom", 0, 1.0
            def session(self, context):
                class Session(CompletionSession):
                    async def prepare(self, request):
                        request.messages[-1].value["content"] += " processed"
                        if False:
                            yield
                return Session()
        class Feature(Component):
            name = directory = "custom"
            capabilities = ("completion_processors",)
            def resolve(self, project, capability):
                return Processor()
        self.app.project_manager.components.register(Feature())
        await self.project.components.aselect(["custom"])
        run, model = await self.run_loop("input")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(model.requests[0]["messages"][-1]["content"], "input processed")

    async def test_invalid_processing_configuration_fails_before_write(self):
        for config in ({"summarize": True}, {"context_tokens": 20}, {"keep_turns": 0},
                       {"extract_scope": "global"}, {"timeout_seconds": float("inf")},
                       {"extract": "yes"}, {"completion": {"messages": []}}):
            with self.assertRaises(ValueError):
                await self.memory.aconfigure({"processing": config})
        self.assertEqual((await self.memory.aconfiguration())["tool_write_status"], "candidate")

    async def test_interrupt_auxiliary_model_does_not_publish_partial_summary(self):
        await self.configure(summarize=True, keep_turns=1, summary_after_chars=1)
        await self.run_loop("first")
        await self.run_loop("second")
        entered, release = threading.Event(), threading.Event()
        def slow(**request):
            entered.set()
            release.wait(10)
            yield from self.aux(**request)
        self.component.completion_fn = slow
        model = ScriptedCompletion(answer())
        self.app.engines.register("interrupt", LoopEngine(completion_fn=model, completion_kwargs={"model": "test/main"}))
        request = await self.session.run.submit("third", engine="interrupt")
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            await self.session.run.interrupt()
        finally:
            release.set()
        run = await request.wait(timeout=15)
        self.assertEqual(run.data.status, RunStatus.INTERRUPTED)
        self.assertIsNone(await self.memory.asummary(self.session.id))
        self.assertEqual(model.requests, [])
        self.component.completion_fn = self.aux
        next_run, _ = await self.run_loop("continue")
        self.assertEqual(next_run.data.status, RunStatus.COMPLETED, next_run.data.error)

    async def test_disable_processing_keeps_tools_and_original_input(self):
        await self.configure(recall=False, summarize=False, extract=False, compress_tools=False)
        await self.memory.acreate({"content": "input remembered"})
        run, model = await self.run_loop("input")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(model.requests[0]["messages"], [{"role": "user", "content": "input"}])
        self.assertIn("memory_search", [t["function"]["name"] for t in model.requests[0]["tools"]])
        self.assertFalse(any(s.kind == "memory" for s in await run.steps.alist()))

    async def test_concurrent_session_extraction_has_independent_scope(self):
        await self.configure(extract=True)
        self.aux.candidates = [{"content": "same session note"}]
        other = await self.project.sessions.acreate()
        results = await asyncio.gather(self.run_loop("one"), self.run_loop("two", session=other))
        for (run, _), session in zip(results, (self.session, other)):
            self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
            records = await self.memory.alist(session_id=session.id)
            self.assertEqual(len(records), 1)
            self.assertEqual(next(iter(records.values()))["source"]["run_id"], run.id)
        self.assertEqual(await self.memory.alist(), {})

    async def test_summary_backup_clear_and_generation_conflict(self):
        await self.configure(summarize=True, keep_turns=1, summary_after_chars=1)
        await self.build_history()
        stale = await self.memory._async_call(self.memory._processing_snapshot, self.session.id)
        original = stale["summary"]
        await self.session.run.shutdown()
        backup = await self.project.abackup(self.root / "backup")
        async with LargeLanguageModel(self.root / "destination", components=[MemoryComponent()]) as app:
            project = await app.projects.arestore_backup(backup)
            self.assertEqual(await (await project.components.aget("memory")).asummary(self.session.id), original)
        await self.memory.aclear_summary(self.session.id, expected_revision=original["revision"])
        self.assertIsNone(await self.memory.asummary(self.session.id))
        fresh = await self.memory._async_call(self.memory._processing_snapshot, self.session.id)
        await self.memory._async_call(self.memory._publish_summary, fresh, original, session_id=self.session.id, source={})
        with self.assertRaises(MemoryConflictError):
            await self.memory._async_call(self.memory._publish_summary, stale, original, session_id=self.session.id, source={})

    async def test_summary_cache_hit_does_not_call_auxiliary_again(self):
        await self.configure(summarize=True, keep_turns=1, summary_after_chars=1)
        await self.build_history()
        # 한 Run의 여러 Tool 반복은 같은 원본 대화 구간을 다시 요약하지 않는다.
        await self.memory.acreate({"content": "value"}, identifier="m")
        model = ScriptedCompletion([chunk(calls=[call('{"identifier":"m"}', name="memory_get")]), chunk(finish="tool_calls")],
                                    [chunk(calls=[call('{"identifier":"m"}', name="memory_get", call_id="call_2")]), chunk(finish="tool_calls")], answer())
        before = len(self.aux.requests)
        run, _ = await self.run_loop("fourth", model=model)
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(len(self.aux.requests) - before, 1)

    async def test_failed_turn_does_not_block_later_summary_progress(self):
        await self.configure(summarize=True, keep_turns=1, summary_after_chars=1)
        failed, _ = await self.run_loop("failed work", model=ScriptedCompletion([RuntimeError("provider failed")]))
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        await self.run_loop("second")
        await self.run_loop("third")
        await self.run_loop("fourth")
        summary = await self.memory.asummary(self.session.id)
        self.assertEqual(summary["metadata"]["coverage_count"], 4)
        first = json.loads(self.aux.requests[0]["messages"][1]["content"])
        self.assertEqual(first["messages"][-1]["status"], "failed")

    async def test_many_turns_keep_model_input_and_summary_cursor_bounded(self):
        await self.configure(summarize=True, keep_turns=2, summary_after_chars=1)
        for index in range(32):
            run, model = await self.run_loop("request " + str(index))
            self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
            self.assertLessEqual(len(model.requests[0]["messages"]), 5)
        summary = await self.memory.asummary(self.session.id)
        self.assertEqual(summary["metadata"]["coverage_count"], 58)
        path = self.project.paths.root / "memory" / "contexts" / (self.session.id + ".json")
        self.assertLess(path.stat().st_size, 2000)
        self.assertEqual(len(await self.session.aconversation()), 64)


if __name__ == "__main__":
    unittest.main()
