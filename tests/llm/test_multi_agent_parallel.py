"""Real barriers, per-Agent FIFO, isolated cancellation and shared quota."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest

from llm.core.models import RunStatus, SessionStatus
from llm.engines.base import BaseEngine
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class ParallelAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    async def setup_app(self, engine, *, policies=None):
        self.app = LargeLanguageModel(self.root / "workspace", engines={"work": engine})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(config={"policies": {} if policies is None else policies})
        for identifier in ("a", "b"):
            definition = {"engine": "work"}
            if isinstance(engine, LoopEngine):
                definition["completion"] = {"model": "test/model"}
            await self.project.agents.acreate(definition, identifier=identifier)
        self.session = await self.project.sessions.acreate(agent_ids=["a", "b"])

    async def test_distinct_agents_reach_barrier_together_and_cancel_independently(self):
        entered = {identifier: asyncio.Event() for identifier in ("a", "b")}
        release = {identifier: asyncio.Event() for identifier in ("a", "b")}
        contexts = {}
        effects = self.root / "effects.txt"
        async def action(context):
            identifier = context.run.agent_id
            contexts[identifier] = context
            entered[identifier].set()
            await release[identifier].wait()
            with effects.open("a") as stream:
                stream.write(identifier + "\n")
            yield identifier
        await self.setup_app(BaseEngine(action=action))
        request = await self.session.run.submit("parallel", recipients="all")
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 10)
        status = await self.session.run.astatus()
        self.assertEqual({run.agent_id for run in status.active_runs}, {"a", "b"})
        self.assertEqual(len(self.session.data.active_run_ids), 2)
        self.assertEqual(self.session.data.status, SessionStatus.RUNNING)
        self.assertIsNot(contexts["a"].tool_scope, contexts["b"].tool_scope)
        self.assertEqual([message.content for message in contexts["a"].messages],
                         [message.content for message in contexts["b"].messages])
        first_id = contexts["a"].run.id
        self.assertTrue(await self.session.run.interrupt(first_id))
        self.assertFalse(await self.session.run.interrupt(first_id))
        status = await self.session.run.astatus()
        self.assertEqual([run.agent_id for run in status.active_runs], ["b"])
        self.assertEqual(self.session.data.status, SessionStatus.RUNNING)
        self.assertFalse(effects.exists())
        release["b"].set()
        runs = await request.wait(timeout=10)
        self.assertEqual([run.data.status for run in runs], [RunStatus.INTERRUPTED, RunStatus.COMPLETED])
        self.assertEqual(effects.read_text().splitlines(), ["b"])
        self.assertEqual(self.session.data.active_run_ids, [])
        self.assertEqual(self.session.data.status, SessionStatus.IDLE)

    async def test_context_budget_failure_cannot_interrupt_another_agent_or_its_effect(self):
        entered, release = asyncio.Event(), asyncio.Event()
        effects = self.root / "budget-effects.txt"
        calls = []
        async def action(context):
            calls.append(context.run.agent_id)
            if context.run.agent_id == "a":
                entered.set()
                await release.wait()
            with effects.open("a") as stream:
                stream.write(context.run.agent_id + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            yield "done"
        await self.setup_app(BaseEngine(action=action), policies={"context": {"mode": "budget", "max_chars": 8}})
        self.addCleanup(release.set)
        first = await self.session.run.submit("short", recipients=["a"])
        await asyncio.wait_for(entered.wait(), 10)
        rejected = await self.session.run.submit("longer than eight characters", recipients=["b"])
        failed, = await rejected.wait(timeout=10)
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        self.assertIn("context character budget", failed.data.error)
        self.assertEqual(calls, ["a"])
        self.assertFalse(effects.exists())
        self.assertEqual([run.agent_id for run in (await self.session.run.astatus()).active_runs], ["a"])
        release.set()
        completed, = await first.wait(timeout=10)
        self.assertEqual(completed.data.status, RunStatus.COMPLETED, completed.data.error)
        self.assertEqual(effects.read_text(), "a\n")
        following, = await (await self.session.run.submit("next", recipients=["b"])).wait(timeout=10)
        self.assertEqual(following.data.status, RunStatus.COMPLETED, following.data.error)
        self.assertEqual(effects.read_text(), "a\nb\n")

    async def test_fifo_predecessor_cannot_be_overtaken_by_younger_mixed_group(self):
        keys = (("a", "one"), ("a", "two"), ("a", "three"), ("b", "three"))
        entered = {key: asyncio.Event() for key in keys}
        release = {key: asyncio.Event() for key in keys}
        observations, order = {}, []
        async def action(context):
            key = (context.run.agent_id, context.messages[-1].content)
            observations[key] = [(message.id, message.content) for message in context.messages]
            order.append(key)
            entered[key].set()
            await release[key].wait()
            yield ":".join(key)
        await self.setup_app(BaseEngine(action=action))
        first = await self.session.run.submit("one", recipients=["a"])
        await asyncio.wait_for(entered[("a", "one")].wait(), 10)
        second = await self.session.run.submit("two", recipients=["a"])
        third = await self.session.run.submit("three", recipients="all")
        await asyncio.wait_for(entered[("b", "three")].wait(), 10)
        self.assertFalse(entered[("a", "two")].is_set())
        self.assertFalse(entered[("a", "three")].is_set())
        release[("a", "one")].set()
        await asyncio.wait_for(entered[("a", "two")].wait(), 10)
        self.assertFalse(entered[("a", "three")].is_set())
        release[("a", "two")].set()
        await asyncio.wait_for(entered[("a", "three")].wait(), 10)
        self.assertEqual(observations[("a", "three")], observations[("b", "three")])
        self.assertEqual([key for key in order if key[0] == "a"], [("a", "one"), ("a", "two"), ("a", "three")])
        release[("a", "three")].set()
        release[("b", "three")].set()
        groups = await asyncio.gather(*(request.wait(timeout=10) for request in (first, second, third)))
        self.assertEqual([len(group) for group in groups], [1, 1, 2])
        self.assertTrue(all(run.data.status == RunStatus.COMPLETED for group in groups for run in group))

    async def test_shared_project_call_reservation_admits_one_actual_provider_call(self):
        model = ScriptedCompletion([chunk("done", finish="stop")], [chunk("must not run", finish="stop")])
        await self.setup_app(LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model)),
                             policies={"usage": {"project_max_calls": 1}})
        request = await self.session.run.submit("shared quota", recipients="all")
        runs = await request.wait(timeout=10)
        self.assertEqual(len(model.requests), 1)
        self.assertCountEqual([run.data.status for run in runs], [RunStatus.COMPLETED, RunStatus.FAILED])
        self.assertEqual(sum(len(run.data.metadata["completions"]) for run in runs), 1)
        failed = next(run for run in runs if run.data.status == RunStatus.FAILED)
        self.assertIn("usage", failed.data.error_code)

    async def test_pending_recipient_quota_still_protects_older_receipts_after_policy_edit(self):
        from threading import Event
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        calls = []
        def model(**request):
            calls.append(request)
            if len(calls) == 2:
                entered.set()
                if not release.wait(15):
                    raise TimeoutError("provider fixture was not released")
            return iter([chunk("done", finish="stop")])
        await self.setup_app(LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model)))
        source = self.session
        original, = await (await source.run.submit("old receipt", recipients=["a"])).wait(timeout=10)
        self.assertEqual(original.data.status, RunStatus.COMPLETED, original.data.error)
        await source.run.shutdown()
        session = await self.project.sessions.acreate(agent_ids=["a", "b"])
        hold = await session.run.submit("hold without quota", recipients=["a"])
        self.assertTrue(await asyncio.to_thread(entered.wait, 10))
        try:
            await self.project.aconfigure_policies({"usage": {"project_max_calls": 1}})
            waiting = await session.run.submit("admitted with quota", recipients="all")
            async with asyncio.timeout(10):
                while not (group := await waiting.aget_runs()) or group[1].data.status != RunStatus.FAILED:
                    await asyncio.sleep(0)
            self.assertEqual(group[0].data.status, RunStatus.PENDING)
            await self.project.aconfigure_policies({"usage": {"project_max_calls": None}})
            before = (original.data.paths.root / "run.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "quota"):
                await source.adelete(permanent=True)
            plan = await self.project.aretention_plan("run")
            self.assertIn("active_usage_accounting", next(row for row in plan.protected
                          if row["run_id"] == original.id)["reasons"])
            self.assertEqual((original.data.paths.root / "run.json").read_bytes(), before)
            self.assertEqual(len(calls), 2)
        finally:
            release.set()
        self.assertEqual((await hold.wait(timeout=10))[0].data.status, RunStatus.COMPLETED)
        self.assertTrue(all(run.data.error_code == "usage_limit" for run in await waiting.wait(timeout=10)))
        self.assertEqual(len(calls), 2)

    async def test_parallel_agents_keep_independent_tool_limits_and_real_effects(self):
        from llm.components.tools import Tool, ToolRegistry
        from tests.llm.support.runtime_tools import RuntimeTools, TEST_CONTRACT
        effects = self.root / "tool-effects.txt"
        calls = {identifier: 0 for identifier in ("a", "b")}
        def model(**request):
            identifier = request["model"].split("/")[-1]
            calls[identifier] += 1
            value = f"{identifier}-{calls[identifier]}"
            return iter([chunk(calls=[call(json.dumps({"value": value}), name="effect", call_id=value)],
                               finish="tool_calls")])
        async def effect(arguments):
            with effects.open("a") as stream:
                stream.write(arguments["value"] + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return {"written": arguments["value"]}
        tools = RuntimeTools(ToolRegistry([Tool("effect", "effect", {
            "type": "object", "properties": {"value": {"type": "string"}},
            "required": ["value"], "additionalProperties": False}, effect, contract=TEST_CONTRACT)]))
        app = LargeLanguageModel(self.root / "tools", engines={"work": LoopEngine(
            completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))}, components=[tools])
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate(components=["tools"])
        await (await project.components.aget("tools")).aenable("effect")
        for identifier in calls:
            await project.agents.acreate({"engine": "work", "completion": {"model": "test/" + identifier},
                "tools": ["effect"], "policy": {"max_tool_calls": 1}}, identifier=identifier)
        session = await project.sessions.acreate(agent_ids=["a", "b"])
        runs = await (await session.run.submit("limited effects", recipients="all")).wait(timeout=15)
        self.assertTrue(all(run.data.status == RunStatus.FAILED for run in runs), [run.data.error for run in runs])
        self.assertEqual(calls, {"a": 2, "b": 2})
        self.assertCountEqual(effects.read_text().splitlines(), ["a-1", "b-1"],
                              "a recipient escaped or consumed another Agent's Tool budget")
