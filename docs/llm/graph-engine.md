# GraphEngine과 코드 수정 Workflow 실행

GraphEngine은 **durable Workflow orchestration engine**이다. WorkflowComponent가 저장과
구조 검증을 소유하고 Workflow의 노드·간선·입출력이 조율 정의의 source of truth다.
같은 handler 환경의 중첩은 workflow 노드, 다른 등록 Graph/handler 환경의 선택은
Graph-backed Agent를 쓴다. 후자는 behavioral Agent가 아니며 Prompt/Skill/Tool 권한을
추가하지 않는다. [허용·거부 필드](nested-workflows.md)는 실행 전 리소스 조회보다 먼저 검증한다.

`GraphEngine`은 WorkflowComponent에 저장된 버전 1 그래프를 LangGraph `StateGraph`로
컴파일하여 하나의 Run에서 실행한다. 모델 호출은 AgentNode → LoopEngine → LiteLLM
`completion(stream=True)` 경로를 사용한다. `langchain-litellm`은 필요하지 않다.
노드 처리는 등록된 Python async 호출 객체가 담당하며 그래프 문서에는 함수나 런타임
객체를 저장하지 않는다. 그래프 전체와 각 노드는 Step으로 기록되고, 완료 이벤트의
metadata는 StepManager를 통해 기존 step.json에 반영된다.

## 실행 백엔드와 설치

`GraphEngine(handlers=...)`은 실행 전략을 등록한다. Workflow는 요청마다
`await session.run.submit(prompt, engine="graph", engine_options={"workflow": workflow_id})`로 선택한다. LangGraph 타입을
프로젝트 설정이나 UI API에 노출하지 않는다. 공개 엔진과 내부 실행부는 모두 `engines/graph/engine.py`에 있다.

Workflow ID는 필수이며 생성자나 기본 설정에서 가져오지 않는다. 선택은 대기 메시지와
Run에 저장하고 정의는 Run 시작 시 스냅샷으로 읽는다. 같은 등록 엔진을 여러 Session에서
서로 다른 Workflow로 실행할 수 있다. 재개는 원본 Run의 ID를 복원하고 정의/설정 변경을
기존 binding 검증으로 거부한다. 변경한 Workflow는 새 submit으로 처음부터 실행한다.

* 일반 노드 → LangGraph 노드, 분기 → 조건부 간선.
* 병렬 → 경로별 독립 하위 그래프와 모든 결과를 기다리는 합류.
* 반복 → 조건부 후방 간선. 하위 그래프도 같은 실행 예산을 사용한다.

라이브러리 설치에는 LangGraph가 기본 의존성으로 포함된다. RAG까지 포함한 전체 테스트는
아래처럼 설치한다. ish 플러그인은 `PLUGIN_META.dependencies`에도 LangGraph를 선언한다.

```sh
uv pip install --python .venv-linux312/bin/python -e ".[rag]"
```

개발·검증 대상은 Linux Python 3.12.14이며 LangGraph 1.2.12 이상을 사용한다. 플러그인 import만으로
LangGraph를 불러오지 않으며 처음 GraphEngine을 실행할 때 로딩한다.

Run/Step/Conversation 저장은 기존 서비스의 책임이다. Workflow 노드 경계 체크포인트와
`session.run.resume`을 제공한다. `pause_before: true`는 노드 실행 전 Run을 PAUSED로 종료한다.
명시적 중단은 INTERRUPTED이며 불확실한 처리 노드는 재실행 승인이 필요하다.
LangGraph 네이티브 checkpointer/`interrupt()`와 자동 재시도는 사용하지 않는다.
저장 구조·UI 예제·제약은 [체크포인트 안내](graph-checkpoints.md)를 참고한다.

저장된 Workflow를 호출하는 `workflow` 노드와 Agent의 GraphEngine 선택도 지원한다.
부모 Run/권한/예산/체크포인트를 공유한다. 정의 예제와 정책은
[중첩 Workflow 안내](nested-workflows.md)를 참고한다.

## 범용 Agent 노드와 입력·출력 계약

`AgentNode`는 저장된 업무 정의가 지정한 엔진으로 실행한다. AgentComponent는
정의 CRUD만 담당하고 Run/Step을 만들거나 모델을 호출하지 않는다.

```python
from llm.engines.graph.agent import AgentNode
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine

agent = AgentNode(engines={"loop": LoopEngine()})
engine = GraphEngine(handlers={"agent": agent, "validate": validate_code})
await project.components.agents.acreate({
    "purpose": "요청과 검증 결과를 보고 코드를 수정한다",
    "engine": "loop",
    "completion": {"model": model, "temperature": 0.2},
    "system_prompt": "기술문서를 확인하고 수정 결과를 JSON으로 반환하세요.",
    "engine_options": {'policy': {'max_iterations': 4, 'request_timeout': 60, 'tool_timeout': 30}},
    "resources": {"skills": ["code_review"], "rag": True},
    "tools": [],
    "policy": {"require_tool": True, "max_tool_calls": 3, "timeout_seconds": 120},
    "input_schema": {"type": "object", "required": ["request"]},
    "output_format": "json",
    "output_schema": {"type": "object", "required": ["data"], "properties": {
        "data": {"type": "object", "required": ["code"],
                 "properties": {"code": {"type": "string"}}}}},
}, identifier="coder")
```

위 코드는 Project에서 agents/workflows/tools/skills/rag를 선택하고 code_review Skill과
RAG 문서를 등록한 후 사용하는 예시다. 전체 사용법은 [Agent 업무 정의](agents.md)를 참고한다.
일반 Tool은 `tools` 허용 목록에 지정하며 생략하면 빈 목록이다. resources.rag=true는
Project의 `rag_search`를 자동 추가한다. Skill 지침을 프롬프트에 추가하고 MCP는 명시적으로
연결한 어댑터가 제공한 Tool만 사용한다. 리소스 참조는 실제 실행 전 검증한다.

Loop의 `engine_options`는 max_iterations/request_timeout/tool_timeout/buffer_size/
max_tool_calls/max_argument_chars/max_output_chars를 config/policy 외형으로 받는다.
Project → Session → Agent의 명시된 값만 적용하고 child는 부모의 제한을 넓힐 수 없다. 미설정 실행 한도나 timeout을 생성하지 않는다.
max_tool_calls는 한 모델 응답의 Tool 개수 제한이고 policy.max_tool_calls는 Agent 전체
호출 예산이다. 부모 Run의 한도와 권한은 항상 함께 적용한다. require_tool을 만족하지
못하거나 입력/출력 schema 검증이 실패하면 Agent/Graph Run이 실패하며 자동 재시도하지 않는다.

AgentNode의 엔진 등록은 백엔드 Run용 엔진 등록과 독립적이다. 커스텀 엔진은 순수 동기
`for_agent(definition)`에서 별도 실행기를 반환하고 `execute(context)`에서 이벤트를 보낸다.
Factory는 사전 검증에서도 호출되므로 외부 작업을 수행하거나 등록 객체를 수정하면 안 된다.
실제 작업은 execute에서 수행한다. 엔진·어댑터 코드를 변경하면 revision도 올린다.

Workflow와 처리 노드에는 아래 JSON 계약을 선택적으로 저장한다. 제어 노드와 loop.body에는
별도 입출력 계약을 넣지 않는다. 반복 본문은 상위 상태를 공유하고 내부 처리 노드에서 매핑한다.

| 위치 | inputs | outputs | 스키마 검증 대상 |
| --- | --- | --- | --- |
| Workflow | `{prompt, message_id}`에서 초기 상태에 추가할 키 선택 | 최종 상태에서 공개 결과 선택 | input_schema: 초기 상태+입력, output_schema: 공개 결과 |
| 처리 노드 | 현재 상태에서 node.inputs로 전달할 키 선택 | 처리기 반환값에서 상태에 병합할 키 선택 | input_schema: 선택된 입력, output_schema: 매핑 전 반환값 |

매핑은 `{목적키: JSON Pointer}`다. 예를 들어 아래 Agent 노드는 기존 코드와 피드백을 받아
새 코드만 상태에 반영한다. 포인터는 배열과 `~0`/`~1` 이스케이프도 지원한다.

```python
{
    "type": "agent", "agent": "coder", "output_format": "json",
    "inputs": {"request": "/request", "code": "/code", "feedback": "/validation"},
    "outputs": {"code": "/data/code"},
    "output_schema": {
        "type": "object", "required": ["data"],
        "properties": {"data": {
            "type": "object", "required": ["code"],
            "properties": {"code": {"type": "string"}}
        }}
    }
}
```

Agent 결과는 마지막 모델 턴의 `{text: ...}`이며 output_format="json"이면 파싱한
`data`도 포함한다. 내부 텍스트는 기본적으로 대화에 출력하지 않으며 `emit_text=True`로
전달할 수 있다. 최종 사용자 답변은 report 처리기 등에서 명시적으로 출력하는 것이 좋다.
ToolNode도 inputs를 인자로 사용한다. 정적 arguments/arguments_key와 동시에 지정하지 않는다.

누락된 포인터, 잘못된 JSON, 스키마 위반은 실행 오류이며 Run을 실패시킨다. 입력 오류는
처리기를 호출하기 전에, 출력 오류는 상태를 변경하기 전에 검사한다. 외부 스키마 참조는
허용하지 않는다. 코드가 문법/테스트를 통과하지 못한 **업무 검증 결과**는 오류를 던지는 대신
`{passed: false, validation: ...}`를 반환해 loop의 다음 회차에 피드백으로 전달한다.
inputs/outputs를 생략한 기존 처리기는 전체 state를 받고 반환 dict를 얕게 병합한다.

결과는 `(await run.aresult()).output.data` 또는 kind="graph" Step의 `step.output.data`로 조회한다.
완료된 노드 Step에는 실제 input/result와 병합 후 output이 저장된다. 대용량 문서는 본문을 반복해서
상태에 넣기보다 문서/산출물 ID를 전달한다. 이 매핑은 데이터 연결이며 접근 권한 샌드박스가 아니다.

## 재현 가능한 실제 검증

저장소 루트에서 Python 3.12.14로 실행한다.

```sh
.venv-linux312/bin/python -m examples.llm.code_workflow --workspace tests/llm/reports/runs/workflow-demo
```

기본 모드는 모델 응답만 고정한 `scripted` 모드다. 실제 파일을 작성하고 AST/compile
검사 및 별도 Python 프로세스의 unittest를 실행한다. 외부 모델 API는 호출하지 않는다.

1. 문법 오류가 있는 코드 작성 → 정적 검사 실패 → 피드백과 함께 재작성.
2. 덧셈 대신 뺄셈 구현 → 정적 검사 통과 → 다섯 테스트 중 네 개 실패 → 재작성.
3. 올바른 덧셈 구현 → 정적 검사와 다섯 테스트 통과 → 완료.

매 실행마다 새 Project/Session와 고유한 `coding-artifacts/<id>/solution.py`를 만든다.
Project 안에는 `agents/records/coder.json`, `workflows/records/code-review.json` 및
Session/Run/Step 이력이 남는다. `coding-artifacts/<id>/result.json`은 예제 출력 보고서이며
라이브러리의 별도 도메인 저장 형식이 아니다. `--workspace`를 생략하면 임시 디렉토리를
사용하고 프로그램 종료 시 제거한다.

반복 상한 실패도 재현할 수 있다. 아래 명령은 완료를 보고하지 않고 종료 코드 1을 반환한다.

```sh
.venv-linux312/bin/python -m examples.llm.code_workflow --workspace tests/llm/reports/runs/workflow-demo --max-attempts 2
```

실제 모델은 `--model`로 지정한다. 해당 제공자 인증 환경변수는 실행 전에 설정한다.

```sh
.venv-linux312/bin/python -m examples.llm.code_workflow --workspace tests/llm/reports/runs/workflow-demo --model <LiteLLM-model-id>
```

이 경우 공통 AgentNode/LoopEngine이 저장된 Agent의 모델과 시스템 프롬프트로 LiteLLM
completion 스트림을 호출한다. 첫 시도에 성공할 수도 있으므로 재시도 횟수는 고정하지 않는다.
실제 모델 연결은 이번 자동 검증에서 실행하지 않았다.

예제 검증은 **add(a, b)라는 작은 순수 산술 함수**에 한정한다. AST 검사는 import,
함수 호출, 루프, 데코레이터 등 이 예제에서 불필요한 구문을 거부한다. 일반 프로젝트의
품질을 판정하는 분석기나 임의 생성 코드를 격리하는 범용 샌드박스가 아니다. 실제
코드베이스에서는 노드 처리기를 교체하여 해당 프로젝트의 정적 분석기·테스트·리뷰
기준을 적용해야 한다.

## 저장된 그래프의 흐름

```text
repair (최대 3회, passed == false 동안 반복)
  implement: 공통 AgentNode + 이전 코드/검증 결과 → 코드 출력 매핑
  persist: 예제 소유의 코드 파일에 저장
  static: AST, 문법, 예제 구현 규칙 검사
  route:
    정적 검사 통과 → tests → 회차 종료
    정적 검사 실패 → 회차 종료
  passed가 false이면 implement부터 다시 실행
report → end
```

검증에서 문제를 발견한 것은 처리기의 정상 결과 `{passed: false, validation: ...}`다.
처리기 자체의 예외, timeout, 반복 상한 소진은 Run 실패다. 중단하면 진행 중인 노드와
병렬 작업을 취소하고 정리하며, 동일 Session에 대기 중인 다음 요청은 기존 RunManager가
계속 처리한다. 프로세스 재시작 뒤 실행 중이던 Workflow를 자동 재실행하지 않는다.

## 개발자용 노드 API

```python
from llm.engines.graph import GraphEngine, GraphNodeContext

async def inspect(node: GraphNodeContext) -> dict:
    source = node.state["code"]
    issues = await my_analyzer(source)
    return {"passed": not issues, "validation": {"issues": issues}}

engine = GraphEngine(handlers={"inspect": inspect})
# ProjectConfig(parameters={"engines": {"graph": {"policy": {
#     "max_steps": 1000, "max_parallelism": 8, "timeout_seconds": 300,
# }}}})
```

`GraphNodeContext`는 다음 정보를 제공한다.

* `context`: 같은 Run의 EngineContext(대화, capability 등).
* `node_id`, `definition`: 노드 ID와 분리된 JSON 정의.
* `state`: 현재 그래프 상태의 독립 복사본. 반환한 dict를 실행 상태에 얕게 병합한다.
* `inputs`: inputs 매핑으로 선택된 독립 입력. 생략하면 전체 상태의 복사본이다.
* `await emit(EngineEvent(...))`: 텍스트·Completion·하위 Step 이벤트를 기존 서비스로 전달.

처리기는 async 호출 객체여야 하며 JSON 객체를 반환해야 한다. 파일/프로세스/연결을
소유한 처리기는 취소 시 자신의 작업도 정리해야 한다. blocking 작업은 실행 루프
밖에서 수행한다. 처리기의 `required_capabilities` 튜플은 GraphEngine의 요구 목록에
합쳐진다. 선택적 `validate(node_definition, engine_context)` 동기 메서드는 모든 노드와
루프 본문에 대해 부작용 발생 전에 호출되며 참조 오류는 예외로 알린다.

Workflow capability에는 하나의 ID가 정확히 한 제공자에서 발견되어야 한다.
그래프 구조·노드 등록·timeout·처리기 참조 검사를 먼저 수행한다. 공통 AgentNode는
모델/프롬프트/허용 Tool/Loop 제한을 적용한다. Skill/MCP 준비나 추가 조직 정책은
사용자 처리기의 validate/실행 함수에서 집행한다.

## 제어 노드와 실행 이력

* `branch`: 조건을 위에서부터 평가하여 처음 참인 port를 선택한다. 없으면 default.
* `parallel`: 경로별 독립 상태에서 병렬 실행한다. 한 경로가 실패하면 다른 경로도 취소하고 기다린다.
* `join`: 모든 경로 완료 후 기존 상태의 `branches` 키에 `{시작노드ID: 최종상태}`를 넣는다.
* `loop`: 이전 회차 출력을 다음 입력으로 전달한다. while은 회차 시작 전에 검사한다.
  while 없는 반복은 정확히 max_iterations회 후 정상 종료한다.
* 조건이 참인 채 반복 상한에 도달하면 on_limit이 fail일 때 실패하고 continue일 때 진행한다.
* `end`: 현재 그래프를 끝낸다. loop.body 안에서는 해당 회차만 끝낸다.

모든 제어 노드도 전체 max_steps 예산에 포함한다. 노드별 timeout_seconds를 지정할 수
있으며 max_parallelism은 동시에 실행되는 사용자 처리기 수를 제한한다. Engine 객체에는
Run별 상태를 저장하지 않는다. 각 Run의 입력은 Workflow initial_state에서 복사한다.

Step 시작 이벤트가 저장된 뒤 해당 노드 작업을 진행한다. root graph Step에 사용한
Workflow 정의와 최종 output/visited_nodes, runtime="langgraph"가 남고, 노드 Step에는 경로와 출력, 선택한
port 또는 반복 횟수가 남는다. 예제의 LLM Step에는 Agent 스냅샷도 저장한다.
AgentNode는 해당 Run의 Agent capability 스냅샷을 하위 Step에 기록한다. 외부 자료까지
자동 고정하지 않으므로 다른 처리기도 필요한 참조 스냅샷을 기록해야 한다. 여러 Step의 상태 스냅샷은 출력 크기에 비례해 커지므로
대용량 파일 내용은 상태에 넣는 대신 작업 산출물 ID/경로를 전달하는 편이 좋다.

## 테스트

```sh
.venv-linux312/bin/python -m unittest tests.llm.test_graph_engine -v
.venv-linux312/bin/python -m unittest tests.llm.test_agent_workflow -v
.venv-linux312/bin/python -m unittest tests.llm.test_langgraph_engine -v
.venv-linux312/bin/python -m unittest discover -s tests/llm -t . -v
```

분기, 반복 성공/상한 실패, 병렬 상태 분리, 형제 취소, 중단 후 대기 요청 처리,
Step 저장 이후 부작용 실행, 사전 등록 검증, 실제 코드 수정/검증 흐름을 확인한다.
Agent 통합 테스트는 실제 Chroma/Kuzu 검색→Agent Tool 응답→코드 작성→AST 검사→
별도 프로세스의 테스트→피드백 재수정→최종 출력/Step 저장까지 연결한다. 모델의
completion/embedding/triple 응답만 결정적 fixture이며 실제 모델 품질 평가는 아니다.
LangGraph 전용 검사는 실제 네이티브 노드 실행, 예약 ID, 중첩 병렬/반복 상태 분리,
긴 반복/공유 예산, 재시도 방지, 취소·timeout의 비동기 정리 및 대기 요청 보존을 검증한다.
