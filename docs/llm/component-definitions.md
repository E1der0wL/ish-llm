# Skill · MCP · RAG · Agent · Workflow · Memory

이 컴포넌트들은 Project에 속하는 **정의와 데이터 저장소**다. 정의 CRUD만으로는
모델 호출, 서버 연결, 인덱싱, 그래프 스케줄링을 시작하지 않는다. 실제 실행은 Engine/Tool/실행 어댑터에서
담당하고, 관찰 결과는 기존 Run → Step 경로로 저장한다.

## 등록과 선택

`LargeLanguageModel`은 Tool을 포함한 일곱 종류를 기본 등록한다. Project를 만들거나
수정할 때 선택한 컴포넌트만 디렉토리를 생성한다. `components=[인스턴스, ...]`를
백엔드 생성자에 직접 전달하면 그 목록으로 기본 등록을 **대체**한다.

```python
from llm.llm import LargeLanguageModel

async with LargeLanguageModel("workspace") as backend:
    project = await backend.projects.acreate(
        "Research", components=["tools", "skills", "mcp", "rag", "agents", "workflows", "memory"]
    )
    agents = await project.components.aget("agents")
    await agents.acreate({
        "purpose": "코드 검토와 개선안 작성",
        "engine": "loop",
        "system_prompt": "근거와 함께 개선안을 제안하세요.",
        "completion": {"model": "gemini/gemini-2.5-flash", "temperature": 0.2},
        "resources": {"skills": ["code_review"]},
        "metadata": {"team": "backend"},
    }, identifier="reviewer")
```

위 모델 이름은 설정 예시이며 사용 가능 여부를 검증하거나 실제 API를 호출하지 않는다.
Memory는 영속 기억 CRUD·revision·이력과 모델용 Tool을 제공한다. 대화의 휘발성 저장 옵션과는
별개이며 사용법과 승인/검색 설정은 [Memory 안내](memory.md)를 참고한다.
ish에서는 `llm_plugin = plugin.get("llm")`로 얻은 모듈의 `LargeLanguageModel`,
`SkillComponent`, `MCPComponent`, `RAGComponent`, `AgentComponent`,
`WorkflowComponent`, `WorkflowGraph`도 사용할 수 있다.

| 클래스 / import | 선택 이름·디렉토리·capability | 레코드 하나의 의미 |
| --- | --- | --- |
| `SkillComponent` / `llm.components.skills` | `skills` | 작업 지침과 참고 리소스 |
| `MCPComponent` / `llm.components.mcp` | `mcp` | 서버 연결 설정 |
| `RAGComponent` / `llm.components.rag` | `rag` | metadata 레코드 (문서는 별도 문서 API로 등록) |
| `AgentComponent` / `llm.components.agents` | `agents` | 엔진·리소스·정책·입출력 계약을 가진 업무 정의 |
| `WorkflowComponent` / `llm.components.workflows` | `workflows` | 노드와 연결로 구성한 작업 그래프 |

공통 API는 `create`, `load`, `list`, `save`, `update`, `delete`,
`configure`, `configuration`이고 비동기 API에는 `a` 접두사가 붙는다.
동기 코드에서는 `project.components.agents.create(...)`처럼 사용할 수 있다.
아래의 `project` CRUD 예제들은 위 `async with`가 종료되기 전에 실행하는 코드 조각이다.
비동기 UI에서는 앞 예제처럼 `a` 접두사 메서드로 파일 I/O를 실행 루프 밖에서 처리한다.
`update`는 최상위 키를 병합하며 중첩 dict는 통째로 교체한다. `configure`도 전체 교체다.
Backend가 소유한 정의는 알려진 필드만 허용한다. Application 확장 키는 `metadata`에 둔다.
선택 Engine/handler/model 인자는 해당 구현체가 검증한다. [계약 inventory](schema-ownership.md)를 참고한다.

```python
agent = project.components.agents.load("reviewer")
agent["completion"]["temperature"] = 0.1
project.components.agents.save("reviewer", agent)
project.components.agents.update("reviewer", {"description": "검토 담당"})
all_agents = project.components.agents.list()  # {id: dict}
```

## 컴포넌트별 데이터

Skill은 `instructions`가 필수다. 리소스는 참고 정보이며 경로를 열거나 스크립트를
실행하지 않는다. SKILL.md 파일 자동 탐색/가져오기 기능은 포함하지 않는다.

```python
project.components.skills.create({
    "description": "코드 검토 규칙",
    "instructions": "정확성, 오류 처리, 테스트를 차례로 확인하세요.",
    "resources": [{"uri": "docs/llm/architecture.md", "description": "reference"}],
}, identifier="code_review")
```

MCP는 서버별 `transport`를 지정한다. 이 라이브러리의 정의 형식은 `stdio`일 때
`command`를, `streamable_http` 또는 `sse`일 때 HTTP(S) `url`을 요구한다.
`args`는 문자열 배열, `env`/`headers`는 문자열 값의 객체다. 접속·인증·도구 발견·도구 호출은
MCPComponent(connector=...)에 주입하는 연결 어댑터에서 구현한다. AgentNode는 저장된 서버
정의와 허용 Tool 별칭을 해석해 세션을 열고 닫는다. 레코드 생성만으로 연결하거나
ToolRegistry에 등록하지 않는다. 자세한 계약은 [Agent 안내](agents.md)를 참고한다.

```python
project.components.mcp.create({
    "transport": "stdio", "command": "python",
    "args": ["my_mcp_server.py"], "env": {"MODE": "read_only"},
}, identifier="local_docs")
project.components.mcp.create({
    "transport": "streamable_http", "url": "https://example.com/mcp",
    "headers": {}, "timeout": 30,
}, identifier="remote_docs")
```

RAG의 records API는 임의의 JSON 정의를 보존하며 자동 색인하지 않는다.

```python
project.components.rag.create({"description": "제품 설명서", "ui": {"label": "설명서"}}, identifier="product_docs")
```

실제 문서는 `aadd_document/aupdate_document/adelete_document`로 관리한다.
RAGComponent가 분할·임베딩·트리플 추출·Chroma/BM25/Kuzu 색인을 함께 수행한다.
`asearch`는 문서·관계·출처를 함께 반환한다. 모델 설정과 사용법은
[RAG Component 안내](rag-components.md)를 참고한다. 정의 저장과 문서 등록은 별개다.

Agent는 `purpose`와 `engine`이 필수이며 나머지는 열린 JSON 설정이다. LoopEngine은
completion.model을 요구한다. resources.skills는 Skill ID 목록, resources.rag는 Project
검색 사용 여부, resources.mcp는 서버 ID별 {모델에 공개할 별칭: 원격 Tool 이름}이다.
AgentNode는 이를 실행 시 검증/연결한다. 정의 생성 자체는 연결하지 않는다.
Workflow 안에서 호출되는 Agent도 동일한 정의를 참조한다. 상세 정책과 UI 편집 API는
[Agent 업무 정의](agents.md)에 정리했다.

## Workflow 조립과 JSON 저장

`WorkflowGraph`는 선택적인 빌더다. 같은 형식의 dict를 직접 만들어 저장해도 된다.
`schema_version: 1`, `entry`, `nodes` 객체와 `edges` 배열이 기본 형식이다.

```python
from llm.components.workflows import WorkflowGraph

body = (WorkflowGraph(entry="revise")
    .node("revise", "agent", agent="reviewer")
    .node("finish", "end")
    .connect("revise", "finish")
    .to_dict())

graph = (WorkflowGraph(entry="route", title="검토 Workflow")
    .node("route", "branch", cases=[{
        "port": "review", "when": {"path": "/needs_review", "op": "eq", "value": True}
    }], default="skip")
    .node("fork", "parallel", join="joined")
    .node("review", "agent", agent="reviewer")
    .node("search", "retrieval", corpus="product_docs")
    .node("joined", "join", wait="all")
    .node("refine", "loop", max_iterations=3, on_limit="continue", body=body,
          **{"while": {"path": "/needs_revision", "op": "eq", "value": True}})
    .node("done", "end")
    .connect("route", "fork", port="review")
    .connect("route", "done", port="skip")
    .connect("fork", "review")
    .connect("fork", "search")
    .connect("review", "joined")
    .connect("search", "joined")
    .connect("joined", "refine")
    .connect("refine", "done")
    .to_dict())

identifier = project.components.workflows.create(graph, identifier="review_flow")
project.components.workflows.validate(identifier)
editable = project.components.workflows.graph(identifier)  # 저장 원본과 분리된 빌더
project.components.workflows.save(identifier, editable.to_dict())
```

`to_dict`와 버전 1 레코드의 create/save/update/load에서 구조를 검증한다.
`WorkflowGraph.from_dict(data)`와 `validate_graph(data)`로 저장 없이 검증할 수도 있다.
`validate/graph` 핸들 메서드에는 `avalidate/agraph`가 있다.

### 그래프의 제어 계약

다음은 GraphEngine이 실행하는 제어 계약이다. 처리 노드의 실행 함수를 등록하여
사용한다. 실제 코드 수정/검증 예제는 [GraphEngine 안내](graph-engine.md)를 참고한다.

| 노드 type | 정의 및 실행기가 구현할 의미 |
| --- | --- |
| 사용자 정의 처리 노드 (`agent`, `tool`, `retrieval` 등) | 임의 설정을 저장하며 다음 노드는 하나. 타입별 처리기는 Engine에서 등록 |
| `branch` | 순서 있는 `cases` 중 처음 참인 port 선택, 없으면 필수 `default` port. port마다 간선 하나 |
| `parallel` | 연결된 둘 이상의 경로를 병렬 실행. 각 경로는 같은 입력의 독립 복사본으로 시작 |
| `join` | 소유 parallel의 모든 경로 완료를 기다림(`wait: all`). 기존 상태의 branches 키에 `{시작노드ID: 결과상태}`를 저장 |
| `loop` | `body` 그래프를 최대 `max_iterations`회 반복. 이전 회차 출력을 다음 입력으로 사용 |
| `end` | 현재 그래프를 종료. loop의 body 안에서는 해당 회차만 종료 |

`loop.while`은 회차 시작 전 검사하는 선택 조건이다. 없으면 정해진 횟수만큼 반복한다.
조건이 참인 상태에서 상한에 도달하면 `on_limit: continue`는 다음 노드로 진행,
`fail`은 Run 실패를 뜻한다. 조건 없는 횟수 반복은 정해진 횟수를 완료하면 정상 종료한다.
루프 안에 분기/병렬/다른 루프를 둘 수 있고 중첩 깊이는 32까지다.

조건은 `{"path": "/state/key", "op": "eq", "value": ...}`처럼 JSON Pointer와
비교 값을 저장한다. 연산자는 `eq/ne/lt/le/gt/ge/in/exists`다. `in`은 배열 값이 필요하고
`exists`에는 비교 값이 필요 없다. 경로가 없으면 exists와 일반 비교는 거짓으로,
타입이 맞지 않는 순서 비교는 실행 오류로 처리하는 계약이다. Python 코드 문자열이나
eval을 사용하지 않는다. 이 컴포넌트는 조건을 평가하지 않고 형태만 검증한다.

검증기는 없는 노드 참조, 도달 불가능한 노드, 중복 연결, 조건 port 누락, 합류 전
병렬 경로의 합침, 병렬 영역 외부에서의 진입, 합류 우회/조기 종료를 거부한다.
일반 간선은 DAG이며 순환은 상한이 명시된 `loop.body`로만 표현한다. 사용자 정의
처리 노드의 설정 의미나 처리기 등록, Agent/Tool 참조, 런타임 조건 결과는 검증하지 않는다.

Workflow는 저장·조회·수정·복제 모두 schema_version 1과 유효한 nodes/entry/edges를 요구한다.
버전 없는 임의 JSON을 보존하는 우회 경로는 없다. 추가 사용자 필드는 그대로 유지한다.
임의의 비그래프 데이터를 관리하려면 별도 Component를 정의한다.

## Engine에서 사용

```python
from llm.engines.base import BaseEngine

class MyEngine(BaseEngine):
    required_capabilities = ("agents", "skills", "workflows")

    async def run(self, context):
        agent_source = context.capabilities["agents"][0]
        reviewer = agent_source["records"]["reviewer"]
        workflows = context.capabilities["workflows"][0]["records"]
        graph = workflows["review_flow"]
        # 여기서 개발자가 노드 처리기/실행 정책을 적용한다.
        yield reviewer["purpose"]
```

일반 capability는 제공자별 튜플이고 각 제공자는
`{"configuration": {...}, "records": {id: {...}}}` 스냅샷을 반환한다.
필요한 것만 선언하면 해당 데이터만 읽는다. 스냅샷 수정은 저장 데이터에 반영되지 않는다.
다음 Run에서 새 설정을 읽으며 사용 중인 Run의 스냅샷은 바뀌지 않는다.
LoopEngine은 자동으로 Agent를 선택하거나 Workflow를 실행하지 않는다.

## 디렉토리

```text
<Project>/
  project.json
  tools/       records/<id>.json
  skills/      records/<id>.json
  mcp/         records/<id>.json
  rag/         records/<definition-id>.json, generations/<id>/
  agents/      records/<agent-id>.json
  workflows/   records/<workflow-id>.json
  sessions/       ... 기존 Session → Run → Step 및 대화 저장 구조
  logs/        ... 기존 도메인 로그
```

컴포넌트 파일의 생성/수정/삭제는 기존 잠금, 원자적 교체와 Project 수명 검사를 사용한다.
Project 복제는 설정과 JSON 레코드의 ID/연결을 보존한다. 외부 문서와 원격 서버 데이터,
런타임 연결, 재생성 가능한 인덱스는 자동 복제하지 않는다.

컴포넌트 설정은 `project.json`의 `config.parameters["components"]`에만 저장한다.
ComponentData.configure 편의 API도 이 설정을 갱신한다.
