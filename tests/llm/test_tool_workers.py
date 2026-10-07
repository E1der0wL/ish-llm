"""Project source의 interpreter 경계·승인·취소와 오류 계약을 실제 Linux child로 검증한다."""

import asyncio
import builtins
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.components.tools import ToolComponent
from llm.components.tools import process
from llm.components.tools.packages import ToolPaths, load_tool
from llm.services.runtime.tools import ToolExecutionError, ToolApprovalRequired, ToolRuntime
from tests.llm import test_tool_packages as packages_tests


def source(body, imports="", decorator="@tool()"):
    return ('from llm.components.tools import tool\n' + imports + '\n' + decorator +
            '\nasync def main():\n    """Worker fixture."""\n' +
            '\n'.join('    ' + line for line in body.splitlines()) + '\n')


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    setup_runtime = packages_tests.PackageRuntimeTests.setup_runtime

    async def resolve(self, text):
        project, _, _ = await self.setup_runtime(text)
        tool = await asyncio.to_thread(load_tool, await project.aget_data(), "act")
        return project, tool

    async def test_pid_fresh_globals_print_capture_and_minimal_environment(self):
        text = source('global count\ncount += 1\nprint("noise")\nos.write(1, b"raw noise")\n'
                      'return {"pid": os.getpid(), "count": count, "secret": os.environ.get("WORKER_SECRET")}',
                      'import os, builtins\ncount = 0\nbuiltins.PROJECT_TOOL_LEAK = True')
        with patch.dict(os.environ, {"WORKER_SECRET": "must-not-cross"}):
            _, tool = await self.resolve(text)
            first, second = await tool.handler({}), await tool.handler({})
        self.assertNotEqual(first["pid"], os.getpid())
        self.assertNotEqual(first["pid"], second["pid"])
        self.assertEqual((first["count"], second["count"]), (1, 1))
        self.assertIsNone(first["secret"])
        self.assertFalse(hasattr(builtins, "PROJECT_TOOL_LEAK"))

    async def test_fingerprint_rejects_source_and_requirements_changes(self):
        project, tool = await self.resolve(source('return 1'))
        await project.components.tools.asave("act", {"source": source('return 2')})
        with self.assertRaises(ToolExecutionError):
            await tool.handler({})
        tool = await asyncio.to_thread(load_tool, await project.aget_data(), "act")
        await project.components.tools.asave("act", {"source": source('return 2'), "requirements": "# changed"})
        with self.assertRaises(ToolExecutionError):
            await tool.handler({})

    async def test_dynamic_signature_cannot_change_between_inspection_and_execution(self):
        text = '''from llm.components.tools import tool
from pathlib import Path
marker = Path(__file__).parents[2] / "descriptor-fixture"
count = int(marker.read_text()) + 1 if marker.exists() else 1
marker.write_text(str(count))
@tool()
async def main(limit: int = count):
    """Dynamic defaults must not contradict inspected metadata."""
    return limit
'''
        _, tool = await self.resolve(text)
        with self.assertRaises(ToolExecutionError):
            await tool.handler({})

    async def test_crash_output_limit_and_invalid_protocol(self):
        for body, code in [('os._exit(9)', 'tool_worker_failed'),
                           ('os.write(1, b"x" * (2 * 1024 * 1024))', 'tool_worker_output_limit'),
                           ('os._exit(0)', 'tool_worker_protocol')]:
            with self.subTest(code=code):
                _, tool = await self.resolve(source(body, 'import os'))
                with self.assertRaises(process.WorkerError) as raised:
                    await asyncio.wait_for(tool.handler({}), 15)
                self.assertEqual(raised.exception.code, code)

    async def test_cancellation_reaps_worker_and_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "pids"
            text = source('child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
                f'Path({str(marker)!r}).write_text(str(os.getpid()) + " " + str(child.pid))\n'
                'await asyncio.sleep(60)', 'import os, sys, subprocess, asyncio\nfrom pathlib import Path')
            _, tool = await self.resolve(text)
            pending = asyncio.create_task(tool.handler({}))
            try:
                async with asyncio.timeout(15):
                    while not marker.exists():
                        await asyncio.sleep(.02)
                pids = [int(p) for p in marker.read_text().split()]
            finally:
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
            for pid in pids:
                status = Path(f"/proc/{pid}/stat")
                self.assertTrue(not status.exists() or status.read_text().split()[2] == "Z")

    async def test_cancel_prepare_terminates_inspector(self):
        from llm.llm import LargeLanguageModel
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "inspector"
            app = LargeLanguageModel(Path(directory) / "ws", components=[ToolComponent()])
            self.addAsyncCleanup(app.shutdown)
            project = await app.projects.acreate(components=["tools"])
            text = f'import os, time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text(str(os.getpid()))\nwhile True: time.sleep(1)\n'
            await project.components.tools.acreate({"source": text}, identifier="act")
            pending = asyncio.create_task(project.components.tools.aprepare("act"))
            try:
                async with asyncio.timeout(15):
                    while not marker.exists():
                        await asyncio.sleep(.02)
            finally:
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(pending, 10)
            pid = int(marker.read_text())
            status = Path(f"/proc/{pid}/stat")
            self.assertTrue(not status.exists() or status.read_text().split()[2] == "Z")

    async def test_approval_before_spawn_and_deny(self):
        async def ask(call):
            raise ToolApprovalRequired()
        for approve in (True, False):
            project, session, _ = await self.setup_runtime(source('return "approved"',
                decorator='@tool(approval_required=True)'), runtime=ToolRuntime(authorize=ask))
            executed = []
            original = process.invoke_worker
            async def observe(request):
                if request["operation"] == "execute":
                    executed.append(request["tool_name"])
                return await original(request)
            with patch.object(process, "invoke_worker", side_effect=observe):
                paused = await (await session.run.submit("act", engine="loop")).wait()
                self.assertEqual(paused.data.status, "paused", paused.data.error)
                self.assertEqual(executed, [])
                request = (await paused.ainteractions())[0]
                await paused.arespond(request.respond("approve" if approve else "deny"))
                resumed = await (await session.run.resume(paused.id, engine="loop")).wait()
                self.assertEqual(resumed.data.status, "completed" if approve else "failed", resumed.data.error)
                self.assertEqual(len(executed), 1 if approve else 0)

    async def test_graph_confirmation_cannot_spawn_before_tool_approval(self):
        from llm.llm import LargeLanguageModel, BackendServices
        from llm.components.workflows import WorkflowComponent, WorkflowGraph
        from llm.engines.graph import GraphEngine
        from llm.engines.graph.tool import ToolNode
        async def ask(call):
            raise ToolApprovalRequired()
        for approve in (True, False):
            with self.subTest(approve=approve), tempfile.TemporaryDirectory() as directory:
                async with LargeLanguageModel(directory, components=[ToolComponent(), WorkflowComponent()],
                    engines={'graph': GraphEngine(handlers={'tool': ToolNode()})},
                    services=BackendServices(tool_runtime=ToolRuntime(authorize=ask))) as app:
                    project = await app.projects.acreate(components=['tools', 'workflows'])
                    await project.components.tools.acreate({'source': source('return None',
                        decorator='@tool(approval_required=True)')}, identifier='act')
                    await project.components.tools.aprepare('act')
                    await project.components.tools.aenable('act')
                    graph = (WorkflowGraph(entry='act').node('act', 'tool', tool='act', pause_before=True)
                             .node('end', 'end').connect('act', 'end').to_dict())
                    await project.components.workflows.acreate(graph, identifier='flow')
                    session = await project.sessions.acreate()
                    executions = []
                    original = process.invoke_worker
                    async def observe(request):
                        if request['operation'] == 'execute':
                            executions.append(request['tool_name'])
                        return await original(request)
                    with patch.object(process, 'invoke_worker', side_effect=observe):
                        confirmation = await (await session.run.submit('act', engine='graph', engine_options={"workflow": "flow"})).wait()
                        self.assertEqual(confirmation.data.status, 'paused', confirmation.data.error)
                        self.assertEqual(executions, [])
                        # raw resume도 Workflow 확인만 승인한다. Tool 승인은 별도다.
                        paused = await (await session.run.resume(confirmation.id, engine='graph')).wait()
                        self.assertEqual(paused.data.status, 'paused', paused.data.error)
                        request, = await paused.ainteractions(pending_only=True)
                        self.assertEqual(request.kind, 'approval')
                        self.assertEqual(executions, [])
                        await paused.arespond(request.respond('approve' if approve else 'deny'))
                        self.assertEqual(executions, [])
                        resumed = await (await session.run.resume(paused.id, engine='graph')).wait()
                        self.assertEqual(resumed.data.status, 'completed' if approve else 'failed', resumed.data.error)
                        self.assertEqual(executions, ['act'] if approve else [])

    async def test_remote_error_small_taxonomy(self):
        for body, imports, expected in [
            ('raise ToolExecutionError("temporary", effect="none", retryable=True)',
             'from llm.services.runtime.tools import ToolExecutionError', 'tool_failed'),
            ('raise ProviderError("provider_rate_limit")', 'from llm.providers.requests import ProviderError', 'provider_rate_limit'),
            ('raise KeyError("private arguments")', '', 'tool_failed'),
            ('1 / 0', '', 'tool_failed'),
            ('error = RuntimeError("private")\nerror.code = "provider_rate_limit"\nraise error', '', 'tool_failed')]:
            with self.subTest(body=body):
                _, tool = await self.resolve(source(body, imports))
                with self.assertRaises((ToolExecutionError, process.RemoteToolError)) as raised:
                    await tool.handler({})
                self.assertEqual(raised.exception.code, expected)
                if 'temporary' in body:
                    self.assertEqual(raised.exception.effect, 'none')
                    self.assertTrue(raised.exception.retryable)
                elif expected == 'tool_failed':
                    self.assertEqual(raised.exception.effect, 'uncertain')
                    self.assertFalse(raised.exception.retryable)

    async def test_run_interrupt_timeout_and_shutdown_reap_worker(self):
        for action in ('interrupt', 'timeout', 'shutdown'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / 'worker'
                text = source(f'Path({str(marker)!r}).write_text(str(os.getpid()))\nawait asyncio.sleep(60)',
                              'import os, asyncio\nfrom pathlib import Path')
                project, session, _ = await self.setup_runtime(text)
                if action == 'timeout':
                    config = (await project.aget_data()).config.to_dict()
                    config['parameters']['engines']['loop'].setdefault("policy", {})["tool_timeout"] = 2
                    await project.asave(config=config)
                request = await session.run.submit('act', engine='loop')
                async with asyncio.timeout(15):
                    while not marker.exists():
                        await asyncio.sleep(.02)
                pid = int(marker.read_text())
                if action == 'interrupt':
                    await session.run.interrupt()
                elif action == 'shutdown':
                    await project.app.shutdown()
                if action != 'shutdown':
                    run = await request.wait()
                    self.assertEqual(run.data.status, 'failed' if action == 'timeout' else 'interrupted')
                    if action == 'timeout':
                        self.assertEqual(run.data.error_code, 'tool_timeout')
                status = Path(f'/proc/{pid}/stat')
                self.assertTrue(not status.exists() or status.read_text().split()[2] == 'Z')

    async def test_changed_source_cannot_reuse_approval(self):
        async def ask(call):
            raise ToolApprovalRequired()
        text = source('return 1', decorator='@tool(approval_required=True)')
        project, session, _ = await self.setup_runtime(text, runtime=ToolRuntime(authorize=ask))
        paused = await (await session.run.submit('act', engine='loop')).wait()
        request, = await paused.ainteractions()
        await paused.arespond(request.respond('approve'))
        await project.components.tools.asave('act', {'source': text.replace('return 1', 'return 2')})
        with self.assertRaisesRegex(Exception, 'changed'):
            await session.run.resume(paused.id, engine='loop')

    async def test_tool_call_provenance_and_operation_key_reach_child(self):
        text = source('call = current_tool_call()\nreturn {"run": call.run_id, "key": call.operation_key, "idempotency": call.idempotency_key}',
                      'from llm.services.runtime.tools import current_tool_call')
        _, session, _ = await self.setup_runtime(text, runtime=ToolRuntime(operation_key=lambda _: 'operation'))
        run = await (await session.run.submit('act', engine='loop')).wait()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        step, = [step for step in await run.steps.alist() if step.kind == 'tool']
        self.assertEqual(step.output.data['run'], run.id)
        self.assertEqual(step.output.data['key'], 'operation')
        self.assertTrue(step.output.data['idempotency'])

    def test_descriptor_and_error_envelopes_are_validated(self):
        for payload in ({}, {"ok": 1}, {"ok": True}, {"ok": False, "error": {"kind": "coded", "code": 3, "message": "x"}}):
            with self.assertRaises(process.WorkerError):
                process._decode(payload, 'execute')
