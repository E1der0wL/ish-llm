from tests.llm.configuration_fixtures import memory_settings
"""프로젝트별 정책 저장·적용·동시 격리와 Run 사본을 실제 서비스 경로에서 검증한다."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.core.models import ProjectConfig, RunStatus
from llm.engines.base import BaseEngine
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from llm.services.configuration import ServiceConfig
from llm.services.runtime.runs import RunRequestError
from llm.services.runtime.policies import model_token_count
from tests.llm.test_loop import ScriptedCompletion, chunk
from tests.llm.configuration_fixtures import configure_engine


class ProjectConfigPolicyTests(unittest.TestCase):
    def test_partial_update_preserves_settings_and_detaches_input_and_result(self):
        config = ProjectConfig(policies={"context": {"mode": "recent"}}, data={"future": {"value": 1}}, parameters={"engines": {"loop": {'config': {'completion': {'model': 'test/model'}}}}})
        changes = {"context": {"max_turns": 3}}
        result = config.configure_policies(changes)
        self.assertEqual(config.policies["context"],
                         {"mode": "recent", "max_turns": 3})
        self.assertEqual(result["context"], {"mode": "recent", "max_turns": 3})
        self.assertEqual(config.parameters["engines"]["loop"]["config"]["completion"], {"model": "test/model"})
        self.assertEqual(config.data["future"], {"value": 1})
        changes["context"]["max_turns"] = 8
        result["context"]["max_turns"] = 9
        self.assertEqual(config.policies["context"]["max_turns"], 3)

    def test_failed_update_preserves_in_memory_settings(self):
        config = ProjectConfig(policies={"run": {"max_queued": 100}})
        before = deepcopy(config)
        original = config.policies
        for changes in ({"run": {"max_queued": 0}},
                        {"context": {"max_turns": True}},
                        {"extension": {"callback": object()}}, []):
            with self.subTest(changes=changes), self.assertRaises((TypeError, ValueError)):
                config.configure_policies(changes)
            self.assertEqual(config, before)
            self.assertIs(config.policies, original)
        # 정책 정규화 이후 다른 설정의 검증이 실패해도 원본에 반영하지 않는다.
        config.parameters["engines"] = []
        before = deepcopy(config)
        with self.assertRaises(TypeError):
            config.configure_policies({"context": {"max_turns": 3}})
        self.assertEqual(config, before)
        self.assertIs(config.policies, original)


class ProjectPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.seen = []
        seen = self.seen
        class Inspect(BaseEngine):
            async def run(self, context):
                seen.append((context.project.id, [m.content for m in context.messages],
                             deepcopy(context.run.metadata["policies"])))
                yield "answer"
        self.engine = Inspect()
        self.app = LargeLanguageModel(self.root, components=[], engines={"inspect": self.engine},
            services=ServiceConfig(token_counters={"length": lambda r: sum(len(m.get("content") or "") for m in r["messages"])}))
        self.addAsyncCleanup(self.app.shutdown)

    async def create(self, policies=None):
        project = await self.app.projects.acreate(config=ProjectConfig(policies=policies or {}))
        return project, await project.sessions.acreate()

    async def execute(self, session, text="q", engine="inspect"):
        return await (await session.run.submit(text, engine=engine)).wait(timeout=10)

    async def test_defaults_schema_and_partial_update_preserve_other_settings(self):
        project, _ = await self.create()
        before = await project.aconfiguration()
        self.assertEqual(before["project"]["config"]["policies"], {})
        self.assertEqual(before["policy_schema"], ProjectConfig.policy_schema())
        config = before["project"]["config"]
        config["parameters"]["engines"] = {"loop": {"config": {"completion": {"model": "test/model"}}}}
        config["data"] = {"future": {"value": 1}}
        await project.asave(config=config)
        await asyncio.gather(project.aconfigure_policies({"context": {"mode": "recent", "max_turns": 2}}),
                             project.aconfigure_policies({"run": {"max_queued": 3}}))
        saved = (await project.aget_data()).config
        self.assertEqual(saved.policies["context"]["max_turns"], 2)
        self.assertEqual(saved.policies["run"]["max_queued"], 3)
        self.assertEqual(saved.parameters["engines"]["loop"]["config"]["completion"], {"model": "test/model"})
        self.assertEqual(saved.data["future"], {"value": 1})
        disk = json.loads((project.paths.root / "project.json").read_text())
        self.assertEqual(disk["config"]["policies"], saved.policies)

    async def test_invalid_values_do_not_replace_saved_policy(self):
        project, _ = await self.create()
        before = (await project.aget_data()).config
        for change in ({"context": {"mode": "budget"}}, {"context": {"max_turns": True}},
                       {"context": {"max_turns": 1.0}}, {"context": {"typo": 1}},
                       {"completion": {"max_tokens": 10, "reserve_tokens": 10}},
                       {"completion": {"counter": " "}}, {"run": {"timeout_seconds": float("nan")}},
                       {"run": {"max_queued": 0}}):
            with self.subTest(change=change), self.assertRaises((TypeError, ValueError)):
                await project.aconfigure_policies(change)
            self.assertEqual((await project.aget_data()).config, before)
        with self.assertRaises(ValueError):
            await project.sessions.acreate(config={"policies": {"run": {"max_queued": 100}}})

    async def test_different_projects_select_their_own_context_without_deleting_history(self):
        recent, one = await self.create({"context": {"mode": "recent", "max_turns": 1}})
        full, two = await self.create()
        for text in ("one", "two", "three"):
            results = await asyncio.gather(self.execute(one, text), self.execute(two, text))
            self.assertTrue(all(r.data.status == RunStatus.COMPLETED for r in results))
        recent_seen = [messages for identity, messages, _ in self.seen if identity == recent.id]
        full_seen = [messages for identity, messages, _ in self.seen if identity == full.id]
        self.assertEqual(recent_seen[-1], ["two", "answer", "three"])
        self.assertEqual(full_seen[-1], ["one", "answer", "two", "answer", "three"])
        self.assertEqual(len(await one.aconversation()), 6)
        self.assertEqual(len(await two.aconversation()), 6)

    async def test_two_projects_apply_independent_completion_limits(self):
        small, one = await self.create()
        large, two = await self.create()
        model = ScriptedCompletion([chunk("ok", finish="stop")])
        self.app.engines.register("loop", LoopEngine(completion_fn=model, completion_kwargs={"model": "test/main"}))
        await configure_engine(small, "loop", input_policy={"max_tokens": 2, "counter": "length"})
        await configure_engine(large, "loop", input_policy={"max_tokens": 100, "counter": "length"})
        failed, passed = await asyncio.gather(self.execute(one, "hello", "loop"), self.execute(two, "hello", "loop"))
        self.assertEqual((await failed.aresult()).error_code, "context_budget_exceeded")
        self.assertEqual(passed.data.status, RunStatus.COMPLETED)
        self.assertEqual(len(model.requests), 1)
        self.assertNotIn("completion", failed.data.metadata["policies"])

    async def test_unknown_counter_is_rejected_before_queue_admission(self):
        project, session = await self.create({"usage": {"max_tokens": 10, "counter": "missing"}})
        with self.assertRaises(RunRequestError) as caught:
            await session.run.submit("never queued", engine="inspect")
        self.assertEqual(caught.exception.code, "policy_unavailable")
        self.assertEqual(await session.aconversation(), [])
        self.assertEqual(await session.run.alist(), [])
        await project.aconfigure_policies({"usage": {"max_tokens": None}})
        self.assertEqual((await self.execute(session)).data.status, RunStatus.COMPLETED)

    async def test_running_policy_is_fixed_and_queued_request_uses_latest(self):
        entered, release = asyncio.Event(), asyncio.Event()
        snapshots = []
        class Hold(LoopEngine):
            async def _execute(self, context):
                snapshots.append(context.completion_policy.max_tokens)
                entered.set()
                await release.wait()
                snapshots.append(context.completion_policy.max_tokens)
                from llm.core.results import EngineOutput
                yield self.output_event(context, EngineOutput(text="done"))
        self.app.engines.register("hold", Hold())
        project, session = await self.create()
        await configure_engine(project, "hold", input_policy={"max_tokens": 100, "counter": "length"})
        first = await session.run.submit("first", engine="hold")
        await asyncio.wait_for(entered.wait(), 5)
        second = await session.run.submit("second", engine="hold")
        await configure_engine(project, "hold", input_policy={"max_tokens": 20})
        await project.aconfigure_policies({"run": {"max_queued": 1}})
        with self.assertRaises(RunRequestError) as caught:
            await session.run.submit("overflow", engine="hold")
        self.assertEqual(caught.exception.code, "queue_full")
        release.set()
        runs = await asyncio.gather(first.wait(), second.wait())
        self.assertEqual(snapshots, [100, 100, 20, 20])
        self.assertTrue(all("completion" not in r.data.metadata["policies"] for r in runs))

    async def test_policy_survives_reopen_and_clone_and_backup(self):
        policies = {"context": {"mode": "recent_completed", "max_turns": 3},
                    "usage": {"max_tokens": 30, "counter": "length"}}
        project, session = await self.create(policies)
        await self.execute(session)
        expected = (await project.aget_data()).config.policies
        await session.run.shutdown()
        clone = await project.aclone()
        self.assertEqual((await clone.aget_data()).config.policies, expected)
        destination = self.root / "archive"
        await project.abackup(destination)
        await self.app.shutdown()
        self.app = LargeLanguageModel(self.root, components=[], engines={"inspect": self.engine},
            services=ServiceConfig(token_counters={"length": lambda r: 1}))
        self.addAsyncCleanup(self.app.shutdown)
        reopened = await self.app.projects.aload(project.id)
        self.assertEqual((await reopened.aget_data()).config.policies, expected)
        session = await reopened.sessions.aload(session.id)
        self.assertEqual((await self.execute(session)).data.metadata["policies"], expected)
        restored_app = LargeLanguageModel(self.root / "restored", components=[], engines={"inspect": self.engine},
            services=ServiceConfig(token_counters={"length": lambda r: 1}))
        self.addAsyncCleanup(restored_app.shutdown)
        restored = await restored_app.projects.arestore_backup(destination)
        self.assertEqual((await restored.aget_data()).config.policies, expected)

    async def test_policy_dependency_changed_while_queued_fails_run_without_stopping_worker(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Hold(BaseEngine):
            async def run(self, context):
                entered.set()
                await release.wait()
                yield "done"
        self.app.engines.register("hold", Hold())
        project, session = await self.create()
        first = await session.run.submit("first", engine="hold")
        await asyncio.wait_for(entered.wait(), 5)
        second = await session.run.submit("second", engine="inspect")
        await project.aconfigure_policies({"usage": {"max_tokens": 10, "counter": "missing"}})
        release.set()
        self.assertEqual((await first.wait()).data.status, RunStatus.COMPLETED)
        failed = await second.wait()
        self.assertEqual((await failed.aresult()).error_code, "policy_unavailable")
        await project.aconfigure_policies({"usage": {"counter": "length"}})
        self.assertEqual((await self.execute(session)).data.status, RunStatus.COMPLETED)

    async def test_run_deadline_is_project_specific(self):
        class Slow(BaseEngine):
            async def run(self, context):
                await asyncio.sleep(.5)
                yield "done"
        self.app.engines.register("slow", Slow())
        _, short = await self.create({"run": {"timeout_seconds": .05}})
        _, long = await self.create({"run": {"timeout_seconds": 5}})
        failed, completed = await asyncio.gather(self.execute(short, engine="slow"), self.execute(long, engine="slow"))
        self.assertEqual((await failed.aresult()).error_code, "run_timeout")
        self.assertEqual(completed.data.status, RunStatus.COMPLETED)

    async def test_default_counter_receives_model_messages_and_tools(self):
        request = {"model": "openai/example", "messages": [{"role": "user", "content": "q"}],
                   "tools": [{"type": "function", "function": {"name": "test"}}], "tool_choice": "auto"}
        with patch("litellm.token_counter", return_value=17) as counter:
            self.assertEqual(model_token_count(request), 17)
        counter.assert_called_once_with(**request)

    async def test_completion_policy_name_has_no_legacy_alias(self):
        import llm.policies as module
        self.assertTrue(hasattr(module, "CompletionPolicy"))
        self.assertFalse(hasattr(module, "Completion" + "Budget"))
