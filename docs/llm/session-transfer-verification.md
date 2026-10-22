# E Session 전송 검증

기준 HEAD는 D의 `4953efad4a31ca6acfc4cffd233d7518389f1ec6`다. E는 로컬 Level 3를 통과했다.
Push와 정확한 SHA의 원격 CI는 실제 결과를 확인한 뒤 별도 기록한다.
[계약](session-transfers.md), [검증 정책](verification-policy.md)을 적용한다.
소유 구현체는 TransferReceipt/TransferLedger/TransferRecords/SessionExchange와 기존
RunManager/SessionRepository/HistoryRetention/Recovery다. 제품 정책·Tool 승인·예산·영수증을
완화하지 않는다. 영향 범위는 두 Session의 admission/Run/필수 저장 증거와 lifecycle 및 Hub 소비자다.

## 집중·관련 통합 검증

원본 argv·결과·발견/실행 ID·실패 trace·환경/소스 지문은
[기계 판독 기록](session-transfer-results.json)에 실행별로 보존한다. 명령은
`.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py <suite> <name> <modules...>`이며
실제로는 `python -m tests.llm.run_regression --suite <suite> --modules <modules...> --output ...`를
사용한다. 서로 다른 실행을 하나의 전체 통과로 합산하지 않는다.

| 실행 | 발견/실행/Pass | Failure/Error/Skip | exit |
|---|---|---|---|
| e-transport-1 | 미실행 | 모듈 이름 오류, 발견 단계에서 거부 | 2 |
| e-transport-2 | 27/27/25 | 0/2/0 | 1 |
| e-transport-3 | 49/49/46 | 1/2/0 | 1 |
| e-transport-4 | 50/50/49 | 1/0/0 | 1 |
| e-safety-5 LLM | 185/185/185 | 0/0/0 | 0 |
| e-hub-1 Hub | 39/39/39 | 0/0/0 | 0 |
| e-final-focus-6 | 25/25/25 | 0/0/0 | 0 |

실행한 시험은 expected failure/unexpected success/누락/중복/예상 밖 실행 모두 0이다.
첫 명령의 모듈 이름 오류는 성공/통과 시험 수에 포함하지 않는다. 이후 실패는 미시작 취소의
Run 알림 enum 누락과 새 시험의 현재 ToolContract 필드·손상 주입/API·진단 예상 문제였다.
기존 Assertion을 삭제하지 않았다. D history revision이 먼저 손상을 거부한 기록도 유지한다.

`e-safety-5`는 전송 23개와 기존 Run 전이·D 병렬/강제 중단·backup/retention·승인 저장·
사용량 삭제·operation·checkpoint·RAG·lifecycle·runtime reliability·서비스 확장·최적화 조회
17개 모듈 전체 185개를 실행했다. Hub는 직접 영향을 받는 D/요청 이력/삭제/승인 continuation/
실행/설정 재시작/context 7개 모듈 39개다. 공통 source
`85e04ad50cab33b7f0059d2b1d9ae2ce7712b02a3d8ba3a58ddf776a2c403999`, input
`4b1126c1d210ad8050709cfab273c1dfd5c1ca40832bf0769ab03e723df2a6c3`는 각각 실행 전후 같다.
Python 3.12.14, 고정 의존성, 실제 ish `d833790959a76ac457fdeea3f21a6bea1eecfe12`,
기존 환경 변수·bwrap/X11 조건을 유지했다. 이후 target 변경/손상 archive 시험 두 개는 별도 추가 검증한다.

실제 fsync 파일 효과를 관찰했다. 다섯 subprocess 경계(target 입력/source 원장/target 원장/
ACK 후/효과 후)는 실제 `os._exit(93)`를 확인했고 재접속·같은 key 요청 뒤 효과는 한 번뿐이다.
승인 전 효과 0, 승인 후 Resume 효과 1, 원래 Receipt 보존, 만료 뒤 직접 Resume 우회 거부,
선택 취소의 sibling 효과 1, 원장 쓰기 실패 롤백, 원장/출처 손실의 실행 차단을 검증했다.

집중 실행은 소단계 검증이며 전체 회귀를 대체하지 않는다. 새 공통 원장과 Run 취소 전이는
소유 범위가 넓어 E 완료 시 LLM/Hub 전체·R3 mutation·S1–S6·문서·실환경 Level 3를 필수로 실행한다.
실제 인증 서비스, 전원 손실·커널 short-write·네트워크 파일시스템은 이 검증의 보장 범위가 아니다.

`e-final-focus-6`은 대상 참여자 변경·checksum이 유효한 손상 backup 복원 거부를 포함한
전송/실제 crash 시험 25개를 통과했다. source `d4a6f76d367266920413a2a993a0f0e815c615bce61797b9b6eb4b23eb2ce938`,
input `f3ad1864b21817b89988b37d7ed9350d0fe60806035810db0e884420e6e5952c`는 전후 같다.
S1–S6 후보 156개를 재검토했고 후보 추가/제거/면제 없이 통과했다. 문서 검사도 통과했다.

`e-cancel-final-7`은 반복 취소의 종료 상태 응답까지 25/25/25, Failure/Error/Skip 0, exit 0이다.
source `5260c8605a1de8f1bbba7e61bb53e545dec3d159ec741ead92fac40d2ab46e32`, input
`d91a2430f1aeaa28f020185d473e683042b8105404456e53c79bbfb5d9e3c24f`가 전후 같다.
이후 변경은 검증 기록과 해당 소유 구현체의 정적 검토 pin뿐이다. 전체 게이트는 이 통합 소스를 실행한다.

## 첫 전체 게이트와 확장점 수정

`e-integration-full-1`: LLM 2234/2234/2232, Failure 0/Error 2/Skip 0, exit 1,
1084.275266초. Hub 584/584/584, 모든 비정상 집계 0, exit 0, 1009.609167초.
expected failure/unexpected success/누락/중복/예상 밖 실행 모두 0이다. source
`5260c8605a1de8f1bbba7e61bb53e545dec3d159ec741ead92fac40d2ab46e32`, input
`4dfda48933c5cd5bd4e27cf37dcb82a163fb4d4fd4b00fcb4b8f030047688b57`는 전후 같았다.
동일 소스의 `e-integration-aux-1`에서 R3·S1–S6·문서·host LLM/Hub·offline Provider는 모두 exit 0이었다.

오류는 `test_external_component_hooks_do_not_require_local_storage`와
`test_session_manager_lifecycle_uses_injected_repository_without_json`다. Component 참조 조회에
SessionManager 의존성을 추가했고, 전송 원장 Reader가 주입 Repository를 우회한 것이 원인이었다.
Component 조회를 원래 계약으로 복구하고 실제 retention에서 전송 참조를 결합했다. Session Repository에
현재 필수 원장 read/write와 revision CAS 계약을 추가했다. 주입 fixture는 생성 때만 필수 원장을
자기 메모리 저장소에 만들며 원장이 없어지면 실패한다. 기존 파일 비사용 Assertion을 유지·강화했다.
필수 원장을 optional로 바꾸거나 Reader에서 빈 상태를 생성하지 않았다.

수정 뒤 `e-repository-fixes-8`의 전송/강제 종료/Repository/외부 Component/retention/backup/
서비스 확장/turn 삭제/트랜잭션 9개 모듈 116/116/116, 모든 비정상 집계 0, exit 0,
49.541749초를 확인했다. source `ca097946b871257e7e15c50d7a5b73042ef1f7daaf50f81b7ff7b38a1c6df5e5`,
input `8f921a57f86aa458b22901a155263ff9bce69b8b7ec0f2ef6e5c5488de15a461`는 전후 같다.
현재 통합 소스의 전체 게이트를 다시 수행하며 첫 실패를 성공으로 덮지 않는다.

`e-ledger-cas-9`은 stale revision/필수 원장 소실 Writer 거부와 두 기존 확장점까지
46/46/46, Failure/Error/Skip 및 나머지 비정상 집계 0, exit 0, 18.202005초다. source
`2cf0cecbeca62a7a9828afbbc90d32d4906630b9ca02e1fb3446766949d39ff7`, input
`a1c5c6aab7a29039d8014b3489eded8040432fce0d6da7807757da347b9d7fa9`는 전후 같다.
S1–S6 156개 후보와 문서도 통과했다. 이 수정 소스로 전체 Level 3를 다시 실행한다.

## 수정 후 전체 게이트

`e-integration-full-2`: LLM 2235/2235/2235, 1092.151169초; Hub 584/584/584, 989.085382초.
각 실행 exit 0, Failure/Error/Skip/expected failure/unexpected success/누락/중복/예상 밖 실행 모두 0이다.
source `2cf0cecbeca62a7a9828afbbc90d32d4906630b9ca02e1fb3446766949d39ff7`, input `88c33ce014db3d6969f2956abc2e4f4d7c870dc6a486d3fd79055f404a2c8937`가 전후 같았다.
동일 입력의 `e-integration-aux-2`에서 R3 Mutation, S1–S6, 문서, 실제 host LLM/Hub,
offline Provider 검증도 모두 exit 0이다. E의 로컬 상위 게이트를 완료했으며 F를 이어간다.
검증 후 제품·시험·의존성은 변경하지 않고 결과 문서만 갱신했다. 원격 CI는 이 로컬 결과와 구분한다.
