"""MCP SDK 어댑터. 도메인/승인/재시도는 소유하지 않고 연결·발견·호출만 수행한다."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from copy import deepcopy
import inspect
import math

from llm.components.tools.registry import Tool, ToolContract, ToolRegistry
from llm.errors import CodedError
from llm.core.contracts import Diagnostic
from llm.services.runtime.tools import ToolExecutionError


class MCPConnectionError(CodedError, RuntimeError):
    code = "mcp_connection_failed"

    def __init__(self, stage, error):
        super().__init__(f"MCP {stage} failed")
        self.diagnostic = Diagnostic(self.code, str(self), details={"stage": stage,
            "exception_type": type(error).__name__})
        self.__cause__ = error


class MCPToolError(ToolExecutionError):
    code = "mcp_tool_failed"

    @property
    def diagnostic(self):
        return Diagnostic(self.code, str(self), details={"effect": self.effect, "retryable": False})


class MCPConnector:
    """명시적으로 주입하는 표준 SDK connector. SDK import는 최초 연결 때 수행한다.

    credentials(definition)는 실행 시 인증 필드(headers/env)만 반환한다. 값은 저장하지
    않는다. contract(name, definition)는 신뢰한 Host 선언이며 서버 annotations는
    승인/재시도 권한으로 사용하지 않는다. 미선언 원격 Tool에는 approval_required를
    강제한다: 외부 실행 효과를 추측해서 승인을 생략하지 않기 위한 경계다.
    """

    def __init__(self, *, credentials=None, contract=None, timeout_seconds=None, errlog=None):
        if credentials is not None and not callable(credentials) or contract is not None and not callable(contract):
            raise TypeError("MCP credentials/contract must be callable")
        if timeout_seconds is not None and (type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("MCP timeout_seconds must be positive and finite")
        self.credentials, self.contract = credentials, contract
        self.timeout_seconds, self.errlog = timeout_seconds, errlog

    async def _connect(self, stack, definition):
        # 무거운 SDK 초기화와 선택 의존성 오류를 실행 경계에 한정한다.
        import importlib
        sdk = await asyncio.to_thread(importlib.import_module, "mcp")
        transport = definition["transport"]
        if self.credentials is not None:
            supplied = self.credentials(deepcopy(definition))
            supplied = await supplied if inspect.isawaitable(supplied) else supplied
            allowed = {"env"} if transport == "stdio" else {"headers"}
            if not isinstance(supplied, dict) or set(supplied) - allowed:
                raise ValueError("MCP credentials may supply only transport authentication fields")
            for key, values in supplied.items():
                if not isinstance(values, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in values.items()):
                    raise ValueError("MCP credentials must be string mappings")
                definition[key] = {**definition.get(key, {}), **values}
        if transport == "stdio":
            from mcp.client.stdio import stdio_client
            params = sdk.StdioServerParameters(**{key: definition[key] for key in ("command", "args", "env") if key in definition})
            streams = await stack.enter_async_context(stdio_client(params, **({"errlog": self.errlog} if self.errlog is not None else {})))
        elif transport == "streamable_http":
            import httpx2
            from mcp.client.streamable_http import streamable_http_client
            # The connector owns its explicit timeout. None means no additional
            # deadline; do not inherit the SDK factory's 30/300 second defaults.
            client = await stack.enter_async_context(httpx2.AsyncClient(
                headers=definition.get("headers"), timeout=self.timeout_seconds))
            streams = await stack.enter_async_context(
                streamable_http_client(definition["url"], http_client=client))
        else:
            raise ValueError("MCPConnector supports stdio and streamable_http; inject another connector for other transports")
        session = await stack.enter_async_context(sdk.ClientSession(*streams[:2],
            **({"read_timeout_seconds": self.timeout_seconds} if self.timeout_seconds is not None else {})))
        await session.initialize()
        return session

    async def _discover(self, session):
        from mcp.types import PaginatedRequestParams
        registry, cursor, seen = ToolRegistry(), None, set()
        while True:
            page = await session.list_tools(
                **({"params": PaginatedRequestParams(cursor=cursor)} if cursor is not None else {}))
            for item in page.tools:
                contract = (self.contract(item.name, item.model_dump(mode="json", by_alias=True, exclude_none=True))
                            if self.contract is not None else ToolContract(approval_required=True))
                if not isinstance(contract, ToolContract):
                    raise TypeError("MCP contract callback must return ToolContract")
                async def invoke(arguments, name=item.name):
                    try:
                        result = await session.call_tool(name, arguments=deepcopy(arguments))
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        raise MCPToolError("MCP Tool transport failed; remote effects are uncertain") from error
                    if result.is_error:
                        raise MCPToolError("MCP server reported a Tool error; remote effects are uncertain")
                    return result.model_dump(mode="json", by_alias=True, exclude_none=True)
                registry.register(Tool(item.name, item.description or "", item.input_schema, invoke, contract=contract))
            cursor = page.next_cursor
            if cursor is None:
                return registry
            if cursor in seen:
                raise ValueError("MCP server repeated a discovery cursor")
            seen.add(cursor)

    @asynccontextmanager
    async def __call__(self, definition):
        failure, stage = None, "initialize"
        try:
            async with AsyncExitStack() as stack:
                try:
                    async with asyncio.timeout(self.timeout_seconds):
                        session = await self._connect(stack, deepcopy(definition))
                        stage = "discover"
                        registry = await self._discover(session)
                except Exception as error:
                    failure = MCPConnectionError(stage, error)
                else:
                    try:
                        yield registry
                    except BaseException as error:
                        # SDK의 AnyIO TaskGroup이 호출 측 Tool/승인 오류를 ExceptionGroup으로
                        # 감싸지 않도록 같은 task에서 닫은 뒤 원래 예외를 전달한다.
                        failure = error
        except BaseException as error:
            if failure is None:
                failure = MCPConnectionError("close", error) if isinstance(error, Exception) else error
        if failure is not None:
            raise failure
