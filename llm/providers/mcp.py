"""MCP SDK 어댑터. 도메인/승인/재시도는 소유하지 않고 연결·발견·호출만 수행한다."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from copy import deepcopy
import inspect
import math
from dataclasses import dataclass
from enum import StrEnum

from llm.components.mcp.records import MCPDefinition, MCPTransport

from llm.core.tool_contracts import ToolContract
from llm.components.tools.registry import Tool, ToolRegistry
from llm.errors import CodedError
from llm.core.contracts import Diagnostic, JsonValue
from llm.services.runtime.tools import ToolExecutionError
from llm.core.tool_contracts import ToolEffect, ToolIsolation


class MCPStage(StrEnum):
    INITIALIZE = "initialize"
    DISCOVER = "discover"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class MCPConnectionDetails(JsonValue):
    stage: str
    exception_type: str


@dataclass(frozen=True, slots=True)
class MCPToolDetails(JsonValue):
    effect: str
    retryable: bool = False


class MCPConnectionError(CodedError, RuntimeError):
    code = "mcp_connection_failed"
    MESSAGE = "MCP {stage} failed"

    def __init__(self, stage, error):
        super().__init__(self.MESSAGE.format(stage=stage))
        self.diagnostic = Diagnostic(self.code, str(self), details=MCPConnectionDetails(stage, type(error).__name__).to_dict())
        self.__cause__ = error


class MCPToolError(ToolExecutionError):
    code = "mcp_tool_failed"
    TRANSPORT_FAILED = "MCP Tool transport failed; remote effects are uncertain"
    REMOTE_FAILED = "MCP server reported a Tool error; remote effects are uncertain"

    @property
    def diagnostic(self):
        return Diagnostic(self.code, str(self), details=MCPToolDetails(self.effect).to_dict())


class MCPSDK:
    MODULE = "mcp"
    ERRLOG = "errlog"
    READ_TIMEOUT = "read_timeout_seconds"
    PAGINATION = "params"
    STREAM_COUNT = 2
    END_CURSOR = None
    EMPTY_DESCRIPTION = ""
    DUMP = dict(mode="json", by_alias=True, exclude_none=True)


class MCPConnector:
    """명시적으로 주입하는 표준 SDK connector. SDK import는 최초 연결 때 수행한다.

    credentials(definition)는 실행 시 인증 필드(headers/env)만 반환한다. 값은 저장하지
    않는다. contract(name, definition)는 신뢰한 Host 선언이며 서버 annotations는
    승인/재시도 권한으로 사용하지 않는다. 미선언 원격 Tool에는 approval_required를
    강제한다: 외부 실행 효과를 추측해서 승인을 생략하지 않기 위한 경계다.
    """

    REMOTE_CONTRACT = ToolContract(
        revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE,
        approval_required=True, operation_key_required=False, retry_fields=())

    INVALID_CALLBACK = "MCP credentials/contract must be callable"
    INVALID_TIMEOUT = "MCP timeout_seconds must be positive and finite"
    UNSUPPORTED_TRANSPORT = "MCPConnector supports stdio and streamable_http; inject another connector for other transports"
    INVALID_CONTRACT = "MCP contract callback must return ToolContract"
    REPEATED_CURSOR = "MCP server repeated a discovery cursor"
    MINIMUM_TIMEOUT = 0
    NO_CREDENTIALS = None
    NO_CONTRACT = None
    NO_TIMEOUT = None
    NO_ERRLOG = None

    def __init__(self, *, credentials=NO_CREDENTIALS, contract=NO_CONTRACT, timeout_seconds=NO_TIMEOUT, errlog=NO_ERRLOG):
        if credentials is not self.NO_CREDENTIALS and not callable(credentials) or contract is not self.NO_CONTRACT and not callable(contract):
            raise TypeError(self.INVALID_CALLBACK)
        if timeout_seconds is not self.NO_TIMEOUT and (type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or timeout_seconds <= self.MINIMUM_TIMEOUT):
            raise ValueError(self.INVALID_TIMEOUT)
        self.credentials, self.contract = credentials, contract
        self.timeout_seconds, self.errlog = timeout_seconds, errlog

    async def _connect(self, stack, definition):
        # 무거운 SDK 초기화와 선택 의존성 오류를 실행 경계에 한정한다.
        import importlib
        sdk = await asyncio.to_thread(importlib.import_module, MCPSDK.MODULE)
        definition = MCPDefinition(definition)
        transport = definition.transport
        if self.credentials is not self.NO_CREDENTIALS:
            supplied = self.credentials(definition.to_dict())
            supplied = await supplied if inspect.isawaitable(supplied) else supplied
            definition = definition.with_credentials(supplied)
        if transport == MCPTransport.STDIO:
            from mcp.client.stdio import stdio_client
            params = sdk.StdioServerParameters(**definition.stdio_parameters())
            streams = await stack.enter_async_context(stdio_client(params, **({MCPSDK.ERRLOG: self.errlog} if self.errlog is not self.NO_ERRLOG else {})))
        elif transport == MCPTransport.STREAMABLE_HTTP:
            import httpx2
            from mcp.client.streamable_http import streamable_http_client
            # The connector owns its explicit timeout. None means no additional
            # deadline; do not inherit the SDK factory's 30/300 second defaults.
            client = await stack.enter_async_context(httpx2.AsyncClient(
                headers=definition.headers, timeout=self.timeout_seconds))
            streams = await stack.enter_async_context(
                streamable_http_client(definition.url, http_client=client))
        else:
            raise ValueError(self.UNSUPPORTED_TRANSPORT)
        session = await stack.enter_async_context(sdk.ClientSession(*streams[:MCPSDK.STREAM_COUNT],
            **({MCPSDK.READ_TIMEOUT: self.timeout_seconds} if self.timeout_seconds is not self.NO_TIMEOUT else {})))
        await session.initialize()
        return session

    async def _discover(self, session):
        from mcp.types import PaginatedRequestParams
        registry, cursor, seen = ToolRegistry(), MCPSDK.END_CURSOR, set()
        while True:
            page = await session.list_tools(
                **({MCPSDK.PAGINATION: PaginatedRequestParams(cursor=cursor)} if cursor is not MCPSDK.END_CURSOR else {}))
            for item in page.tools:
                contract = (self.contract(item.name, item.model_dump(**MCPSDK.DUMP))
                            if self.contract is not self.NO_CONTRACT else self.REMOTE_CONTRACT)
                if not isinstance(contract, ToolContract):
                    raise TypeError(self.INVALID_CONTRACT)
                async def invoke(arguments, name=item.name):
                    try:
                        result = await session.call_tool(name, arguments=deepcopy(arguments))
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        raise MCPToolError(MCPToolError.TRANSPORT_FAILED) from error
                    if result.is_error:
                        raise MCPToolError(MCPToolError.REMOTE_FAILED)
                    return result.model_dump(**MCPSDK.DUMP)
                registry.register(Tool(item.name, item.description or MCPSDK.EMPTY_DESCRIPTION, item.input_schema, invoke, contract=contract))
            cursor = page.next_cursor
            if cursor is MCPSDK.END_CURSOR:
                return registry
            if cursor in seen:
                raise ValueError(self.REPEATED_CURSOR)
            seen.add(cursor)

    @asynccontextmanager
    async def __call__(self, definition):
        failure, stage = None, MCPStage.INITIALIZE
        try:
            async with AsyncExitStack() as stack:
                try:
                    async with asyncio.timeout(self.timeout_seconds):
                        session = await self._connect(stack, deepcopy(definition))
                        stage = MCPStage.DISCOVER
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
                failure = MCPConnectionError(MCPStage.CLOSE, error) if isinstance(error, Exception) else error
        if failure is not None:
            raise failure
