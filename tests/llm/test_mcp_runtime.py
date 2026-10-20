"""MCP 발견·호출·승인·취소·재시작을 실제 SDK/local transport로 검증한다."""

import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest

from llm.components.mcp import MCPComponent
from llm.components.tools import Tool, ToolRegistry, ToolContract, ToolClassification
from llm.llm import LargeLanguageModel, LoopEngine, BackendServices
from llm.providers.mcp import MCPConnector, MCPToolError
from llm.services.runtime.tools import ToolRuntime
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from llm.components.tools import ToolEffect, ToolIsolation


class MCPRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls, self.connections = [], []
        self.schema = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}

    @asynccontextmanager
    async def connector(self, definition):
        self.connections.append("open")
        async def echo(arguments):
            self.calls.append(arguments)
            return arguments
        try:
            yield ToolRegistry((Tool("echo", "echo", self.schema, echo,
                contract=ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=True, operation_key_required=False, retry_fields=())),))
        finally:
            self.connections.append("close")

    async def setup_app(self, connector=None, *, revision=MCPComponent.REVISION):
        self.model = ScriptedCompletion(
            [chunk(calls=[call('{"value":"hello"}', name="remote_echo")], finish="tool_calls")],
            [chunk("done", finish="stop")])
        app = LargeLanguageModel(self.root / "workspace", components=[MCPComponent(
            connector=self.connector if connector is None else connector, revision=revision)],
            engines={"loop": LoopEngine(completion_fn=self.model)}, services=BackendServices(tool_runtime=ToolRuntime(
                classify=lambda call: ToolClassification("remote.call", "test", 1))))
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate(components=["mcp"], config={"policies": {"approval": {
            "enabled": True, "risk_scheme": "test", "rules": [{"id": "allow", "category": "remote.call", "max_risk": 1}]}},
            "parameters": {"engines": {"loop": {"config": {"completion": {"model": "test/model"}}}}}})
        data = project.components.mcp
        await data.acreate({"transport": "stdio", "command": sys.executable,
            "args": [str(Path(__file__).parent / "support/mcp_server.py")],
            "env": {"MCP_TEST_MARKER": str(self.root / "marker")}}, identifier="server")
        await data.aprepare("server")
        await data.aconfigure({"config": {"tools": {"remote_echo": {"server": "server", "tool": "echo"}}}})
        return app, project, await project.sessions.acreate(), data

    async def test_approval_before_connection_and_explicit_resume_exactly_once(self):
        app, project, session, data = await self.setup_app()
        self.assertEqual(self.connections, ["open", "close"])
        self.assertEqual(len(await data.alist_tools("server")), 1)
        paused = await (await session.run.submit("go", engine="loop")).wait()
        self.assertEqual(paused.data.status, "paused", paused.data.error)
        self.assertEqual(self.connections, ["open", "close"])
        self.assertEqual(self.calls, [])
        self.assertEqual([r.actor for r in await paused.ainteraction_responses()], ["policy"])
        done = await (await session.run.resume(paused.id, engine="loop")).wait()
        self.assertEqual(done.data.status, "completed", done.data.error)
        self.assertEqual(self.calls, [{"value": "hello"}])
        self.assertEqual(self.connections, ["open", "close", "open", "close"])

    async def test_changed_definition_or_schema_rejects_execution(self):
        app, project, session, data = await self.setup_app()
        paused = await (await session.run.submit("go", engine="loop")).wait()
        # 원격 schema 변경을 승인 후 발견해도 effects 이전에 거부한다.
        self.schema["properties"]["value"]["maxLength"] = 2
        done = await (await session.run.resume(paused.id, engine="loop")).wait()
        self.assertEqual(done.data.status, "failed")
        self.assertEqual(self.calls, [])
        await data.aupdate("server", {"args": ["changed"]})
        with self.assertRaisesRegex(ValueError, "stale"):
            await data.alist_tools("server")

    async def test_cancel_closes_remote_invocation_and_connection(self):
        entered, closed = asyncio.Event(), []
        @asynccontextmanager
        async def connector(definition):
            async def wait(arguments):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.append("call")
            try:
                yield ToolRegistry((Tool("echo", "echo", self.schema, wait, contract=ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=True, operation_key_required=False, retry_fields=())),))
            finally:
                closed.append("connection")
        app, project, session, _ = await self.setup_app(connector)
        paused = await (await session.run.submit("go", engine="loop")).wait()
        handle = await session.run.resume(paused.id, engine="loop")
        await asyncio.wait_for(entered.wait(), 5)
        await session.run.interrupt()
        self.assertEqual((await handle.wait()).data.status, "interrupted")
        self.assertEqual(closed, ["connection", "call", "connection"])

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "optional MCP SDK is not installed")
    async def test_real_remote_error_keeps_code_and_uncertain_receipt(self):
        with open('/dev/null', 'w') as errlog:
            app, project, session, data = await self.setup_app(MCPConnector(errlog=errlog))
            await data.aconfigure({'config': {'tools': {'remote_echo': {'server': 'server', 'tool': 'fail'}}}})
            self.model.responses[0] = [chunk(calls=[call('{}', name='remote_echo')], finish='tool_calls')]
            paused = await (await session.run.submit('go', engine='loop')).wait()
            failed = await (await session.run.resume(paused.id, engine='loop')).wait()
            self.assertEqual(failed.data.status, 'failed')
            self.assertEqual(failed.data.error_code, 'mcp_tool_failed')
            with self.assertRaisesRegex(Exception, 'uncertain Tool'):
                await session.run.resume(failed.id, engine='loop')

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "optional MCP SDK is not installed")
    async def test_real_stdio_approval_and_backend_reopen(self):
        with open('/dev/null', 'w') as errlog:
            connector = MCPConnector(errlog=errlog)
            app, project, session, data = await self.setup_app(connector)
            marker = self.root / "marker"
            self.assertEqual(marker.read_text().splitlines(), ["start"])
            paused = await (await session.run.submit("go", engine="loop")).wait()
            self.assertEqual(paused.data.status, "paused", paused.data.error)
            self.assertEqual(marker.read_text().splitlines(), ["start"])
            project_id, session_id = project.id, session.id
            await app.shutdown()
            reopened = LargeLanguageModel(self.root / "workspace", components=[MCPComponent(connector=connector)],
                engines={"loop": LoopEngine(completion_fn=self.model)}, services=BackendServices(tool_runtime=ToolRuntime(
                    classify=lambda call: ToolClassification("remote.call", "test", 1))))
            try:
                session = (await reopened.projects.aload(project_id)).sessions.load(session_id)
                done = await (await session.run.resume(paused.id, engine="loop")).wait()
                self.assertEqual(done.data.status, "completed", done.data.error)
                self.assertEqual(marker.read_text().splitlines(), ["start", "start", "effect"])
            finally:
                await reopened.shutdown()

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "optional MCP SDK is not installed")
    async def test_real_crash_after_effect_blocks_resume_and_preserves_one_effect(self):
        with open("/dev/null", "w") as errlog:
            connector = MCPConnector(errlog=errlog)
            app, project, session, data = await self.setup_app(connector)
            await data.aconfigure({"config": {"tools": {"remote_echo": {"server": "server", "tool": "crash"}}}})
            self.model.responses[0] = [chunk(calls=[call("{}", name="remote_echo")], finish="tool_calls")]
            paused = await (await session.run.submit("go", engine="loop")).wait()
            marker = self.root / "marker"
            self.assertEqual(marker.read_text().splitlines(), ["start"])
            failed = await (await session.run.resume(paused.id, engine="loop")).wait()
            self.assertEqual(failed.data.status, "failed")
            self.assertEqual(failed.data.error_code, "mcp_tool_failed")
            self.assertEqual(marker.read_text().splitlines(), ["start", "start", "crash-effect"])
            project_id, session_id, run_id = project.id, session.id, failed.id
            await app.shutdown()
            reopened = LargeLanguageModel(self.root / "workspace",
                components=[MCPComponent(connector=connector)],
                engines={"loop": LoopEngine(completion_fn=self.model)})
            try:
                session = (await reopened.projects.aload(project_id)).sessions.load(session_id)
                with self.assertRaisesRegex(Exception, "uncertain Tool"):
                    await session.run.resume(run_id, engine="loop")
                self.assertEqual(marker.read_text().splitlines(), ["start", "start", "crash-effect"])
            finally:
                await reopened.shutdown()

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "optional MCP SDK is not installed")
    async def test_changed_connector_revision_rejects_resume_before_connection(self):
        with open("/dev/null", "w") as errlog:
            connector = MCPConnector(errlog=errlog)
            app, project, session, data = await self.setup_app(connector, revision="prior-implementation")
            paused = await (await session.run.submit("go", engine="loop")).wait()
            self.assertEqual(paused.data.status, "paused")
            marker = self.root / "marker"
            self.assertEqual(marker.read_text().splitlines(), ["start"])
            project_id, session_id = project.id, session.id
            await app.shutdown()
            reopened = LargeLanguageModel(self.root / "workspace",
                components=[MCPComponent(connector=connector)],
                engines={"loop": LoopEngine(completion_fn=self.model)})
            try:
                session = (await reopened.projects.aload(project_id)).sessions.load(session_id)
                with self.assertRaisesRegex(ValueError, "stale"):
                    await session.run.resume(paused.id, engine="loop")
                self.assertEqual(marker.read_text().splitlines(), ["start"])
            finally:
                await reopened.shutdown()

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "optional MCP SDK is not installed")
    async def test_real_streamable_http_result_error_and_cancellation(self):
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            port = bound.getsockname()[1]
        process = await asyncio.create_subprocess_exec(sys.executable,
            str(Path(__file__).parent / "support/mcp_server.py"), str(port),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            async with asyncio.timeout(10):
                while True:
                    self.assertIsNone(process.returncode, "MCP test server exited before readiness")
                    try:
                        reader, writer = await asyncio.open_connection("127.0.0.1", port)
                        writer.close()
                        await writer.wait_closed()
                        break
                    except OSError:
                        await asyncio.sleep(.01)  # 테스트 서버 readiness; 제품 재시도 정책이 아니다.
            connector = MCPConnector(timeout_seconds=5, credentials=lambda definition: {"headers": {"Authorization": "Bearer test"}})
            async with connector({"transport": "streamable_http", "url": f"http://127.0.0.1:{port}/mcp"}) as registry:
                self.assertEqual(set(registry.names()), {"echo", "fail", "wait", "authorization", "crash"})
                self.assertTrue(registry.get("echo").contract.approval_required)
                auth = await registry.get("authorization").handler({})
                self.assertEqual(json.loads(auth["content"][0]["text"]), {"authorization": "Bearer test"})
                result = await registry.get("echo").handler({"value": "http"})
                self.assertEqual(json.loads(result["content"][0]["text"]), {"value": "http"})
                with self.assertRaises(MCPToolError) as failure:
                    await registry.get("fail").handler({})
                self.assertEqual(failure.exception.effect, "uncertain")
                pending = asyncio.create_task(registry.get("wait").handler({}))
                await asyncio.sleep(.02)
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
            timed = MCPConnector(timeout_seconds=1)
            async with timed({"transport": "streamable_http", "url": f"http://127.0.0.1:{port}/mcp"}) as registry:
                with self.assertRaises(MCPToolError) as deadline:
                    await asyncio.wait_for(registry.get("wait").handler({}), 5)
                self.assertEqual(deadline.exception.effect, "uncertain")
        finally:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 5)
