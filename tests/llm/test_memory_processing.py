
from llm.core.model_protocol import ModelMessage, ToolCall, ToolResult

from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.llm.configuration_fixtures import memory_settings
"""Memory 중심의 장기 문맥 처리, Session 격리, 원본 보존과 오류/재시작 경계를 검증한다."""

import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest

from tests.llm.configuration_fixtures import memory_processing
from llm.components.memory import MemoryComponent, MemoryConflictError
from llm.components.prompts import PromptComponent
from llm.components.base import Component
from llm.components.processing import CompletionSession
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.core.agents import AgentDefinition
from llm.core.models import RunStatus
from llm.engines.graph.agent import AgentNode
from llm.engines.graph import GraphEngine
from llm.engines.loop import LoopEngine
from tests.llm.configuration_fixtures import configure_engine
from llm.llm import LargeLanguageModel
from llm.services.composition import BackendServices
from llm.policies import CompletionPolicy
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
    async def test_explicit_compaction_cancellation_does_not_apply_or_repeat(self):
        await self.configure()
        await self.build_history()
        view = await self.memory.aget_context(self.session.id)
        await self.memory.arequest_compaction(self.session.id,
            {"method": "summary", "keep_turns": 0, "model_input_chars": 10000, "max_summary_calls": 10, "summary_chars": 500, "prompt": "Summarize",
             "completion": {"model": "test/aux"}}, expected_revision=view['revision'])
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []
        def slow(**request):
            calls.append(1)
            entered.set()
            try:
                release.wait(10)
                yield from answer('{"summary":"must not apply"}')
            finally:
                closed.set()
        self.component.completion_adapter = LiteLLMCompletionAdapter(completion_fn=slow)
        task = asyncio.create_task(self.run_loop('continue'))
        self.assertTrue(await asyncio.to_thread(entered.wait, 5))
        await self.session.run.interrupt()
        release.set()
        self.assertTrue(await asyncio.to_thread(closed.wait, 5))
        run, model = await task
        self.assertEqual(run.data.status, RunStatus.INTERRUPTED)
        self.assertEqual(model.requests, [])
        self.assertEqual((await self.memory.aget_context(self.session.id))['request']['status'], 'interrupted')
        run, model = await self.run_loop('again')
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(calls, [1])
        self.assertNotIn('must not apply', str(model.requests))

    async def test_explicit_compaction_stale_sources_and_scope_are_not_reused(self):
        await self.configure()
        await self.build_history()
        view = await self.memory.aget_context(self.session.id)
        self.assertIsNone((await self.memory.aget_context(self.session.id, scope='another/node'))['context'])
        await self.memory.arequest_compaction(self.session.id,
            {"method": "summary", "keep_turns": 0, "model_input_chars": 10000, "max_summary_calls": 10, "summary_chars": 500, "prompt": "Summarize",
             "completion": {"model": "test/aux"}}, expected_revision=view['revision'])
        # 앞선 처리기가 입력 사본을 바꾸면 예전 범위에 대한 압축은 적용하지 않는다.
        from unittest.mock import patch
        from llm.components.memory.compaction import prepare_compaction
        async def edit(session, request):
            request.messages[0] = request.messages[0].with_text('edited source')
            async for event in prepare_compaction(session, request):
                yield event
        with patch('llm.components.memory.compaction.prepare_compaction', edit):
            run, model = await self.run_loop('continue')
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual((await self.memory.aget_context(self.session.id))['request']['status'], 'stale')
        self.assertEqual(self.aux.requests, [])

    async def test_explicit_compaction_request_is_deferred_and_preserves_history(self):
        await self.configure()
        await self.build_history()
        view = await self.memory.aget_context(self.session.id)
        original = await self.session.aconversation()
        options = {"method": "summary", "keep_turns": 0, "model_input_chars": 10000, "max_summary_calls": 10, "summary_chars": 500,
                   "prompt": "Summarize prior decisions", "completion": {"model": "test/aux"}}
        request = await self.memory.arequest_compaction(self.session.id, options, expected_revision=view["revision"])
        self.assertEqual(request["status"], "pending")
        self.assertEqual(self.aux.requests, [])
        with self.assertRaises(ValueError):
            await self.memory.arequest_compaction(self.session.id, options, expected_revision=view["revision"])
        run, model = await self.run_loop("continue")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(len(self.aux.requests), 1)
        self.assertIn("Compacted prior conversation", model.requests[0]["messages"][-1]["content"])
        self.assertEqual((await self.memory.aget_context(self.session.id))["request"]["status"], "applied")
        self.assertEqual([(m.id, m.content) for m in (await self.session.aconversation())[:len(original)]], [(m.id, m.content) for m in original])
        run, model = await self.run_loop("again")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(len(self.aux.requests), 1)
        self.assertIn("Compacted prior conversation", model.requests[0]["messages"][-1]["content"])

    async def test_explicit_compaction_failure_keeps_original_and_does_not_retry(self):
        await self.configure()
        await self.build_history()
        self.component.completion_adapter = LiteLLMCompletionAdapter(completion_fn=lambda **kwargs: iter(answer('{"summary":"too long"}')))
        view = await self.memory.aget_context(self.session.id)
        await self.memory.arequest_compaction(self.session.id,
            {"method": "summary", "keep_turns": 0, "model_input_chars": 10000, "max_summary_calls": 10, "summary_chars": 1, "prompt": "summarize", "completion": {"model": "test"}},
            expected_revision=view["revision"])
        run, model = await self.run_loop()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual((await self.memory.aget_context(self.session.id))["request"]["status"], "failed")
        self.assertTrue(any(m["content"] == "first requirement" for m in model.requests[0]["messages"]))

    async def test_explicit_compaction_missing_owner_is_interrupted(self):
        await self.configure()
        await self.build_history()
        view = await self.memory.aget_context(self.session.id)
        request = await self.memory.arequest_compaction(self.session.id,
            {"method": "summary", "keep_turns": 0, "model_input_chars": 10000, "max_summary_calls": 10, "summary_chars": 500, "prompt": "Summarize",
             "completion": {"model": "test/aux"}}, expected_revision=view["revision"])
        # crash 뒤 소유 Run이 정리되어도 파생 요청을 자동 재실행하거나 영구 대기시키지 않는다.
        self.memory._claim_compaction(self.session.id, None, request["id"], "0" * 32)
        self.assertEqual((await self.memory.aget_context(self.session.id))["request"]["status"], "interrupted")
        run, _ = await self.run_loop()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(self.aux.requests, [])

    async def test_explicit_compaction_keeps_tool_pairs_and_resume_receipts(self):
        await self.configure()
        await self.build_history()
        view = await self.memory.aget_context(self.session.id)
        await self.memory.arequest_compaction(self.session.id,
            {"method": "summary", "keep_turns": 0, "model_input_chars": 10000, "max_summary_calls": 10, "summary_chars": 500, "prompt": "Summarize",
             "completion": {"model": "test/aux"}}, expected_revision=view["revision"])
        model = ScriptedCompletion(
            [chunk(calls=[call('{"content":"one effect","scope":"session"}', name="memory_create")], finish="tool_calls")],
            [ConnectionError("connection lost after completed Tool")], answer())
        failed, _ = await self.run_loop(model=model)
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        before = await self.memory.alist(session_id=self.session.id)
        self.assertEqual(len(before), 1)
        resumed = await (await self.session.run.resume(failed.id, agent_id=failed.data.agent_id)).wait()
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual(await self.memory.alist(session_id=self.session.id), before)
        self.assertEqual(len(self.aux.requests), 1)
        messages = model.requests[-1]["messages"]
        self.assertEqual([m["tool_call_id"] for m in messages if m["role"] == "tool"], ["call_1"])
        self.assertEqual([c["id"] for m in messages for c in m.get("tool_calls", [])], ["call_1"])
        self.assertIn("Compacted prior conversation", str(messages))

    async def test_loop_retry_does_not_retry_memory_auxiliary_model(self):
        await self.build_history()
        calls = []
        def unavailable(**request):
            calls.append(request)
            raise ConnectionError("auxiliary unavailable")
            yield
        self.component.completion_adapter = LiteLLMCompletionAdapter(completion_fn=unavailable)
        await self.configure(summarize=True, keep_turns=1, summary_after_chars=1)
        await configure_engine(self.project, "loop" + str(self.serial), provider={"max_attempts": 3})
        run, main = await self.run_loop()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(len(calls), 1)
        self.assertEqual(main.requests, [])

    async def test_memory_auxiliary_uses_own_provider_and_keeps_sdk_kwargs(self):
        await self.build_history()
        calls = []
        def retry_once(**request):
            calls.append(request)
            if len(calls) == 1:
                raise ConnectionError("auxiliary unavailable")
            yield from self.aux(**request)
        self.component.completion_adapter = LiteLLMCompletionAdapter(completion_fn=retry_once)
        await self.configure(summarize=True, keep_turns=1, summary_after_chars=1,
                             provider={"max_attempts": 2})
        await configure_engine(self.project, "loop" + str(self.serial), provider={"max_attempts": 1})
        run, main = await self.run_loop()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(main.requests), 1)
        for request in calls:
            for key in ("provider", "max_attempts", "input_policy", "timeout", "num_retries", "max_retries"):
                self.assertNotIn(key, request)

    async def test_active_work_compaction_preserves_original_tool_results(self):
        def summarize(**request):
            yield from answer(json.dumps({'summary': 'Completed lookups; continue remaining work.'}))
        self.component.completion_adapter = LiteLLMCompletionAdapter(completion_fn=summarize)
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
        step, = [s for s in await first.steps.alist() if s.kind == 'tool']
        query = json.dumps({'run_id': first.id, 'step_id': step.id, 'offset': 0, 'limit': 50})
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
        self.component = MemoryComponent(completion_adapter=LiteLLMCompletionAdapter(completion_fn=self.aux))
        self.app = LargeLanguageModel(self.root / "workspace", components=[self.component, PromptComponent()], engines={})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("Long work", components=["memory", "prompts"])
        await self.project.components.prompts.acreate({"messages": [{"role": "system", "content":
            "Retain reusable facts and preferences."}]}, identifier="extract")
        self.memory = await self.project.components.aget("memory")
        await self.configure(recall=True)
        self.session = await self.project.sessions.acreate()
        self.serial = 0
        self.config_lock = asyncio.Lock()

    async def configure(self, **options):
        await self.memory.aconfigure(memory_settings({'tool_write_status': 'candidate', 'processing': memory_processing({'recall': False,
            'extract_prompt_id': 'extract', 'completion': {'model': 'test/aux'}, **options})}))

    async def run_loop(self, prompt="request", *, model=None, session=None):
        model = model or ScriptedCompletion(answer())
        name = "loop" + str(self.serial)
        self.serial += 1
        self.app.engines.register(name, LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model)))
        # 테스트가 생성하는 Engine별 Project 설정의 read-modify-save만 직렬화한다.
        async with self.config_lock:
            await configure_engine(self.project, name, completion={"model": "test/main"})
        # Application fixture saves the selected Tool catalogue explicitly.
        from llm.components.tools.resolver import ComponentToolResolver
        manager = self.app.project_manager
        def selected_tools():
            with manager.ownership.scope():
                project = manager.access.require(self.project.data)
                return list(ComponentToolResolver(manager.components,
                    data_factory=manager.component, agents=manager.agents,
                    engines=self.app.engines).resolve_tools(project).names())
        await self.project.agents.acreate({"engine": name,
            "tools": [*(await asyncio.to_thread(selected_tools)),
                *(["memory_tool_result"] if "memory" in self.project.data.components else [])]}, identifier=name)
        run = await (await (session or self.session).run.submit(prompt, agent_id=name)).wait(timeout=20)
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
        await self.memory.aconfigure({'policy': {'processing': {'context_chars': 1}}})
        _, model = await self.run_loop("Python", session=other)
        self.assertEqual(model.requests[0]["messages"][-1]["content"], "Python")

    async def test_session_scope_crud_and_model_tools_cannot_cross_sessions(self):
        other = await self.project.sessions.acreate()
        await self.memory.acreate({"content": "private", "scope": "session", "session_id": other.id}, identifier="private")
        generation = (await self.memory.aload("private", session_id=other.id))["generation"]
        for operation in (lambda: self.memory.aload("private", session_id=self.session.id),
                          lambda: self.memory.aupdate("private", {"content": "wrong"}, expected_revision=1, expected_generation=generation, session_id=self.session.id),
                          lambda: self.memory.adelete("private", expected_revision=1, expected_generation=generation, session_id=self.session.id),
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
            await self.memory.aupdate(record["id"], {"scope": "project"}, expected_revision=1, expected_generation=record["generation"], session_id=self.session.id)

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
        session = MemorySession(SimpleNamespace(data=None), SimpleNamespace(completion_policy=None,
            run=SimpleNamespace(input_message_id='input', id='run')))
        session.config = {'active_keep_iterations': 1, 'summary_after_chars': 1,
                          'summary_chars': 100, 'model_input_chars': 540, 'max_summary_calls': 2}
        submitted = []
        async def summarize(instruction, payload, result):
            self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False)), 540)
            submitted.extend(c['id'] for m in payload['exchanges'] for c in m['calls'])
            result['summary'] = 'completed'
            if False:
                yield
        session._model = summarize
        messages = [CompletionMessage(ModelMessage('user', 'work'), 'input')]
        for index, size in enumerate((120, 120, 1000, 120)):
            messages.extend([CompletionMessage(ModelMessage('assistant', calls=(ToolCall(str(index), 'read', {}),))),
                CompletionMessage(ModelMessage('tool', result=ToolResult(str(index), 'x' * size)))])
        request = CompletionRequest({}, messages, 5)
        async for _ in session._compact_active(request, {}, {}):
            pass
        self.assertEqual(submitted, ['0', '1'])
        self.assertEqual(request.compacted_tool_calls, ('0', '1'))
        self.assertEqual([m.message.result.call_id for m in request.messages if m.message.role == 'tool'], ['2', '3'])
        self.assertEqual(request.messages[2].message.text, 'x' * 1000)

    async def test_restart_reuses_summary_and_does_not_import_other_session_history(self):
        await self.configure(summarize=True, keep_turns=1, summary_after_chars=1)
        await self.build_history()
        await self.app.shutdown()
        self.app = LargeLanguageModel(self.root / "workspace", components=[self.component, PromptComponent()], engines={})
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

    async def test_fragment_summary_resumes_stored_cursor_after_restart(self):
        original = "large source " * 100
        await self.run_loop(original)
        await self.run_loop("recent")
        requests = []
        def auxiliary(**request):
            requests.append(json.loads(request["messages"][1]["content"]))
            yield from answer('{"summary":"accumulated"}')
        self.component.completion_adapter = LiteLLMCompletionAdapter(completion_fn=auxiliary)
        await self.configure(summarize=True, keep_turns=1, summary_after_chars=1,
                             model_input_chars=300, summary_chars=20, max_summary_calls=1)
        run, model = await self.run_loop("current")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        saved = await self.memory.asummary(self.session.id)
        offset = saved['metadata']['partial']['offset']
        self.assertGreater(offset, 0)
        self.assertEqual(saved['metadata']['coverage_count'], 0)
        self.assertTrue(any(m['content'] == original for m in model.requests[0]['messages']))
        self.assertEqual(len(requests), 1)
        await self.app.shutdown()
        self.app = LargeLanguageModel(self.root / 'workspace', components=[self.component, PromptComponent()], engines={})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.aload(self.project.id)
        self.session = await self.project.sessions.aload(self.session.id)
        self.memory = await self.project.components.aget('memory')
        run, _ = await self.run_loop('after restart')
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1]['messages'][0]['offset'], offset)
        self.assertEqual(requests[1]['previous_summary'], 'accumulated')
        self.assertTrue(all(len(json.dumps(p, ensure_ascii=False)) <= 300 for p in requests))
        self.assertEqual((await self.session.aconversation())[0].content, original)

    async def test_active_fragments_keep_tool_pair_until_all_fragments_complete(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from llm.components.memory.processing import MemorySession
        from llm.components.processing import CompletionRequest, CompletionMessage
        session = MemorySession(SimpleNamespace(data=None), SimpleNamespace(completion_policy=None,
            run=SimpleNamespace(input_message_id='input', id='run')))
        session.config = {'active_keep_iterations': 1, 'summary_after_chars': 1,
                          'summary_chars': 20, 'model_input_chars': 300, 'max_summary_calls': 1}
        requests = []
        async def auxiliary(instruction, payload, result):
            requests.append(deepcopy(payload))
            result['summary'] = 'accumulated'
            if False:
                yield
        session._model = auxiliary
        original = [CompletionMessage(ModelMessage('user', 'work'), 'input')]
        for identifier, content in (('old', 'x' * 900), ('latest', 'keep')):
            original.extend([CompletionMessage(ModelMessage('assistant', calls=(ToolCall(identifier, 'read', {}),))),
                             CompletionMessage(ModelMessage('tool', result=ToolResult(identifier, content)))])
        for attempt in range(20):
            request = CompletionRequest({}, deepcopy(original), attempt)
            before = len(requests)
            async for _ in session._compact_active(request, {}, {}):
                pass
            self.assertEqual(len(requests) - before, 1)
            if request.compacted_tool_calls:
                self.assertEqual(request.compacted_tool_calls, ('old',))
                self.assertEqual(request.messages[1:], original[3:])
                break
            self.assertEqual(request.messages, original)
            self.assertIsNotNone(session.active_partial)
        else:
            self.fail('Explicit fragment budget never completed the exchange')
        fragments = [p['exchanges'][0] for p in requests]
        self.assertEqual(''.join(p['fragment'] for p in fragments), json.dumps([m.message.to_dict() for m in original[1:3]], ensure_ascii=False))
        self.assertTrue(all(len(json.dumps(p, ensure_ascii=False)) <= 300 for p in requests))
        self.assertIsNone(session.active_partial)

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
            step = next(s for s in await run.steps.alist() if s.metadata.get("tool_call_id") == message["tool_call_id"])
            self.assertEqual(preview["source"]["step_id"], step.id)
            self.assertLess(len(message["content"]), 600)
        steps = [s for s in await run.steps.alist() if s.kind == "tool"]
        self.assertEqual(len(steps[0].output.data["content"]), 10000)
        self.assertEqual(steps[0].output.data, steps[1].output.data)

    async def test_extraction_candidates_dedup_proposals_and_expiry(self):
        await self.configure(extract=True)
        await self.memory.acreate({"content": "Old Python choice"}, identifier="old")
        self.aux.candidates = [{"content": "New Python choice", "replaces": [{"id": "old",
            "generation": (await self.memory.aload("old"))["generation"], "revision": 1}]}]
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
        await self.memory.adelete(candidate["id"], expected_revision=1, expected_generation=candidate["generation"], session_id=self.session.id)
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

    async def test_extraction_rejects_missing_or_stale_generation(self):
        await self.configure(extract=True)
        await self.memory.acreate({"content": "Existing fact"}, identifier="old")
        old = await self.memory.aload("old")
        for reference in ({"id": "old", "revision": old["revision"]},
                          {"id": "old", "revision": old["revision"], "generation": "0" * 32}):
            with self.subTest(reference=reference):
                self.aux.candidates = [{"content": "valid"}, {"content": "replacement", "replaces": [reference]}]
                run, _ = await self.run_loop()
                self.assertEqual(run.data.status, RunStatus.FAILED)
                self.assertEqual(await self.memory.alist(session_id=self.session.id), {"old": old})
        payload = json.loads(self.aux.requests[-1]["messages"][1]["content"])
        self.assertEqual(payload["existing"][0]["generation"], old["generation"])

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
        await self.memory.aconfigure({'policy': {'processing': {'context_chars': 2}}})
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
        await self.memory.aconfigure({'policy': {'processing': {'context_tokens': 1}}})
        run, model = await self.run_loop("budget")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(model.requests[0]["messages"][-1]["content"], "budget")
        await self.app.shutdown()
        self.app = LargeLanguageModel(self.root / "workspace", components=[self.component, PromptComponent()], engines={},
            services=BackendServices(token_counters={"test": lambda request: sum(
                len(m.text) for m in request.messages)}))
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.aload(self.project.id)
        await configure_engine(self.project, "loop" + str(self.serial), input_policy={"max_tokens": 10, "counter": "test"})
        self.memory = await self.project.components.aget("memory")
        await self.configure(recall=True)
        await self.memory.aconfigure({})
        self.session = await self.project.sessions.acreate()
        run, model = await self.run_loop("budget")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(model.requests[0]["messages"][-1]["content"], "budget")

    async def test_graph_agent_uses_processor_without_reusing_session_summary(self):
        for component in (WorkflowComponent(),):
            self.app.project_manager.components.register(component)
        await self.project.components.aselect(['memory', 'prompts', 'workflows'])
        await self.memory.acreate({"content": "Python guidelines"})
        await self.configure(recall=True, summarize=True, extract=True)
        model = ScriptedCompletion(answer())
        worker = LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))
        self.app.engines.register("loop", worker)
        agents = self.project.agents
        await agents.acreate({"engine": "loop", "purpose": "Python", "completion": {"model": "test/model"}}, identifier="reader")
        graph = (WorkflowGraph(entry="read", inputs={"request": "/prompt"})
                 .node("read", "agent", agent="reader", inputs={"request": "/request"})
                 .node("end", "end").connect("read", "end"))
        await (await self.project.components.aget("workflows")).acreate(graph.to_dict(), identifier="g")
        self.app.engines.register("graph", GraphEngine(handlers={"agent": AgentNode(engines={"loop": worker})}))
        await self.project.agents.acreate({"engine": "graph", "engine_options": {"workflow": "g"}}, identifier="graph")
        run = await (await self.session.run.submit("Python", agent_id="graph")).wait(timeout=20)
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
                        request.messages[-1] = request.messages[-1].with_text(request.messages[-1].message.text + " processed")
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
                await self.memory.aconfigure(memory_settings({'processing': config}))
        self.assertEqual((await self.memory.aget_config())["policy"]["tool_write_status"], "candidate")

    async def test_interrupt_auxiliary_model_does_not_publish_partial_summary(self):
        await self.configure(summarize=True, keep_turns=1, summary_after_chars=1)
        await self.run_loop("first")
        await self.run_loop("second")
        entered, release = threading.Event(), threading.Event()
        def slow(**request):
            entered.set()
            release.wait(10)
            yield from self.aux(**request)
        self.component.completion_adapter = LiteLLMCompletionAdapter(completion_fn=slow)
        model = ScriptedCompletion(answer())
        self.app.engines.register("interrupt", LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model)))
        await self.project.agents.acreate({"engine": "interrupt", "completion": {"model": "test/main"}}, identifier="interrupt")
        request = await self.session.run.submit("third", agent_id="interrupt")
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            await self.session.run.interrupt()
        finally:
            release.set()
        run = await request.wait(timeout=15)
        self.assertEqual(run.data.status, RunStatus.INTERRUPTED)
        self.assertIsNone(await self.memory.asummary(self.session.id))
        self.assertEqual(model.requests, [])
        self.component.completion_adapter = LiteLLMCompletionAdapter(completion_fn=self.aux)
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
        async with LargeLanguageModel(self.root / "destination", components=[MemoryComponent(), PromptComponent()]) as app:
            project = await app.projects.arestore_backup(backup)
            self.assertEqual(await (await project.components.aget("memory")).asummary(self.session.id), original)
        await self.memory.aclear_summary(self.session.id, expected_revision=original["revision"], expected_generation=original["generation"])
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
