"""Per-request completion, result navigation and off-loop public operations."""

import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.components.tools import ToolComponent
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.core.models import MessageStatus, RunStatus
from llm.engines.base import BaseEngine
from llm.engines.loop import LoopEngine
from llm.engines.pipeline import PipelineEngine
from llm.services.api import RequestHandle
from llm.services.infrastructure.locking import WorkspaceBusyError
from llm.services.lifecycle.projects import ProjectRepository
from llm.services.runtime.runs import RunRepository
from tests.llm.test_loop import ScriptedCompletion, chunk


class ControlledEngine(BaseEngine):
    def __init__(self):
        super().__init__("Controlled", kind="test")
        self.gates = {}
        self.entered = {name: asyncio.Event() for name in ("first", "second", "held")}

    async def run(self, context):
        text = context.messages[-1].content
        if text in self.entered:
            self.entered[text].set()
        yield "answer:" + text
        if text in self.gates:
            await self.gates[text].wait()
        if text == "fail":
            raise ValueError("requested failure")


class FacadeRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.engine = ControlledEngine()
        self.app = LargeLanguageModel(self.root, engines={"test": self.engine},
            components=[ToolComponent(), WorkflowComponent()])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("test")
        self.session = await self.project.sessions.acreate("test")

    async def test_wait_returns_only_its_request_with_multiple_waiters(self):
        self.engine.gates = {name: asyncio.Event() for name in ("first", "second")}
        first = await self.session.run.submit("first", engine="test")
        second = await self.session.run.submit("second", engine="test")
        self.assertIsInstance(first, RequestHandle)
        self.assertIsNone(await second.aresult())
        self.assertIsNone(await second.aget_run())
        waiters = [asyncio.create_task(first.wait(timeout=5)) for _ in range(3)]
        self.engine.gates["first"].set()
        runs = await asyncio.gather(*waiters)
        self.assertEqual(len({run.id for run in runs}), 1)
        run = runs[0]
        self.assertEqual((await run.aget_data()).input_message_id, first.id)
        self.assertEqual((await run.aresponse()).content, "answer:first")
        self.assertEqual((await run.aresult()).status, RunStatus.COMPLETED)
        await asyncio.wait_for(self.engine.entered["second"].wait(), 5)
        self.assertEqual((await second.aresult()).status, RunStatus.RUNNING)
        self.engine.gates["second"].set()
        self.assertNotEqual((await second.wait(timeout=5)).id, run.id)
        # Repeated waiting reads the same persisted result.
        self.assertEqual((await first.wait()).id, run.id)

    async def test_timeout_and_cancelled_wait_do_not_cancel_execution(self):
        self.engine.gates["held"] = asyncio.Event()
        request = await self.session.run.submit("held", engine="test")
        await asyncio.wait_for(self.engine.entered["held"].wait(), 5)
        with self.assertRaises(asyncio.TimeoutError):
            await request.wait(timeout=0.01)
        waiting = asyncio.create_task(request.wait())
        await asyncio.sleep(0.02)
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        self.assertEqual((await request.aresult()).status, RunStatus.RUNNING)
        self.engine.gates["held"].set()
        run = await request.wait(timeout=5)
        self.assertEqual((await run.aresult()).status, RunStatus.COMPLETED)

    async def test_failures_and_interrupts_are_terminal_results(self):
        failed = await self.session.run.submit("fail", engine="test")
        run = await failed.wait(timeout=5)
        self.assertEqual((await run.aresult()).status, RunStatus.FAILED)
        self.assertEqual((await run.aresponse()).content, "answer:fail")
        self.assertEqual((await run.aresult()).error_code, "engine_failed")
        self.engine.gates["held"] = asyncio.Event()
        held = await self.session.run.submit("held", engine="test")
        await asyncio.wait_for(self.engine.entered["held"].wait(), 5)
        waiting = asyncio.create_task(held.wait(timeout=5))
        self.assertTrue(await self.session.run.interrupt())
        stopped = await waiting
        self.assertEqual((await stopped.aresult()).status, RunStatus.INTERRUPTED)
        self.assertEqual((await stopped.aresponse()).status, MessageStatus.INTERRUPTED)

    async def test_shutdown_releases_waiters_and_reopened_request_recovers_queue(self):
        self.engine.gates["held"] = asyncio.Event()
        active = await self.session.run.submit("held", engine="test")
        await asyncio.wait_for(self.engine.entered["held"].wait(), 5)
        queued = await self.session.run.submit("second", engine="test")
        waiting = asyncio.create_task(queued.wait())
        async with asyncio.timeout(5):
            while self.app._manager(self.session._snapshot)._request_changed is None:
                await asyncio.sleep(0.005)
        await self.session.run.shutdown()
        with self.assertRaisesRegex(RuntimeError, "stopped"):
            await asyncio.wait_for(waiting, 5)
        self.assertEqual((await queued.aget_data()).status, MessageStatus.QUEUED)
        self.assertEqual((await active.aresult()).status, RunStatus.INTERRUPTED)
        await self.app.shutdown()
        async with LargeLanguageModel(self.root, engines={"test": ControlledEngine()}, components=[]) as fresh:
            project = await fresh.projects.aload(self.project.id)
            session = await project.sessions.aload(self.session.id)
            request = await session.run.arequest(queued.id)
            run = await request.wait(timeout=5)
            self.assertEqual((await run.aresponse()).content, "answer:second")
            self.assertEqual((await run.aresult()).status, RunStatus.COMPLETED)
            past = await session.run.arequest(active.id)
            self.assertEqual((await (await past.wait()).aresult()).status, RunStatus.INTERRUPTED)

    async def test_unknown_other_session_and_assistant_ids_are_rejected(self):
        request = await self.session.run.submit("ok", engine="test")
        run = await request.wait(timeout=5)
        other = await self.project.sessions.acreate("other")
        with self.assertRaises(KeyError):
            await other.run.arequest(request.id)
        with self.assertRaises(ValueError):
            await self.session.run.arequest((await run.aget_data()).assistant_message_id)
        with self.assertRaises(KeyError):
            await RequestHandle(self.session, "missing").wait(timeout=5)
        with self.assertRaises(FileNotFoundError):
            await other.results.aload(run.id)

    async def test_wait_recovers_stale_run_without_replaying_engine(self):
        request = await self.session.run.submit("once", engine="test")
        run = await request.wait(timeout=5)
        await self.session.run.shutdown()
        state = await run.aget_data()
        state.status = RunStatus.RUNNING
        with self.app.project_manager.ownership.scope():
            RunRepository().save(state)
        with patch.object(self.engine, "execute", side_effect=AssertionError("must not replay")):
            recovered = await request.wait(timeout=5)
        self.assertEqual(recovered.id, run.id)
        self.assertEqual((await recovered.aresult()).status, RunStatus.INTERRUPTED)
        self.assertEqual((await recovered.aresult()).error_code, "process_restart")

    async def test_storage_failure_wakes_request_wait_without_success_claim(self):
        self.engine.gates["held"] = asyncio.Event()
        request = await self.session.run.submit("held", engine="test")
        await asyncio.wait_for(self.engine.entered["held"].wait(), 5)
        manager = self.app._manager(self.session._snapshot)
        with patch.object(manager, "_finish", side_effect=OSError("finalization failed")):
            waiting = asyncio.create_task(request.wait(timeout=5))
            self.engine.gates["held"].set()
            with self.assertRaisesRegex(OSError, "finalization failed"):
                await waiting
            with self.assertRaises(OSError):
                await self.app.shutdown()

    async def test_result_queries_exclude_active_runs_unless_requested(self):
        self.engine.gates["held"] = asyncio.Event()
        request = await self.session.run.submit("held", engine="test")
        await asyncio.wait_for(self.engine.entered["held"].wait(), 5)
        self.assertEqual(await self.project.results.alist(), [])
        self.assertEqual((await self.session.results.alist(include_running=True))[0].status, RunStatus.RUNNING)
        self.engine.gates["held"].set()
        await request.wait(timeout=5)
        self.assertEqual(len(await self.project.results.alist()), 1)

    async def test_fast_completions_have_no_lost_notifications(self):
        requests = [await self.session.run.submit(str(i), engine="test") for i in range(8)]
        runs = await asyncio.gather(*(request.wait(timeout=10) for request in requests))
        self.assertEqual(len({run.id for run in runs}), 8)
        for index, run in enumerate(runs):
            self.assertEqual((await run.aresponse()).content, "answer:" + str(index))

    async def test_scoped_result_queries_and_steps_do_not_create_runtime(self):
        request = await self.session.run.submit("ok", engine="test")
        run = await request.wait(timeout=5)
        self.assertEqual(run.result, await run.aresult())
        self.assertEqual((await self.project.results.aload(run.id)).run_id, run.id)
        self.assertEqual((await self.session.results.alist())[0].run_id, run.id)
        steps = await run.steps.alist()
        self.assertEqual((await run.steps.aload(steps[0].id)).id, steps[0].id)
        self.assertEqual((await self.session.run.aload(run.id)).id, run.id)
        self.assertEqual((await self.session.run.alist())[0].id, run.id)
        await self.session.run.shutdown()
        await self.session.adelete()
        self.assertEqual(await self.project.results.alist(), [])
        self.assertEqual((await self.project.results.alist(include_deleted=True))[0].run_id, run.id)
        self.assertEqual(self.app._managers, {})
        await self.session.arestore()
        self.assertEqual((await self.session.aconversation())[-1].content, "answer:ok")

    async def test_async_lifecycle_and_components_preserve_generic_and_tool_apis(self):
        await self.project.asave(title="edited")
        self.assertEqual((await self.project.aget_data()).title, "edited")
        await self.session.asave(config={"custom": 1})
        self.assertEqual((await self.session.aget_data()).config, {"custom": 1})
        await self.project.components.aselect(["tools", "workflows"])
        tools = await self.project.components.aget("tools")
        await tools.aconfigure({'config': {'enabled': [], 'extension': 3}})
        await tools.acreate({"source": "# editable"}, identifier="offline")
        await tools.aenable("offline")
        self.assertEqual(await tools.aenabled(), ["offline"])
        await tools.adisable("offline")
        await tools.aset_enabled([])
        self.assertEqual((await tools.aconfiguration())["config"]["extension"], 3)
        graphs = await self.project.components.aget("workflows")
        identifier = await graphs.acreate(WorkflowGraph(entry="end").node("end", "end").to_dict())
        await graphs.aupdate(identifier, {"custom": 1})
        self.assertEqual((await graphs.aload(identifier))["custom"], 1)
        replacement = WorkflowGraph(entry="end", replacement=True).node("end", "end").to_dict()
        await graphs.asave(identifier, replacement)
        self.assertEqual(await graphs.alist(), {identifier: replacement})
        await graphs.adelete(identifier)
        copy = await self.session.aclone()
        await copy.adelete(permanent=True)
        copied_project = await self.project.aclone()
        await copied_project.adelete()
        await copied_project.arestore()
        await copied_project.adelete(permanent=True)
        self.assertEqual(len(await self.app.projects.alist()), 1)
        self.assertEqual(len(await self.project.sessions.alist()), 1)
        await self.project.components.aremove("tools")
        with self.assertRaises(ValueError):
            await tools.aenable("disabled")
        await self.app.shutdown()
        with self.assertRaises(RuntimeError):
            await graphs.alist()

    async def test_slow_crud_stays_off_loop_and_shutdown_drains_cancelled_write(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.app.project_manager.repository.save
        def slow_save(project):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("event loop did not release the write")
            original(project)
        with patch.object(self.app.project_manager.repository, "save", side_effect=slow_save):
            creation = asyncio.create_task(self.app.projects.acreate("slow"))
            await asyncio.wait_for(asyncio.to_thread(entered.wait, 3), 4)
            self.assertTrue(entered.is_set())
            self.assertFalse(creation.done())
            creation.cancel()
            closing = asyncio.create_task(self.app.shutdown())
            while self.app._closing is None:
                await asyncio.sleep(0)
            self.assertFalse(closing.done())
            with self.assertRaises(RuntimeError):
                await self.app.projects.alist()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await creation
            await asyncio.wait_for(closing, 5)
        async with LargeLanguageModel(self.root, components=[]) as fresh:
            titles = [(await item.aget_data()).title for item in await fresh.projects.alist()]
            self.assertIn("slow", titles)

    async def test_async_operations_obey_workspace_ownership(self):
        competing = ProjectRepository(self.app.project_manager.repository.root)
        with competing.ownership.scope():
            with self.assertRaises(WorkspaceBusyError):
                await self.app.projects.alist()

    async def test_alias_configuration_and_completion_results(self):
        usage = {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}
        provider = ScriptedCompletion([chunk("alpha", finish="stop"), usage],
                                      [chunk("beta", finish="stop"), usage])
        shared = LoopEngine(completion_fn=provider)
        self.app.engines.register("alpha", shared)
        self.app.engines.register("beta", shared)
        await self.project.asave(config={"parameters": {"engines": {"alpha": {'config': {'system_prompt': 'project alpha', 'completion': {'model': 'openai/test'}}, 'policy': {'request_timeout': 12}}, "beta": {'config': {'system_prompt': 'project beta', 'completion': {'model': 'openai/test'}}, 'policy': {'request_timeout': 23}}, "loop": {'config': {'system_prompt': 'must not use', 'completion': {'model': 'openai/test'}}, 'policy': {'request_timeout': 99}}}}})
        await self.session.asave(config={"parameters": {"engines": {"alpha": {'config': {'system_prompt': 'session alpha'}}}}})
        other = await self.project.sessions.acreate("other")
        a, b = await asyncio.gather(self.session.run.submit("a", engine="alpha"),
                                    other.run.submit("b", engine="beta"))
        runs = await asyncio.gather(a.wait(timeout=5), b.wait(timeout=5))
        self.assertEqual({r["messages"][0]["content"] for r in provider.requests},
                         {"session alpha", "project beta"})
        self.assertTrue(all("timeout" not in r for r in provider.requests))
        self.assertIsNone(shared.settings_name)
        for run in runs:
            result = await run.aresult()
            self.assertEqual(result.total_tokens, 5)
            self.assertEqual(result.finish_reasons, ["stop"])
        self.assertEqual(len(await self.project.results.alist()), 2)
        self.assertEqual(len(await self.session.results.alist()), 1)

    async def test_pipeline_can_choose_explicit_stage_configuration(self):
        provider = ScriptedCompletion([chunk("one", finish="stop")], [chunk("two", finish="stop")])
        await self.project.asave(config={"parameters": {"engines": {
            "pipeline": {'config': {'stages': {'0': {'config': {'system_prompt': 'outer', 'completion': {'model': 'openai/test'}}}}}},
            "stage": {'config': {'system_prompt': 'inner', 'completion': {'model': 'openai/test'}}}}}})
        self.app.engines.register("pipeline", PipelineEngine([
            LoopEngine(completion_fn=provider),
            LoopEngine(completion_fn=provider, settings_name="stage"),
        ]))
        run = await (await self.session.run.submit("go", engine="pipeline")).wait(timeout=5)
        self.assertEqual((await run.aresult()).status, RunStatus.COMPLETED)
        self.assertEqual([request["messages"][0]["content"] for request in provider.requests],
                         ["outer", "inner"])
        self.assertEqual([step.kind for step in await run.steps.alist()], ["engine", "llm", "engine", "llm"])
        for invalid in ("", " ", 1):
            with self.assertRaises(ValueError):
                LoopEngine(settings_name=invalid)
