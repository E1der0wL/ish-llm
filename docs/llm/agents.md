# Agent: 재사용 가능한 업무 정의

Agent는 목적, 사용할 엔진, 모델/프롬프트, 리소스, 허용 Tool, 입출력 계약과 실행 정책을
저장한다. 진행 상태와 결과는 Agent JSON에 쓰지 않는다. Workflow의 AgentNode가 정의를
읽어 같은 Run 안에서 실행하고 Agent/LLM/Tool Step으로 기록한다.

## 등록, 저장, 실행

다음 예제의 `model`과 인증은 호출 환경에서 설정한다. 모델 API를 호출하는 실제 실행 예시다.
`workspace`는 Linux 경로를 사용한다.

```python
from llm.llm import LargeLanguageModel
from llm.engines.graph.agent import AgentNode
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.components.workflows import WorkflowGraph

agent_node = AgentNode(engines={"loop": LoopEngine()})
graph_engine = GraphEngine("review", handlers={"agent": agent_node})

async with LargeLanguageModel(workspace, engines={"graph": graph_engine}) as backend:
    project = await backend.projects.acreate(
        "Review", components=["agents", "workflows", "tools", "skills"])
    skills = await project.components.aget("skills")
    await skills.acreate({"instructions": "정확성, 오류 처리, 테스트 누락을 검토하세요."},
                         identifier="review_rules")
    agents = await project.components.aget("agents")
    await agents.acreate({
        "purpose": "코드 검토",
        "engine": "loop",
        "completion": {"model": model, "temperature": 0.2},
        "system_prompt": "문제점과 근거를 간결하게 설명하세요.",
        "engine_options": {"max_iterations": 4},
        "resources": {"skills": ["review_rules"]},
        "tools": [],
        "policy": {"timeout_seconds": 120},
        "input_schema": {"type": "object", "required": ["request"]},
        "output_schema": {"type": "object", "required": ["text"]},
        "ui": {"label": "검토 담당"},  # 확장 키도 보존한다.
    }, identifier="reviewer")
    workflow = (WorkflowGraph(entry="review", inputs={"request": "/prompt"},
                              outputs={"answer": "/answer"})
        .node("review", "agent", agent="reviewer", inputs={"request": "/request"},
              outputs={"answer": "/text"}, emit_text=True)
        .node("end", "end").connect("review", "end"))
    workflows = await project.components.aget("workflows")
    await workflows.acreate(workflow.to_dict(), identifier="review")
    session = await project.sessions.acreate("Review request")
    run = await (await session.run.submit("이 코드를 검토해줘: ...", engine="graph")).wait()
    response = await run.aresponse()
    steps = await run.steps.alist()
```

UI에서는 같은 JSON을 생성/수정하면 된다. 일반 CRUD는 create/load/list/save/update/delete,
비동기 API는 앞에 a를 붙인다. 저장 경로는 `<project>/agents/records/<id>.json`,
Workflow는 `<project>/workflows/records/<id>.json`이다. Component 설정은 ProjectConfig.component_configurations에 저장한다.

## 정책과 리소스

| 필드 | 의미 |
| --- | --- |
| purpose, engine | 필수 업무 목적과 AgentNode에 등록된 엔진 이름 |
| completion | Loop 모델 인자. model 필수, messages/tools는 엔진이 관리 |
| engine_options | 선택 엔진의 설정. Loop 반복/응답/Tool 시간 등 |
| tools | 일반 Tool 허용 목록. Project에서 사용 가능한 Tool만 선택 |
| resources.skills | 저장된 Skill ID 배열. instructions를 system prompt에 추가 |
| resources.rag | true이면 Project corpus 검색 Tool rag_search를 추가 |
| resources.mcp | `{서버 ID: {공개 Tool 별칭: 원격 Tool 이름}}` |
| policy.max_tool_calls | Agent 호출 하나의 전체 Tool 시도 횟수 상한 |
| policy.require_tool | 성공적으로 완료한 Tool이 하나 이상이어야 성공 |
| policy.timeout_seconds | 연결과 엔진 실행을 포함한 Agent 실행 시간 상한 |
| input_schema | 매핑된 node.inputs 검증. 외부 JSON Schema 참조는 지원하지 않음 |
| output_format | text 또는 json. JSON이면 결과의 data에 파싱된 값 추가 |
| output_schema | 반환 객체 `{text, data?}` 검증. data 자체를 검증하려면 properties.data 사용 |

예를 들어 문서를 반드시 검색하도록 하려면 `tools: []`, `resources: {"rag": true}`,
`policy: {"require_tool": true, "max_tool_calls": 3}`로 저장한다. 먼저 Project에서 rag를
선택하고 문서를 등록해야 한다. 여러 Tool을 허용한 경우 require_tool은 그중 하나의 성공이며
특정 Tool 이름의 사용을 강제하는 정책은 아니다. RAG는 문서 ID별 접근 제한이 아닌 Project
단위 corpus 검색이다. corpus 내용은 검색 시점에 읽으며 검색 결과/근거는 Tool Step에 남는다.

Loop의 기본 제한은 8회 반복, 모델/Tool 각각 60초다. 명시적으로 등록한 Loop 설정이 이를
덮어쓰고 Agent engine_options가 마지막에 적용된다. policy와 부모 Run/ToolPolicy 한도는
동시에 적용하며 Agent가 부모 권한이나 예산을 늘릴 수 없다. require_tool이 설정되면 Loop는
첫 성공 전 tool_choice=required를 사용하고, 모델이 무시하면 성공으로 처리하지 않는다.
출력 검증 실패나 정책 위반 후 자동 모델/Tool 재시도는 없다. 업무상 재수정은 Workflow의
검증 노드/분기/유한 루프로 표현한다.

Skill.resources의 URI는 참고 정보이며 파일 읽기나 스크립트 실행을 자동으로 하지 않는다.
자료를 읽을 필요가 있다면 허용 Tool로 실행한다.

## MCP 연결 계약

MCP 서버 정의의 CRUD와 실제 접속은 분리한다. 호스트가
`MCPComponent(connector=connect_mcp, revision="1")`를 등록한다.
`connect_mcp(server_definition)`은 **ToolRegistry를 yield하는 async context manager**다.
어댑터는 MCP SDK를 사용한 초기화/도구 발견/호출/세션 정리를 구현한다. 이 변경에는
특정 SDK의 stdio/HTTP 전송 구현을 포함하지 않는다. 연결기는 실행 직전에만 호출한다.

Agent 정의의 다음 설정은 docs 서버의 read_document만 read_docs라는 이름으로 노출한다.

```json
{"resources": {"mcp": {"docs": {"read_docs": "read_document"}}}}
```

반환 ToolRegistry의 handler는 비동기이며 JSON 결과를 반환한다. Agent는 등록된 별칭만
허용하고 중복 이름을 거부한다. 실제 Tool 호출은 부모 ToolPolicy 승인/실행기/원장을 거친다.
연결/도구 발견 자체는 호스트 어댑터의 책임이며 Tool 실행 승인을 대신하지 않는다.
등록된 연결기가 없으면 Workflow 실행 전 실패한다. 실행 중 취소에도 Tool 정리 후 세션을 닫는다.
MCPComponent를 명시적으로 주입할 때 LargeLanguageModel.components는 기본 목록을 대체하므로
나머지 필요한 컴포넌트도 목록에 포함한다.

## UI 편집과 재현성

```python
snapshot = await agents.asnapshot("reviewer")
definition = snapshot["definition"]
definition["system_prompt"] = "변경된 검토 지침"
await agents.arevise("reviewer", definition, expected_revision=snapshot["revision"])
```

동시에 다른 편집이 저장되면 revise는 실패하며 다시 읽어야 한다. snapshot의 revision은
전체 정의의 SHA256이다. 기존 prompt/update_prompt도 같은 버전을 사용한다. save/update는
조건 없는 CRUD이므로 UI 동시 편집에는 revise를 권장한다. 실행 중인 Run은 읽어둔 정의를 유지한다.

Agent Step에 정의/버전/리소스/입출력을 저장하고 자식 Step은 agent_step_id로 연결한다.
Graph 체크포인트는 Agent·Skill·MCP 정의와 어댑터 revision을 비교해 변경 후 재개를 거부한다.
완료한 노드의 출력 재사용과 불확실한 노드의 명시적 재시도 계약은 기존 Graph와 같다.
Agent가 별도 Run이나 체크포인트 수명주기를 만들지 않는다.

## 개발자 엔진 확장

AgentNode(engines={"name": engine})에 등록하는 엔진은 순수 동기
`for_agent(definition) -> Engine`과 `execute(context)`를 구현한다. for_agent는 사전
검증에서도 호출되므로 I/O를 수행하거나 공유 엔진을 수정하지 않는다. LoopEngine의 구현은
서브클래스의 준비/실행 메서드와 주입된 provider를 보존한다. 다른 엔진은 completion이 없어도 된다.

선택 엔진이 선언한 required_capabilities만 실행 context에 전달한다. 시스템 지침을 적용하는
방식은 엔진 계약이며 Tool 실행은 ToolExecutor를 사용해야 공유 권한/예산/이력이 적용된다.
trusted Python 엔진을 격리하는 장치는 아니다. GraphEngine은 for_agent를 제공하며 부모
GraphNodeContext의 실행기를 통해 하위 Workflow를 실행한다. Graph Agent는 data에 Workflow
출력을 반환한다. 체크포인트 초기화와 PAUSED 발행은 최상위 GraphEngine이 소유하며 일반
Agent 엔진이 이를 임의로 발행하는 것은 거부한다. 자세한 계약은 [중첩 Workflow](nested-workflows.md)를 따른다.
호스트 코드나 등록 엔진 설정을 변경하면 AgentNode 또는 GraphEngine revision을 올려야 한다.

구형 Agent의 loop/skills/rag/mcp 최상위 필드는 자동 변환하지 않는다. engine을 명시하고
engine_options와 resources를 사용하는 현재 정의로 저장한다.
