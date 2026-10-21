# 운영 준비 상태와 검증 한계

기준: main `94229cb865374d99f20ec48c5e48269488041a4e`, 문서 검토일 2026-10-20.
[현행 아키텍처](architecture.md)와 [검토 기록](documentation-review.md)을 함께 읽는다.
이전 날짜별 시험·성능 기록은 [보존본](production-readiness-history.md)에 있다.
문서 최신화가 제품 구현이나 배포 준비 완료를 뜻하지 않는다.

## 현재 구현한 제어

- workspace ownership, 원자적 도메인 트랜잭션, append-only 대화/출력, 명시 복구·보관.
- durable 요청, Run/Step 수명, 최상위·중첩 Loop/Graph checkpoint, 명시적 Resume와 restart admission.
- Tool 승인·권한·한도·retry·operation receipt·불확실 효과 재실행 차단.
- 실제 Provider Adapter의 admission·사용량·retry·stream/진단 및 선택적 Linux Tool 격리.
- Component별 generation/revision·참조·CAS, RAG Job/청크/세대 공개, Memory 처리와 자료 provenance.
- Hub의 정규 오류/복구 API 연결, 승인 UI, 작업 환경 binding, immutable 화면 projection.

이 목록은 모든 입력과 장애가 검증됐다는 뜻이 아니다. 특히 [알려진 구현 차이](documentation-review.md)의
truthiness·버전 타입·호환 Reader·이중 경로는 아직 제품에 남아 있다. A–F 클린 설계는 구현 전이다.

## 기준선과 완료 판정

같은 main SHA의 Python 3.12.14·실제 ish 검증 기준선은 LLM 1,635개 중 1,634 pass/1 fail,
Hub 534 pass다. 실패는 host의 `_is_library_available` 대신 `_library_available`을 참조하는 테스트다.
로컬 Xvfb/xclip 9개는 실행·통과했다. GitHub bwrap 단계 실패와 뒤 테스트 Skip은 별개이며 미해결이다.
이 결과는 이번 문서 변경 후 전체 suite를 다시 실행했다는 의미가 아니다.

기능/Schema·문서 예제·정적 게이트·실제 효과·회귀·필수 CI를 각각 기록한다. 날짜별 pass 수를 합치거나
Skip/환경 차단을 pass로 바꾸지 않는다. 실제 인증 모델 호출과 kernel short-write는 계속 미검증이다.

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

추가 제한의 부재와 알고리즘 필수값 누락은 다르다. 필요한 기능 설정은 실행 전에 거부하고,
Host 공유 자원과 Project 제품 정책을 분리한다. 구체적인 완료 gate는 [클린 계획](clean-refactor.md)에 있다.
