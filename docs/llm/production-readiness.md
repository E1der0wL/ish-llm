# 운영 준비 상태와 검증 한계

2026-10-26, main `b4bd478f7f8ccc7326a25173e9bbae6398b69bd4`와 필수 Tool scope 보강 기준입니다.
[현행 아키텍처](architecture.md), [최신 검증 기록](handoff.md), [문서 검토](documentation-review.md)를 함께 봅니다.
이전 날짜별 결과는 [보관 목록](documentation-history.md)에 남아 있습니다.

## 구현과 검증 범위

- durable 요청/출력·Run/Step 수명, 독립 Agent·수신자별 실행, Graph/Loop checkpoint와 명시적 재개.
- Project 정책·Tool 승인·부모 scope 축소·인자 제약·효과 원장·불확실 효과 재실행 차단.
- 원자적 저장·참조/CAS·삭제 보호, Component별 generation/revision, Provider Adapter·usage·진단.
- Run 문맥의 필수 ToolExecutionScope 누락 시 즉시 실패. 기존 scope를 새 객체로 대체하지 않음.

이번 코드 스냅샷에서 전체 LLM **2,510 passed / 0 failed / 0 skipped**, Hub 공개 API 연결
**29 passed / 0 failed / 0 skipped**를 확인했습니다. 이번 Hub 검사는 전체 Hub suite가 아닙니다.
제품 SHA `3652bbba8878f0658a66fff3213b1976da075687`의 이전 전체 Hub 588개 및 원격 CI는
[당시 전수 점검](contract-closure.md)의 별도 증거입니다. 새 변경의 전체 Hub/CI 통과로 승계하지 않습니다.

이 결과는 검증한 로컬 Backend 계약의 회귀 근거입니다. 실서버 모델 품질, 무제한 자료 규모,
장시간 운영, 모든 디스크 장애의 성공을 보장하지 않습니다.

## 운영 경계

| 영역 | 보장 범위와 남은 검증 |
|---|---|
| 저장 | 로컬 Linux atomic rename/hard link/fsync·협력 잠금 아래의 transaction. 외부 직접 작성자, NFS/SMB, 전원 차단·손상 디스크 전체를 보장하지 않음. |
| 효과 | 외부 Tool 효과와 receipt는 원자화할 수 없음. 실제 idempotency 지원·외부 조회·uncertain 보호 필요. |
| 취소 | 동기 SDK 스레드를 강제로 종료하지 못함. PendingWork/슬롯/ownership은 실제 정리까지 유지. |
| 재개 | 완료된 모델/Tool·노드 경계의 기록 복원. 공급자 스트림 바이트나 임의 LangGraph 프레임 복원 아님. |
| 사용량 | 명시 counter/출력 상한으로 예약, unknown/부분 usage 보존. 공급자 내부 retry·실제 청구의 정확한 비용 보장 아님. |
| RAG | 청크별 embedding, 명시 concurrency, cache/Job fingerprint·generation 검증. 대형 corpus JSON·DB 복사·잠금 비용은 남음. |
| 부하 | 5 Session/3시간/2GB 같은 과거 목표는 실환경 측정 필요. 짧은 합성 시험으로 운영 보장하지 않음. |
| sandbox | 실제 bwrap·network deny·mount·process 종료 검증 필수. 실패 시 비격리 실행으로 강등하거나 Skip하지 않음. |
| 확장 | trusted host callback/custom Engine이 직접 호출하는 SDK·외부 저장을 자동 감시/원자화하지 않음. |
| 보관 | 자동 무음 삭제 없음. 명시 계획/CAS와 checkpoint·receipt·usage·자료 참조 보호. 로그 회전은 Application sink의 명시 설정. |


알고리즘 필수 설정은 실행 전에 검증하며 누락된 값에 숨은 제한·재시도·모델 값을 채우지 않습니다.
정확한 재현 환경과 명령은 [인계 문서](handoff.md), 통과 판정은 [검증 정책](verification-policy.md)을 따릅니다.
