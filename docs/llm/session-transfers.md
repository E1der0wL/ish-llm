# Session 간 요청 전송

E의 소유 구현체는 `llm/services/runtime/transfers.py`다. 실행 접수와 fan-out은
기존 RequestAdmission/RunManager/Dispatcher를 사용한다. 별도 재시도나 scheduler는 없다.
[현재 설계](clean-refactor.md), [데이터 수명](data-lifecycle.md),
[트랜잭션](storage-transactions.md), [검증 정책](verification-policy.md)이 우선한다.

## API와 권한

`source.exchange.send(target.id, content, recipients=[agent_id], key=key,
source_run_id=None, deadline=None)`는 durable 접수 후 TransferHandle을 반환한다.
수신자는 명시한 정규 Agent 목록이며 대상 Session 참여자에 포함돼야 한다.
`get(key)`/`snapshot()`/`wait(timeout=...)`는 원래 요청과 이후 Resume 요청의 Run 목록,
Assistant result reference를 반환한다. `cancel(run_id=...)`는 선택 Run만 취소하고,
생략하면 그 전송에 속한 대기 요청·활성 Run을 취소한다. `resume(run_id, agent_id=...)`는
기존 Resume/CAS/승인 계약을 사용한다. async 조회는 `aget`, `asnapshot`, `ainspect`다.
`inspect(target_id)`는 제목·참여자 snapshot만 반환한다. Live target handle은 반환하지 않는다.

이 facade는 신뢰된 Application/host의 같은 Project 작업 권한이다. Agent/Tool에 자동 노출하지
않고 ID만으로 다른 Project에 접근하지 않는다. Host는 `BackendServices.transfer_authorize`로
send/inspect/query/cancel/resume/execute 각각을 제한한다. callback은 동기 literal bool을
반환해야 한다. 미설정은 기존 host의 같은 Project 권한이며 새로운 모델 실행 권한이 아니다.
실행 직전과 Resume에서 현재 권한·대상 상태·원본 증거를 다시 확인한다.

송신 Agent/Run/Message는 `source_run_id`가 명시된 때 저장된 Run에서 검증한다. 미설정은
host 출처이며 세 필드는 명시 Null이다. source의 approval, Tool 권한, 정책, request context,
비공개 conversation을 target에 복사하지 않는다. target의 현재 접수 binding과 원래 Run 정책,
승인·Tool 효과·Checkpoint·usage 계약이 실행을 결정한다.

## 단일 영속 형식과 필수 증거

TransferReceipt schema 1은 `id`, `source`의 Project/Session/Run/Agent/Message,
`target_session_id`, `recipients`, `key`, `content_digest`, `deadline`, `chain`,
`target_message_id`, `accepted_at`, `revision`을 모두 요구한다. version은 exact int이며
타입·필드·identity·revision·순환·양쪽 원장을 검사한다. `id`는 Project/source Session/key의
정규 digest로 correlation과 idempotency를 함께 소유한다. result reference는 대상 admission의
완전한 Run map과 각 Assistant로 조회하며 별도 결과 원장을 만들지 않는다.

각 새 Session 생성 때 `state/transfers.json`을 반드시 만든다. schema 1의
Project/Session/outgoing/incoming/revision은 빈 원장에서도 필수다. Reader는 누락 원장,
누락 항목, 다른 소유자의 값, 불일치한 revision을 빈 상태로 보충하지 않는다. source의 outgoing과
target의 incoming에는 같은 불변 Receipt를 저장한다. target User의 transfer와 Run의 transfer는
그 Receipt와 일치해야 한다. continuation은 실제 Resume/restart 원본을 따라 검증하며 숨은
hop/depth 한도나 추정 parent를 만들지 않는다.

source 원장 + target 원장 + target User/admission/provenance는 기존 workspace 트랜잭션
하나로 커밋한다. 큐 등록은 커밋 후다. ACK 중 caller 취소는 저장을 drain한다. 같은 key/같은
payload는 원래 User를 반환하며 다시 실행하지 않는다. 다른 payload는 충돌이다. 이미 접수된
수신자는 이후 참여자 변경으로 재선택하지 않는다. explicit deadline은 UTC 절대시각이며 접수,
실행 timeout, Resume에서 유지한다. 만료된 원래 결과 조회는 가능하나 새 실행은 거부한다.

## 취소·재개·수명

fan-out 전 취소는 기존 User CANCELLED다. fan-out 후 PENDING Run 취소는 소유 전이
`pending_cancel: PENDING → CANCELLED`다. execution_started=False, Step 없음, active 집합
미포함을 검증한다. Dispatcher 등록과 같은 scheduling lock 안에서 처리하며 Engine을 시작하지
않는다. 이미 RUNNING이면 기존 Run별 interrupt/finalizer를 사용한다. 종료한 시도를 다시 열지
않고 sibling의 task·Session active ID·예산·원장은 변경하지 않는다. Run 알림 owner는 CANCELLED를
명시한다. 완료하지 않은 효과나 영수증을 취소로 지우지 않는다.

Runtime 복구·수동 Recovery·조회·삭제·보관·backup/restore는 같은 Receipt/필수 원장과
admission을 검증한다. ACK 전 중단은 원래 상태로 롤백하고, ACK 후 미dispatch 요청만 기존
Dispatcher가 처리한다. dispatch/효과 후 중단은 기존 INTERRUPTED 기록으로 남고 자동 replay하지 않는다.

전송이 참조하는 source/target Session의 이력은 보수적으로 전체 보호한다. 개별 Session 삭제,
turn 삭제, Run/Session retention으로 링크를 끊을 수 없다. 전체 Project 삭제는 기존 inactive/
효과/자료 검증 후 같은 소유 범위 전체를 종료한다. Session/Project clone은 관찰만 복제하고
transfer를 `cloned_transfer`의 id/revision으로 축소하며 새 원장은 빈 상태다. 실행 authority는
승계하지 않는다. file conversation만 durable transport를 제공한다. backup은 양쪽 ID/원장을
함께 검증하며 checksum이 유효해도 필수 소유 증거가 없으면 복원하지 않는다.

이전 `refactor/llm-backend`의 선택한 전송/시험에서 idempotent 접수, 출처 체인, ACK 중단 시나리오만
참고했다. glob-only 발견, 느슨한 버전 Reader, optional admission, 숨은 16/64 한도,
옛 Run/Agent API는 채택하지 않았다. 과거 시험 통과는 현재 증거로 승계하지 않는다.

## Repository 주입 경계

SessionRepository의 `read_transfers(session)`/`write_transfers(session, value)`가 원장 접근을
소유한다. 기본 구현은 위 필수 파일을 사용하고 주입 구현은 같은 필수 Schema/identity/revision을
자기 저장소에 유지한다. 새 Session 생성에서만 `TransferLedger.empty(session)`를 사용할 수 있다.
Reader는 원본이 없으면 실패한다. Writer는 전달받은 원래 revision을 CAS로 확인하고 두 소유자의
변경을 기존 workspace 저장 트랜잭션과 원자적으로 결합해야 한다. 이 계약을 제공하지 않는 저장소는
durable 전송을 제공할 수 없다. Service는 주입된 Repository를 파일 경로로 우회하지 않는다.
Portable archive는 archive 소유 경로의 현재 파일 형식을 직접 검증한다. Component의 독립
`history_references`는 외부 저장소를 계속 지원하며 실제 retention 경로에서만 Session 전송 참조를 합친다.
