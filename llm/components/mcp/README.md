# MCP — 서버 연결 정의와 런타임 연결 계약

MCP 서버의 stdio/HTTP 연결 정보를 Project 데이터로 관리합니다. 실제 SDK 세션 생성·Tool 발견은 호스트가 주입하는 connector가 담당합니다. 정의 CRUD만으로 서버에 연결하지 않습니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | MCPComponent 공개 import입니다. |
| [component.py](component.py) | transport별 schema, connector 주입과 런타임 capability를 정의합니다. |

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

현재 Component가 모든 MCP SDK/서버와의 연결을 자동 제공하는 것은 아닙니다. connector가 연결·인증·정리를 구현해야 합니다. 어댑터 의미가 바뀌면 revision도 변경해 과거 승인·재개 binding을 잘못 재사용하지 않도록 합니다.

[상위 안내](../README.md)
