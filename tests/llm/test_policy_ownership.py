"""공통 정책과 구현체별 입력/provider 설정의 책임·상속·재개 경계를 검증한다."""

import asyncio
from contextlib import aclosing
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from llm.llm import LargeLanguageModel, ProjectConfig, ServiceConfig, RunPolicy, CompletionPolicy
from llm.engines.base import BaseEngine
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.engines.graph.agent import AgentNode
from llm.components.agents import AgentComponent
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.core.policies import normalize_policies
from tests.llm.configuration_fixtures import configure_engine
from tests.llm.test_loop import ScriptedCompletion, chunk


class PolicySettingsTests(unittest.TestCase):
    def test_empty_and_removed_policies_and_public_names(self):
        self.assertEqual(normalize_policies({}), {})
        for name in ("completion", "provider_retry"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                ProjectConfig(policies={name: {}})
        import llm.llm as api
        import llm.services.history.context as history
        self.assertFalse(hasattr(api, "RunLimits"))
        self.assertFalse(hasattr(history, "CompletionPolicy"))
        self.assertIsNone(RunPolicy().timeout_seconds)
        self.assertIsNone(CompletionPolicy.from_settings(None, {}))
        with self.assertRaises(ValueError):
            RunPolicy(timeout_seconds=0)

    def test_engine_empty_inheritance_and_agent_cannot_clear_parent_budget(self):
        engine = LoopEngine()
        self.assertEqual(engine.configuration(ProjectConfig(), "writer")["values"], {})
        project = ProjectConfig(parameters={"engines": {"writer": {'policy': {'completion': {'max_tokens': 100, 'counter': 'words'}, 'provider': {'max_attempts': 2, 'wall_timeout': 30}}}}})
        session = {"parameters": {"engines": {"writer": {'policy': {'completion': {'max_tokens': 80}}}}}}
        self.assertEqual(engine.configuration(project, "writer", session_config=session)["values"]["policy"]["completion"]["max_tokens"], 80)
        agent = engine.for_agent({"engine": "writer", "engine_options": {'policy': {'completion': {'max_tokens': 60}}}})
        view = agent.configuration(project, "writer", session_config=session)
        self.assertEqual(view["sources"]["/policy/completion/max_tokens"], "agent")
        self.assertEqual(view["values"]["policy"]["completion"]["max_tokens"], 60)
        child = LoopEngine().for_agent({"engine": "writer", "engine_options": {"policy": {"completion": None}}})
        with self.assertRaisesRegex(ValueError, "widens"):
            child.configuration(project, "writer", session_config=session)
        null_session = {"parameters": {"engines": {"writer": {'policy': {'completion': None, 'provider': None}}}}}
        with self.assertRaisesRegex(ValueError, "widens"):
            engine.configuration(project, "writer", session_config=null_session)
        self.assertIsNone(engine.configuration({}, "writer", session_config=null_session)["values"]["policy"]["completion"])

    def test_invalid_input_policies(self):
        for settings in ({"max_tokens": True}, {"max_tokens": 1.0}, {"max_tokens": 0},
                         {"max_tokens": 10, "reserve_tokens": 10}, {"reserve_tokens": 1},
                         {"counter": " "}, {"unknown": 1}):
            with self.subTest(settings=settings), self.assertRaises((ValueError, TypeError)):
                CompletionPolicy.validate_settings(settings)
        with self.assertRaisesRegex(Exception, "not registered"):
            CompletionPolicy.from_settings({"max_tokens": 100, "counter": "missing"}, {})


class PolicyOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def backend(self, engines, *, components=(), counters=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        app = LargeLanguageModel(Path(temp.name), engines=engines, components=components,
            services=ServiceConfig(token_counters=counters or {"count": lambda request: 1}))
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate(components=[c.name for c in components])
        return app, project, await project.sessions.acreate()

    async def test_schema_and_shared_usage_counter_are_independent_of_input_policy(self):
        model = ScriptedCompletion([chunk("done", finish="stop")])
        app, project, session = await self.backend({"writer": LoopEngine(completion_fn=model)},
            counters={"input": lambda r: 1, "usage": lambda r: 5})
        await configure_engine(project, "writer", completion={"model": "test", "max_tokens": 2},
            input_policy={"max_tokens": 100, "counter": "input"})
        await project.aconfigure_policies({"usage": {"max_tokens": 6, "counter": "usage"}})
        run = await (await session.run.submit("hello", engine="writer")).wait(timeout=10)
        self.assertEqual(run.data.error_code, "usage_limit")
        self.assertEqual(model.requests, [])
        schema = app.project_schema()["properties"]["config"]["properties"]
        self.assertNotIn("completion", schema["policies"]["properties"])
        fields = schema["parameters"]["properties"]["engines"]["properties"]["writer"]["properties"]
        self.assertIn("input", fields["policy"]["properties"]["completion"]["properties"]["counter"]["enum"])
        self.assertIn("usage", schema["policies"]["properties"]["usage"]["properties"]["counter"]["enum"])

    async def test_unrelated_engine_does_not_receive_completion_policy(self):
        seen = []
        class Inspect(BaseEngine):
            async def run(self, context):
                seen.append(context.completion_policy)
                yield "done"
        _, project, session = await self.backend({"inspect": Inspect(), "writer": LoopEngine()})
        await configure_engine(project, "writer", input_policy={"max_tokens": 1, "counter": "missing"})
        run = await (await session.run.submit("hello", engine="inspect")).wait(timeout=10)
        self.assertEqual(run.data.status, "completed", run.data.error)
        self.assertEqual(seen, [None])

    async def test_missing_counter_fails_only_selected_engine_before_model(self):
        model = ScriptedCompletion([chunk("done", finish="stop")])
        _, project, session = await self.backend({"writer": LoopEngine(completion_fn=model)})
        await configure_engine(project, "writer", completion={"model": "test"},
            input_policy={"max_tokens": 10, "counter": "missing"})
        run = await (await session.run.submit("hello", engine="writer")).wait(timeout=10)
        self.assertEqual(run.data.error_code, "policy_unavailable")
        self.assertEqual(model.requests, [])

    async def test_graph_agents_keep_own_input_and_provider_configuration(self):
        seen = []
        model = ScriptedCompletion([chunk("one", finish="stop")], [chunk("two", finish="stop")])
        class InspectLoop(LoopEngine):
            async def _execute(self, context):
                seen.append((context.completion_policy.max_tokens, deepcopy(self.provider)))
                async with aclosing(super()._execute(context)) as events:
                    async for event in events:
                        yield event
        implementation = InspectLoop(completion_fn=model)
        graph = GraphEngine(handlers={"agent": AgentNode(engines={"writer": implementation})})
        _, project, session = await self.backend({"graph": graph}, components=[AgentComponent(), WorkflowComponent()])
        await configure_engine(project, "writer", completion={"model": "test"},
            input_policy={"max_tokens": 400, "counter": "count"}, provider={"max_attempts": 2})
        for name, size in (("a", 200), ("b", 300)):
            await project.components.agents.acreate({"engine": "writer", "purpose": name,
                "engine_options": {'policy': {'completion': {'max_tokens': size}, 'provider': {'max_attempts': 2}}}}, identifier=name)
        workflow = (WorkflowGraph(entry="a").node("a", "agent", agent="a")
            .node("b", "agent", agent="b").node("end", "end").connect("a", "b").connect("b", "end"))
        await project.components.workflows.acreate(workflow.to_dict(), identifier="flow")
        run = await (await session.run.submit("hello", engine="graph", engine_options={"workflow": "flow"})).wait(timeout=10)
        self.assertEqual(run.data.status, "completed", run.data.error)
        self.assertEqual(seen, [(200, {"max_attempts": 2}), (300, {"max_attempts": 2})])
        self.assertNotIn("input_policy", implementation.configuration(ProjectConfig(), "writer")["values"].get("completion", {}))

    async def test_changed_engine_policy_rejects_checkpoint_resume(self):
        def offline(**request):
            raise ConnectionError("offline")
            yield
        _, project, session = await self.backend({"writer": LoopEngine(completion_fn=offline)})
        await configure_engine(project, "writer", completion={"model": "test"},
            input_policy={"max_tokens": 100, "counter": "count"})
        for change in ({"input_policy": {"max_tokens": 200}}, {"provider": {"max_attempts": 2}}):
            with self.subTest(change=change):
                run = await (await session.run.submit("hello", engine="writer")).wait(timeout=10)
                self.assertEqual(run.data.status, "failed")
                await configure_engine(project, "writer", **change)
                with self.assertRaisesRegex(ValueError, "changed"):
                    await session.run.resume(run.id, engine="writer")

    async def test_stream_options_never_enter_sdk_kwargs_and_no_ambient_retry(self):
        calls = []
        def busy(**request):
            calls.append(request)
            raise ConnectionError("offline")
            yield
        engine = BaseEngine(completion_fn=busy)
        for provider, expected in ((None, 1), ({"max_attempts": 3}, 3)):
            calls.clear()
            with self.assertRaises(Exception):
                async with aclosing(engine.stream_completion({"model": "test"}, provider=provider)) as events:
                    async for _ in events:
                        pass
            self.assertEqual(len(calls), expected)
            self.assertEqual(calls[0], {"model": "test", "stream": True})

    async def test_stream_wall_timeout_is_separate_and_does_not_cancel_event_storage(self):
        stored = []
        class Stream(BaseEngine):
            async def _stream_completion(self, request, response, result, progress, *limits):
                yield "first"
                await asyncio.Event().wait()
        async with aclosing(Stream().stream_completion({"model": "test"}, include_events=False,
                provider={"wall_timeout": .01})) as stream:
            self.assertEqual(await anext(stream), "first")
            await asyncio.sleep(.03)  # 소비자 저장이 진행 중일 때 timer가 소비자를 취소해서는 안 된다.
            stored.append("persisted")
            with self.assertRaisesRegex(Exception, "provider timeout"):
                await anext(stream)
        self.assertEqual(stored, ["persisted"])

    async def test_stream_wall_timeout_covers_advancement_and_usage_preparation(self):
        from llm.providers.requests import ProviderError
        from llm.services.runtime.usage import UsageScope
        calls = []
        class Stream(BaseEngine):
            async def _stream_completion(self, request, response, result, progress, *limits):
                calls.append("provider")
                await asyncio.Event().wait()
                yield "unreachable"
        class SlowUsage(UsageScope):
            async def reservation(self, request):
                await asyncio.Event().wait()

        async def consume():
            async with aclosing(Stream().stream_completion({"model": "test"},
                    provider={"wall_timeout": .01})) as stream:
                async for _ in stream:
                    pass
        with self.assertRaises(ProviderError) as error:
            await consume()
        self.assertEqual(error.exception.code, "provider_timeout")
        self.assertEqual(calls, ["provider"])
        calls.clear()
        with SlowUsage({}, None).scope(), self.assertRaises(ProviderError) as error:
            await consume()
        self.assertEqual(error.exception.code, "provider_timeout")
        self.assertEqual(calls, [])
