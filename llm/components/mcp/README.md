# MCP — 서버 연결 정의와 런타임 연결 계약

MCP 연결 정의와 발견한 Tool 목록을 Project 데이터로 관리합니다. 실제 연결은 주입한 connector가 담당하며 표준 SDK 어댑터 `llm.providers.mcp.MCPConnector`도 제공합니다. 정의 CRUD만으로 서버에 연결하지 않습니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | MCPComponent 공개 import입니다. |
| [component.py](component.py) | transport별 schema, connector 주입과 런타임 capability를 정의합니다. |
| [records.py](records.py) | 정의·선택 Schema, 발견 기록·실행 binding·런타임 연결의 타입과 Reader/Writer입니다. |
| [catalogs.py](catalogs.py) | 발견 기록의 경로와 원자적 파일 저장을 소유합니다. |
| [data.py](data.py) | 명시적 발견, 정의·connector 최신성 검사와 선택한 원격 Tool의 실행 연결입니다. |
| [access.py](access.py) | 애플리케이션이 연 연결 범위에서 상태·리소스·prompts·선택 Tool을 재사용합니다. |
| [client.py](client.py) | 선택적 connector 기능의 공개 계약입니다. SDK 세부 옵션은 해석하지 않습니다. |

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

표준 어댑터는 MCP 2 SDK의 stdio와 Streamable HTTP를 지원합니다. 의존성 범위의 원본은
[pyproject.toml](../../../pyproject.toml)의 `mcp` extra이며 검증한 설치 조합은
[Linux test lock](../../../tests/llm/dependencies-linux.lock)에 기록합니다.
SSE 정의는 다른 명시적 connector를 연결할 때 사용할 수 있습니다. 초기화, 발견 pagination,
호출 및 연결 정리를 SDK에 위임합니다. 취소는 호출과 연결로 전파하며 오류가 났다고 Tool을
자동 재호출하지 않습니다. 인증은 저장된 env/headers 또는 실행 시 `credentials(definition)`
callback으로 공급할 수 있습니다. callback 결과는 인증 필드로 제한하며 저장하지 않습니다.
`timeout_seconds`는 명시했을 때 초기화·발견, SDK 요청, HTTP I/O에 적용합니다.
미설정이면 SDK timeout 인자를 생략하며 SDK의 native default를 따릅니다. 명시적 None은
지원되는 timeout을 해제합니다. backend가 SDK 기본값을 복제하지 않으며 인증 headers는
연결에서만 사용합니다.
stdio는 SDK의 환경 상속 동작을 사용하며
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

정의·설정 필드는 MCPDefinition/MCPConfiguration이 선언하며, Agent와 SDK 어댑터는
런타임 연결과 전송용 Projection을 사용합니다. SDK 인자·직렬화 옵션·오류 단계는 어댑터가
소유합니다. 인증 사본만 전송에 반영하고 원본 정의·저장 설정의 생략 상태는 보존합니다.

발견 기록은 MCPCatalog/MCPToolSnapshot의 완전한 현재 형식으로 읽습니다. version,
connector, tools 및 각 Tool의 contract/classification 필드는 필수이며, classification의
명시적 null과 필드 소실을 구분합니다. 저장된 classification 객체의 필수 필드가 빠져도
새 선언용 기본값으로 복원하지 않습니다. 손상된 기록의 조회·선택은 연결 전에 실패하고
파일을 자동 복구하지 않습니다. 정상 저장 JSON, 경로, binding hash와 공개 반환 모양은
유지합니다. 메모리 타입을 별도 정의나 실행 기록으로 중복 저장하지 않습니다.

MCP 2 전환의 구현 revision은 `MCPComponent.REVISION`이 소유합니다. 이전 revision의 발견
catalog·승인 binding은 stale로 거부하며 자동 변환하지 않습니다. Agent의 `resources.mcp`
경로도 유지됩니다. 어댑터 의미가 바뀌면 revision을 변경하여
과거 승인·재개 binding을 거부하세요. 오류 코드는 `mcp_connection_failed`, `mcp_tool_failed`,
`mcp_authentication_required`, `mcp_access_denied`, `mcp_timeout`으로 구분합니다.
원격 effect가 불명확한 실패는 자동 재실행하지 않습니다.

## 명시적 연결과 리소스

```python
async with mcp.connect("company-docs") as connection:
    status = await connection.aget_status()
    resources = await connection.alist_resources()
    templates = await connection.alist_templates()
    document = await connection.aread_resource("company://manual")  # 실제 서버 URI
    # 다음 페이지가 있으면 반환 nextCursor를 cursor=...로 명시합니다.
```

같은 범위에서 여러 작업이 연결을 공유합니다. 일회성 `mcp.aget_status(id)`,
`alist_resources(id)`, `alist_templates(id)`, `aread_resource(id, uri)`도 지원합니다.
리소스는 원격 JSON 결과 그대로 반환하며 모델에 자동 주입하지 않습니다. 서버 지침도
상태의 관찰값일 뿐입니다. 지원하지 않는 connector 기능은 명확하게 실패합니다.

`connection.aprepare()`로 catalog를 갱신하고 `connection.aget_tools({alias: remote_name})`로
그 연결에 묶인 ToolRegistry를 선택할 수 있습니다. 애플리케이션이 이미 연결을 연 경우의
선택적 API이며, 모델 실행은 여전히 ToolExecutor의 승인·인자·receipt 검사를 거쳐야 합니다.
일반 Project Tool 경로는 승인 이후 연결하는 기존 동작을 유지합니다. 범위 종료 뒤 handler는
사용할 수 없고 진행 중 요청은 취소됩니다. 자동 pool/TTL/재연결/Tool 재실행은 없습니다.

## 인증과 서버 질문

`MCPConnector(oauth=MCPOAuth(...), elicitation=async_handler)`를 명시할 수 있습니다.
`MCPOAuth`는 `llm.providers.mcp_auth`에서 가져옵니다. OAuth client metadata와 SDK
TokenStorage, 명시적 server_url, 로그인 URL 전달 함수, callback 응답 함수는 애플리케이션이 주입합니다.
서버별 credential namespace를 분리해야 합니다. llm이 브라우저나 토큰 저장소를 선택하지 않습니다.

elicitation handler는 공통 InteractionRequest를 받고 InteractionResponse를 반환합니다.
응답은 `request.respond("reply", value={"action": "accept", "content": {...}})` 또는
action `decline`/`cancel`입니다. 폼은 원격 schema와 요청 지문으로 검증하며 URL은 자동으로
열지 않습니다. 이 질문은 살아 있는 MCP 요청에 묶여 있으므로 연결 종료 후 재개하지 않습니다.
sampling, prompts, 전용 UI renderer는 제공하지 않습니다.

자세한 소유권과 취소 계약은 [MCP 실행 계약](../../../docs/llm/mcp-runtime.md)을 참고하세요.

[상위 안내](../README.md)
