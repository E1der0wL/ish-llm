"""프로세스 강제 종료와 영속 작업 키를 실제 OS/Run 서비스에서 검증한다."""

import asyncio
import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.llm.support.runtime_tools import RuntimeTools
from llm.components.tools import Tool, ToolComponent, ToolRegistry
from llm.core.models import RunStatus
from llm.engines.graph import GraphEngine
from llm.engines.graph.tool import ToolNode
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from contextlib import aclosing
from llm.llm import LargeLanguageModel
from llm.services.composition import BackendServices
from llm.services.runtime.tools import ToolCall, ToolExecutor, ToolRuntime
from llm.services.runtime.processes import ProcessToolRunner
from llm.policies import ExecutionLimitError


class ToolEngine:
    required_capabilities = ("tools",)

    def __init__(self, arguments=None):
        self.arguments = arguments or {"operation": "invoice-1"}

    async def execute(self, context):
        tool = context.tools.get("external")
        async with aclosing(ToolExecutor(timeout_seconds=3).execute(
                tool, self.arguments, result={}, context=context)) as events:
            async for event in events:
                yield event


class OperationTests(unittest.IsolatedAsyncioTestCase):
    async def test_receipt_and_step_save_fail_together_without_replaying_effect(self):
        original = self.app.step_manager.save
        def fail_completion(step):
            if step.metadata.get("operation_receipt", {}).get("status") == "completed":
                raise OSError("step receipt failed")
            return original(step)
        with patch.object(self.app.step_manager, "save", fail_completion):
            failed = await self.execute()
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        receipt = await self.session.run.aoperation("invoice-1")
        self.assertEqual(receipt["status"], "started")
        steps = await failed.steps.alist()
        self.assertEqual(steps[0].metadata["operation_receipt"]["status"], "started")
        blocked = await self.execute()
        self.assertEqual(blocked.result.error_code, "operation_uncertain")
        self.assertEqual(len(self.calls), 1)
        await self.session.run.shutdown()
        await self.session.run.areconcile_operation("invoice-1", result=None, evidence="externally confirmed")
        self.assertEqual((await self.session.run.aoperation("invoice-1"))["status"], "completed")
        self.assertEqual((await failed.steps.alist())[0].metadata["operation_receipt"]["status"], "completed")

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []

        async def handler(args):
            self.calls.append(args)
            return {"receipt": "receipt-1"}

        self.engine = ToolEngine()
        self.tools = RuntimeTools(ToolRegistry([Tool("external", "external", {"type": "object"}, handler)]))
        self.services = BackendServices(tool_runtime=ToolRuntime(operation_key=lambda call: call.arguments["operation"]))
        self.app = self.open()
        self.project = await self.app.projects.acreate("test", components=["tools"])
        await (await self.project.components.aget("tools")).aenable("external")
        self.session = await self.project.sessions.acreate()

    def open(self):
        app = LargeLanguageModel(self.root, components=[self.tools], engines={"tool": self.engine}, services=self.services)
        self.addAsyncCleanup(app.shutdown)
        return app

    async def execute(self):
        return await (await self.session.run.submit("perform", engine="tool")).wait(timeout=10)

    async def test_completed_operation_reused_after_backend_restart(self):
        first = await self.execute()
        self.assertEqual(first.data.status, RunStatus.COMPLETED)
        token = (await self.session.run.aoperation("invoice-1"))["idempotency_key"]
        await self.app.shutdown()
        self.app = self.open()
        self.session = await (await self.app.projects.aload(self.project.id)).sessions.aload(self.session.id)
        second = await self.execute()
        self.assertEqual(second.data.status, RunStatus.COMPLETED)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue((await second.steps.alist())[0].metadata["reused"])
        self.assertEqual((await self.session.run.aoperation("invoice-1"))["idempotency_key"], token)

    async def test_same_key_different_input_is_conflict(self):
        await self.execute()
        self.engine.arguments = {"operation": "invoice-1", "amount": 99}
        result = await self.execute()
        self.assertEqual(result.result.error_code, "operation_conflict")
        self.assertEqual(len(self.calls), 1)

    async def test_claim_write_failure_has_no_effect(self):
        with patch.object(self.app.run_repository, "claim_tool_operation", side_effect=OSError("disk full")):
            run = await self.execute()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(self.calls, [])

    async def test_uncertain_completion_blocks_reexecution_and_requires_evidence(self):
        with patch.object(self.app.run_repository, "complete_tool_operation", side_effect=OSError("disk full")):
            failed = await self.execute()
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        blocked = await self.execute()
        self.assertEqual(blocked.result.error_code, "operation_uncertain")
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(ValueError):
            await self.session.run.areconcile_operation("invoice-1", result={}, evidence="checked")
        await self.session.run.shutdown()
        with self.assertRaises(ValueError):
            await self.session.run.areconcile_operation("invoice-1", result={}, evidence="")
        await self.session.run.areconcile_operation("invoice-1", result={"receipt": "receipt-1"}, evidence="remote GET receipt-1 confirmed")
        self.assertEqual((await self.execute()).data.status, RunStatus.COMPLETED)
        self.assertEqual(len(self.calls), 1)

    async def test_parallel_same_key_calls_do_not_both_execute(self):
        # 첫 SDK import는 WSL의 호스트 마운트에서 오래 걸릴 수 있다.
        # 아래 10초 기한은 병렬 실행/취소 완료를 검증하며 SDK 로딩은 포함하지 않는다.
        await asyncio.to_thread(importlib.import_module, "langgraph.graph")
        async def runner(tool, call):
            self.calls.append(call.idempotency_key)
            await asyncio.Event().wait()
        self.app.services.tool_runtime = ToolRuntime(operation_key=lambda call: "same", runner=runner)
        self.app.project_manager.components.register(WorkflowComponent())
        await self.project.components.aselect(["tools", "workflows"])
        graph = (WorkflowGraph(entry="fork").node("fork", "parallel", join="join")
                 .node("a", "tool", tool="external").node("b", "tool", tool="external")
                 .node("join", "join").node("end", "end").connect("fork", "a").connect("fork", "b")
                 .connect("a", "join").connect("b", "join").connect("join", "end").to_dict())
        await (await self.project.components.aget("workflows")).acreate(graph, identifier="flow")
        self.app.engines.register("parallel", GraphEngine(handlers={"tool": ToolNode()}))
        run = await (await self.session.run.submit("test", engine="parallel", engine_options={"workflow": "flow"})).wait(timeout=10)
        self.assertEqual(run.result.error_code, "operation_uncertain")
        self.assertLessEqual(len(self.calls), 1)

    async def test_real_crash_after_effect_blocks_replay_on_restart(self):
        await self.app.shutdown()
        child = await asyncio.create_subprocess_exec(sys.executable, "-m", "tests.llm.support.operation_crash_worker",
            str(self.root), self.project.id, self.session.id, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, error = await asyncio.wait_for(child.communicate(), 15)
        self.assertEqual(child.returncode, 23, error.decode(errors="replace"))
        self.app = self.open()
        self.session = await (await self.app.projects.aload(self.project.id)).sessions.aload(self.session.id)
        result = await self.execute()
        self.assertEqual(result.result.error_code, "operation_uncertain")
        self.assertEqual((self.root / "external-effect.txt").read_text(), "effect\n")
        self.assertEqual(self.calls, [])

    async def test_runner_receives_stable_external_idempotency_key(self):
        tokens = []
        async def runner(tool, call):
            tokens.append(call.idempotency_key)
            return {"receipt": "remote-receipt"}
        self.app.services.tool_runtime = ToolRuntime(operation_key=lambda call: "business-key", runner=runner)
        await self.execute()
        await self.execute()
        self.assertEqual(tokens, [(await self.session.run.aoperation("business-key"))["idempotency_key"]])

    async def test_process_runner_uses_same_run_and_step_path(self):
        code = "import json,sys; x=json.load(sys.stdin); print(json.dumps({'receipt':x['idempotency_key']}))"
        runner = ProcessToolRunner({"external": [sys.executable, "-I", "-c", code]}, cwd=self.root, isolation="process", allow_network=False)
        self.app.services.tool_runtime = ToolRuntime(operation_key=lambda call: "integrated", runner=runner)
        run = await self.execute()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        steps = await run.steps.alist()
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].output.data["receipt"], (await self.session.run.aoperation("integrated"))["idempotency_key"])


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.call = ToolCall("worker", {"text": "한글"}, "step", idempotency_key="stable-key")

    def runner(self, code, **options):
        return ProcessToolRunner({"worker": [sys.executable, "-I", "-c", code]}, cwd=self.root,
                                 isolation="process", **options, allow_network=False)

    async def test_json_protocol_and_clean_environment(self):
        runner = self.runner("import json,sys,os; d=json.load(sys.stdin); print(json.dumps({'value':d['arguments'], 'key':d['idempotency_key'], 'leak':os.environ.get('ISH_TEST_LEAK')}))")
        with patch.dict(os.environ, {"ISH_TEST_LEAK": "must not inherit"}):
            value = await runner(None, self.call)
        self.assertEqual(value, {"value": {"text": "한글"}, "key": "stable-key", "leak": None})

    async def test_nonzero_invalid_json_and_output_flood_fail(self):
        for code in ("import sys;sys.exit(7)", "print('not json')", "print('x'*1000000)"):
            with self.subTest(code=code), self.assertRaises(ExecutionLimitError):
                await self.runner(code, max_output_bytes=1000)(None, self.call)

    async def test_timeout_kills_a_noncooperative_worker(self):
        runner = self.runner("import time; time.sleep(60)")
        with self.assertRaises(asyncio.TimeoutError):
            async with asyncio.timeout(.3):
                await runner(None, self.call)

    async def test_cancel_kills_descendant_even_if_it_outlives_its_parent(self):
        marker = self.root / "escaped.txt"
        ready = self.root / "ready.txt"
        child = f"import time,pathlib; time.sleep(1.5); pathlib.Path({str(marker)!r}).write_text('escaped'); time.sleep(60)"
        code = ("import subprocess,sys,pathlib,time; "
                f"subprocess.Popen([sys.executable,'-I','-c',{child!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                f"pathlib.Path({str(ready)!r}).write_text('ready'); time.sleep(60)")
        pending = asyncio.create_task(self.runner(code)(None, self.call))
        try:
            for _ in range(100):
                if ready.exists():
                    break
                if pending.done():
                    await pending
                await asyncio.sleep(.02)
            self.assertTrue(ready.exists())
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 8)
            await asyncio.sleep(1.7)
            self.assertFalse(marker.exists())
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async def test_sandbox_never_falls_back_when_unavailable(self):
        runner = ProcessToolRunner({"worker": [sys.executable, "-c", "print('unsafe')"]}, cwd=self.root, isolation="sandbox", allow_network=False)
        with patch("llm.services.runtime.processes.shutil.which", return_value=None):
            with self.assertRaises(ExecutionLimitError) as raised:
                await runner(None, self.call)
        self.assertEqual(raised.exception.code, "sandbox_unavailable")

    async def test_network_sharing_requires_explicit_host_configuration(self):
        # 명령 조립만 검증한다. 실제 Linux 격리 검증으로 간주하지 않는다.
        for enabled in (False, True):
            runner = ProcessToolRunner({"worker": [sys.executable, "-c", "print(1)"]},
                cwd=self.root, allow_network=enabled, isolation="sandbox")
            with patch("llm.services.runtime.processes.shutil.which", return_value="/usr/bin/bwrap"):
                command = runner._command("worker")
            self.assertEqual("--share-net" in command, enabled)

    async def test_backend_crash_kills_tool_group(self):
        ready, marker = self.root / "ready.txt", self.root / "orphan.txt"
        child_code = (f"import pathlib,time; pathlib.Path({str(ready)!r}).write_text('ready'); "
                      f"time.sleep(2); pathlib.Path({str(marker)!r}).write_text('orphan'); time.sleep(60)")
        source = f'''
import asyncio, os, sys
from pathlib import Path
from llm.services.runtime.processes import ProcessToolRunner
from llm.services.runtime.tools import ToolCall
async def main():
    runner = ProcessToolRunner({{'worker': [sys.executable, '-I', '-c', {child_code!r}]}}, cwd={str(self.root)!r}, isolation='process')
    pending = asyncio.create_task(runner(None, ToolCall('worker', {{}}, 'step')))
    while not Path({str(ready)!r}).exists():
        if pending.done():
            await pending
        await asyncio.sleep(.02)
    os._exit(23)
asyncio.run(main())
'''
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", source,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, error = await asyncio.wait_for(child.communicate(), 10)
            self.assertEqual(child.returncode, 23, error.decode(errors="replace"))
            await asyncio.sleep(2.2)
            self.assertFalse(marker.exists())
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()

    async def test_linux_sandbox_denies_host_file_and_network(self):
        self.assertIsNotNone(shutil.which("bwrap"), "Install Bubblewrap for the Linux isolation test suite")
        secret = self.root.parent / (self.root.name + "-outside")
        secret.write_text("host-only")
        self.addCleanup(secret.unlink)
        server = socket.socket()
        self.addCleanup(server.close)
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        code = ("import json,pathlib,socket; blocked=False; "
                "s=socket.socket(); s.settimeout(.2); "
                f"\ntry: s.connect(('127.0.0.1',{port}))\nexcept OSError: blocked=True\n"
                f"print(json.dumps({{'host_visible':pathlib.Path({str(secret)!r}).exists(),'network_blocked':blocked}}))")
        commands = {"worker": [str(Path(sys.executable).resolve()), "-I", "-c", code]}
        plain = ProcessToolRunner(commands, cwd=self.root, isolation="process", allow_network=False)
        self.assertEqual(await plain(None, self.call), {"host_visible": True, "network_blocked": False})
        runner = ProcessToolRunner(commands, cwd=self.root, read_only_paths=[sys.base_prefix], isolation="sandbox", allow_network=False)
        self.assertEqual(await runner(None, self.call), {"host_visible": False, "network_blocked": True})
