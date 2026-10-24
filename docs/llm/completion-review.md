# 명시적 최종 후보 검토

[Completion 처리](completion-processing.md)의 `after_completion`은 관찰이며 응답을 고치거나
모델을 재실행하지 않는다. F의 `review_final`은 Tool 호출 없는 최종 후보에 대해
`CompletionReview(ReviewAction.ACCEPT | CONTINUE | BLOCK, feedback)`를 반환하는 별도 계약이다.
action과 feedback은 필수다. 기본 accept·자동 활성화·숨은 재시도는 없다.

## 활성화와 소유자

Loop Engine의 현재 parameter envelope `policy.final_review`가 Missing/Null이면 비활성이다.
객체로 활성화하면 네 값을 모두 명시한다. 생성·설정 API가 대신 기본값을 넣지 않는다.

| 필드 | 의미와 유효 범위 |
|---|---|
| max_continuations | 같은 Run에서 허용하는 CONTINUE 수, 정수 0 이상 |
| max_attempts | 후보별 순수 Reviewer 평가의 총 시도 수, 정수 1 이상 |
| hook_timeout | 개별 Reviewer 호출의 초 단위 기한, 유한 양수 |
| max_feedback_chars | 한 평가의 전체 Reviewer feedback 문자 수 상한, 정수 1 이상 |

bool/잘못된 타입/필드 누락은 거부한다. 하위 Session/Agent는 상위 한도를 확대하거나 Null로
해제하지 못한다. Agent의 명시 객체도 필수 네 필드를 갖춘다. 기존 provider.max_attempts 및
SDK 재시도와 별개인 **순수 검토 hook 예산**이다. hook 실패에 대한 Backend 자동 재시도는 없다.

`CompletionPipeline`은 기존 `completion_processors` capability로 등록된 처리기의
`name`, 정수 `priority`, 명시적 비어 있지 않은 `review_revision`을 평가 출처로 사용한다.
활성 정책에는 하나 이상의 Reviewer와 구현된 async `CompletionSession.review_final`이 필요하다.
우선순위/이름으로 정렬하고 모든 Reviewer를 평가한다. `components.review.review_action`만
`block > continue > accept` 우선순위를 선언한다. Caller가 정책·Reviewer를 선택하고 Loop가
상태 전이를 소유하며 저장은 기존 CheckpointRepository가 담당한다.

Reviewer는 외부 효과 없는 순수 평가다. Application은 구현 변경 시 review_revision을 변경해야 한다.
실제 I/O·Tool 실행·승인은 기존 Tool/Component 실행 계약으로 처리한다. feedback은 참고 자료이며
새 권한, Tool 승인, 예산 확대, 원본 User 수정으로 사용하지 않는다.

## 실행과 공개 경계

`prepare → completion → after_completion → review_final → accept일 때 finish` 순서다.
검토 활성 시 LLM Step과 처리기 출력은 internal이며 처리기가 루트 최종 출력을 발행할 수 없다.
검토 전 후보는 Assistant의 최종 content나 user visibility로 공개하지 않는다. 승인된 후보만
Loop의 최종 출력 경계를 통과한다. block/잘못된 verdict/예외/시간 초과/예산 소진은 Run 실패이며
finish나 후보 공개를 수행하지 않는다. 취소는 현재 Task와 기존 자원 정리를 따른다.

continue는 원본 ModelResponse의 signed/opaque 정보와 Tool transcript를 보존하고 검토 feedback을
참고 User 메시지로 추가해 **같은 Run**을 이어간다. iteration/논리 Agent Tool 호출/모델 호출·토큰/
Run timeout 한도는 유지한다. 새 live instruction이 accept 도중 도착하면 기존 지시 선택 경계에서
다음 후보를 생성·검토한다. 검토가 지시를 무시하고 이전 후보를 확정하지 않는다.

`CompletionReviewObservation` schema 1은 iteration, 준비된 공통 messages, response,
original_messages, tools를 모두 저장하며 Reviewer마다 분리 복사한다. 공급자 호출 parameters나
SDK credential은 포함하지 않는다. 사본 변경은 실제 후보·다른 Reviewer에 전파되지 않는다.

## 필수 증거와 Resume

Loop의 현재 `loop-iterations-v2` Checkpoint 안에 `completion-review-v1` 계약을 저장한다.
header.review에는 현재 정책과 전체 Reviewer identity/revision이 필수다. Tool 없는 iteration에는
원본 response와 review_observation이 필수이며 다음 키를 저장한다.

| 키 | 저장 시점과 의미 |
|---|---|
| iteration:N | after_completion 성공 후, 후보와 공통 관찰 원본 |
| review_attempt:N | hook 호출 **전** 후보 digest와 예약한 attempt |
| review:N | 모든 verdict, 각 출처/revision, aggregate action, 동일 후보·attempt |

Checkpoint manifest/revision/CAS와 기존 트랜잭션이 기록 소실·쓰기 중단을 검사한다. verdict는
동일 후보·예약·전체 Reviewer 출처·feedback bound·우선순위와 일치해야 한다. 필수 기록을 빈 값이나
기본 accept로 복원하지 않는다. 서비스의 runtime_tool_usage는 기존 서비스 Reader가 검증한다.

명시적 Resume는 기존 후보/완료 verdict/Tool 영수증을 재사용한다. 미완료 순수 검토만 남은 명시
attempt 예산 안에서 다시 평가한다. block, exhausted attempt/continuation, 변경된 Reviewer revision,
손상·누락 증거는 재개를 거부한다. 완료 모델 호출과 Tool 효과를 재실행하지 않는다. 이미 완료된
후보는 prepare/after_completion을 반복하지 않으며 기존 Memory lazy prepare/finish 의미도 유지한다.
Graph 안에서는 기존 checkpoint_scope/output_step_id로 같은 Run의 노드에 증거를 귀속한다.

새 Resume Run의 timeout과 Run-local 사용량은 [기존 계약](long-running.md)을 그대로 따른다.
Project 누적 사용량·미정산 예약, 논리 Agent Tool 예산, 검토 attempt/continuation은 원본 증거를
보호한다. 이전 Run-local timeout/모델 사용량을 새로 합산하는 구형 브랜치 정책은 도입하지 않는다.

## 검증과 범위

[검증 기록](completion-review-verification.md)과 [Level 1–3 정책](verification-policy.md)을 따른다.
실제 fsync 파일 효과, 승인/Resume, Reviewer 오류/취소, checkpoint 쓰기 실패와 프로세스 강제 종료를
검증한다. Reviewer가 악의적으로 임의 Python I/O를 하지 못하도록 sandbox하는 기능은 아니다.
명시 Reviewer 순수성·revision의 소유 책임과 기존 Tool 실행 권한 경계는 유지한다.


구현의 정규 선언: 관찰/verdict/Reviewer identity의 필드명은 해당 타입 선언에서 파생한다.
`ReviewPolicy`가 필드·유효 범위·schema와 조회 API를 제공하며 Pipeline/Loop는 정책 키를
재선언하지 않는다. read는 원래 명시 객체의 독립 사본을 반환하고 생략/Null은 비활성 그대로다.
저장 JSON 필드·정책·digest·판정 우선순위·예약/완료 경계는 유지한다.

`LoopIterationRecord`가 iteration 키와 응답/검토 관찰 증거의 필드 및 Writer를 소유한다.
LoopReview는 header 계약·예약/결정의 필수 증거·digest·대응 관계·재개 한도를 검사한다.
Reviewer identity와 개별 verdict의 provenance 검증은 각각 ReviewerIdentity와
CompletionReview가 제공하며 저장/실행 소비자는 이 단일 검증을 호출한다.
서비스 논리 Tool 사용량 필드는 ToolUsageCheckpoint 선언을 참조하고 그 의미는 해석하지 않는다.
