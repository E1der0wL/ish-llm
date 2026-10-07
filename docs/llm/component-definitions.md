# Skill · MCP · RAG · Agent · Workflow · Memory

이 컴포넌트들은 Project에 속하는 정의와 데이터를 소유한다. 정의 CRUD만으로는 모델 호출,
서버 연결, 인덱싱, Workflow 실행을 시작하지 않는다. RAG 문서 등록처럼 명시적으로 실행하는
Component API는 자체 처리를 수행한다. Engine/Tool에서 호출한 작업의 관찰 결과는 기존
Run → Step 경로를 이용하며 별도 실행 도메인을 만들지 않는다.

## 등록과 선택

`LargeLanguageModel`은 [Component 목록](../../llm/components/README.md)의 구현을 기본 등록한다.
Project는 `components`에서 선택한 구현만 연결한다. backend 생성자에 `components=[인스턴스, ...]`를
넘기면 기본 등록 목록을 대체한다. 등록·선택만으로 모델 설정이나 MCP connector가 생기지 않는다.

`DefinitionComponent`의 기본 schema는 닫힌 빈 object다. 일반 `Component`의 JSON 저장/직렬화
기능과 정의의 허용 필드는 별개다. 열린 plugin 레코드가 필요한 구현은
`open_schema("plugin-owned record", category="implementation")`로 소유자를 명시한다.
내장 정의의 Application 확장 필드는 `metadata`에 둔다. metadata는 실행 권한이나 설정이 아니다.

`required_components = ("component_id", ...)`는 정확한 영속 Component ID 의존성이다.
capability 의존성이나 개별 Agent의 선택적 리소스 참조와 다르다. Project 생성·설정 저장·선택·제거
경계에서 누락을 거부한다. Backend는 의존성을 자동 설치하거나 선택하지 않는다.
[설정 소유권과 의존성 계약](project-authority.md)을 참고한다.

다음 함수는 정의만 저장하며 모델을 호출하지 않는다. 열린 backend에서 호출한다.

```python
from llm.llm import LargeLanguageModel

async def create_definitions(backend):
    project = await backend.projects.acreate(
        "Research", components=["tools", "skills", "mcp", "rag", "agents", "workflows", "memory"])
    skills = await project.components.aget("skills")
    await skills.acreate({
        "instructions": "정확성, 오류 처리, 테스트를 차례로 확인하세요.",
        "resources": [{"uri": "docs/llm/architecture.md", "description": "reference"}],
    }, identifier="code_review")
    agents = await project.components.aget("agents")
    await agents.acreate({
        "purpose": "코드 검토와 개선안 작성",
        "engine": "loop",
        "system_prompt": "근거와 함께 개선안을 제안하세요.",
        "completion": {"model": "gemini/gemini-2.5-flash", "temperature": 0.2},
        "resources": {"skills": ["code_review"]},
        "metadata": {"team": "backend"},
    }, identifier="reviewer")
    return project

# 호출 애플리케이션의 async 진입점에서:
# async with LargeLanguageModel("workspace") as backend:
#     project = await create_definitions(backend)
```

모델 이름은 설정 예시이며 실제 사용 가능 여부를 보증하지 않는다. 실행할 때 선택 Engine에
유효한 모델/인증을 명시한다. default Project/Session이나 자동 Engine 선택은 없다.

| 클래스 / import | 선택 이름·디렉토리 | 제공 capability / 레코드 의미 |
| --- | --- | --- |
| `SkillComponent` / `llm.components.skills` | skills | skills, tools / 지침과 참고 리소스 |
| `MCPComponent` / `llm.components.mcp` | mcp | mcp / 서버 연결 정의 |
| `RAGComponent` / `llm.components.rag` | rag | rag, tools / metadata 레코드; 실제 문서는 문서 API |
| `AgentComponent` / `llm.components.agents` | agents | agents, EngineRegistry 연결 시 tools / 업무 정의 |
| `WorkflowComponent` / `llm.components.workflows` | workflows | workflows / 실행할 그래프 정의 |

Memory의 영속 기억과 대화의 `conversation_storage="memory"`는 별개다.
[Memory 안내](memory.md)를 참고한다. ish에서는 `plugin.get("llm")` 모듈에서
LargeLanguageModel과 공개 Component/WorkflowGraph 클래스를 가져올 수 있다.

## 정의 CRUD와 설정

핸들의 공통 API는 `create/load/list/save/update/delete`, `configure/configuration`이며
비동기 API에는 `a` 접두사가 붙는다. UI에서는 `await project.components.aget(name)`으로 핸들을
얻고 비동기 API를 사용한다. 동기 코드는 `project.components.agents.load(...)`도 지원한다.
`update`는 최상위 키를 병합하므로 중첩 dict는 통째로 교체한다. `save`와 `configure`는 전체 교체다.
CAS 편집에는 snapshot과 expected_version을 사용한다.

```python
async def edit_definitions(project):
    agents = await project.components.aget("agents")
    agent = await agents.aload("reviewer")
    agent["completion"]["temperature"] = 0.1
    await agents.asave("reviewer", agent)
    await agents.aupdate("reviewer", {"description": "검토 담당"})
    all_agents = await agents.alist()  # {id: dict}

    skills = await project.components.aget("skills")
    await skills.aupdate("code_review", {"description": "코드 검토 규칙"})
    await skills.acreate({"instructions": "임시 지침"}, identifier="temporary")
    await skills.adelete("temporary")

    mcp = await project.components.aget("mcp")
    await mcp.acreate({
        "transport": "stdio", "command": "python",
        "args": ["my_mcp_server.py"], "env": {"MODE": "read_only"},
    }, identifier="local_docs")
    await mcp.acreate({
        "transport": "streamable_http", "url": "https://example.com/mcp", "headers": {},
    }, identifier="remote_docs")
    await mcp.aupdate("remote_docs", {"metadata": {"ui": {"label": "원격 설명서"}}})
    await mcp.adelete("remote_docs")

    rag = await project.components.aget("rag")
    await rag.acreate({"metadata": {"description": "제품 설명서"}}, identifier="product_docs")
    return all_agents
```

Skill은 instructions가 필수다. 허용 필드는 instructions/description/title/tags/resources/lineage/metadata다.
resources 항목은 uri, 선택 description/metadata만 허용하며 파일을 열거나 실행하지 않는다.
lineage는 parent/parent_revision의 불변 참조다. 다른 Skill 또는 Agent가 참조하면 삭제를 거부한다.
SKILL.md 자동 탐색/가져오기와는 별개다.

MCP의 최상위 필드는 transport/command/args/env/url/headers/metadata다. stdio는 command,
streamable_http/sse는 HTTP(S) url이 필수다. args는 문자열 배열이고 env/headers는 문자열 값의
객체다. `timeout`이나 범용 `options` 필드는 없다. 실제 접속/인증/발견/호출은
`MCPComponent(connector=...)`의 호스트 어댑터가 소유한다. AgentExecution은 선택 서버를 연결하고
정리하며, 레코드 CRUD는 연결하거나 Tool을 실행하지 않는다.

RAG records API는 **metadata만** 저장하며 색인하지 않는다. 실제 문서는
`aadd_document/aupdate_document/adelete_document`로 관리한다. 분할·임베딩·관계 추출과
Chroma/BM25/Kuzu 색인은 문서 API 책임이며 `asearch`는 같은 generation의 문서·관계·출처를 반환한다.
[필수 설정과 문서 API](rag-components.md)를 참고한다.

## Agent 실행 계약

Agent 최상위는 닫힌 계약이며 purpose와 engine이 필수다. 허용 필드는
purpose/description/engine/system_prompt/completion/engine_options/tools/resources/policy/
input_schema/output_schema/output_format/metadata다. 알 수 없는 키나 오타는 저장 시 실패한다.
Application 확장은 metadata에 둔다. completion은 선택 Engine의 provider 인자,
engine_options는 선택 Engine이 소유·검증하는 설정 경계다. 이를 이유로 Agent 전체가 열리지 않는다.

behavioral Agent의 resources는 prompt/skills/rag/mcp만 받는다. skills는 저장 ID 목록,
rag는 Project 검색 사용 여부, mcp는 `{서버 ID: {공개 Tool 별칭: 원격 Tool 이름}}`이다.
Loop 기반 Agent는 실행 시 유효한 completion.model이 필요하다. Graph-backed Agent는
purpose/description/engine/engine_options/metadata만 허용하고 behavioral 필드는 빈 값도 거부한다.
다른 Graph/handler 환경 선택은 Graph-backed Agent, 같은 환경의 Workflow 재사용은 workflow 노드를 쓴다.
상세 내용은 [Agent 안내](agents.md)와 [중첩 Workflow](nested-workflows.md)를 참고한다.

실행 consumer는 두 가지다.

- `AgentNode`: Workflow 노드에서 저장 Agent ID를 실행한다.
- `agent_run`: Tool을 사용할 수 있는 Engine이 저장 agent_id와 input으로 업무를 위임한다.

둘 다 `engines/agents.py::AgentExecution`을 사용한다. 별도 Run을 만들지 않고 같은 Run의
자식 Step으로 실행한다. agent_run에는 inline Engine/model/prompt/tools/권한 override를 넣을 수 없다.

```json
{"agent_id": "reviewer", "input": {"request": "이 변경을 검토해줘"}}
```

기본 backend는 AgentComponent에 EngineRegistry를 연결한다. 사용자 정의 등록 목록에서는
`AgentComponent(engines=registry)`를 명시해야 agent_run을 제공한다. Component 선택만으로
Agent가 자동 실행되지는 않는다. 부모 Tool 허용 목록과 인자 제약을 자식이 넓힐 수 없다.
중첩 Tool도 ToolContract와 동일 Project 승인 정책을 사용한다. 승인이 필요하면 Run은 PAUSED가
되며 policy/user 응답을 저장한 뒤 명시적으로 재개한다. 자동 응답도 즉시 효과를 실행하지 않는다.

## Workflow 조립과 JSON 저장

Workflow 최상위는 schema_version/entry/nodes/edges/initial_state/inputs/outputs/input_schema/
output_schema/metadata만 허용한다. schema_version은 1이다. 사용자 제목 등은 metadata에 둔다.
`WorkflowGraph`는 선택적인 빌더이며 같은 계약의 dict를 직접 저장해도 된다.
생성자는 알려진 최상위 인자만 받고 nodes/edges는 node/connect로 조립한다. 잘못된 최상위 키는
생성 시 거부하고, 완성된 구조는 to_dict에서 검증한다.

```python
from llm.components.workflows import WorkflowGraph

async def create_workflow(project):
    body = (WorkflowGraph(entry="revise")
        .node("revise", "agent", agent="reviewer")
        .node("finish", "end").connect("revise", "finish").to_dict())

    graph = (WorkflowGraph(entry="route", metadata={"title": "검토 Workflow"})
        .node("route", "branch", cases=[{
            "port": "review", "when": {"path": "/needs_review", "op": "eq", "value": True}
        }], default="skip")
        .node("fork", "parallel", join="joined")
        .node("review", "agent", agent="reviewer")
        .node("search", "retrieval", query="제품 설명서")
        .node("joined", "join", wait="all")
        .node("refine", "loop", max_iterations=3, on_limit="continue", body=body,
              **{"while": {"path": "/needs_revision", "op": "eq", "value": True}})
        .node("done", "end")
        .connect("route", "fork", port="review").connect("route", "done", port="skip")
        .connect("fork", "review").connect("fork", "search")
        .connect("review", "joined").connect("search", "joined")
        .connect("joined", "refine").connect("refine", "done").to_dict())

    workflows = await project.components.aget("workflows")
    identifier = await workflows.acreate(graph, identifier="review_flow")
    await workflows.avalidate(identifier)
    editable = await workflows.agraph(identifier)
    await workflows.asave(identifier, editable.to_dict())
    return graph
```

이 예제는 저장·구조 검증 예제다. 실행하려면 GraphEngine에 AgentNode와 아래 retrieval handler를
명시적으로 등록하고, Agent의 Engine 및 RAG 문서/필수 설정을 준비한다.

```python
from llm.core.schema import object_schema, field

class RetrievalNode:
    def __init__(self, search):
        self.search = search

    @staticmethod
    def describe_config():
        return object_schema({"query": field("string", minLength=1)}, required=["query"])

    async def __call__(self, node):
        result = await self.search(node.definition["query"])
        return {"evidence": result}
```

Graph는 common node 필드(type/inputs/outputs/input_schema/output_schema/pause_before/
resume_schema/timeout_seconds/metadata)를 소유한다. action 고유 필드는 선택된 handler가
describe_config() 또는 validate(node, context)로 실행 전에 검증한다. action variant의 열린
경계는 **선택 handler가 의미를 소유하기 때문**이며 미래의 임의 키를 무조건 허용하기 위해서가 아니다.
위 query는 RetrievalNode의 계약이며 GraphEngine이나 RAG record의 필드가 아니다.
예를 들어 같은 Project의 `rag = await project.components.aget("rag")` 핸들을 얻고
`RetrievalNode(rag.asearch)`로 연결한다. handler는 GraphNodeContext 한 개를 받고 반환 dict를
상태에 병합한다. Project가 바뀌면 해당 Project 핸들을 연결한다. 더 일반적인 capability
주입이 필요하면 handler의 required_capabilities/validate 공개 계약을 사용한다.

### 제어 구조

| 노드 type | 계약 |
| --- | --- |
| 등록한 action (`agent`, `tool`, `retrieval` 등) | 공통 필드는 Graph, 고유 필드는 선택 handler가 검증. 다음 노드는 하나 |
| branch | 순서대로 처음 참인 case의 port 선택, 없으면 필수 default. port마다 간선 하나 |
| parallel | 연결된 둘 이상의 경로를 독립 상태 복사본으로 실행하고 지정 join에서 합류 |
| join | 소유 parallel의 모든 경로를 기다림(wait=all). branches에 시작 노드 ID별 결과 저장 |
| loop | 유한 max_iterations와 on_limit, body Workflow. 이전 회차 결과를 다음 입력으로 전달 |
| workflow | 같은 Graph 환경에서 저장 workflow ID 실행. 중첩 binding/checkpoint는 소유 Run에 연결 |
| end | 현재 그래프 종료. loop.body 안에서는 해당 회차 종료 |

loop.while은 회차 시작 전 검사한다. 없으면 지정 횟수 반복이다. 조건이 참인데 상한에 도달하면
on_limit=continue는 다음 노드로 진행하고 fail은 실패한다. 중첩 loop/parallel/branch를 지원한다.
Backend는 임의의 고정 중첩 깊이 제한을 추가하지 않는다. 명시적으로 설정한 Graph
policy.max_nested_depth는 실행 정책이며 loop.body/cycle 구조 검증과 구분한다.

조건은 JSON Pointer의 path와 eq/ne/lt/le/gt/ge/in/exists다. in은 배열 value가 필요하고 exists에는
value가 필요 없다. 경로가 없으면 거짓이며, 비교할 수 없는 타입의 순서 비교는 실행 오류다.
Python 코드나 eval을 사용하지 않는다. WorkflowComponent는 조건 형태와 구조를 검증하고
GraphEngine이 실제 상태에서 평가한다.

없는 노드, 도달 불가능한 노드, 중복 간선, 조건 port 누락, 잘못된 병렬 합류를 거부한다.
일반 간선은 DAG이며 순환은 유한 loop.body로 표현한다. Component는 handler 등록/고유 설정이나
Agent/Tool 리소스를 실행하지 않는다. 이 검증은 Graph 실행 준비와 선택 handler가 소유한다.
create/save/update/load와 WorkflowGraph.from_dict/validate_graph도 구조 검증을 수행한다.
최상위 title/ui 같은 확장을 metadata로 자동 이동하는 migration은 없다.

## Capability를 읽는 Engine

```python
from llm.engines.base import BaseEngine

class DescribeEngine(BaseEngine):
    required_capabilities = ("agents", "skills", "workflows")

    async def run(self, context):
        reviewer = context.capabilities["agents"][0]["records"]["reviewer"]
        yield self.delta_event(context, reviewer["purpose"])
```

정의 capability는 제공자별 tuple이며 제공자는 configuration/records 사본을 반환한다.
실행 어댑터가 있는 capability는 자체 공개 인터페이스를 가지므로 모든 capability가 같은 dict는 아니다.
Engine은 EngineEvent로 관찰을 전달하며 영속 도메인 파일을 직접 쓰지 않는다.
Loop는 모델이 agent_run을 선택할 수 있게 하지만 Agent나 Workflow를 임의로 자동 선택하지 않는다.

## 저장과 수명

```text
<Project>/
  project.json
  tools/<tool-id>/<tool-id>.py       # Project Python Tool 패키지
  skills/records/<id>.json
  mcp/records/<id>.json
  rag/records/<id>.json             # metadata만
  rag/generations/<id>/             # 실제 문서·벡터·그래프 색인
  agents/records/<id>.json
  workflows/records/<id>.json
  memory/                          # 전용 기억·이력 구조
  sessions/                        # Session → Run → Step와 대화
  logs/
```

ComponentData는 Project 수명 검사·잠금·원자적 저장 경계를 이용한다. 설정은 project.json의
config.parameters.components에만 저장하고 configure도 이 원본을 갱신한다. component.json은 없다.
일반 JSON 정의의 clone은 ID/참조를 보존한다. RAG clone은 불변 원문·벡터·관계를 복제하여
색인을 재구축하며 모델을 재호출하지 않는다. 외부 서버나 살아 있는 연결은 복제하지 않는다.
특수 데이터는 각 Component의 clone 계약을 따른다.
