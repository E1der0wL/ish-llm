# Engines — Run 실행 전략

Engine은 하나의 Run을 어떻게 실행할지 정의합니다. 모델을 반복 호출하거나 Workflow를 실행해도 Project → Session → Run → Step의 저장 관계는 바뀌지 않습니다. Engine은 비동기 이벤트를 발생시키고 서비스가 저장합니다.

## 파일과 실행 전략

| 파일/폴더 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | BaseEngine, EngineContext, EngineRegistry와 Graph 타입의 공개 import입니다. |
| [base.py](base.py) | Engine protocol, EngineContext/EngineEvent, BaseEngine과 Step·출력·Tool helper입니다. |
| [registry.py](registry.py) | 실행 객체 등록, 이름 조회와 실행 전 설정 검증입니다. |
| [loop/](loop/README.md) | LiteLLM completion → Tool → 다음 completion을 반복합니다. |
| [graph/](graph/README.md) | LangGraph로 Workflow를 실행하고 Agent/Tool·중첩 체크포인트를 연결합니다. |
| [pipeline/](pipeline/README.md) | 준비 작업과 여러 Engine을 같은 Run에서 순서대로 실행합니다. |

현재 공개 구현은 LoopEngine, GraphEngine, PipelineEngine입니다. 단일 동작은 BaseEngine으로 구현할 수 있으며 별도 SingleEngine 구현은 없습니다.

## 공개 import와 등록

```python
from llm.engines import BaseEngine, EngineContext, EngineRegistry
from llm.engines.base import EngineEvent, EngineEventType
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.engines.pipeline import PipelineEngine, PreparationStep

engines = {"assistant": LoopEngine()}
# LargeLanguageModel(..., engines=engines)
# await session.run.submit("안녕하세요", engine="assistant")
```

등록 이름이 설정 키입니다. Project 설정은 `parameters.engines.assistant`에 둡니다. 이름이나 파일 위치만으로 자동 발견하지 않으며, 항상 등록한 Engine 이름을 submit/resume에 전달합니다.

## 새 Engine의 최소 계약

[메인 README](../README.md#새-engine-만들기)의 EchoEngine처럼 BaseEngine.run에서 문자열·EngineDelta·최종 EngineOutput을 yield할 수 있습니다. 복잡한 Engine은 `execute(context)`를 구현하고 BaseEngine의 이벤트 helper를 재사용합니다.

- `execute`는 `AsyncIterator[EngineEvent]`를 반환합니다.
- 요청별 인자는 `submit(..., engine_options={...})`로 전달합니다. 선택적인 동기
  `for_request(options)`가 이를 검증하고 실행별 Engine 사본을 반환합니다. 외부 I/O·효과나
  등록 객체 변경을 하지 않습니다. 옵션 사본을 받으며 capability 탐색과 실제 실행은 같은
  사본을 사용합니다. `checkpoint_name`과 `steering_mode`는 등록 Engine과 같아야 합니다.
  접수 검증·실행·재개 검증에서 여러 번 호출될 수 있으므로 호출 자체가 작업을 실행하면
  안 됩니다. 이 hook이 없는 Engine은 빈 옵션만 허용합니다. 재개도 원본 옵션으로 연결합니다.
- `required_capabilities`는 필요한 기능 이름의 중복 없는 tuple입니다. 미선언은 외부 기능 없음입니다.
- `context`에는 도메인 스냅샷, 메시지, Run별 state/capabilities와 공유 실행 범위가 있습니다. 실행 상태를 Engine 인스턴스에 누적하지 않습니다.
- Step/Run/Session/대화/체크포인트 파일을 직접 쓰지 않습니다.
- 이벤트 소비자는 조기 종료에도 generator를 닫도록 `aclosing`을 사용합니다. 취소는 자원을 정리한 뒤 전달합니다.
- 사용자 설정은 명시한 값만 적용합니다. `configuration_schema()`는 UI·사전 검증에, `configuration(config, name, *, session_config=None)`는 출처를 포함한 유효 설정 조회에 사용합니다. 동일 해석을 실행에서도 사용해야 합니다.

Graph AgentNode에서도 사용할 Engine은 `for_agent(definition)`이 호출별 Engine을 반환하도록 구현하고 AgentNode의 엔진 맵에 등록합니다. 일반 `execute` 계약만 구현했다고 Agent 설정이 자동 적용되지는 않습니다. 명시적 재개까지 지원하려면 Engine별 체크포인트 생성과 `validate_resume` 검증도 구현해야 합니다. BaseEngine의 Tool helper가 체크포인트를 대신 생성하지는 않습니다.

실행 중 추가 지시는 `steering_mode`로 `UNSUPPORTED` / `CONSUME` / `FORWARD`를 선언합니다. BaseEngine은 `open_instructions`, `select_instructions`, `apply_instructions`, `close_instructions` helper와 저장 ACK 계약을 제공합니다. 상속만으로 지원되지는 않습니다. Loop는 직접 처리하고 Graph는 사용자가 지정한 자식 실행으로 전달합니다. Pipeline 라우팅은 지원하지 않습니다. [공통 계약과 custom Engine 구현](../../docs/llm/steering.md)을 참고하세요.

미시작 Agent 예약은 전달 Engine이 `declare_instruction_routes`로 검증된 경로를 선언하고 `bind_instruction_route`로 실제 실행 시작에 한 번 결합합니다. 서비스 저장 ACK 뒤 작업을 시작하며 소비 Engine은 기존 선택/반영 helper를 그대로 사용합니다. Graph가 중첩·병렬·반복 경로를 해석하고 서비스는 경로별 영수증을 저장합니다.

## 출력과 Step

문자열은 BaseEngine이 EngineDelta로 연결합니다. 최종 출력은 EngineOutput, 모델 사용량과 종료 이유는 CompletionResult입니다. UI는 TEXT_DELTA/OUTPUT/COMPLETION 등 EngineEvent를 읽고, 저장 결과는 RunHandle에서 조회합니다.

BaseEngine의 `step()`으로 하위 작업을 Step 이벤트로 감쌀 수 있습니다. Step 시작·진행·실패 이벤트를 발생시켜도 Engine이 persistence owner가 되는 것은 아닙니다. 사용자 출력과 내부 Agent 출력의 visibility, 중첩 Step의 output ownership을 보존하세요.

## Durable Tool 호출 연결

custom Engine은 기존 체크포인트의 invocation key를 공통 helper에 전달합니다.
새 ID나 승인 영수증을 만들지 않으며 checkpoint/Interaction/Run resume 기록이 원본입니다.
아래는 BaseEngine 하위 클래스에 둘 수 있는 메서드 예입니다. 호출자가 결과용 dict와
현재 invocation의 안정적인 key를 전달하고, 이벤트를 끝까지 소비한 뒤 `result["value"]`를 읽습니다.

```python
from contextlib import aclosing

async def tool_step(self, context, tool, arguments, *, key, result, executor=None):
    async with aclosing(self.execute_tool(
        context, tool, arguments, checkpoint_key=key, result=result,
        executor=executor,  # 명시된 timeout 등 기존 ToolExecutor 설정을 유지
    )) as events:
        async for event in events:
            yield event
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
API 및 책임 구분은 [승인 API](../../docs/llm/interactions.md)를 참고하세요.

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
