# 재시도와 수정 인자

재시도의 횟수·간격은 기존 설정 소유자가 정한다. Application은 중단할지, 동일 인자 또는
수정 인자로 다시 시도할지 선택한다. Backend는 그 선택이 횟수, 입력, 승인, 효과 안전성
계약 안에 있는지 집행한다. 모든 함수를 감싸는 재시도 wrapper는 없다.

## 책임과 적용 단위

| 경계 | 반복 단위 | 설정 및 안전성 소유자 |
|---|---|---|
| `BaseEngine.stream_completion` | 첫 chunk 전의 completion 요청 하나 | 호출 Engine의 `policy.provider`, Provider SDK retry 검사 |
| `providers.requests.invoke` | embedding/rerank/비스트리밍 completion 요청 하나 | 해당 모델 호출자의 명시적 provider 정책 |
| `ToolExecutor` | Tool invocation 하나 | Project `policies.tool_retry`, ToolContract, ToolRuntime, 효과 영수증 |
| `TripleExtractor.extract` | 검증 실패 응답의 의미 수정 한 번 | RAG의 명시적 `repair_attempts` 및 기존 JSON 수정 계약 |

`llm.policies.retry.RetryPolicy`는 횟수, fixed/exponential delay, 절대 deadline만 계산한다.
오류 분류, 효과 판단, 승인, 저장, 스케줄링은 하지 않는다. `RetryFailure`와 `RetryDecision`은
Application 선택 경계다. `select_retry`와 `change_arguments`는 선택 결과와 수정 범위를
검증하며 실행 함수를 호출하지 않는다.

`ProjectManager.create`, Session/Run 생성, 개별 `atomic_json`/append/replace, transaction commit,
checkpoint 저장, Tool 내부 파일 열기, worker spawn에는 이 재시도를 붙이지 않는다.
기존 저장 복구·CAS·명시적 Run 재개·RAG Job 재개는 별도 계약으로 유지한다.
Graph 전체나 완료된 Tool을 처음부터 반복하지 않는다.

## Application 선택

선택 함수는 동기 또는 비동기 함수다. 비동기 I/O가 필요하면 async 함수를 쓴다.
`RetryFailure`에는 `operation`, 현재 `arguments` 사본, `code`, `message`, 1부터 시작하는
실패 `attempt`, 동일 인자 재시도 가능 여부 `retryable`, 수정 가능 필드 `changeable`이 있다.

| 반환값 | 의미 |
|---|---|
| `None` | 중단하고 실패를 전달 |
| `RetryDecision()` 또는 `RetryDecision({})` | 동일 인자 재시도. `retryable=True`여야 함 |
| `RetryDecision({"path": "src/main.py"})` | 선언된 필드만 교체하고 전체 인자를 재검증 |

수정은 최상위 key 교체다. `null`은 삭제가 아니라 명시적 값이며, 중첩 object를 바꾸면 해당
필드의 전체 값을 전달한다. 원본 인자와 Tool definition을 수정하지 않는다. 선택기는 권한이
없으며 Project 정책, Tool schema, fixed/bounded/selectable 제약을 넓힐 수 없다.

## Tool 사용 예

```python
from llm.llm import BackendServices, ToolRuntime, RetryDecision

async def choose_retry(failure):
    # 경로 탐색/모델 호출/사용자 선택 전략은 Application이 구현한다.
    if failure.operation == "file_read" and failure.arguments.get("path") == "old.txt":
        return RetryDecision({"path": "current.txt"})
    return None

services = BackendServices(tool_runtime=ToolRuntime(decide_retry=choose_retry))
# LargeLanguageModel(..., services=services)에 주입한다.
# ProjectConfig(policies={"tool_retry": {"max_retries": 2, "delay_seconds": 0.1}})
```

`max_retries`는 최초 호출을 제외한 추가 시도 수다. 동일 인자 반복과 수정 인자 반복이
이 한도를 공유한다. `delay_seconds`는 기존 fixed delay다. 미설정이면 자동 재시도하지 않는다.
선택기가 없어도 기존 `retryable=True` + 안전한 효과 조건의 동일 인자 재시도는 유지된다.
선택기가 없으면 Backend가 수정안을 생성하지 않는다.

Tool 구현은 `ToolContract(retry_fields=("path",))` 또는 `@tool(retry_fields=("path",))`로
수정 가능한 parameter를 선언한다. 이 선언 자체는 오류의 안전성을 보장하지 않는다.
실행이 효과를 전혀 발생시키지 않았음을 아는 경우에만 다음 오류를 사용한다.

```python
from llm.services.runtime.tools import ToolExecutionError

raise ToolExecutionError("File not found", effect="none", recoverable=True)
```

`recoverable=True`는 인자 수정 가능성, `retryable=True`는 동일 조건 재실행 가능성을 뜻한다.
둘 다 필요한 Tool은 명시적으로 둘 다 지정한다. `recoverable`은 `effect="none"`일 때만 허용된다.
일반 예외, 취소, uncertain effect는 수정 재시도하지 않는다. 호스트가 `retry_safe_tools`로
선언한 기존 동일 인자 반복은 유지하지만, 그것이 인자 변경 권한을 만들지는 않는다.

기본 Tool에서는 `file_read`의 **파일 없음**만 이 계약을 사용한다. 경로 탈출·symlink·권한·
파일 버전 충돌을 복구 가능으로 바꾸지 않는다. file mutation, shell, subprocess 오류 전체를
자동으로 안전하다고 분류하지 않는다.

## 승인·재개·영수증

수정 인자는 새 invocation으로 schema → Project constraints → trusted classification →
technical authorization → approval 순서를 다시 통과한다. 수정 전 승인으로 실행하지 않는다.
Project의 자동 승인이 있어도 응답 저장 후 명시적 resume이 필요하다. 수정 invocation은
Tool 호출 예산에도 포함된다. 물리 재시도 횟수는 기존 observability 실행 계수로 관찰한다.

재시도 실패는 같은 Tool Step의 `retry_failures`와 CHECKPOINT 이벤트로 기록한다. 처리된
실패마다 FAILED Step을 만들지 않으며, 최종 실패는 기존 실패 Step/Run 계약을 따른다.
`None` 반환은 여전히 성공이다.

기존 checkpoint에 `@tool-retry:<request_key>` 보조 record를 저장한다. 원래 인자, 현재 인자,
누적 추가 시도/수정 횟수, 실패 이력, 효과 불확실성, 다음 시도 대기 여부를 보존한다.
별도 파일 writer나 복구 DB는 없다. RunRepository가 CHECKPOINT 이벤트를 저장한다.
`@tool-retry:` 접두사는 내부 전용이며 custom Engine의 request_key에는 사용할 수 없다.
Loop/Graph/중첩 Engine은 기존 stable checkpoint key와 checkpoint_name을 전달한다.
명시적 재개도 실행한 시도 횟수를 초기화하지 않는다. 승인 대기로 아직 실행하지 않은
예약 시도는 중복 차감하지 않는다. checkpoint가 없는 standalone Tool 호출은 메모리 범위다.

`operation_key`는 계속 Application adapter가 계산한다. 수정 인자로 같은 business key를
재사용하면 기존 원장이 충돌을 거부한다. Backend가 임의로 키를 바꾸지 않는다. 확인된
무효과 실패만 `not_applied`로 기록하며, 이전 uncertain effect를 무효과로 덮지 않는다.
선택 중 취소도 즉시 전달하고 다음 handler를 실행하지 않는다.

선택 함수는 영속 JSON에 저장하지 않는다. 재시작 시 Application이 다시 주입해야 한다.
ToolRuntime의 선택기 존재 여부는 승인 binding에 포함되며 구현 변경 시 기존처럼 runtime
`revision`을 올린다. ToolContract의 `retry_fields`도 binding에 포함된다. 변경 전 계약으로
만든 승인/체크포인트는 그대로 호환 처리하지 않는다. 자동 migration은 없다.

## 모델 호출과 Provider

`LoopEngine(retry_decider=...)`, `BaseEngine.stream_completion(..., retry_decider=...)`,
RAG 모델의 `.with_retry(callback)`, `VisionModel(retry_decider=...)`로 선택기를 연결한다.
실제 횟수·간격·기한은 각 구현체의 기존 provider 정책을 사용한다. Tool 정책을 상속하지 않는다.

이 고수준 모델 경계에서 수정 가능한 요청 필드는 `timeout`이다. 요청 본문, 모델, embedding
차원, cache identity, Tool authority, usage budget은 이미 준비되어 있으므로 재시도 중 바꾸지
않는다. 특히 chunk↔vector 매핑을 바꿀 수 없다. 다른 의미의 요청은 소유 구현체에서 새 작업으로
준비해야 한다. `providers.requests.invoke`의 명시적인 `changeable` 계약을 쓰는 adapter도
SDK retry/stream/cache/Tool 권한 같은 보호 필드를 변경할 수 없다.

`providers.litellm.resolve_call`이 SDK 초기화/함수 선택을 소유하고,
`providers.requests.request_model`이 비스트리밍 public 호출 경계를 제공한다. RAG/Vision은
SDK를 직접 선택하지 않는다. 새 라이브러리 추가 시 그 adapter는 providers 안에 두되,
현재 사용하지 않는 registry/factory 계층은 만들지 않는다.

SDK retry가 활성화되면 outer 호출 1회, `DEFAULT_MAX_RETRIES=0` compatibility,
LiteLLM embedding cache 비활성, 사용량/admission 검사는 유지한다. 첫 streaming chunk가
발생한 뒤에는 자동 재시도하지 않는다. delay/선택 시간도 동일한 wrapper deadline 안에 있다.
SDK 내부 HTTP 재시도와 RAG의 의미 검증 repair는 각각의 소유 계약을 유지한다.

이 기능은 Hub의 자동 경로 수정 전략이나 오류 복구 UI를 새로 제공하지 않는다.
Application이 선택기를 연결하고 Project 재시도 정책을 명시해야 복구가 활성화된다.
