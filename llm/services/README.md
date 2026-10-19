# Services — 공개 API와 도메인 수명 관리

서비스는 UI/Engine과 영속 데이터 사이를 연결합니다. 일반 사용자는 [LargeLanguageModel](../README.md)의 핸들 API를 사용하고, 저장소·처리기를 주입하는 개발자는 이 폴더를 살펴보면 됩니다.

```text
LargeLanguageModel → api.py의 핸들
  ├ lifecycle: 생성·설정·복제·삭제
  ├ runtime: 요청·실행·승인·이벤트
  ├ history: 대화·문맥·보관·복구
  └ infrastructure: 저장·잠금·트랜잭션·로그
```

## 파일과 하위 폴더

| 파일/폴더 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | 서비스 패키지 설명입니다. |
| [api.py](api.py) | Project/Session/Request/Run/Step/Component를 탐색하는 공개 Facade입니다. |
| [composition.py](composition.py) | BackendServices로 저장소·factory·호스트 자원을 한 번 구성합니다. |
| [schema.py](schema.py) | 등록된 Engine/Component를 반영한 UI 설정 schema와 유효 설정을 계산합니다. |
| [query.py](query.py) | Query의 상태·위치·개수 등 목록 필터 계약입니다. |
| [results.py](results.py) | Project/Session에서 Run 소유 결과를 모아 조회합니다. 별도 결과 파일을 만들지 않습니다. |
| [lifecycle/](lifecycle/README.md) | Project/Session/Step 및 ComponentData의 수명 관리입니다. |
| [runtime/](runtime/README.md) | RunManager, ToolExecutor, 체크포인트·승인·이벤트·사용량 처리입니다. |
| [history/](history/README.md) | ConversationStore, 문맥 정책, 복구·보관입니다. |
| [infrastructure/](infrastructure/README.md) | 공통 I/O, 잠금, 트랜잭션, 프로세스, 로그·관찰입니다. |

## 공개 API를 사용할 때

비동기 UI는 `acreate/aload/asave/alist` 같은 I/O API를 사용합니다. `submit/steer/wait/interrupt/shutdown`은 원래 비동기입니다. Facade는 백엔드에서 구성한 같은 저장소·ConversationStore factory·StorageIO를 실행과 조회에 사용합니다.

반환되는 핸들은 실행 서비스를 연결하는 객체이며 JSON으로 저장하는 도메인 모델이 아닙니다. `aget_data()`는 독립된 도메인 스냅샷을 반환하고, 그 객체를 변경해도 저장되지 않습니다. 명시적 저장 API를 사용하세요.

설정은 [ProjectConfig 계약](../CONFIGURATION.md)을 따릅니다. BackendServices는 구현체와 호스트 공유 자원을 주입하는 곳이며 Project의 정책 값을 대신 생성하지 않습니다.

## Run 상태 전이

| 작업 | 이전 상태 | 다음 상태 |
| --- | --- | --- |
| 시작 | PENDING | RUNNING |
| 정상 종료·실패·중단·대기 | RUNNING | COMPLETED / FAILED / INTERRUPTED / PAUSED |
| 미시작 수신자 취소 | PENDING | CANCELLED |
| 재시작 복구 | 불확실한 PENDING / RUNNING | INTERRUPTED |

`core.models.validate_run_transition`은 허용 전이만 판단합니다. 시각·저장·Step/Conversation 변경은 서비스가 수행합니다. 종료된 Run을 다시 열지 않으며 명시적 재개는 새로운 Run을 만듭니다. 대기 요청 취소는 Message의 CANCELLED로 처리하고 새 Run을 만들지 않습니다.

`steer(run_id, text, targets=...)`는 같은 RUNNING Run에 입력을 추가하며 Run 상태 전이를 만들지 않습니다. 단독 Loop는 대상 생략이 가능하고 Graph는 `run.ainstruction_targets()`의 소비자 ID를 지정합니다. 대상별 반영 및 전체 pending/applied/unapplied/partially_applied 상태는 `run.ainstructions()`로 조회합니다. [접수·반영·재개 경계](../../docs/llm/steering.md)를 따릅니다.

미시작 Agent에는 `run.ainstruction_routes()`의 경로를 선택해 `session.run.reserve_instruction(run_id, text, targets=routes)`로 예약합니다. 경로별 다음 실행 한 번에만 결합하며 미사용 예약을 명시적 재개에 이전하지 않습니다. 적용한 예약은 기존 문맥만 복원합니다.

RunManager는 종료 상태와 오류의 조합, Session의 current_run_ids에서 해당 Run의 소유권을 확인합니다. 늦은 종료가 다른 활성 Run의 소유권을 해제하거나 이미 저장된 종료를 덮어쓰지 못하게 합니다. Step 정리 → Assistant 상태 → Run → Session 기록은 기존 트랜잭션 경계를 따르고 저장 성공 후 수명 알림을 전달합니다.

## 확장할 때

- Engine은 이벤트만 발생시키고, StepEventRecorder/StepManager가 Step을 저장합니다.
- 사용자 이벤트 처리기는 기존 EventContext API를 사용합니다. 정책·출력·체크포인트 등 소유자가 정해진 메타데이터를 덮어쓰지 않습니다.
- UI 구독은 관찰용입니다. 저장소 원본과 누락 복구 경로를 유지하세요.
- Repository 교체 시 Facade 조회와 runtime이 같은 인스턴스를 사용하도록 BackendServices에서 구성합니다.
- 새 코드는 책임별 하위 패키지에 둡니다. 단순 폴더 분류를 새 실행 계층으로 만들지 않습니다.

[이벤트·구독 예제](runtime/README.md), [서비스 확장 계약](../../docs/llm/service-extensions.md), [공통 데이터 API](../../docs/llm/data-contracts.md)에 세부 계약이 있습니다.


## 여러 Agent가 참여하는 Session

`project.agents`는 정의를 소유하고 `session.agents.attach/detach/list`는 ID 참조를
변경합니다. `session.run.submit(text, agent_ids=("writer", "reviewer"))` 또는
`recipients="all"`로 접수한 한 User Message는 Agent마다 별도 Run과 Assistant를
갖습니다. `all`은 접수 때 참여자 집합을 고정합니다. 결과는
`request.runs()` / `await request.wait_all()`로 조회합니다. 한 수신자는 기존
`await request.wait()`를 사용하며 다중 수신자에 대한 단일 결과 조회는 거부합니다.

동일 Session의 같은 Agent는 FIFO, 다른 Agent는 병렬입니다. 하나의 Dispatcher가
모든 실행·종료를 관리하며 `run.max_parallel_runs`가 선택적 상한입니다. 기존
`engine=` 호출은 명시적인 호환 Agent와 독점 FIFO 경로로 같은 Dispatcher에
들어갑니다. User 접수 후 호환 Agent 참여 등록·Run 생성·시작을 한 트랜잭션에서
수행하여 기존 저장 실패 경계를 보존합니다. 신규 다중 접수는 전체 Run 그룹을
원자적으로 만들고, 실행하지 않았음이 증명되는 PENDING 수신자만 재기동 후
예약합니다. RUNNING/불확실한 기록은 자동 재실행하지 않습니다.

`session.run.interrupt()`는 현재 실행(실행 시작 직전인 첫 접수 포함)을 중단하고,
`interrupt(run_id)` 또는 `cancel(run_id)`는 선택한 Run만 처리합니다. `cancel`은
증명된 미시작 Run의 PENDING→CANCELLED도 지원합니다. `request.cancel()`은 아직
Run 그룹으로 청구되지 않은 User만 취소합니다. `shutdown()`은 대기 요청을 보존하고
활성 실행·최종 저장·분리할 수 없는 외부 효과 정리를 기다립니다. 자세한 저장 버전,
공유 문맥, 복구·보관 경계와 검증 결과는 [단계 기록](../REFACTOR_BF.md)을 따릅니다.

## Session 간 명시적 전송

같은 Project의 신뢰된 호스트 Facade에서 다음처럼 사용합니다. 모델 Tool은 자동으로
등록하지 않으며, Session ID만으로 호출 권한을 부여하지 않습니다.

```python
transfer = await source.exchange.send(
    target.id, "명시적으로 전달할 입력", agent_ids=("writer", "reviewer"),
    key="review-42", source_run_id=source_run.id,  # 호스트 직접 요청은 None
    deadline="2026-10-20T12:00:00+00:00",          # 선택 사항, 절대 UTC 마감
)
status = await transfer.asnapshot()  # 접수/실행/대기/실패 및 개별 Run 결과 참조
result = await transfer.wait(timeout=30)  # 선택 사항; 대기 취소는 대상 실행을 취소하지 않음
await transfer.cancel(run_id=selected_run_id)  # 생략하면 이 전송의 대기/활성 실행 전체
transfer = await source.exchange.aget("review-42")  # 새 Backend에서도 재조회
```

`source.exchange.inspect(target.id, conversation_limit=0)` / `ainspect`는 상태를
조회합니다. 대화는 별도 허용 시 1~100개의 User/Assistant 공개 본문만 반환하며,
Memory·내부 Tool trace·설정·원본 승인 기록은 전송하거나 조회 결과에 복사하지 않습니다.
`BackendServices(session_authorizer=callback)`은 `send`, `execute`, `inspect`,
`conversation`, `query`, `cancel`, `resume` 작업을 각각 더 제한할 수 있습니다.
callback은 복사된 출처/대상/수신자 정보를 받고 동기적으로 정확한 bool을 반환합니다.
기본 권한은 기존 Backend를 소유한 신뢰된 호스트의 같은 Project 접근이며, 임의
Python 확장 코드를 격리하는 다중 사용자 보안 경계로 해석하면 안 됩니다.

명시적 `agent_ids`만 지원하며 임의 기본 Agent나 전체 참여자를 추측하지 않습니다.
`source_run_id`를 지정하면 실제 해당 Session의 Run에서 Agent와 입력 Message ID를
도출합니다. 같은 원본 Session/key와 동일 payload는 원래 접수를 반환하고 다른
payload는 충돌합니다. 저장은 파일 ConversationStore만 지원합니다. 임시 메모리
대상을 영속 접수로 표시하지 않도록 명확히 거부하며 일반 메모리 요청은 유지됩니다.

원본 Session의 전달 영수증과 대상 User는 기존 workspace 트랜잭션에서 함께
커밋됩니다. 대상 User의 기존 Dispatcher가 유일한 실행 소유자입니다. 접수 후
참여자 detach는 대상 집합을 바꾸지 않으며 새로운 요청은 거부됩니다. 실행 직전
출처/권한/마감을 다시 검사하고, 기존 Run timeout과 더 짧은 기한을 적용합니다.
진행 중 외부 효과의 취소는 기존 quarantine/영수증 규칙을 따릅니다. 권한 거부는
`transfer_denied`, 실행 전 만료는 `transfer_expired`, 실행 중 기한은 `run_timeout`으로
관찰됩니다. 만료 뒤 조회/동일 키 재조회는 가능하지만 재개가 기한을 연장하지 않습니다.

`await transfer.resume(run_id, ...)`도 기존 대상 승인·Checkpoint API를 사용합니다.
원래 Session의 Run API로 재개해도 전달 출처/기한을 보존합니다. 대기 중인 승인은
cancel로 암묵적으로 승인하거나 포기하지 않습니다. 결과는 원본과 재개 시도를 각각
반환하며 `previous_run_id`로 연결합니다. 대기는 호출 시 알려진 시도들을 기다립니다.
최상위 status가 `partial_failure`이면 개별 Run 상태를 확인합니다.

출처 체인은 반복 Session과 16개를 넘는 Session을 거부합니다. 요청자의 Worker가
대상 완료를 암묵적으로 기다리지 않습니다. 전달 영수증이 유지되는 동안 연결된
Session의 삭제·대화 턴 삭제·retention을 보수적으로 막아 멱등성 및 결과 참조를
보존합니다. clone은 실행 라우팅을 비활성 출처 자료로 바꾸고 전송하지 않으며,
backup/recovery는 연결 손상을 보고하고 재전송하지 않습니다. 전체 Project 삭제는
원래 수명 계약대로 소유 영역 전체를 삭제할 수 있습니다.

## 최종 후보 검토 (`review_final`)

호스트가 등록한 CompletionProcessor에 `review_revision`을 명시하면 해당
Processor의 호출별 Session이 검토에 참여합니다. 작업별 성공 기준은 호스트가
구현합니다. 백엔드는 판정 기준을 만들거나 Tool 승인을 대신하지 않습니다.

```python
from llm.components.processing import (
    CompletionSession, CompletionReview, CompletionReviewObservation, ReviewAction,
)

class ReviewSession(CompletionSession):
    async def review_final(self, observation: CompletionReviewObservation) -> CompletionReview:
        # 호스트 검증기는 observation과 선언한 revision에 대한 순수 결정을 반환합니다.
        return CompletionReview(ReviewAction.ACCEPT)

class Reviewer:
    name = "quality"
    priority = 100
    close_timeout = 1.0
    review_revision = "quality-v1"

    def session(self, context):
        return ReviewSession()
```

기존 `completion_processors` capability로 `Reviewer()`를 제공하면 됩니다.
`review_revision` 없이 `review_final`을 재정의하면 모델 호출 전에 실패합니다.
검토 훅이 없는 기존 Processor는 그대로 사용할 수 있고 스트리밍도 유지합니다.
Agent 정의의 선택 필드 `completion_processors: ["quality", ...]`는 등록된
Processor 이름을 선택합니다. 생략하면 부모/호스트 선택을 상속하고, 빈 배열은
모두 제외합니다. 자식 Agent는 부모가 제외한 Processor를 다시 추가할 수 없습니다.
이 선택은 Memory/Goal을 포함한 전체 Processor 목록에 적용됩니다. 등록 이름과
revision/priority 변경은 검토 Checkpoint binding에 반영됩니다.

`CompletionReviewObservation`은 `iteration`, 준비된 공통 `messages`, 공통
`answer`, 준비 전 `original_messages`, 공통 `tools`의 독립 사본입니다.
Provider 옵션·API 키·클라이언트는 포함하지 않습니다. 검증기마다 사본이 분리되고
반환값은 `CompletionReview(ReviewAction.ACCEPT|CONTINUE|BLOCK, feedback="...")`
입니다. 문자열/딕셔너리 반환, 과도한 피드백, 예외는 실패입니다. 모든 검증기를
priority/name 순서로 호출하며 **block > continue > accept**가 적용됩니다.
취소는 Run 중단으로, 협력적 훅 시간 초과는 `completion_review_timeout`으로
처리됩니다. 임의 Python 확장을 격리하는 sandbox 계약은 아닙니다.

Tool 호출 없는 후보만 `after_completion()` 후 검토합니다. `continue`는 같은
Run의 다음 의미적 iteration입니다. 후보와 출처·revision이 붙은 피드백을
명시적인 비신뢰 참고 자료로 추가하고 원본 User/system/developer 및 Tool 쌍은
보존합니다. 후속 Tool 요청은 기존 ToolExecutor와 승인·operation 영수증을
사용합니다. `accept` 후 마지막 Steering 입력을 확인하고 `finish()` 및 최종
OUTPUT을 확정합니다. 검토 중 들어온 Steering 입력도 같은 Run에서 처리됩니다.
`finish()`는 수락한 최종 후보에만 호출됩니다.

검토 사용 시 모델 Step의 후보 delta/output은 `visibility="internal"`입니다.
대화의 Assistant 본문에는 수락된 최종 OUTPUT만 반영됩니다. 검토 중 Run은
`running`이며 별도 Assistant 도메인이나 Message 상태 enum을 만들지 않습니다.
내부 Step/출력 저널을 표시하는 호스트는 기존 visibility 계약을 따라야 합니다.
Graph 조율자 자체에는 모델 검토가 없고, 하위 Loop Agent는 부모 Run 안의 Step과
독립된 Checkpoint scope에서 검토합니다. `agent_run` 위임도 같은 계약입니다.

선택적인 Engine `policy.final_review` 설정과 실행 기본값:

```json
{"max_continuations": 8, "max_attempts": 3,
 "hook_timeout": 30.0, "max_feedback_chars": 4096}
```

`max_continuations`는 추가 의미적 호출 수(0 허용), `max_attempts`는 후보별 순수
검토 재시도 수, `hook_timeout`은 각 훅의 초 단위 협력적 기한,
`max_feedback_chars`는 모든 반환 피드백 문자열의 합계 상한입니다.
기존 iteration/Run timeout/모델 사용량/토큰/Tool 한도도 함께 적용됩니다.
명시적 Resume의 모델 quota는 이전 시도들의 모델 및 Component 영수증까지
합산하며, 미확인 사용량을 0으로 환산하지 않습니다. Project 합계에는 중복
합산하지 않습니다. 별도 Restart는 새 요청입니다. 손상·순환·4096개를 넘는
Resume 계보는 quota 조회에서 거부합니다.

검토를 사용하는 Run에 `policies.run.timeout_seconds`가 있으면 원래 시작 시각의
절대 기한을 Checkpoint에 보존합니다. **이 보수적인 기한은 승인 대기 시간도
포함하며 Resume로 연장되지 않습니다.** 만료 후 재개는 모델/Tool 실행 전에
`run_timeout`으로 실패합니다. 검토가 없는 기존 timeout 계약은 유지됩니다.

공개 `run.acheckpoint("loop")` 및 기존 Checkpoint 이벤트에서 버전이 있는
`header.review`, `iteration:N.review_observation`, `review_attempt:N`, `review:N`을
확인할 수 있습니다. 시도 예약을 먼저 확정하고, 판정/출처/피드백을 확정한 뒤
후속 모델 호출 또는 최종 OUTPUT으로 넘어갑니다. Graph/위임에서는 같은 payload가
기존 Engine scope envelope에 들어갑니다. 완료 판정은 재사용하고, 판정이 확정되지
않은 경우에만 저장된 후보를 제한된 횟수로 다시 검토합니다. 재개는 기존 공개
`session.run.resume(run.id)`와 승인 API를 사용합니다.

저장된 모델 응답의 `prepare`/`after_completion`은 재실행하지 않습니다.
검토기는 fresh Session에서도 저장 관찰만으로 같은 결정을 내릴 수 있어야 합니다.
기존 `finish` 확장은 재개 중 다시 호출될 수 있으며, `prepare`에 의존하는 Memory의
기존 지연 초기화 경계도 유지합니다. 임의 확장 효과나 사용자 알림의 exactly-once
전달을 보장하지 않습니다. Tool 효과는 별도의 기존 영수증/불확실성 규칙을 따릅니다.
