"""여러 컴포넌트의 순서·출처·관찰 사본·자원 정리와 Memory 조합을 검사한다."""

import asyncio
from contextvars import ContextVar
from types import SimpleNamespace
import unittest

from llm.components.base import Component
from llm.components.processing import (CompletionMessage, CompletionRequest, CompletionObservation,
    CompletionSession, CompletionPipeline, ordered_processors)
from llm.core.models import RunStatus
from llm.engines.loop import LoopEngine
from tests.llm.configuration_fixtures import configure_engine
from llm.llm import LargeLanguageModel
from llm.services.composition import BackendServices
from llm.policies import CompletionPolicy
from tests.llm import test_memory_processing as memory_tests
from tests.llm.test_loop import ScriptedCompletion, call, chunk


class Processor:
    def __init__(self, name, factory, priority=0, close_timeout=1.0):
        self.name, self.factory = name, factory
        self.priority, self.close_timeout = priority, close_timeout

    def session(self, context):
        return self.factory(context)


class Feature(Component):
    def __init__(self, name, processor):
        self.name = self.directory = name
        self.capabilities = ("completion_processors",)
        self.processor = processor

    def resolve(self, project, capability):
        return self.processor


async def consume(events):
    async for _ in events:
        pass


class ProcessingContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = SimpleNamespace(run=SimpleNamespace(input_message_id="current"))

    async def test_order_and_reverse_close_even_when_one_close_fails(self):
        trace = []
        class Session(CompletionSession):
            def __init__(self, name):
                self.name = name
            async def aclose(self, error):
                trace.append(self.name)
                if self.name == "b":
                    raise ValueError("close failed")
        processors = [Processor("b", lambda _: Session("b")), Processor("a", lambda _: Session("a"))]
        self.assertEqual([p.name for p in ordered_processors(processors)], ["a", "b"])
        with self.assertRaisesRegex(ValueError, "close failed"):
            async with CompletionPipeline(processors, self.context):
                pass
        self.assertEqual(trace, ["b", "a"])

    async def test_partial_session_creation_closes_existing_and_keeps_original_error(self):
        trace = []
        class Session(CompletionSession):
            async def aclose(self, error):
                trace.append(str(error))
                raise RuntimeError("cleanup failure")
        def broken(_):
            raise ValueError("factory failure")
        with self.assertRaisesRegex(ValueError, "factory failure"):
            async with CompletionPipeline([Processor("a", lambda _: Session()), Processor("b", broken)], self.context):
                self.fail("must not enter")
        self.assertEqual(trace, ["factory failure"])

    async def test_close_timeout_does_not_skip_other_sessions(self):
        closed = []
        class Fast(CompletionSession):
            async def aclose(self, error):
                closed.append("fast")
        class Slow(CompletionSession):
            async def aclose(self, error):
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.append("slow")
        with self.assertRaises(asyncio.TimeoutError):
            async with CompletionPipeline([Processor("a", lambda _: Fast()),
                    Processor("b", lambda _: Slow(), close_timeout=.05)], self.context):
                pass
        self.assertEqual(closed, ["slow", "fast"])

    async def test_cancelled_prepare_closes_generator_and_session(self):
        started, closed = asyncio.Event(), []
        class Session(CompletionSession):
            async def prepare(self, request):
                try:
                    started.set()
                    await asyncio.Event().wait()
                    if False:
                        yield
                finally:
                    closed.append("generator")
            async def aclose(self, error):
                closed.append(type(error))
        original = [CompletionMessage({"role": "user", "content": "q"}, "current")]
        async def work():
            async with CompletionPipeline([Processor("p", lambda _: Session())], self.context) as chain:
                await consume(chain.prepare(CompletionRequest({}, original, 1), original))
        worker_future = asyncio.create_task(work())
        await started.wait()
        worker_future.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker_future
        self.assertEqual(closed, ["generator", asyncio.CancelledError])

    async def test_registration_errors_are_rejected_without_creating_sessions(self):
        def forbidden(_):
            self.fail("validation must not instantiate sessions")
        for processors in ([Processor("x", forbidden), Processor("x", forbidden)],
                [Processor("x", forbidden, priority=True)], [Processor("x", forbidden, close_timeout=0)]):
            with self.assertRaises(ValueError):
                ordered_processors(processors)

    async def test_cleanup_uses_same_task_context(self):
        variable = ContextVar("processor_test", default="original")
        class Session(CompletionSession):
            async def prepare(self, request):
                self.token = variable.set("changed")
                if False:
                    yield
            async def aclose(self, error):
                variable.reset(self.token)
        original = [CompletionMessage({"role": "user", "content": "q"}, "current")]
        async with CompletionPipeline([Processor("p", lambda _: Session())], self.context) as chain:
            await consume(chain.prepare(CompletionRequest({}, original, 1), original))
            self.assertEqual(variable.get(), "changed")
        self.assertEqual(variable.get(), "original")

    async def test_invalid_sources_and_active_transcript_changes_rejected(self):
        original = [CompletionMessage({"role": "user", "content": "q"}, "current"),
                    CompletionMessage({"role": "assistant", "tool_calls": [call("{}")]}),
                    CompletionMessage({"role": "tool", "tool_call_id": "call_1", "content": "result"})]
        for messages in ([*original, original[0]], original[1:],
                         [CompletionMessage({"role": "user"}, "forged"), *original],
                         [original[0], original[2], original[1]]):
            with self.assertRaises(ValueError):
                CompletionPipeline._validate(CompletionRequest({}, messages, 1), original, {}, "current")

    async def test_partial_history_turn_removal_is_rejected(self):
        original = [CompletionMessage({"role": "user", "content": "old"}, "old"),
                    CompletionMessage({"role": "assistant", "content": "answer"}, "answer"),
                    CompletionMessage({"role": "user", "content": "q"}, "current")]
        with self.assertRaisesRegex(ValueError, "whole prior turns"):
            CompletionPipeline._validate(CompletionRequest({}, original[1:], 1), original, {}, "current")


class ProcessingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = memory_tests.MemoryProcessingTests.asyncSetUp
    configure = memory_tests.MemoryProcessingTests.configure
    run_loop = memory_tests.MemoryProcessingTests.run_loop
    build_history = memory_tests.MemoryProcessingTests.build_history

    async def add_feature(self, name, factory, priority=0):
        self.app.project_manager.components.register(Feature(name, Processor(name, factory, priority)))

    async def test_inserted_reference_survives_summary_in_both_processor_orders(self):
        await self.configure(summarize=True, keep_turns=1, summary_after_chars=1)
        await self.build_history()
        class Session(CompletionSession):
            async def prepare(self, request):
                request.messages.insert(0, CompletionMessage({"role": "user", "content": "skill reference"}))
                if False:
                    yield
        for priority in (0, 200):
            name = "skill" + str(priority)
            await self.add_feature(name, lambda _: Session(), priority)
            await self.project.components.aselect([name, "memory"])
            run, model = await self.run_loop("next request")
            self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
            contents = [m["content"] for m in model.requests[0]["messages"]]
            self.assertEqual(contents[0], "skill reference")
            self.assertNotIn("first requirement", contents)
            self.assertIn("conversation_summary", contents[-1])
            self.assertTrue(all("source_id" not in m for m in model.requests[0]["messages"]))
        history = await self.session.aconversation()
        self.assertFalse(any("skill reference" in m.content for m in history))

    async def test_edited_history_is_not_overwritten_by_memory_summary(self):
        await self.configure(summarize=True, keep_turns=1, summary_after_chars=1)
        await self.build_history()
        class Session(CompletionSession):
            async def prepare(self, request):
                request.messages[0].value["content"] = "edited by skill"
                if False:
                    yield
        await self.add_feature("edit", lambda _: Session())
        await self.project.components.aselect(["memory", "edit"])
        run, model = await self.run_loop("next")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        contents = [m["content"] for m in model.requests[0]["messages"]]
        self.assertEqual(contents[0], "edited by skill")
        self.assertNotIn("conversation_summary", contents[-1])

    async def test_each_completion_is_observed_before_tools_and_snapshots_are_isolated(self):
        trace, observations = [], []
        class Session(CompletionSession):
            def __init__(self, name):
                self.name = name
            async def prepare(self, request):
                trace.append((self.name, "prepare", request.iteration))
                request.messages[0].value["content"] += " prepared"
                if False:
                    yield
            async def after_completion(self, observation):
                trace.append((self.name, "after", observation.iteration))
                observations.append(observation.snapshot())
                observation.response.clear()
                observation.request.clear()
                if False:
                    yield
            async def finish(self, observation):
                trace.append((self.name, "finish", observation.iteration))
                observation.response["content"] = "must not replace answer"
                if False:
                    yield
            async def aclose(self, error):
                trace.append((self.name, "close", error))
        await self.add_feature("a", lambda _: Session("a"))
        await self.add_feature("b", lambda _: Session("b"))
        await self.project.components.aselect(["b", "memory", "a"])
        await self.memory.acreate({"content": "saved"}, identifier="m")
        model = ScriptedCompletion([chunk(calls=[call('{"identifier":"m"}', name="memory_get")]),
                                    chunk(finish="tool_calls")], memory_tests.answer("final"))
        run, _ = await self.run_loop("query", model=model)
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual((await run.aresult()).output.text, "final")
        self.assertEqual([(n, k, i) for n, k, i in trace if k == "after"],
                         [("a", "after", 1), ("b", "after", 1), ("a", "after", 2), ("b", "after", 2)])
        self.assertEqual(trace[-2:], [("b", "close", None), ("a", "close", None)])
        self.assertTrue(observations[1].response["tool_calls"])
        self.assertEqual(observations[0].original_messages[0]["content"], "query")
        self.assertEqual(observations[0].request["messages"][0]["content"], "query prepared prepared")
        self.assertEqual(len([x for x in trace if x[1] == "finish"]), 2)

    async def test_after_completion_failure_prevents_tool_effect(self):
        closed = []
        class Session(CompletionSession):
            async def after_completion(self, observation):
                raise ValueError("review rejected")
                if False:
                    yield
            async def aclose(self, error):
                closed.append(str(error))
        await self.add_feature("review", lambda _: Session())
        await self.project.components.aselect(["memory", "review"])
        model = ScriptedCompletion([chunk(calls=[call('{"content":"must not save"}', name="memory_create")]),
                                    chunk(finish="tool_calls")])
        run, _ = await self.run_loop(model=model)
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(await self.memory.alist(), {})
        self.assertEqual(closed, ["review rejected"])

    async def test_processor_cannot_override_tool_contract(self):
        class Session(CompletionSession):
            async def prepare(self, request):
                request.parameters["tools"] = []
                if False:
                    yield
        await self.add_feature("override", lambda _: Session())
        await self.project.components.aselect(["memory", "override"])
        run, model = await self.run_loop()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(model.requests, [])

    async def test_cleanup_failure_marks_run_failed(self):
        class Session(CompletionSession):
            async def aclose(self, error):
                raise RuntimeError("cannot close")
        await self.add_feature("closing", lambda _: Session())
        await self.project.components.aselect(["closing"])
        run, _ = await self.run_loop()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertIn("cannot close", run.data.error)

    async def test_observation_contains_budgeted_request_and_original_history(self):
        await self.build_history()
        await self.app.shutdown()
        observations = []
        class Session(CompletionSession):
            async def after_completion(self, observation):
                observations.append(observation)
                if False:
                    yield
        feature = Feature("observe", Processor("observe", lambda _: Session()))
        self.app = LargeLanguageModel(self.root / "workspace", components=[self.component, feature, memory_tests.PromptComponent()], engines={},
            services=BackendServices(token_counters={"test": lambda r: len(r["messages"])}))
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.aload(self.project.id)
        await configure_engine(self.project, "loop" + str(self.serial), input_policy={"max_tokens": 1, "counter": "test"})
        self.session = await self.project.sessions.aload(self.session.id)
        await self.project.components.aselect(["observe"])
        run, model = await self.run_loop("current")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(observations[0].request["messages"], model.requests[0]["messages"])
        self.assertEqual(len(observations[0].request["messages"]), 1)
        self.assertEqual(len(observations[0].original_messages), 7)

    async def test_concurrent_sessions_have_separate_processor_sessions(self):
        sessions, ready = [], asyncio.Event()
        class Session(CompletionSession):
            def __init__(self, context):
                self.session_id = context.session.id
                self.closed = False
                sessions.append(self)
            async def prepare(self, request):
                if len(sessions) == 2:
                    ready.set()
                await ready.wait()
                request.messages[-1].value["content"] += self.session_id
                if False:
                    yield
            async def aclose(self, error):
                self.closed = True
        await self.add_feature("parallel", Session)
        await self.project.components.aselect(["parallel"])
        other = await self.project.sessions.acreate()
        results = await asyncio.gather(self.run_loop("one"), self.run_loop("two", session=other))
        for (run, model), prefix, session in zip(results, ("one", "two"), (self.session, other)):
            self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
            self.assertEqual(model.requests[0]["messages"][-1]["content"], prefix + session.id)
        self.assertEqual(len(sessions), 2)
        self.assertTrue(all(s.closed for s in sessions))

    async def test_memory_order_is_configurable(self):
        await self.configure(priority=-10)
        exported = self.component.resolve_runtime(self.project.data, "completion_processors",
                                                  data_factory=lambda _: self.memory)
        self.assertEqual(exported.priority, -10)

    async def test_real_run_interrupt_closes_session_and_next_request_can_run(self):
        entered, closed = asyncio.Event(), []
        class Session(CompletionSession):
            async def prepare(self, request):
                entered.set()
                await asyncio.Event().wait()
                if False:
                    yield
            async def aclose(self, error):
                closed.append(type(error).__name__)
        await self.add_feature("waiting", lambda _: Session())
        await self.project.components.aselect(["waiting"])
        model = ScriptedCompletion(memory_tests.answer())
        self.app.engines.register("interruptible", LoopEngine(completion_fn=model).for_agent({"engine": 'loop', "engine_options": LoopEngine.parameter_layout.pack({'completion': {"model": "test/main"}})}))
        request = await self.session.run.submit("stop", engine="interruptible")
        await asyncio.wait_for(entered.wait(), 5)
        await self.session.run.interrupt()
        run = await request.wait(timeout=10)
        self.assertEqual(run.data.status, RunStatus.INTERRUPTED)
        self.assertEqual(len(closed), 1)
        self.assertEqual(model.requests, [])
        await self.session.run.wait_idle()
        await self.project.components.aselect([])
        run, _ = await self.run_loop("continue")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
