# 서비스 확장 API

기본 실행 계층은 `Project → Session → Run → Step`이다. Conversation은
Session에 속하고, Engine은 이벤트만 생성한다. 서비스 구성을 바꾸어도 이 소유 관계와
기본 JSON/JSONL 파일 형식은 유지된다. 대화만 메모리에 보관하려면
`backend.projects.create(..., conversation_storage="memory")`로 프로젝트별로 선택한다.
백엔드의 `conversation_storage` 또는 `BackendServices.conversations`는 새 프로젝트의 기본값이다. 저장된 프로젝트 선택을 덮어쓰지 않는다.
수명과 복구 범위는 [대화 저장 방식](conversation-storage.md)을 참고한다.

## 서비스 구성을 한 번 지정하기

```python
from llm.llm import LargeLanguageModel
from llm.services.composition import BackendServices
from llm.services.infrastructure.logging import DomainLogger, LogConfig

services = BackendServices(
    logger=DomainLogger(LogConfig(
        max_bytes=2 * 1024 * 1024,
        backup_count=5,
        log_reads=False,
    )),
)

backend = LargeLanguageModel("./workspace", services=services)
project = await backend.projects.acreate(config={
    "policies": {"context": {"mode": "recent_completed", "max_turns": 10}},
})
```

`BackendServices`에는 `project_repository`, `session_repository`, `run_repository`,
`step_repository`, `conversations`, `context_builder`, `token_counters`, `policy_resolver`,
`event_handlers`, `logger`를 지정할 수 있다. 미지정 저장소는 백엔드 구성 시 생성한다.
실행, Facade 조회, Project/Session 결과 조회는 같은 저장소 인스턴스를 사용한다.
사용자 policy_resolver의 `resolve(policies)`는 `(ContextPolicy 또는 None, RunPolicy)`를
반환하고 `token_counters`에 등록된 계산기만 제공한다. 모델 입력 정책이나 Engine/Component
재시도 객체를 만들어 반환하지 않는다. 기본 resolver도 같은 계약을 사용한다.
주입 context_builder의 for_run은 keyword policy로 해당 Run의 프로젝트 정책을 받는다.
추가 지시는 원래 요청과 같은 턴이다. 사용자 context_builder도 Message.metadata.steering을
구분하여 따로 새 턴으로 자르지 않아야 한다. 기본 CompletionPolicy는
prepare_turn(request, current_index, turn_starts)로 활성 턴 전체를 보호한다. 주입한
completion policy도 추가 지시가 포함된 입력을 다룰 때 이 메서드를 제공해야 한다.
공통 선택값은 ProjectConfig.policies, 모델 입력 정책은 해당 Engine의 policy.completion에서
관리한다. custom Engine은 필요한 정책 객체를 직접 구성한다. [소유권과 설정 예](project-policies.md)

저장소 교체는 기존 모델/경로/동기 저장/ownership 계약을 따르는 구현을 대상으로 한다.
이 기능 자체가 데이터베이스 어댑터나 분산 실행을 제공하는 것은 아니다.

## Engine의 capability 선언

```python
from llm.engines.base import BaseEngine

class ResearchEngine(BaseEngine):
    required_capabilities = ("documents",)

    async def run(self, context):
        document_providers = context.capabilities["documents"]
        yield str(document_providers[0])
```

선언하지 않은 Engine은 capability를 요청하지 않는다. Tool 설정이 잘못되어도 Tool을
쓰지 않는 Engine에는 영향을 주지 않는다. `LoopEngine`은 `("tools",)`를 선언한다.
기존 사용자 Engine이 `context.tools`를 사용한다면 같은 선언을 추가해야 한다.

일반 capability 값은 해당 기능을 제공하는 선택된 Component들의 결과 튜플이다.
`tools`는 기존 ToolRegistry로 합쳐져 `context.tools`와
`context.capabilities["tools"]`에서 접근할 수 있다. Tool이 없는 프로젝트의 빈 ToolRegistry는
정상이며, 다른 필수 capability의 제공자가 없으면 Run이 `capability_failed`로 실패한다.
PipelineEngine은 모든 단계의 선언을 합쳐 Run 시작 시 한 번 구성한다.
준비 단계에서 생성하는 임시 데이터는 `context.state`를 사용한다.

## 사용자 이벤트 처리기

```python
from llm.engines.base import BaseEngine, EngineEvent
from llm.services.runtime.events import EventHandlers

handlers = EventHandlers()

async def validate(context, event):
    accepted = bool(event.metadata.get("input"))
    if not accepted:
        raise ValueError("입력이 비어 있습니다")
    await context.update_metadata({"validation": "accepted"})
    context.engine_context.state["validated"] = True

handlers.register("validation.request", validate)

class ValidatedEngine(BaseEngine):
    async def run(self, context):
        yield EngineEvent(
            "validation.request",
            metadata={"input": context.messages[-1].content},
        )
        yield "검증 완료"

# BackendServices(event_handlers=handlers)로 백엔드에 전달한다.
```

처리기는 동기 함수 또는 async 함수다. RunManager는 처리 완료를 기다린 후 다음 이벤트로
진행한다. 실패하면 Run이 실패하고, 명시적 interrupt로 대기 중인 비동기 처리기를 중단할 수 있다.
검증 결과·검색 결과 등을 Run에 남길 때는 `update_metadata()`를 사용한다.
`policies`, `completions`, `resume`, `checkpoints`, `output`, `steering`은 서비스 소유 키다.
하나라도 포함하면 `ValueError`로 전체 갱신을 거부하며 허용 키만 부분 저장하지 않는다.
정책은 Project 설정 API로 변경하고, 실행 중인 Run의 시작 시점 정책 사본은 유지한다.
그 밖의 사용자 JSON 키는 기존처럼 사용할 수 있다. 저장·커밋 실패 시 기존 트랜잭션이
파일과 메모리 Run 객체를 함께 복원한다. 저장 형식이나 기존 자료를 변환하지 않는다.

텍스트, Completion, Step 수명 주기 이벤트는 기본 처리기가 담당하며 교체할 수 없다.
등록하지 않은 사용자 이벤트는 조용히 무시하지 않고 실패시킨다.
별도의 승인 UI, 승인 대기 영속 큐, GraphEngine은 여기서 자동 구현되지 않는다.
사용자 처리기에서 그 동작을 연결할 수 있으며, 재시작 시 기존 Run 재실행 금지 규칙은 동일하다.

## 여러 이벤트 구독자 연결

```python
def display(run, event):
    print(event.type)

async def monitor(event):
    # 비동기 모니터링 시스템으로 전달하는 위치
    print(event.run.id, event.type)

unsubscribe = backend.events.subscribe(display, channel="engine")
backend.events.subscribe(
    monitor, channel="run", delivery="queued", buffer_size=64,
)

# 새 전달 중단과 대기 알림 폐기 요청
unsubscribe()
# 실행 중이던 콜백과 자원 정리 완료까지 기다리기 (해제 요청도 포함한다)
await unsubscribe.aclose()
```

`engine` 콜백은 `(run, event)`, `run` 콜백은 `(event)`를 받는다. 각 구독자에게 복사본을
전달한다. `inline`은 실행 루프에서 호출하고 async 반환값을 기다린다. `queued`는 구독자별
유한 큐를 사용하며 동기 콜백은 작업 스레드에서, async 콜백은 원래 이벤트 루프에서 실행한다.
기본 `overflow="block"`은 큐가 가득 차면 생산자가 기다린다. UI용
`overflow="drop_oldest"`는 오래된 알림을 버리므로 dropped 통계와 저장 API로 재조회한다.
각 구독자의 큐 순서는 보장되지만, 서로 다른 Session의 알림은 섞일 수 있다.

구독자는 관찰 전용이다. 같은 백엔드의 실행 완료나 shutdown을 구독자 안에서 기다리면
서로 기다리는 상황이 생길 수 있다. 실행 제어가 필요한 동작은 이벤트 처리기에 둔다.
구독자 예외는 기록하고 다른 구독자/Run을 계속 진행한다. 백엔드 종료는 큐를 배출한다.
기존 `on_event`, `on_run_event` 인자도 유지되며 async 콜백을 지원한다.

개별 해제는 아직 전달하지 않은 알림을 버리고 막힌 생산자를 깨운다. 이미 시작한
콜백은 완료까지 기다리며 해제 자체가 콜백이나 inline 실행 Task를 취소하지 않는다.
콜백 안에서는 `unsubscribe()`를 호출할 수 있지만 자기 `aclose()`나 전체 이벤트
`close()`를 기다리면 `RuntimeError`다. queued 동기 콜백 스레드의 해제 요청은
소유 이벤트 루프로 예약한다. 다른 async API는 처음 사용한 이벤트 루프에서 호출한다.

`aclose()`는 반복/동시 호출할 수 있다. 한 대기자를 취소해도 다른 대기자나 콜백을
취소하지 않는다. `callback_timeout`을 명시했다면 기존 시간 제한을 적용하고 그 뒤
자동 회수한다. 미설정이면 새 제한을 만들지 않으며 끝나지 않는 콜백은 정리를 지연시킨다.
시간이 초과된 동기 콜백 스레드는 강제 종료되지 않고 실제 반환까지 참조가 남을 수 있다.

worker 종료 후 콜백/큐 참조를 회수한다. `Subscription.stats`는 해제 후에도 조회할 수
있으며 백엔드는 종료된 구독별 객체 대신 숫자 합계만 보존한다. `pending`은 대기 중인
알림 수이고, 의도적인 해제 폐기는 overflow의 `dropped`나 전달 성공으로 세지 않는다.
`flush()`는 수락된 큐 작업 처리를 기다린다. 정상 백엔드 종료는 해제하지 않은 구독의
대기 알림을 처리한 뒤 회수하며, `aclose()`는 특정 구독의 최종 정리까지 기다린다.

## 대화 문맥 프리셋

| mode | 포함할 과거 대화 |
| --- | --- |
| `full` | 전체 대상 대화; 정책 미설정 시에도 별도 필터링 없음 |
| `recent` | 최근 `max_turns`개 사용자-응답 그룹 |
| `completed` | 정상 완료된 Assistant 응답이 있는 그룹만 |
| `recent_completed` | 정상 완료된 그룹 중 최근 `max_turns`개 |
| `budget` | `max_chars` 이내에 들어오는 최근 그룹 |

모든 정책은 현재 입력을 유지한다. `max_chars`는 다른 mode와 함께 사용할 수도 있다.
현재 입력 자체가 문자 예산을 넘으면 잘라내지 않고 오류를 반환한다. 문자 예산은 토큰 예산이
아니며, 시스템 프롬프트/Tool 정의/공급자 포맷 비용을 포함하지 않는다.
정책은 모델 입력에만 적용되며 저장된 대화와 복제 내용은 삭제하거나 요약하지 않는다.
토큰 계산은 BackendServices.token_counters와 프로젝트 completion 정책, 요약은 Memory로 구성한다.

## 조건 조회

```python
from llm.services.query import Query

page = await session.run.alist(query=Query(
    after=last_run_id,
    status="completed",
    limit=20,
))
recent = await project.results.alist(query=Query(descending=True, limit=10))
messages = await session.aconversation(query=Query(offset=20, limit=20))
steps = await run.steps.alist(query=Query(status="failed"))
```

Project/Session/Run/Step 목록과 결과/대화 목록에 같은 Query를 사용한다. Project 상태 조건은
`active`/`deleted`, 다른 도메인은 각 status 문자열이다. 기존 `include_deleted`와
`include_running` 필터가 먼저 적용되므로 해당 범위 안에서 커서를 지정한다.

정렬 방향 → `after` ID 다음 → `status` 필터 → `offset` → `limit` 순서다.
`offset`은 0부터 시작하는 건너뛸 개수이며 `limit=0`은 빈 목록이다. 찾을 수 없는 커서는
ValueError다. 커서는 고정 스냅샷이 아니므로 조회 사이에 상태가 바뀌거나 삭제될 수 있다.

기본 JSON 저장소는 정렬을 위해 여전히 파일 메타데이터를 읽는다. 이번 변경은 조건 조회
계약을 추가하며 디스크 인덱스/DB를 도입하지 않는다. 비동기 API는 이벤트 루프의 직접 파일
I/O를 피하지만, 전체 스캔 비용까지 제거하지는 않는다.

## 로그 출력과 실행 중 Session 수정

```python
def host_log(path, event, fields):
    # 호스트 로거로 전달. 여러 저장 스레드에서 호출될 수 있다.
    print(path, event, fields)

services = BackendServices(logger=DomainLogger(sink=host_log))

# 실행 중에도 가능: metadata는 전체 교체
await session.asave(title="새 제목", metadata={"label": "리뷰"})

# 실행 설정 변경은 기존처럼 런타임 해제 후 수행
await session.run.wait_idle()
await session.run.shutdown()
await session.asave(config={"parameters": {"engines": {"loop": {'config': {'completion': {'temperature': 0.2}}}}}})
```

기본 출력은 각 도메인의 `logs/service.log`, 1 MiB, 백업 3개다. sink를 지정하면 그 출력을
대체한다. sink는 동기·스레드 안전 함수여야 하며, 실패가 도메인 저장을 취소하지 않는다.
설정은 workspace 작업 문맥에 적용되어 서로 다른 백엔드 사이에 섞이지 않는다.

ComponentData에는 `bind_runtime(runner=..., access_check=...)` 계약이 추가되었다.
Facade는 이 메서드로 실행기/수명 검사기를 연결하며 내부 속성을 직접 대입하지 않는다.
