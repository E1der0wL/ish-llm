# MCP — 서버 연결 정의와 런타임 연결 계약

MCP 연결 정의와 발견한 Tool 목록을 Project 데이터로 관리합니다. 실제 연결은 주입한 connector가 담당하며 표준 SDK 어댑터 `llm.providers.mcp.MCPConnector`도 제공합니다. 정의 CRUD만으로 서버에 연결하지 않습니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | MCPComponent 공개 import입니다. |
| [component.py](component.py) | transport별 schema, connector 주입과 런타임 capability를 정의합니다. |
| [data.py](data.py) | 명시적 Tool 발견, 발견 스냅샷, 선택한 원격 Tool의 실행 연결입니다. |

## 정의 저장 예

```python
mcp = await project.components.aget("mcp")
await mcp.acreate({
    "transport": "streamable_http",
    "url": "https://your-mcp-server/mcp",
}, identifier="company-docs")
```

`stdio`에는 command와 선택적인 args/env, HTTP 계열에는 url과 선택적인 headers를 저장합니다. 경로는 `<project>/mcp/records/<id>.json`입니다.

## 실행 연결

`MCPComponent(connector=..., revision="...")`로 실행 어댑터를 주입합니다. `connector(definition)`은 ToolRegistry를 제공하는 async context manager여야 합니다. Agent가 사용할 서버·Tool을 resources.mcp에 명시하고, 실행 중 연결을 열어 공통 ToolExecutor로 호출합니다.

표준 어댑터는 선택 의존성 `mcp>=1.30,<2`가 필요하며 stdio와 Streamable HTTP를 지원합니다.
SSE 정의는 다른 명시적 connector를 연결할 때 사용할 수 있습니다. 초기화, 발견 pagination,
호출 및 연결 정리를 SDK에 위임합니다. 취소는 호출과 연결로 전파하며 오류가 났다고 Tool을
자동 재호출하지 않습니다. 인증은 저장된 env/headers 또는 실행 시 `credentials(definition)`
callback으로 공급할 수 있습니다. callback 결과는 인증 필드로 제한하며 저장하지 않습니다.
`timeout_seconds`는 명시했을 때만 적용합니다. stdio는 SDK의 환경 상속 동작을 사용하며
OS sandbox가 아닙니다. 서버 command를 실행하는 `aprepare`는 신뢰한 애플리케이션 API입니다.
TUI 호스트는 `MCPConnector(errlog=...)`에 파일 등의 stderr 대상을 명시할 수 있습니다.
생략하면 stdio SDK의 stderr 동작을 따르며 backend가 서버 로그를 임의로 버리지 않습니다.

```python
from llm.providers.mcp import MCPConnector
from llm.components.mcp import MCPComponent

component = MCPComponent(connector=MCPConnector(), revision="company-adapter-1")
# 위 component를 backend 생성 시 등록하고 Project에서 mcp를 선택합니다.
mcp = await project.components.aget("mcp")
await mcp.aprepare("company-docs")  # 실제 연결/발견, Tool 효과는 실행하지 않음
tools = await mcp.alist_tools("company-docs")  # 저장 목록 조회, 네트워크 호출 없음
await mcp.aconfigure({"config": {"tools": {
    "company_search": {"server": "company-docs", "tool": "search"}
}}})
```

선택한 alias는 일반 Loop/Graph의 tools capability로 제공됩니다. 호출용 연결은 공통
ToolExecutor의 승인 이후에 열립니다. 연결 정의 또는 connector revision이 바뀌면 재준비가
필요합니다. 실행 중 원격 schema가 바뀐 경우에도 원격 Tool 호출 전에 거부합니다.
서버 annotations는 승인 선언이 아니며, 표준 어댑터는 approval decision을 요구합니다.
Project 정책의 자동 승인도 receipt를 저장하며 실행에는 명시적 resume이 필요합니다.
신뢰한 호스트만 `contract(name, remote_definition)` callback으로 실제 계약을 선언합니다.

Agent의 기존 `resources.mcp` 경로도 유지됩니다. 어댑터 의미가 바뀌면 revision을 변경하여
과거 승인·재개 binding을 거부하세요. 오류 코드는 `mcp_connection_failed`, `mcp_tool_failed`이며
원격 effect가 불명확한 실패는 자동 재실행하지 않습니다. OAuth 로그인 UI,
resources/prompts/sampling/elicitation, 연결 pool과 자동 재연결은 이번 범위가 아닙니다.

[상위 안내](../README.md)
