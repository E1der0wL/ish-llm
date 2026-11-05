# 실행 경계의 취소 계약

취소 접수, 로컬 실행 종료 확인, 원격 효과 확인은 서로 다르다. 취소는
rollback이나 no-effect 증거가 아니며, 승인·재개·operation receipt를 대체하지 않는다.
새 Run 상태, 기본 timeout, 자동 재시도, 자동 migration은 추가하지 않는다.

## 소유 경계

| Owner | 취소 책임 | 유지할 차이 |
| --- | --- | --- |
| RunManager / Engine | 선택한 실행 중단, 후속 실행 차단, Run/Step 기록 | 저장 ACK와 invocation 방향 전환을 구분 |
| ToolExecutor | 취소 전파, 승인·효과·재시도 경계 유지 | cancellation을 recoverable/no-effect 오류로 변환하지 않음 |
| Provider / MCP 연결 | 요청·스트림·자식 task 회수 | SDK context는 생성한 task에서 닫고 원격 종료를 추정하지 않음 |
| 프로세스 Runner / Tool worker | 생성 중 취소 포함 프로세스 그룹·파이프 회수 | 이미 발생한 외부 효과는 되돌릴 수 없음 |
| 파일 Tool | 준비 중 블록별 협력 취소, 임시 파일 정리 | 게시 시작 후 확정과 실제 결과 보존 |
| StorageIO | 진행 중 transaction 완료/실패 확인 | 취소와 겹친 저장 실패를 숨기지 않음 |
| Graph / RAG | 소유한 병렬 task 회수, 미완성 결과 공개 방지 | 완료 checkpoint와 원자적 generation publish 유지 |
| 동기 SDK | 진행 중 native call 회수 | 스레드 강제 종료를 약속하지 않음 |

`services.infrastructure.cancellation.cancel_tasks()`는 소유한 자식 task에
취소를 한 번 전달하고 회수를 마칠 때까지 기다리는 공통 절차다. 이미 취소 중인
task에 다시 cancel을 보내 SDK cleanup을 끊지 않는다. 회수 중 호출자가 다시 취소돼도
정리를 마친 뒤 취소를 전달한다. 이 helper는 자식 실행의 성공 결과를 반환하지 않으며
exception collection을 실행 성공으로 해석해서는 안 된다.

Graph/Agent 이벤트 중계, 출력 producer, Run 대기 task, RAG worker와 MCP 호출,
프로세스 pipe reader는 이 회수 절차를 공유한다. 출력 owner는 producer 회수 후
이미 접수한 델타를 저장한다. 프로세스 owner는 동시 stop/close가 같은 종료 작업을
기다리게 하고, 모든 종료 시도가 끝난 뒤 capture를 닫는다. pipe 실패가 있어도
프로세스 종료를 기다리며, 수집한 writer/종료 오류를 성공으로 바꾸지 않는다.

별도로 유지하는 처리는 이유가 있다. Graph의 명시적 `cleanup_timeout`과 PendingWork
인계, LiteLLM stream의 같은 task에서 수행하는 SDK close, 파일 publish의 결과 우선순위,
StorageIO transaction 확정은 단순 자식 task 취소로 대체하지 않는다. Run 종료 기록을
저장하는 orchestration task에도 직접 cancel을 보내지 않고 실행 중단 후 저장을 기다린다.

SDK의 close/cancel, 프로세스 종료, 저장 확정 시점, 결과·오류 우선순위는 각 owner가
계속 소유한다. 모든 함수를 wrapper로 감싸거나 새 cancellation registry를 만들지 않는다.
안전한 종료가 불가능한 native/원격 작업은 한계를 드러내고 불확실 효과를 자동 반복하지 않는다.

## 검증 기준

시작 전·생성 중·실행 중·완료와 동시·정리 중 반복 취소를 경계별로 검사한다.
취소 후 자원·admission 반환, 완료 결과 보존, 승인 전 실행 금지, 명시적 재개 시
효과 중복 방지를 함께 검증한다. 파일/저장 경계는 결과 보존 때문에 별도 검사를 유지한다.
