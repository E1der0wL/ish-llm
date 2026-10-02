import asyncio
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace

from contextlib import aclosing
from llm.core.models import ProjectConfig, Run, RunStatus, StepStatus, new_id
from llm.core.results import EngineDelta, EngineOutput
from llm.engines import EngineContext, EngineRegistry, BaseEngine
from llm.engines.base import EngineEventType
from llm.services.history.conversation import ConversationStore
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.runtime.runs import RunManager
from llm.services.lifecycle.sessions import SessionManager
from tests.llm.test_loop import ScriptedCompletion, call, chunk


class BaseEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.sessions = SessionManager()
        self.projects = ProjectManager(ProjectRepository(Path(temporary.name)), self.sessions)
        self.project = self.projects.create("Simple", config=ProjectConfig())
        self.session = self.sessions.create(self.project, "Session")
        self.engines = EngineRegistry()
        self.manager = RunManager(self.sessions, self.engines, session=self.session)
        self.addAsyncCleanup(self.manager.shutdown)

    async def idle(self, session=None):
        await asyncio.wait_for(self.manager.wait_idle(), 10)

    def context(self):
        run_id = new_id()
        run = Run(run_id, self.session.id, new_id(), new_id(), "simple",
                  self.manager.repository.paths(self.session, run_id))
        return EngineContext(self.project, self.session, run, ())

    async def test_typed_deltas_append_replace_clear_and_bind_one_step(self):
        supplied = EngineDelta("developer-id", "draft", step_id="developer-step", sequence=99)
        async def action(context):
            yield supplied
            yield EngineDelta(text="revised", operation="replace")
            yield " answer"
            yield EngineDelta(text="", operation="replace")
            yield EngineDelta(text="final")
        context = self.context()
        events = [event async for event in BaseEngine(action=action).execute(context)]
        deltas = [event.delta for event in events if event.delta is not None]
        step_id = events[0].step_id
        self.assertTrue(all(delta.output_id == delta.step_id == step_id for delta in deltas))
        self.assertTrue(all(delta.sequence == 0 for delta in deltas))
        self.assertEqual([delta.operation for delta in deltas], ["append", "replace", "append", "replace", "append"])
        self.assertEqual(events[-2].output.text, "final")
        self.assertEqual(events[-1].output.text, "final")
        self.assertEqual(events[-1].output.output_id, context.run.id)
        self.assertEqual((supplied.output_id, supplied.step_id, supplied.sequence),
                         ("developer-id", "developer-step", 99))

    async def test_final_output_validation_emits_failure_and_closes_generator(self):
        closed = []
        async def invalid_final(context):
            try:
                yield EngineOutput(text="partial", final=False)
            finally:
                closed.append(True)
        async def mutated_final(context):
            result = EngineOutput(data={})
            try:
                yield result
                result.data["invalid"] = object()
            finally:
                closed.append(True)
        for action in (invalid_final, mutated_final):
            events = []
            with self.assertRaises((ValueError, TypeError)):
                async for event in BaseEngine(action=action).execute(self.context()):
                    events.append(event)
            self.assertEqual([event.type for event in events],
                             [EngineEventType.STEP_STARTED, EngineEventType.STEP_FAILED])
        self.assertEqual(closed, [True, True])

    async def test_typed_delta_after_final_is_rejected_and_closes_source(self):
        closed = []
        async def action(context):
            try:
                yield EngineOutput(text="final")
                yield EngineDelta(text="too late")
            finally:
                closed.append(True)
        events = []
        with self.assertRaisesRegex(ValueError, "after a final"):
            async for event in BaseEngine(action=action).execute(self.context()):
                events.append(event)
        self.assertEqual(events[-1].type, EngineEventType.STEP_FAILED)
        self.assertFalse(any(event.delta for event in events))
        self.assertEqual(closed, [True])

    async def test_internal_delta_is_not_promoted_by_default_final_output(self):
        async def action(context):
            yield EngineDelta(text="internal", visibility="internal")
            yield EngineOutput(text="internal final")
        events = [event async for event in BaseEngine(action=action).execute(self.context())]
        self.assertTrue(all((event.delta or event.output).visibility == "internal"
                            for event in events if event.delta or event.output))

    def test_output_helpers_bind_nested_scope_and_detach_values(self):
        context = replace(self.context(), output_step_id=new_id(), output_visibility="internal")
        original = EngineOutput(text="result", data={"items": [1]}, sequence=17)
        event = BaseEngine.output_event(context, original)
        self.assertEqual(event.step_id, context.output_step_id)
        self.assertEqual((event.output.output_id, event.output.visibility, event.output.sequence),
                         (context.output_step_id, "internal", 0))
        event.output.data["items"].append(2)
        self.assertEqual(original.data, {"items": [1]})
        self.assertIsNone(original.step_id)
        explicit_step = new_id()
        delta = BaseEngine.delta_event(context, "text", step_id=explicit_step)
        completed = BaseEngine.step_completed_event(context, explicit_step, original)
        self.assertEqual((delta.step_id, completed.step_id), (explicit_step, explicit_step))
        self.assertEqual(delta.delta.visibility, "internal")
        self.assertEqual(completed.output.visibility, "internal")
        with self.assertRaises(ValueError):
            BaseEngine.output_event(context, EngineOutput(final=False))
        with self.assertRaises(ValueError):
            BaseEngine.step_completed_event(context, "", original)
        with self.assertRaises(ValueError):
            BaseEngine.delta_event(replace(context, output_visibility="bad"), "text")

    async def test_subclass_reuses_completion_with_automatic_step_persistence(self):
        class Chat(BaseEngine):
            def run(self, context):
                return self.stream_completion({"model": "openai/test", "messages": [
                    {"role": "user", "content": context.messages[-1].content},
                ]})
        provider = ScriptedCompletion([chunk("Hello "), chunk("there", finish="stop")])
        self.engines.register("simple", Chat("Answer", kind="llm", completion_fn=provider))
        await self.manager.submit("hello", engine="simple")
        await self.idle()
        run, = self.manager.repository.list(self.session)
        step, = self.manager.steps.list(run)
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(step.status, StepStatus.COMPLETED)
        self.assertEqual(step.kind, "llm")
        self.assertEqual(ConversationStore(self.session.paths.conversation).get(
            run.assistant_message_id).content, "Hello there")
        self.assertTrue(provider.requests[0]["stream"])
        self.assertEqual(provider.closed, 1)

    async def test_subclass_yields_text_and_records_one_completed_step(self):
        class Echo(BaseEngine):
            async def run(self, context):
                yield "Echo: "
                yield ""
                yield context.messages[-1].content
        self.engines.register("simple", Echo("Echo", kind="text"))
        await self.manager.submit("hello", engine="simple")
        await self.idle()
        run, = self.manager.repository.list(self.session)
        step, = self.manager.steps.list(run)
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(step.status, StepStatus.COMPLETED)
        self.assertEqual(step.name, "Echo")
        self.assertEqual(step.kind, "text")
        self.assertEqual(ConversationStore(self.session.paths.conversation).get(
            run.assistant_message_id).content, "Echo: hello")

    async def test_coroutine_action_stores_private_state_without_emitting_text(self):
        seen = []
        async def prepare(context):
            context.state["secret"] = "private-action-value"
            seen.append(context.state)
        self.engines.register("simple", BaseEngine("Prepare", action=prepare))
        await self.manager.submit("hello", engine="simple")
        await self.idle()
        run, = self.manager.repository.list(self.session)
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(seen, [{"secret": "private-action-value"}])
        for path in self.project.paths.root.rglob("*.json*"):
            self.assertNotIn("private-action-value", path.read_text(encoding="utf-8"))

    async def test_partial_failure_keeps_diagnostics_closes_source_and_continues_queue(self):
        closed = []
        async def respond(context):
            try:
                yield "partial"
                if context.messages[-1].content == "bad":
                    raise ValueError("private-sdk-error")
                yield " done"
            finally:
                closed.append(True)
        self.engines.register("simple", BaseEngine("Respond", action=respond))
        await self.manager.submit("bad", engine="simple")
        await self.manager.submit("good", engine="simple")
        await self.idle()
        first, second = self.manager.repository.list(self.session)
        self.assertEqual(first.status, RunStatus.FAILED)
        self.assertEqual(second.status, RunStatus.COMPLETED)
        self.assertEqual(self.manager.steps.list(first)[0].error, "Step execution failed: private-sdk-error")
        self.assertEqual(ConversationStore(self.session.paths.conversation).get(
            first.assistant_message_id).content, "partial")
        self.assertEqual(closed, [True, True])
        self.assertEqual(first.error, "private-sdk-error")

    async def test_interrupt_closes_stream_and_preserves_queued_request(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        async def respond(context):
            try:
                yield "partial"
                if context.messages[-1].content == "first":
                    entered.set()
                    await asyncio.Event().wait()
            finally:
                closed.set()
        self.engines.register("simple", BaseEngine("Respond", action=respond))
        await self.manager.submit("first", engine="simple")
        await asyncio.wait_for(entered.wait(), 5)
        await self.manager.submit("second", engine="simple")
        self.assertTrue(await self.manager.interrupt())
        await self.idle()
        first, second = self.manager.repository.list(self.session)
        self.assertEqual(first.status, RunStatus.INTERRUPTED)
        self.assertEqual(self.manager.steps.list(first)[0].status, StepStatus.INTERRUPTED)
        self.assertEqual(second.status, RunStatus.COMPLETED)
        self.assertTrue(closed.is_set())

    async def test_timeout_finalizes_failed_step_and_closes_coroutine(self):
        closed = asyncio.Event()
        async def action(context):
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        self.engines.register("simple", BaseEngine("Wait", action=action, timeout_seconds=0.02))
        await self.manager.submit("request", engine="simple")
        await self.idle()
        run, = self.manager.repository.list(self.session)
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(self.manager.steps.list(run)[0].status, StepStatus.FAILED)
        self.assertTrue(closed.is_set())

    async def test_shared_engine_has_unique_steps_and_isolated_metadata_and_state(self):
        states = []
        entered, release = asyncio.Event(), asyncio.Event()
        async def action(context):
            self.assertEqual(context.state, {})
            context.state["session"] = context.session.id
            states.append(context.state)
            if len(states) == 2:
                entered.set()
            await release.wait()
        metadata = {"labels": ["fixed"]}
        engine = BaseEngine("Shared", action=action, metadata=metadata)
        metadata["labels"].append("external")
        self.engines.register("simple", engine)
        other = self.sessions.create(self.project, "Other")
        other_manager = RunManager(self.sessions, self.engines, session=other)
        self.addAsyncCleanup(other_manager.shutdown)
        await self.manager.submit("one", engine="simple")
        await other_manager.submit("two", engine="simple")
        await asyncio.wait_for(entered.wait(), 5)
        release.set()
        await asyncio.gather(self.idle(), other_manager.wait_idle())
        steps = [self.manager.steps.list(self.manager.repository.list(session)[0])[0]
                 for session in (self.session, other)]
        self.assertNotEqual(steps[0].id, steps[1].id)
        self.assertIsNot(states[0], states[1])
        self.assertTrue(all(step.metadata["labels"] == ["fixed"] for step in steps))
        self.assertTrue(all(step.output.step_id == step.id for step in steps))

    async def test_close_after_started_does_not_invoke_action_or_emit_terminal_event(self):
        called = []
        async def action(context):
            called.append(True)
        stream = BaseEngine(action=action).execute(self.context())
        self.assertEqual((await stream.__anext__()).type, EngineEventType.STEP_STARTED)
        await stream.aclose()
        self.assertEqual(called, [])
        with self.assertRaises(StopAsyncIteration):
            await stream.__anext__()

    async def test_close_after_text_closes_underlying_generator(self):
        closed = []
        async def action(context):
            try:
                yield "partial"
                raise AssertionError("must not advance after close")
            finally:
                closed.append(True)
        stream = BaseEngine(action=action).execute(self.context())
        await stream.__anext__()
        self.assertEqual((await stream.__anext__()).delta.text, "partial")
        await stream.aclose()
        self.assertEqual(closed, [True])

    async def test_invalid_output_fails_without_persisting_raw_objects(self):
        async def wrong_generator(context):
            yield {"private": "response"}
        async def wrong_coroutine(context):
            return "use yield instead"
        for index, action in enumerate((wrong_generator, wrong_coroutine)):
            name = str(index)
            self.engines.register(name, BaseEngine(action=action))
            await self.manager.submit("request", engine=name)
            await self.idle()
            run = self.manager.repository.list(self.session)[-1]
            self.assertEqual(run.status, RunStatus.FAILED)
            self.assertEqual(ConversationStore(self.session.paths.conversation).get(
                run.assistant_message_id).content, "")

    async def test_generic_async_iterator_and_close_failure(self):
        class Iterator:
            def __init__(self):
                self.used = False
            def __aiter__(self):
                return self
            async def __anext__(self):
                if self.used:
                    raise StopAsyncIteration
                self.used = True
                return "hello"
        engine = BaseEngine(action=lambda context: Iterator())
        events = [event async for event in engine.execute(self.context())]
        self.assertEqual([event.type for event in events], [EngineEventType.STEP_STARTED,
                         EngineEventType.TEXT_DELTA, EngineEventType.STEP_COMPLETED, EngineEventType.OUTPUT])
        class BadClose(Iterator):
            async def aclose(self):
                raise ValueError("private-close-error")
        events = []
        with self.assertRaisesRegex(ValueError, "private-close-error"):
            async for event in BaseEngine(action=lambda context: BadClose()).execute(self.context()):
                events.append(event)
        self.assertEqual(events[-1].type, EngineEventType.STEP_FAILED)
        self.assertNotIn(EngineEventType.STEP_COMPLETED, [event.type for event in events])

    async def test_cleanup_failure_during_close_does_not_yield_failure_event(self):
        closed = []
        class Source:
            def __aiter__(self):
                return self
            async def __anext__(self):
                return "text"
            async def aclose(self):
                closed.append(True)
                raise ValueError("private-close-error")
        stream = BaseEngine(action=lambda ctx: Source()).execute(self.context())
        await stream.__anext__()
        await stream.__anext__()
        await stream.aclose()
        self.assertEqual(closed, [True])
        with self.assertRaises(StopAsyncIteration):
            await stream.__anext__()

    async def test_unimplemented_operation_is_a_failed_step(self):
        events = []
        with self.assertRaisesRegex(NotImplementedError, "Implement run"):
            async for event in BaseEngine().execute(self.context()):
                events.append(event)
        self.assertEqual([event.type for event in events],
                         [EngineEventType.STEP_STARTED, EngineEventType.STEP_FAILED])


class StepConfigurationTests(unittest.TestCase):
    def test_invalid_persistent_metadata_is_rejected_before_execution(self):
        for metadata in ({"not_json": object()}, {"nonfinite": float("nan")}, ["wrong"]):
            with self.assertRaises((TypeError, ValueError)):
                BaseEngine(metadata=metadata)


class CompletionHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_chunks_and_interleaved_tool_fragments_build_assistant_message(self):
        provider = ScriptedCompletion([
            SimpleNamespace(choices=[SimpleNamespace(index=0, finish_reason=None,
                delta=SimpleNamespace(content="Thinking", tool_calls=None))]),
            chunk(calls=[call('{"b":', index=1, name="second", call_id="id_2"),
                         call('{"a":', name="first", call_id="id_1")]),
            chunk(calls=[{"index": 0, "function": {"arguments": "1}"}},
                         {"index": 1, "function": {"arguments": "2}"}}], finish="tool_calls"),
            {"choices": [], "usage": {"total_tokens": 4}},
        ])
        response = {}
        engine = BaseEngine(completion_fn=provider)
        text = [part async for part in engine.stream_completion(include_events=False, request={"model": "test"}, response=response)]
        self.assertEqual(text, ["Thinking"])
        self.assertEqual(response["role"], "assistant")
        self.assertEqual(response["content"], "Thinking")
        self.assertEqual([item["id"] for item in response["tool_calls"]], ["id_1", "id_2"])
        self.assertEqual([item["function"]["arguments"] for item in response["tool_calls"]],
                         ['{"a":1}', '{"b":2}'])
        self.assertEqual(provider.closed, 1)

    async def test_incomplete_or_oversized_stream_does_not_publish_complete_response(self):
        cases = [
            ({}, [chunk("partial")]),
            ({}, [chunk("partial", finish="length")]),
            ({"max_output_chars": 2}, [chunk("abc", finish="stop")]),
            ({"max_argument_chars": 2}, [chunk(calls=[call()], finish="tool_calls")]),
            ({"max_argument_chars": 1024}, [chunk(calls=[call(call_id="x" * 1025)], finish="tool_calls")]),
            ({"max_tool_calls": 1}, [chunk(calls=[call(index=1)], finish="tool_calls")]),
            ({}, [chunk(calls=[call(), call(index=1)], finish="tool_calls")]),
        ]
        for limits, chunks in cases:
            with self.subTest(limits=limits, chunks=chunks):
                response = {}
                provider = ScriptedCompletion(chunks)
                engine = BaseEngine(completion_fn=provider, **limits)
                with self.assertRaises(ValueError):
                    async for _ in engine.stream_completion(include_events=False, request={}, response=response):
                        pass
                self.assertEqual(response, {})

    async def test_simultaneous_streams_keep_results_and_request_copies_isolated(self):
        client = object()
        original = {"model": "test", "client": client,
                    "messages": [{"role": "user", "content": "shared"}]}
        def provider(**request):
            if request["client"] is not client:
                raise AssertionError("SDK client identity changed")
            label = request["label"]
            request["messages"][0]["content"] = label
            yield chunk(label)
            yield chunk("!", finish="stop")
        engine = BaseEngine(completion_fn=provider)
        async def collect(label):
            response = {}
            text = [part async for part in engine.stream_completion(include_events=False, request=
                dict(original, label=label), response=response)]
            return text, response
        first, second = await asyncio.gather(collect("first"), collect("second"))
        self.assertEqual(first, (["first", "!"], {"role": "assistant", "content": "first!"}))
        self.assertEqual(second, (["second", "!"], {"role": "assistant", "content": "second!"}))
        self.assertEqual(original["messages"][0]["content"], "shared")
        self.assertNotIn("stream", original)

    async def test_early_close_closes_provider_and_leaves_response_unfinished(self):
        closed = threading.Event()
        def provider(**request):
            try:
                while True:
                    yield chunk("delta")
            finally:
                closed.set()
        response = {}
        engine = BaseEngine(completion_fn=provider, buffer_size=1)
        async with aclosing(engine.stream_completion(include_events=False, request={}, response=response)) as stream:
            self.assertEqual(await stream.__anext__(), "delta")
        self.assertTrue(await asyncio.to_thread(closed.wait, 5))
        self.assertEqual(response, {})

    async def test_nonstreaming_or_multiple_choices_rejected_before_provider_call(self):
        provider = ScriptedCompletion([])
        engine = BaseEngine(completion_fn=provider)
        for request in ({"stream": False}, {"n": 2}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                async for _ in engine.stream_completion(include_events=False, request=request):
                    pass
        self.assertEqual(provider.requests, [])
