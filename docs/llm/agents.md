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

## Engine별 설정 폼 조회

```python
# 공통 Agent 계약과 등록 Engine 이름 목록. 실행 가능한 기본 Agent를 만들지 않는다.
catalog = await project.agents.adescribe()

# 실제 등록 이름을 선택한다. for_agent/model/Tool 실행 없이 스키마만 조회한다.
form = await project.agents.adescribe(engine="loop")
schema = form["schema"]
snapshot = await project.agents.asnapshot("reviewer")
values = snapshot["definition"]
```

동기 API는 `project.agents.describe(engine=...)`다. 반환 외형은 계속
`schema`, `storage_version`, `engines`이며 선택 스키마의 `properties.engine.const`가
등록 이름을 지정한다. 이름을 생략하면 공통 스키마를 반환하고 특정 Engine을 선택하지 않는다.
조회 스키마와 저장된 값은 별개다. 모델·API 키 등 현재 값은 snapshot으로 읽고,
수정한 전체 정의는 `arevise(..., expected_revision=snapshot["revision"])`로 저장한다.

Loop는 선택 Adapter의 모델 스키마를 `completion`에, 자신의 `describe_config()`를
`engine_options`에 결합한다. Graph는 `purpose`, `description`, `engine`,
`engine_options`, `metadata`만 공개하고 `engine_options.workflow`를 필수로 표시한다.
Graph 폼에는 모델·Tool·behavior 설정을 넣지 않는다. 조회 결과는 독립 사본이며
미설정값을 채우거나 설정·Agent·Run을 생성하지 않는다.

선택 Engine이 순수 동기 `describe_agent()`를 제공하면 그 스키마는 UI 조회뿐 아니라
실제 Agent binding에서도 검사한다. 공통 Agent 계약과의 교집합만 허용하며,
추가 의미 검증은 기존 `for_agent()`가 계속 담당한다. 스키마 검증만으로 Project 상한,
참조 존재, 승인·재개 적합성, 실제 서버의 모델 지원까지 보장하지 않는다.
특히 Loop의 모델 미지정 초안은 계속 저장할 수 있으나 실제 호출에는 모델이 필요하다.

Engine 전용 스키마를 제공하지 않는 사용자 정의 Engine은 기존 실행 계약을 유지한다.
선택 폼 조회는 `Engine does not provide describe_agent`로 실패하며, Backend가 폼을
추측하거나 내부 속성에서 옵션을 수집하지 않는다. 공통 목록 조회는 영향을 받지 않는다.
Hub는 조회된 타입·enum·required·제약을 입력 UI로 표현하고, 생략/null을 구별해 저장한다.
위젯·배치·번역·preset과 초기 입력값은 Application 책임이다. 열린 SDK 인자는
`additionalProperties`와 ownership 정보를 유지하므로 추가 JSON 편집도 제공할 수 있다.

AgentDefinition이 정의 필드·리소스 선택·자체 policy·출력 형식과 제약을 선언한다.
공개 Schema에는 enum 객체 표현을 노출하지 않고 기존 문자열 키/값을 제공하여 검증 순서와
진단을 유지한다. 정의 revision의 JSON 정렬·UTF-8·비 ASCII 처리와 바이트 의미는 바꾸지 않는다.
AgentProfile은 같은 정규 검증기를 거친 정의 사본을 속성·정책·자원 선택·입출력 검증 API로
제공한다. 선택되지 않은 옵션을 원본에 채우지 않으며 `to_dict()`는 생략/null/명시값을 그대로
반환한다. AgentPolicy와 AgentResourceSelection은 그 검증된 정의의 메모리 Projection이다.
Root/Graph/Tool 위임의 AgentResourceResolver는 같은 정의 선택·alias/부모 Tool 범위 검사와
MCP 연결·Prompt/Skill 합성을 사용한다. AgentResources/AgentMCPResource는 기존 binding의
자원 snapshot을 직렬화하며 추가 영속 정의가 아니다. 사용량 복원·승인·checkpoint ACK와
실행 책임은 기존 실행 Owner에 남는다. 부모 Tool 허용 목록의 포함 판단은
ToolPolicyValues.permits_selection이 소유하며 자원 사전 검사와 자식 scope 생성이 공유한다.

AgentNodeDefinition은 Graph action의 Agent 선택·출력 옵션과 공개 Schema를 소유한다.
Graph 공통 필드나 Application 확장 필드를 별도 Schema로 정규화하지 않는다.
AgentExecutionBinding은 handler binding과 자식 실행 state, AgentStepMetadata는 Step의
선택/입력/부모 관찰값 Projection을 제공한다. 공개 binding override를 그대로 호출한 뒤
확장 필드까지 보존하며, 기존 metadata의 명시 null을 기본 선택값으로 덮어쓰지 않는다.
Root AgentInvocationBinding은 ToolRegistry의 공개 binding을 같은 순서로 합성하고,
기존 실행 revision/hash를 유지한다. 이 증거는 Run admission 권한이나 Engine private 상태를
대체하지 않는다. AgentOutputValue는 text/data 출력을 소유하며 data 생략과 JSON으로
파싱된 null을 구분한다. EngineOutput.has_data는 공통 출력의 데이터 유무만 판별한다.
Agent 출력 Schema 검증 이후 확정 출력 전달, 사용량/승인/효과 기록 순서는 유지한다.

AgentCheckpoint는 위임 key·container 선택·Agent 사용량 기록을 소유한다. Graph와 Agent는
같은 쓰기 Projection을 사용하며, Reader는 선택된 자식 envelope와 필수 container 사용량을
검증한다. 사용량을 가진 실행 기록과 추가 지시 기록을 구분하고, 필수 사용량 누락·손상은
ToolUsageRecord의 같은 검증기로 거부한다. GraphHandlerSelection은 실제 환경/정의/handler
binding을 해석하며 자식 binding의 내부 의미는 해석하지 않는다. AgentDelegationArguments의
Schema/Reader와 GraphNodeContext를 확장한 위임 문맥을 통해 입력과 checkpoint scope를
전달한다. 사용량 복원·기록·효과·저장 ACK의 기존 순서를 보존하며 승인·예산을 새로 만들지 않는다.

ToolEventResult는 ToolExecutor와 신뢰된 event handler의 기존 가변 결과 dict를 공유한다.
이 dict를 별도 사본으로 교체하지 않는다. 필수 value/content 누락과 명시 null을 구분하고,
완료/재시도 관찰값의 기존 사본 경계를 유지한다. Agent/Graph/Loop는 해당 Owner로 결과를
조회하며 저장된 완료 영수증을 재사용할 때 handler를 다시 실행하지 않는다. SDK ToolResult의
wire 메시지와는 별도 계약이며, 결과 관찰값이 실행 허가·효과 원장을 대체하지 않는다.

AgentRecord, AgentCatalog, AgentBinding은 각자의 버전·필수 필드·identity 검증을 소유한다.
저장 목록과 Run admission 증거의 누락을 빈 데이터나 생성 기본값으로 복원하지 않는다.

선언부 정리만으로 외부 소비자의 완료를 선언하지 않는다. Engine·수명 서비스·Hub의
정의 필드 접근은 같은 Owner 선언/Projection/API로 이행해야 하며, 현재 단계별 현황은
[전체 소유권 검증](whole-ownership-verification.md)과 coverage index에 별도로 기록한다.

AgentRepository의 내부 목록은 AgentInventory의 catalog/records로 전달한다. Repository만
파일 구조·직렬화·무결성 목록을 해석하며, AgentManager는 정의/참조 Owner와 공개 저장
API를 사용한다. 조회 응답은 AgentDescription, AgentRevision, AgentPrompt가 직렬화한다.
프롬프트 미설정·명시적 null·빈 문자열을 구분하고 수정 결과의 subsequent_runs 의미를
보존한다. 새 영속 사본이나 호환 Reader를 만들지 않는다.

AgentReferenceReader는 Engine/Component/handler가 제공하는 참조 hook을 찾기만 한다.
각 호출자는 기존 인자·결과 계약을 지키며 참조 조회로 자원을 활성화하거나 실행하지 않는다.
Step의 Agent 선택은 AgentExecution이 읽는다. 삭제 검사는 비활성 자원·대기 요청·Run·
Step·Checkpoint와 soft delete한 Session 참여까지 계속 보호한다.

## 정책과 리소스

| 필드 | 의미 |
| --- | --- |
| engine | 필수 EngineRegistry 등록 이름. 선택 Engine이 자신의 필수 인자를 검증 |
| purpose | 선택적인 업무 설명 |
| completion | 선택 Agent의 모델·인증·SDK 인자. Loop Adapter는 실제 호출 전에 model을 요구하며 messages/tools는 엔진이 관리. Project/Session/다른 Agent에서 모델 값을 상속하지 않음 |
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

Loop에 숨은 반복/모델/Tool 시간 제한은 없다. 실행 설정은 Project → Session → Agent의 명시값만
적용한다. 모델 인자는 Agent `completion`만 사용한다. [모델 설정과 실행 설정의 경계](agent-models.md).
Agent policy와 부모 Run/ToolExecutionScope의 Project 한도는
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

Agent 폼을 제공하려면 다음처럼 공통 외형을 재사용한다. `engine_options`의 내부 필드는
선택 Engine만 해석한다. 아래 `strategy` 값은 이 예제 Engine의 명시적 계약이다.

```python
from llm.core.agents import AgentDefinition
from llm.core.schema import object_schema

def describe_agent(self):
    return AgentDefinition.describe(
        properties={"engine_options": object_schema({
            "config": object_schema({"strategy": {"enum": ["local", "remote"]}},
                                    required=["strategy"]),
        }, required=["config"])},
        required=("engine_options",),
    )
```

`describe_agent()`는 JSON Schema만 반환하며 네트워크·모델·Tool·저장 I/O나 `for_agent()`를
호출하지 않는다. 동기 함수, 닫힌 Agent 최상위, 필수 engine 및 알려진 최상위 필드를
요구하며 schema default와 외부 reference는 거부한다. `AgentDefinition.describe()`는
공통 필드를 선택하거나 구현체 소유 필드의 스키마를 결합하는 도우미이며 새 저장 계층이 아니다.
중첩 스키마의 `$id`와 로컬 pointer·anchor·dynamic reference는 소유 스키마의 참조 범위를
유지한다. 참조가 있지만 루트 `$id`가 없거나 빈 식별자(`""` 또는 `"#"`)인 스키마에는
`nest_schema()`가 결정적인
`https://schemas.ish-llm.invalid/nested/<hash>/` 식별자를 부여해 독립된 Schema resource로
삽입한다. 계층형 주소이므로 서로 다른 소유자의 `inner.json` 같은 상대 `$id`가 같은 주소로
겹치지 않는다. 이 식별자는 UI/검증용 스키마 조합 정보이며 사용자 설정·Agent 저장값·
네트워크 조회 주소가 아니다. 스키마를 내려받지 않는다.
참조 문자열을 변환하지 않으므로 같은 Python 스키마 객체를 여러 필드가 공유해도 이중 변환하지
않는다. 참조 없는 스키마에는 식별자를 추가하지 않으며, 외부 reference와 schema default의
거부 계약도 유지한다.

공통 `checked_schema()`는 상대 `$id`를 부모 주소 기준으로 해석한 뒤 충돌을 검사한다.
같은 resource 주소에 동일한 스키마가 반복되면 공유할 수 있지만, 서로 다른 스키마가 있으면
조회·실행 전에 주소와 두 선언 위치를 포함한 `ValueError`로 거부한다. 동일 여부는 자신의
`$id`를 해석된 주소로 비교한 정규 JSON 기준이며, 서로 다르게 작성한 규칙의 논리적 동등성을
추측하지 않는다. 같은 resource 안의 `$anchor`·`$dynamicAnchor` 중복도 같은 원칙이다.
명시한 `$id`를 임의로 바꾸지 않으므로 충돌이 있는 확장 구현체는 고유한 주소를 제공해야 한다.
검사는 실제 스키마 선언만 순회하며 `const`·`enum`·예제 안의 데이터는 해석하지 않는다.
UI 합성 오류를 피하려고 `EngineAgentBinding.bind()`나 `for_agent()` 검증을 생략하지 않는다.

EngineRegistry는 등록 이름 조건을 루트 `allOf`에 추가한다. 원본 `properties.engine`이나
그 아래 `$defs`의 위치·제약을 옮기거나 덮어쓰지 않으며 기존 `const`와 충돌하면 그대로
거부한다. UI는 전체 JSON Schema의 교집합을 사용해야 한다. binding에서도 같은 소유 스키마를
새로 검증하며 `for_agent()` 검증을 생략하거나 이전 검증 결과를 재사용하지 않는다.
공통 Agent 외형을 교차 검증할 때에도 `allOf` 목록을 복사해 조합하므로 다른 필드가 공유하는
목록의 제약이 바뀌지 않는다.

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
