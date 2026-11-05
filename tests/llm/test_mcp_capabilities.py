"""Explicit MCP features do not choose application policy or authority."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from llm.components.mcp import MCPComponent
from llm.components.tools import ToolRegistry
from llm.core.configuration import UNSET
from llm.core.interactions import InteractionResponse
from llm.llm import LargeLanguageModel
from llm.providers.mcp import MCPConnector, MCPConnectionError
from llm.providers.mcp_auth import MCPOAuth
from llm.providers.mcp_interactions import MCPElicitation
from llm.providers.mcp_session import MCPSession, MCPElicitationSession


def reply(request, value):
    return InteractionResponse(request.id, request.revision, "reply", request.fingerprint, value=value)


class MCPFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_error_preserves_remote_result_but_details_require_explicit_opt_in(self):
        from llm.providers.mcp import MCPToolError
        from llm.providers.runtime import logging_scope, ProviderDiagnostics
        from unittest.mock import AsyncMock
        from types import SimpleNamespace
        raw = {'isError': True, 'content': [{'type': 'text', 'text': 'private missing file'}]}
        item = SimpleNamespace(name='fail', description='fixture', input_schema={'type': 'object'},
                               model_dump=lambda **_: {})
        session = SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=[item], next_cursor=None)),
            call_tool=AsyncMock(return_value=SimpleNamespace(is_error=True, model_dump=lambda **_: raw)))
        connector = MCPConnector()
        client = MCPSession(connector, session, SimpleNamespace(model_dump=lambda **_: {}), connection_id='fixture')
        registry = await connector._discover(session, client=client)
        with self.assertRaises(MCPToolError) as caught:
            await registry.get('fail').handler({})
        error = caught.exception
        self.assertEqual(error.result, raw)
        self.assertEqual(error.effect, 'uncertain')
        self.assertFalse(error.retryable)
        self.assertFalse(error.recoverable)
        with tempfile.TemporaryDirectory() as folder:
            with logging_scope(folder):
                self.assertNotIn('private', str(error.diagnostic.to_dict()))
            with logging_scope(folder, diagnostics=ProviderDiagnostics(error_details=True, max_detail_chars=7)):
                self.assertEqual(error.diagnostic.details['provider_error']['message'], 'private')
        self.assertEqual(session.call_tool.await_count, 1)
        explicit = MCPToolError('trusted host failure', effect='none', retryable=True, recoverable=True)
        self.assertEqual(explicit.diagnostic.details, {'effect': 'none', 'retryable': True, 'recoverable': True})
        with self.assertRaises(TypeError):
            MCPToolError('invalid', unknown=True)

    async def test_real_sdk_resources_and_live_elicitation_share_connection(self):
        requests = []
        async def answer(request):
            requests.append(request)
            return reply(request, {"action": "accept", "content": {"name": "Ada"}})
        with open("/dev/null", "w") as errlog:
            connector = MCPConnector(elicitation=answer, errlog=errlog)
            definition = {"transport": "stdio", "command": sys.executable,
                "args": [str(Path(__file__).parent / "support/mcp_capability_server.py")]}
            async with connector(definition) as client:
                connection_id = client.get_status()["connection_id"]
                self.assertEqual(client.get_status()["instructions"], "Untrusted server instructions")
                resources = await client.list_resources()
                self.assertEqual(resources["resources"][0]["uri"], "fixture://document")
                templates = await client.list_templates()
                self.assertEqual(templates["resourceTemplates"][0]["uriTemplate"], "fixture://item/{name}")
                contents = await client.read_resource("fixture://document")
                self.assertEqual(contents["contents"][0]["text"], "Resource body")
                prompts = await client.list_prompts()
                self.assertEqual(prompts["prompts"][0]["name"], "explain")
                prompt = await client.get_prompt("explain", arguments={"topic": "asyncio"})
                self.assertEqual(prompt["messages"][0]["content"]["text"], "Explain asyncio.")
                self.assertEqual(client.names(), ("ask",))
                self.assertTrue(client.get("ask").contract.approval_required)
                # Transport test only; Application/Engine uses ToolExecutor.
                result = await client.get("ask").handler({})
                self.assertEqual(json.loads(result["content"][0]["text"]), {"action": "accept", "name": "Ada"})
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].source["connection_id"], connection_id)
            with self.assertRaisesRegex(ValueError, "closed"):
                await client.get("ask").handler({})

    async def test_timeout_missing_null_and_explicit_are_distinct_sdk_kwargs(self):
        import httpx2
        received = []

        @asynccontextmanager
        async def http_client(**kwargs):
            received.append(("http", kwargs))
            yield object()

        @asynccontextmanager
        async def transport(url, **kwargs):
            yield (object(), object())

        @asynccontextmanager
        async def session(*streams, **kwargs):
            received.append(("session", kwargs))
            yield SimpleNamespace(initialize=AsyncMock(return_value=SimpleNamespace(
                model_dump=lambda **kw: {"capabilities": {}})))

        with patch("mcp.shared._httpx_utils.create_mcp_http_client", http_client), \
             patch("mcp.client.streamable_http.streamable_http_client", transport), \
             patch("mcp.ClientSession", session):
            for value in (UNSET, None, 7):
                received.clear()
                connector = MCPConnector(**({} if value is UNSET else {"timeout_seconds": value}))
                async with connector({"transport": "streamable_http", "url": "http://localhost/mcp"}) as client:
                    self.assertEqual(client.names(), ())
                options, sdk = received[0][1], received[1][1]
                if value is UNSET:
                    self.assertNotIn("timeout", options)
                    self.assertNotIn("read_timeout_seconds", sdk)
                else:
                    self.assertEqual(options["timeout"], httpx2.Timeout(value))
                    self.assertEqual(sdk["read_timeout_seconds"], value)
                self.assertNotIn("elicitation_callback", sdk)

    async def test_prompt_paging_arguments_and_capability_contract(self):
        initialized = SimpleNamespace(model_dump=lambda **_: {"capabilities": {"prompts": {}}})
        result = SimpleNamespace(model_dump=lambda **_: {"prompts": [], "nextCursor": "next"})
        sdk = SimpleNamespace(list_prompts=AsyncMock(return_value=result), get_prompt=AsyncMock(return_value=result))
        client = MCPSession(MCPConnector(), sdk, initialized, connection_id="test")
        self.assertEqual(await client.list_prompts(), {"prompts": [], "nextCursor": "next"})
        sdk.list_prompts.assert_awaited_once_with()
        await client.list_prompts(cursor="next")
        self.assertEqual(sdk.list_prompts.call_args.kwargs["params"].cursor, "next")
        await client.get_prompt("explain")
        sdk.get_prompt.assert_awaited_once_with("explain")
        await client.get_prompt("explain", arguments=None)
        sdk.get_prompt.assert_awaited_with("explain", arguments=None)
        for arguments in ({"topic": 1}, [], False):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, "arguments"):
                await client.get_prompt("explain", arguments=arguments)
        self.assertEqual(sdk.get_prompt.await_count, 2)
        entered = asyncio.Event()
        async def blocked(name):
            entered.set()
            await asyncio.Event().wait()
        sdk.get_prompt.side_effect = blocked
        pending = asyncio.create_task(client.get_prompt("explain"))
        await asyncio.wait_for(entered.wait(), 5)
        await client.close()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        with self.assertRaisesRegex(ValueError, "closed"):
            await client.list_prompts()
        client = MCPSession(MCPConnector(), sdk, SimpleNamespace(model_dump=lambda **_: {}), connection_id="none")
        with self.assertRaisesRegex(ValueError, "advertise prompts"):
            await client.get_prompt("explain")

    async def test_live_session_resources_native_paging_and_cancellation(self):
        initialized = SimpleNamespace(model_dump=lambda **kw: {
            "protocolVersion": "test", "capabilities": {"resources": {}},
            "serverInfo": {"name": "fixture", "version": "1"}, "instructions": "untrusted"})
        result = SimpleNamespace(model_dump=lambda **kw: {"resources": [], "nextCursor": "next"})
        sdk = SimpleNamespace(list_resources=AsyncMock(return_value=result),
            list_resource_templates=AsyncMock(return_value=result), read_resource=AsyncMock(return_value=result))
        client = MCPSession(MCPConnector(), sdk, initialized, connection_id="test")
        self.assertEqual(client.get_status()["instructions"], "untrusted")
        await client.list_resources()
        sdk.list_resources.assert_awaited_once_with()
        await client.list_resources(cursor="next")
        self.assertEqual(sdk.list_resources.call_args.kwargs["params"].cursor, "next")
        await client.list_templates()
        await client.read_resource("data://document")
        sdk.read_resource.assert_awaited_once_with("data://document")
        entered = asyncio.Event()
        async def blocked(uri):
            entered.set()
            await asyncio.Event().wait()
        sdk.read_resource.side_effect = blocked
        pending = asyncio.create_task(client.read_resource("data://slow"))
        await asyncio.wait_for(entered.wait(), 5)
        await client.close()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(client.get_status()["state"], "closed")
        with self.assertRaisesRegex(ValueError, "closed"):
            await client.read_resource("data://document")

    async def test_component_scope_reuses_connection_and_rejects_changed_definition(self):
        connections = []
        class Client(ToolRegistry):
            def get_status(self):
                return {"state": "connected", "instructions": "not injected"}
            async def list_resources(self, *, cursor=UNSET):
                return {"resources": [{"uri": "data://one"}]}
            async def read_resource(self, uri):
                return {"contents": [{"uri": uri, "text": "data"}]}
            async def list_prompts(self, *, cursor=UNSET):
                return {"prompts": [{"name": "example"}]}
            async def get_prompt(self, name, *, arguments=UNSET):
                return {"messages": [{"role": "user", "content": {"type": "text", "text": "untrusted"}}]}
        @asynccontextmanager
        async def connector(definition):
            connections.append("open")
            try:
                yield Client()
            finally:
                connections.append("close")
        with tempfile.TemporaryDirectory() as root:
            app = LargeLanguageModel(Path(root), components=[MCPComponent(connector=connector)])
            try:
                project = await app.projects.acreate(components=["mcp"])
                data = project.components.mcp
                await data.acreate({"transport": "stdio", "command": "fixture"}, identifier="server")
                async with data.connect("server") as access:
                    self.assertEqual((await access.aget_status())["state"], "connected")
                    self.assertEqual((await access.alist_resources())["resources"][0]["uri"], "data://one")
                    self.assertEqual((await access.aread_resource("data://one"))["contents"][0]["text"], "data")
                    self.assertEqual((await access.alist_prompts())["prompts"][0]["name"], "example")
                    self.assertEqual((await access.aget_prompt("example"))["messages"][0]["content"]["text"], "untrusted")
                    await access.aprepare()
                    self.assertEqual((await access.aget_tools({})).names(), ())
                    self.assertEqual(connections, ["open"])
                    await data.aupdate("server", {"command": "changed"})
                    with self.assertRaisesRegex(ValueError, "changed"):
                        await access.aread_resource("data://one")
                    with self.assertRaisesRegex(ValueError, "changed"):
                        await access.aget_prompt("example")
                self.assertEqual(connections, ["open", "close"])
                with self.assertRaisesRegex(ValueError, "closed"):
                    await access.aget_status()
                with self.assertRaisesRegex(ValueError, "closed"):
                    await access.aget_prompt("example")
                self.assertEqual((await data.alist_prompts("server"))["prompts"][0]["name"], "example")
                self.assertEqual((await data.aget_prompt("server", "example"))["messages"][0]["role"], "user")
            finally:
                await app.shutdown()

    async def test_plain_tool_connector_remains_valid_but_optional_operations_fail(self):
        from llm.components.mcp.client import MCPClientOperation
        registry = ToolRegistry()
        with self.assertRaisesRegex(ValueError, "support"):
            MCPClientOperation.require(registry, MCPClientOperation.RESOURCES)
        for operation in (MCPClientOperation.PROMPTS, MCPClientOperation.PROMPT):
            with self.assertRaisesRegex(ValueError, "support"):
                MCPClientOperation.require(registry, operation)

    async def test_elicitation_form_decline_cancel_validation_and_binding(self):
        from mcp.types import ElicitRequestFormParams
        params = ElicitRequestFormParams.model_validate({"mode": "form", "message": "Choose",
            "requestedSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}})
        request = MCPElicitation.request(params, connection_id="c", request_id=3)
        self.assertEqual(MCPElicitation.response(request, reply(request, {"action": "accept", "content": {"name": "yes"}})),
                         {"action": "accept", "content": {"name": "yes"}})
        for action in ("decline", "cancel"):
            self.assertEqual(MCPElicitation.response(request, reply(request, {"action": action})), {"action": action})
        for value in ({"action": "accept", "content": {}}, {"action": "accept", "content": {"name": 3}},
                      {"action": "decline", "content": {"name": "x"}}, {"action": "approve"}):
            with self.assertRaises(ValueError):
                MCPElicitation.response(request, reply(request, value))
        other = MCPElicitation.request(params, connection_id="new", request_id=3)
        with self.assertRaisesRegex(ValueError, "changed"):
            MCPElicitation.response(other, reply(request, {"action": "cancel"}))

    async def test_elicitation_scope_close_cancels_waiter_and_rejects_late_reply(self):
        from mcp.types import ElicitRequestFormParams
        params = ElicitRequestFormParams.model_validate({"message": "Question", "requestedSchema": {"type": "object", "properties": {}}})
        entered = asyncio.Event()
        async def handler(request):
            entered.set()
            await asyncio.Event().wait()
        client = MCPElicitationSession(handler)
        task = asyncio.create_task(client(SimpleNamespace(request_id=1), params))
        await asyncio.wait_for(entered.wait(), 5)
        await client.close()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(client.pending)
        with self.assertRaisesRegex(ValueError, "closed"):
            await client(SimpleNamespace(request_id=1), params)

    async def test_oauth_uses_injected_storage_and_sdk_native_options(self):
        storage = SimpleNamespace(**{name: AsyncMock(return_value=None) for name in MCPOAuth.STORAGE_METHODS})
        redirect, callback = AsyncMock(), AsyncMock()
        oauth = MCPOAuth(server_url="https://server.example/mcp", client_metadata={"redirect_uris": ["http://localhost/callback"]},
            storage=storage, redirect_handler=redirect, callback_handler=callback)
        with patch("mcp.client.auth.OAuthClientProvider") as provider:
            oauth.create_auth("https://server.example/mcp")
        values = provider.call_args.kwargs
        self.assertIs(values["storage"], storage)
        self.assertIs(values["redirect_handler"], redirect)
        self.assertIs(values["callback_handler"], callback)
        self.assertNotIn("client_metadata_url", values)
        redirect.assert_not_called()
        callback.assert_not_called()
        for name in MCPOAuth.STORAGE_METHODS:
            getattr(storage, name).assert_not_called()
        with self.assertRaisesRegex(ValueError, "another server"):
            oauth.create_auth("https://other.example/mcp")

    async def test_real_sdk_oauth_uses_host_stored_token_without_login_or_backend_storage(self):
        import httpx2
        from mcp.shared.auth import OAuthToken
        storage = SimpleNamespace(**{name: AsyncMock(return_value=None) for name in MCPOAuth.STORAGE_METHODS})
        storage.get_tokens.return_value = OAuthToken(access_token="fixture-token", token_type="Bearer")
        redirect, callback = AsyncMock(), AsyncMock()
        oauth = MCPOAuth(server_url="https://server.example/mcp",
            client_metadata={"redirect_uris": ["http://localhost/callback"]}, storage=storage,
            redirect_handler=redirect, callback_handler=callback)
        received = []
        def endpoint(request):
            received.append(request.headers.get("authorization"))
            return httpx2.Response(200, json={})
        async with httpx2.AsyncClient(auth=oauth.create_auth("https://server.example/mcp"),
                transport=httpx2.MockTransport(endpoint)) as client:
            await client.get("https://server.example/mcp")
        self.assertEqual(received, ["Bearer fixture-token"])
        redirect.assert_not_called()
        callback.assert_not_called()
        storage.set_tokens.assert_not_called()

    async def test_url_elicitation_is_data_only_and_cannot_reuse_another_request(self):
        from mcp.types import ElicitRequestURLParams
        params = ElicitRequestURLParams.model_validate({"mode": "url", "message": "Sign in",
            "url": "https://example.test/login", "elicitationId": "remote"})
        request = MCPElicitation.request(params, connection_id="a", request_id=1)
        self.assertEqual(request.action["url"], "https://example.test/login")
        self.assertEqual(MCPElicitation.response(request, reply(request, {"action": "cancel"})), {"action": "cancel"})

    async def test_close_rejects_answer_even_if_handler_suppresses_cancellation(self):
        from mcp.types import ElicitRequestFormParams
        params = ElicitRequestFormParams.model_validate({"message": "Question", "requestedSchema": {"type": "object", "properties": {}}})
        entered = asyncio.Event()
        async def handler(request):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return reply(request, {"action": "accept", "content": {}})
        client = MCPElicitationSession(handler)
        task = asyncio.create_task(client(SimpleNamespace(request_id=1), params))
        await asyncio.wait_for(entered.wait(), 5)
        await client.close()
        with self.assertRaisesRegex(ValueError, "no longer active"):
            await task

    async def test_connection_diagnostic_keeps_status_without_credentials(self):
        for status, code in ((401, "mcp_authentication_required"), (403, "mcp_access_denied"), (500, "mcp_connection_failed")):
            error = RuntimeError("Authorization: secret")
            error.status_code = status
            result = MCPConnectionError("initialize", error)
            self.assertEqual(result.code, code)
            self.assertEqual(result.diagnostic.details["http_status"], status)
            self.assertNotIn("secret", str(result.diagnostic))
            wrapped = MCPConnectionError("initialize", ExceptionGroup("SDK", [error]))
            self.assertEqual(wrapped.code, code)
