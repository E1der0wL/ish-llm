# Agent: 재사용 가능한 업무 정의

현재 독립 Project Agent와 root·Graph/Loop 위임의 공통 계약이다. 구현 및 검증 상태는 [B/C 기록](agent-refactor-verification.md)을 따른다.

Agent는 목적, 사용할 엔진, 모델/프롬프트, 리소스, 허용 Tool, 입출력 계약과 실행 정책을
저장한다. 최상위는 알려진 필드만 허용하고 Application 확장은 metadata에 둔다.
진행 상태와 결과는 Agent JSON에 쓰지 않는다. Workflow의 AgentNode와 Tool-capable Engine의
agent_run이 공통 AgentExecution으로 정의를 읽어 같은 Run 안에서 실행하고 Step으로 기록한다.
agent_run은 저장 agent_id와 input만 받으며 inline 권한 override를 받지 않는다.
부모 Tool/인자 제약을 넓히지 못하고 중첩 Tool 승인도 동일 Project 정책을 사용한다.

## 등록, 저장, 실행

Session의 `agent_ids`는 Application이 명시적으로 저장하는 참여자 목록이다.
`project.sessions.acreate(agent_ids=[...])`와 런타임이 분리된 Session의
`session.asave(agent_ids=[...])`로 편집한다. Project에 저장된 ID의 중복 없는 정렬된 목록을
사용하며 생성 시 생략은 빈 참여자 목록, 명시적 Null은 오류다. 읽기에서 필수 목록 소실을
빈 목록으로 메우지 않는다. 참여 중인 Agent는 삭제할 수 없고, Session의 soft delete도
그 참조를 해제하지 않는다. 복제·백업은 목록과 실제 Project Agent 참조를 함께 검증한다.
요청 1:N·병렬 실행의 통합 진행 상태는 [D 검증 기록](multi-agent-verification.md)을 따른다.

다음 예제의 `model`과 인증은 호출 환경에서 설정한다. 모델 API를 호출하는 실제 실행 예시다.
`workspace`는 Linux 경로를 사용한다.

```python
from llm.llm import LargeLanguageModel
from llm.engines.graph.agent import AgentNode
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.components.workflows import WorkflowGraph

agent_node = AgentNode(engines={"loop": LoopEngine()})
graph_engine = GraphEngine(handlers={"agent": agent_node})

async with LargeLanguageModel(workspace, engines={"graph": graph_engine, "loop": LoopEngine()}) as backend:
    project = await backend.projects.acreate(
        "Review", components=["workflows", "tools", "skills"])
    skills = await project.components.aget("skills")
    await skills.acreate({"markdown": "---\nname: review-rules\ndescription: 코드 검토 지침\n---\n정확성, 오류 처리, 테스트 누락을 검토하세요."},
                         identifier="review-rules")
    agents = project.agents
    await agents.acreate({
        "purpose": "코드 검토",
        "engine": "loop",
        "completion": {"model": model, "temperature": 0.2},
        "system_prompt": "문제점과 근거를 간결하게 설명하세요.",
        "engine_options": {'policy': {'max_iterations': 4}},
        "resources": {"skills": ["review-rules"]},
        "tools": [],
        "policy": {"timeout_seconds": 120},
        "input_schema": {"type": "object", "required": ["request"]},
        "output_schema": {"type": "object", "required": ["text"]},
        "metadata": {"ui": {"label": "검토 담당"}},  # Application 표시용 데이터.
    }, identifier="reviewer")
    workflow = (WorkflowGraph(entry="review", inputs={"request": "/prompt"},
                              outputs={"answer": "/answer"})
        .node("review", "agent", agent="reviewer", inputs={"request": "/request"},
              outputs={"answer": "/text"}, emit_text=True)
        .node("end", "end").connect("review", "end"))
    workflows = await project.components.aget("workflows")
    await workflows.acreate(workflow.to_dict(), identifier="review")
    await agents.acreate({"engine": "graph", "engine_options": {"workflow": "review"}},
                         identifier="coordinator")
    session = await project.sessions.acreate("Review request")
    await session.asave(agent_ids=['coordinator'])
    run, = await (await session.run.submit("이 코드를 검토해줘: ...", recipients=["coordinator"])).wait()
    response = await run.aresponse()
    steps = await run.steps.alist()
```

UI에서는 같은 JSON을 생성/수정하면 된다. 일반 CRUD는 create/load/list/save/update/delete,
비동기 API는 앞에 a를 붙인다. 저장 경로는 `<project>/agents/records/<id>.json`,
필수 Agent 목록은 `<project>/agents/records.manifest.json`이다. Workflow는
`<project>/workflows/records/<id>.json`이다. Agent는 Component 선택과 독립되며
Component 설정만 ProjectConfig.parameters["components"]에 저장한다.

## 정책과 리소스

| 필드 | 의미 |
| --- | --- |
| engine | 필수 EngineRegistry 등록 이름. 선택 Engine이 자신의 필수 인자를 검증 |
| purpose | 선택적인 업무 설명 |
| completion | Loop 모델 인자. model 필수, messages/tools는 엔진이 관리 |
| engine_options | 선택 엔진의 설정. Loop 반복/응답/Tool 시간 등 |
| tools | 일반 Tool 허용 목록. Project에서 사용 가능한 Tool만 선택 |
| resources.skills | 저장된 Skill ID 배열. SKILL.md 본문을 system prompt에 추가 |
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

Loop에 숨은 반복/모델/Tool 시간 제한은 없다. Project → Session → Agent의 명시값만
적용한다. Agent policy와 부모 Run/ToolExecutionScope의 Project 한도는
동시에 적용하며 Agent가 부모 권한이나 예산을 늘릴 수 없다. require_tool이 설정되면 Loop는
첫 성공 전 tool_choice=required를 사용하고, 모델이 무시하면 성공으로 처리하지 않는다.
출력 검증 실패나 정책 위반 후 자동 모델/Tool 재시도는 없다. 업무상 재수정은 Workflow의
검증 노드/분기/유한 루프로 표현한다.

Skill.resources의 URI는 참고 정보이며 자동 접근하지 않는다. 등록한 Skill/Data 파일은
허용한 skill_read_resource Tool로 명시적으로 조회한다. Project Tool 참조는 prepare/enable/실행을
하지 않으며 Agent의 Tool 허용 목록을 확대하지 않는다. 자료 revision이 바뀌면 참조를
명시적으로 갱신해야 한다. [Skill 계약](../../llm/components/skills/README.md)을 참고한다.

## Graph-backed Agent와 behavioral Agent

AgentRepository의 공통 저장 참조 owner는 resources.skills/prompt/mcp와 rag 선택을 제공합니다.
직접 레코드 삭제와 상위 Component 제거는 같은 참조를 확인하며 비활성 Agent의 저장된 정의도
보호에 포함합니다. RAG는 Component 전체 참조이며 특정 문서 ID나 세대를 고정하지 않습니다.
참조 해제는 원본 Agent 정의의 명시적 수정입니다. 이 선언은 모델 호출·MCP 연결·Tool 준비나
권한을 만들지 않으며, 기존 실행 시 정의/리소스 revision 검증도 유지합니다.

이 문서의 모델·리소스·Agent policy는 behavioral Agent에 해당한다. Graph-backed Agent는
다른 GraphEngine/handler environment로 Workflow를 보내는 이름이며 purpose/description/engine/
engine_options 및 비실행 metadata만 받는다. completion/system_prompt/tools/resources/
policy/input_schema/output_schema/output_format은 존재 자체를 for_agent에서 거부한다.
Workflow 노드 inputs/outputs/input_schema/output_schema는 계속 유효하다. Graph Agent 노드의
emit_text/output_format은 해당 Agent가 선택될 때 실행 전에 거부한다. 같은 환경은 workflow 노드를 사용한다.
Graph Agent는 부모 tools/tool_scope/capabilities를 그대로 전달하고 의미 없는 resource binding을
만들지 않는다. [예제와 중첩 계약](nested-workflows.md)을 참고한다.

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
허용하고 중복 이름을 거부한다. 실제 Tool 호출은 부모 ToolRuntime 승인/실행기/원장을 거친다.
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
전체 정의의 SHA256이다. save/update/delete/revise/update_prompt는 모두 필수
expected_revision으로 CAS를 검사한다. 접수 시 정의·revision·Engine binding을 고정하며
시작·재개 시 현재 정의가 달라졌으면 실행을 거부한다. 누락된 증거를 최신 설정으로 복원하지 않는다.

Agent Step에 정의/버전/리소스/입출력을 저장하고 자식 Step은 agent_step_id로 연결한다.
Graph 체크포인트는 Agent·Skill·MCP 정의와 어댑터 revision을 비교해 변경 후 재개를 거부한다.
완료한 노드의 출력 재사용과 불확실한 노드의 명시적 재시도 계약은 기존 Graph와 같다.
Agent가 별도 Run이나 체크포인트 수명주기를 만들지 않는다.

## 개발자 엔진 확장

Project의 EngineRegistry와 필요한 AgentNode(engines={"name": engine})에 등록하는 엔진은 순수 동기
`for_agent(definition) -> Engine`과 `execute(context)`를 구현한다. for_agent는 선택·재개
검증에서도 호출되므로 I/O를 수행하거나 공유 엔진을 수정하지 않는다. LoopEngine의 구현은
서브클래스의 준비/실행 메서드와 주입된 provider를 보존한다. 다른 엔진은 completion이 없어도 된다.

반환 Engine의 `steering_mode`는 등록 Engine의 선언과 일치해야 한다. 미시작 노드의
예약 탐색은 이 선언만 읽고 factory를 실행하지 않는다. 선택된 Agent는 승인 대기 전과
실행·재개 시 이 일치를 검증한다. 구현체에 따라 지원 방식이 달라지면 서로 다른 등록으로
표현한다. 선택하지 않은 Agent의 전체 설정이나 자원을 사전에 해석하지 않는다.

선택 엔진이 선언한 required_capabilities만 실행 context에 전달한다. 시스템 지침을 적용하는
방식은 엔진 계약이며 Tool 실행은 ToolExecutor를 사용해야 공유 권한/예산/이력이 적용된다.
trusted Python 엔진을 격리하는 장치는 아니다. GraphEngine은 for_agent를 제공하며 부모
GraphNodeContext의 실행기를 통해 하위 Workflow를 실행한다. Graph Agent는 data에 Workflow
출력을 반환한다. 최상위 GraphEngine이 최종 체크포인트와 PAUSED 발행을 소유한다. 일반
Agent 엔진은 checkpoint_name/validate_resume 계약이 있을 때만 EngineCheckpointScope를 통해
체크포인트와 승인을 부모에 연결한다. 자세한 계약은 [중첩 Workflow](nested-workflows.md)를 따른다.
호스트 코드나 등록 엔진 설정을 변경하면 AgentNode 또는 GraphEngine revision을 올려야 한다.

Prompt/Skill을 합성하는 Engine은 `with_instructions(context, before=..., after=...)`를
제공한다. AgentExecution은 지침 텍스트만 전달하고 Engine의 private 설정을 읽지 않는다.
재개 identity에는 실제 선택한 Agent만 포함한다. 승인 대기 전에 선택을 저장하고 완료된
Agent 및 실제 사용한 하위 Agent도 재개 전에 검사한다. 미사용 Agent 목록은 지문이 아니다.
`agent_run`의 선택된 자원은 서비스의 capability resolver로 연결하며 Tool 권한 확대는 없다.
Workflow에 명시된 참조/순환/역량의 사전 검증은 유지한다.

구형 Agent의 loop/skills/rag/mcp 최상위 필드는 자동 변환하지 않는다. engine을 명시하고
engine_options와 resources를 사용하는 현재 정의로 저장한다.


## 보존된 Workflow의 참조

Agent 삭제는 비활성 Workflow와 반복 본문도 확인한다. GraphEngine의
`workflow_handlers()`가 등록 action owner를 공개하고, 해당 handler의 순수
`agent_references(node)`가 선택 ID를 읽는다. AgentNode는 등록 이름과 무관하게 자기
`agent` 필드를 해석한다. Workflow 저장 Component는 handler의 사유 옵션을 추측하지 않는다.
등록 owner가 없어 참조 여부를 판단할 수 없으면 삭제를 거부한다. 참조 조회는 factory,
handler 실행, 모델 호출이나 Tool 효과를 발생시키지 않는다.
