> 설정은 [명시적 설정 계약](../CONFIGURATION.md)을 따른다. 미설정 정책을 생성하지 않으며, SDK 옵션은 생략한다.

# Engine 구성

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

이번 폴더 정리는 도메인 저장 버전이나 체크포인트 형식을 바꾸지 않습니다.

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
