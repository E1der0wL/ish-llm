> 설정은 [명시적 설정 계약](../CONFIGURATION.md)을 따른다. 미설정 정책을 생성하지 않으며, SDK 옵션은 생략한다.

# Engine 구성

## Durable Tool 호출 연결

custom Engine은 기존 체크포인트의 invocation key를 공통 helper에 전달합니다.
새 ID나 승인 영수증을 만들지 않으며 checkpoint/Interaction/Run resume 기록이 원본입니다.

```python
from contextlib import aclosing

result = {}
async with aclosing(self.execute_tool(
    context, tool, arguments, checkpoint_key=key, result=result,
    executor=executor,  # 명시된 timeout 등 기존 ToolExecutor 설정을 유지
)) as events:
    async for event in events:
        yield event
value = result["value"]
```

BaseEngine을 상속하지 않는 처리기는 `context.execute_tool(...)`을 사용합니다.
decision 생략 시 waiting Tool approval의 Interaction 선택지 값과 effect에서만 해석합니다.
Loop bool과 Graph object를 호출자가 별도로 해석할 필요가 없습니다.
`GraphNodeContext.decision`은 노드 확인일 수도 있으므로 Tool 승인으로 전달하지 않습니다.
`pause_before` 승인 후에도 ToolPolicy.authorize를 실행하며, ASK이면 별도의 Tool 승인을
기다립니다. 명시적 Tool decision은 실제 waiting Tool 요청과 저장 응답에 일치해야 합니다.
confirmation이나 started 기록에 decision을 붙이면 실행 전에 거부합니다.
같은 Tool/인자를 사용하는 반복/병렬 노드도 각자의 기존 key를 씁니다.

직접 ToolExecutor.execute를 사용하는 경우 durable 승인 재개에는 request_key가 필요합니다.
누락·모호함·불일치는 `ToolInvocationError` (`tool_invocation_invalid`)로 실행 전에 거부합니다.
일반 비영속 호출에는 key를 강제하지 않습니다. helper는 Step 저장, 승인 정책, retry,
operation receipt를 소유하지 않으며 ToolExecutor가 이를 계속 담당합니다.

Engine은 Run에 등록·선택되는 실행 전략입니다. 영속 소유 관계는
Project → Session → Run → Step이며 Engine은 이벤트를 발생시킵니다.
도메인 저장과 실행 수명 관리는 기존 서비스가 담당합니다.

```text
engines/
├── base.py                 # Engine 계약, 문맥·이벤트, BaseEngine
├── registry.py             # Engine 등록·조회·설정 사전 검증
├── loop/
│   ├── __init__.py         # LoopEngine 공개 진입점
│   └── engine.py           # LiteLLM 스트리밍·Tool 반복 실행
├── graph/
│   ├── __init__.py         # GraphEngine, GraphNodeContext 공개 진입점
│   ├── engine.py           # LangGraph 컴파일·실행·노드 수명 관리
│   ├── agent.py            # AgentNode: 등록 Engine을 Graph 노드에 연결
│   ├── tool.py             # ToolNode: 공통 ToolExecutor에 연결
│   └── checkpoints.py      # 자식 Engine 체크포인트를 부모 Graph에 연결
└── pipeline/
    ├── __init__.py         # PipelineEngine, PreparationStep 공개 진입점
    └── engine.py           # 준비 작업과 여러 Engine의 순차 조합
```

## 공개 import

```python
from llm.engines.base import BaseEngine, EngineContext, EngineEvent, EngineEventType
from llm.engines.registry import EngineRegistry
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine, GraphNodeContext
from llm.engines.graph.agent import AgentNode
from llm.engines.graph.tool import ToolNode
from llm.engines.pipeline import PipelineEngine, PreparationStep
```

각 패키지의 `__init__.py`는 공개 클래스를 노출합니다. 실제 구현을 패치하거나 내부
실행부를 조사할 때는 `llm.engines.loop.engine` 같은 구현 모듈을 사용합니다.
이전 `engines.agent`, `engines.tool`, `engines.checkpoints`와
`engines.base.EngineRegistry`의 호환 경로는 제공하지 않습니다.

신규 실행 전략도 `engines/<name>/engine.py`와 공개 `__init__.py`로 추가할 수 있습니다.
플러그인 외부에서 구현한 객체를 EngineRegistry 또는 LargeLanguageModel의 engines에
등록하는 방식도 유지됩니다. 폴더 구조가 등록 이름이나 ProjectConfig 설정 키를 결정하지는
않으며 Engine을 파일에서 자동 발견·실행하는 기능은 추가하지 않습니다.

Graph 구현과 Run별 내부 실행 객체는 같은 `graph/engine.py`에 유지합니다.
Agent/Tool 노드는 Graph에 특화된 어댑터이고, 여러 Engine이 공유하는 Tool 실행 정책은
`services/runtime/tools.py`가 계속 담당합니다. LangGraph는 실제 Graph 실행 시 로드합니다.

Graph의 다음 판단은 같은 파일의 순수 함수로 분리합니다.

| 내부 함수 | 판단 | 수행하지 않는 작업 |
| --- | --- | --- |
| `_branch_port` | 선언 순서의 첫 일치 port 또는 default | 상태 수정, 이벤트 전송 |
| `_loop_continues` | 다음 반복, 정상 종료, 조건부 반복 상한 오류 | 반복 횟수 갱신, 하위 그래프 실행 |
| `_plan_node` | 완료 결과 재사용, 사전 대기, 실행 및 검토 응답 선택 | 승인 생성, 체크포인트 저장, 처리기 호출 |

판단 함수에는 JSON 정의·상태·체크포인트 값만 전달합니다. 입력을 변경하지 않으며,
`_NodePlan`의 decision은 원본과 분리된 사본입니다. 계획은 내부 런타임이 즉시 사용하고
별도 저장하거나 새 상태 원본으로 삼지 않습니다. 공개 Workflow/GraphNodeContext API는 같습니다.

실행부는 기존 순서대로 started 체크포인트 저장 확인 → Step 시작 확인 → 처리기 실행 →
completed 체크포인트 저장 확인 → Step 완료를 수행합니다. 완료 노드 재사용은 처리기를
호출하거나 원본 체크포인트를 덮어쓰지 않습니다. pause는 waiting을 저장한 뒤 실행을 양보합니다.
분기 비교·반복 상한 오류도 기존 try/except 경계를 통해 Step/Run 실패로 전달합니다.

LangGraph의 병렬 스케줄링·합류, 세마포어, 실행 예산, 취소 정리 및 이벤트 ACK는 실행부가
계속 소유합니다. 재개 binding/retry_nodes/Interaction 검증도 기존 진입 경계에 남습니다.
판단 계획은 승인 증명이 아니며 부작용 재실행 권한을 새로 부여하지 않습니다.

이번 폴더 정리는 도메인 저장 버전이나 체크포인트 형식을 바꾸지 않습니다.

## 승인·재개 값 해석

`InteractionRequest.select_decision()`은 저장된 option과 입력 schema를 사용해 재개 값을
해석하고 `decision_for()`는 선택지 ID를 Engine 값으로 변환합니다. 서비스, Loop/Graph의
재개 검증, ToolExecutor, 중첩 checkpoint bridge가 이 계약을 공유합니다.
Loop의 bool 및 Graph의 approved/state 형식, binding과 불확실 실행의 retry_nodes 검증은
각 Engine에 유지합니다. `False`와 누락을 혼동하거나 추천 선택지를 자동 적용하지 않습니다.

중첩 bridge는 부모 요청이 자식 요청을 해당 scope로 bind한 결과인지 검증한 뒤 같은
option ID로 자식 값을 복원합니다. 응답 영수증을 재생성하거나 갱신 전 요청의 만료 시각을
재검사하지 않습니다. 순수 해석 함수가 승인 권한을 대신하지 않으므로 custom Engine도
기존 서비스 재개 경계와 `context.execute_tool(..., checkpoint_key=...)`를 사용해야 합니다.
API 및 책임 구분은 `docs/llm/interactions.md`를 참고하세요.

## 실패 전달 계약

Subsystem은 오류를 분류하고 Engine은 `BaseEngine.step_failed_event(step_id, error)`로
`STEP_FAILED + Diagnostic`을 전달합니다. Graph 노드·중첩 Graph·Agent·Pipeline·Tool도
같은 helper를 사용합니다. 기존 Diagnostic의 source/details를 보존하며, source가 없으면
Step에 연결합니다. 오류에 code가 없으면 Step은 `step_failed`(Tool은 `tool_failed`)입니다.
문자열 error와 구조화된 diagnostic은 별도 필드이며 raw message 정책은 변경하지 않습니다.

RunManager는 `llm.errors.CodedError` 계약을 명시한 오류의 code만 보존합니다.
ProviderError, 분류된 StreamError, EmbeddingIntegrityError, GraphValidationError,
MemoryConflictError, ProviderCapacityError, ExecutionLimitError, RunRequestError,
ToolExecutionError가 이 계약을 사용합니다. 알려지지 않은 실패는 `engine_failed`입니다.
RunRequestError는 요청 거절용이므로 공급자 오류를 이 클래스로 변환하지 않습니다.

```python
from llm.errors import CodedError

class DocumentError(CodedError, ValueError):
    code = "document_invalid"
```

확장 코드가 명시적으로 안정적인 오류 의미를 제공할 때도 이 mixin을 사용할 수 있습니다.
임의 SDK 예외에 `.code`가 있다는 사실만으로는 Run code로 신뢰하지 않습니다.
Diagnostic은 기존 관찰 계약에 따라 이러한 문자열도 표시할 수 있지만, 이것이 Run의
분류·재시도·승인 권한으로 승격되지는 않습니다. 명시적인 Diagnostic만 전달하는 사용자
Engine도 해당 Step 진단을 그대로 저장할 수 있습니다.

`stable_error_code()`는 바깥쪽의 분류된 오류를 우선하며, 없으면 명시적 cause 또는
숨기지 않은 context를 탐색합니다. `raise ... from None`은 경계를 끊습니다.
순환을 검사하고 최대 32개까지만 관찰합니다. 이는 실행 정책이 아닌 종료 처리의 작업량
상한이며, 오류 메시지·HTTP status·vendor code의 의미를 여기서 추측하지 않습니다.

Pipeline은 실패 이벤트 뒤 자식 generator를 더 진행하지 않습니다. 이를 위해 공통 helper가
분류된 오류에 한해 `EngineEvent.failure_code`를 채웁니다. Pipeline은 이 명시적인 Engine
계약만 전달하고 관찰용 Diagnostic code를 임의로 승격하지 않습니다. failure_code는
런타임 조합용 문자열이며 영속 Step에는 추가하지 않습니다. 예외 객체/traceback도 저장하지 않습니다.

StepEventRecorder/StepManager는 전달된 status/error/diagnostic을 기존 소유 경로에 저장할 뿐
오류를 재분류하지 않습니다. Run timeout·interrupt·pause/resume 및 Provider/Tool/Graph의
retry·effect·checkpoint 책임과 정책은 그대로 유지됩니다.
