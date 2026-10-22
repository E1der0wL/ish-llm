# D 요청 1:N·병렬 실행 검증

기준은 B/C 통합 커밋 `975913e71b413618125b7b770802baaba0d88e52`다.
현재 계약은 [clean-refactor](clean-refactor.md), [검증 정책](verification-policy.md),
[저장 트랜잭션](storage-transactions.md)을 따른다. D1/D2의 로컬 상위 Level 3를 완료했다. 작업 브랜치 Push와 정확한 SHA의 원격 CI 확인은 아직 남아 있다.

## D1/D2 로컬 상위 게이트 완료

`d-integration-full-3`은 현재 통합 소스 전체를 실행했다.

| suite | 발견/실행/Pass | Failure/Error/Skip | exit | 초 |
|---|---|---|---|---|
| LLM | 2209/2209/2209 | 0/0/0 | 0 | 1057.952618 |
| Hub | 584/584/584 | 0/0/0 | 0 | 988.522377 |

expected failure/unexpected success/누락/중복/예상 밖 실행도 모두 0이다. 동일 source/input의
`d-integration-aux-4`에서 R3 Mutation, S1–S6, 문서, 실제 host LLM/Hub 환경, offline Provider
여섯 명령도 모두 exit 0이다. 실제 명령은 `.venv-linux312/bin/python -m tests.llm.run_regression
--suite llm|hub --output /workspace/llm-clean-resume/b/d-integration-full-3/<suite>.json`의
suite별 독립 실행이며, 정확한 argv·원본 결과·전체 ID는 JSON에 보존했다.

source `65dbd3e4083b4843a66577b35b9192dbdd527060da7a60d35bc5da82d3286e7d`, input
`cd35901d2a143a58050437f5dbdcb70141a36a2ccbfe5e7df46732d7a84ce45d`는 실행 전후 같다.
기준 HEAD `8d84fa0`에 D 통합 변경을 더한 소스이며 Python 3.12.14/고정 의존성/환경 5개/
실제 ish `d833790959a76ac457fdeea3f21a6bea1eecfe12`/bwrap/X11 조건을 유지했다.
전체 실행 뒤에는 완료 기록·상태 문서·그 문서를 참조하는 정적 검토 pin만 갱신한다.
제품 코드·시험·Fixture의 전체 실행 결과를 다른 소스로 승계하지 않는다.

완료 범위는 명시 Session 참여자/수신자, 원자적 1:N admission/dispatch와 공유 이력,
Agent별 FIFO·다른 Agent 병렬, Run별 취소·정리 소유권, shared quota/R3, 원본/전체 그룹
삭제 보호, ACK 전후 필수 이력 소실 거부, 실제 process crash·파일 효과, Hub/CLI/예제의
단일 목록 API 전환이다. 과거 실패와 수정 기록은 아래에 그대로 남긴다.
E Session 전송과 F 최종 후보 검토는 아직 구현하지 않았다. 실제 인증 모델, 커널 short-write,
전원 손실·네트워크 파일시스템은 이 로컬 검증의 보장 범위가 아니다.

## D1-a: 접수 기록의 순수 계약

소유 구현체는 `llm/services/runtime/requests.py`의 RequestAdmission이다. 명시한
Session 참여자 중 수신자를 선택하며 `all`만 현재 참여자 snapshot으로 확장한다.
누락/Null/빈 수신자·중복·참여하지 않은 Agent는 거부한다. 원본 입력을 수정하지 않는다.

접수 기록은 Project/Session/User identity, 정렬된 recipients, 각 AgentBinding,
필수 dispatch 필드를 갖는다. 정상 미배정은 명시적 Null이다. 배정 시에는 모든 수신자의
서로 다른 Run ID, 공통 이력 snapshot과 revision을 함께 확정한다. 이력에서는 이 owner의
admission envelope만 복사본에서 제거하여 과거 이력 snapshot의 재귀적 중첩을 막는다.
사용자 원본·다른 metadata·명시적 요청 문맥의 Missing/Null/Value는 덮어쓰지 않는다.

배정 기록은 재배정할 수 없다. User/Run 양쪽 증거를 확인하여 부분 Run 소실·중복·잘못된
Session/Agent/Engine·공유 Assistant·변경된 요청 문맥을 거부한다. 기록 소실을 정상 미배정으로
추정하지 않는다. 단일 수신자도 같은 목록 계약이며 User.run_id에 대표 Run을 넣지 않는다.

과거 `refactor/llm-backend`에서는 수신자 정규화·Run 집합 교차 검사·dispatcher 흐름과
실제 crash 지점을 선택적으로 참고했다. 구형 admission 누락 Reader, exclusive Engine 경로,
암묵적 Agent, count=1 결과 분기는 채택하지 않는다. 과거 완료/검증 결과를 승계하지 않는다.

### 집중 검증과 발견한 결함

정확한 명령·exit·집계·원본 결과 경로·실행 전후 source/input 지문은
[multi-agent-results.json](multi-agent-results.json)에 실행별로 기록했다.
실제 명령은 `.venv-linux312/bin/python -m tests.llm.run_regression --suite llm
--output <llm.json> --modules <8개 모듈>`이며 전체 argv는 JSON에 있다.

`d-admission-1`은 발견/실행 76, Pass 71, Failure 0, Error 5, exit 1이다.
이력 직렬화가 metadata를 얕게 복사하여 원본 admission을 제거하는 결함을 원본 보존
Assertion이 검출했다. 이력 복사 경계를 수정하고 기존 Assertion을 그대로 재실행했다.
`d-admission-2`는 발견/실행/Pass 76/76/76, Failure/Error/Skip/expected failure/
unexpected success/누락/중복/예상 밖 실행 모두 0, exit 0(21.328775초)이다.

범위는 새 접수 계약 8개와 Agent 저장/실행, Checkpoint, 효과 원장, 수명주기, 사용량 삭제,
승인 저장의 기존 안전성 시험이다. 실제 파일 효과·중단/재개 Assertion을 유지했다.
후자의 실행 전후 source `8fdd665dd041036c0ae5b9943335627e3f2e58a363dc4095bb3e535a0af52d0d`,
input `27dbf029fccea2286c152e06bf41b6475f47dc47573700417e2dc44fa402cda4`가 동일하다.
Python 3.12.14·고정 의존성·host `d833790959a76ac457fdeea3f21a6bea1eecfe12`와
실제 격리 환경을 유지했고, 실패 실행은 삭제하지 않았다.

이 소단계는 아직 런타임에 연결하지 않은 순수 기록 계약이다. 기존 제품 실행·승인·예산·
Tool 효과·Resume 경로에 호출이 추가되지 않아 전체 회귀를 유예한다. D1의 실제 접수/ACK/
원자적 fan-out·파일/메모리·삭제 참조, 공개 결과/Hub 소비자 이관과 D2 실제 병렬/FIFO/
공유 quota/개별 취소는 후속 작업이다. 통합 D 게이트에서 전체 LLM/Hub·R3·정적·문서·
실환경 검증을 수행한 뒤 Push하고 정확한 SHA의 CI를 확인한다.


D1-a Level 2의 `d-admission-aux-1`도 R3 Mutation·S1–S6·문서·LLM/Hub 실환경·
오프라인 Provider SDK 검사 각각 실제 exit 0이다. 새 정적 후보나 검토 근거 변경은 없었다.
실행 전후 source는 위 집중 성공과 같고 input은
`8f666e830c0eb58a3e7301725421081073ee00816a9ff228989cf65dc54aeb36`으로 동일하다.
이 독립 소단계는 로컬 검증 완료이며 상위 D 완료·전체 회귀·원격 CI 성공을 뜻하지 않는다.

## D1-b: Session 참여자·보존 참조

Session이 필수 `agent_ids`를 소유하고 기존 Agent ID 검증을 재사용한다. 생성 시 생략은
빈 목록이며 명시적 Null·중복·정렬되지 않은 목록은 거부한다. 공개 생성/편집은 실제 Project
Agent 존재를 같은 잠금 아래 확인한다. 런타임이 붙은 Session의 참여자 변경은 거부하고,
제목만 수정할 때는 목록을 덮어쓰지 않는다. Session soft delete는 Agent 삭제 보호를 해제하지
않는다. clone·백업·독립 archive validator도 필수 목록과 Project Agent 참조를 검증한다.

소유 구현체는 Session 모델, SessionManager/Repository, AgentManager의 삭제 참조와
ProjectManager의 백업 검증이다. 공개 Sessions/SessionHandle이 해당 계약을 소비한다.
기존 저장 형식 Reader에 기본값 주입이나 구형 field 변환을 추가하지 않았다. 현재 단일 실행
경로를 다중 실행으로 보고하지 않으며 요청 접수·dispatcher 연결은 다음 작업이다.

`d-membership-1`은 새 시험의 import 경로 오류로 discovery exit 2, 실행 미완료다.
`d-membership-2`는 76개 실행 중 72 Pass, 4개 실패 ID(Failure 1/Error 7), exit 1이다.
기존 AgentRepository가 내는 FileNotFoundError와 새 Session 필드를 시험의 기대값에 반영했다.
원본 보존·실제 파일 효과 Assertion은 그대로 유지했다. `d-membership-3`은 76/76/76,
exit 0(15.463016초), 관련 `d-membership-safety-1`은 82/82/82, exit 0(31.966508초),
`d-membership-hub-1`은 24/24/24, exit 0(78.112897초)다. 각 성공 실행의 Failure/Error/
Skip/expected failure/unexpected success/누락/중복/예상 밖 실행은 모두 0이다.

세 성공 실행은 source `ce5c41393a3897b0f52510126a6a096da014eecf29e8d71564215e7a931d6681`,
input `bec737d495d02d836259453d72e27e9ca145543077f578b1af43026706fd5a2a`가 각각 전후 동일하다.
범위는 변경 owner의 저장·필수 기록·삭제·복제·백업·실제 파일 교체 후 rollback, Project 권한,
승인·누적 예산·효과 원장·Checkpoint·RAG 안전성과 Hub 생성/기본값/설정/Agent 선택이다.
쓰기 실패 주입에서 실제 새 목록 bytes에 도달한 뒤 원본 bytes가 복원됨도 확인했다.

이후 새 Session 시험에 Project clone과 독립 archive validator의 손상 참조 거절 Assertion만
추가했고 `d-membership-archive-1`의 해당 모듈 8/8/8, 모든 엄격 오류 집계 0, exit 0
(2.476654초)을 확인했다. 제품·다른 시험·fixture·의존성·환경이 이전 세 성공 실행과 같음을
소스/input manifest로 비교했다. 재사용 근거와 각 발견/실행 ID는 JSON에 보존하며, 서로 다른
실행을 하나의 전체 회귀로 합산하지 않는다.

최종 source `b41ab02710e1e716a0db9663bf2bb4f9a086a21ac273aa59fba432b9c2cdde13`,
input `e165402e71618f2d7e3956f4eaa650c86679506fa772dcf03fd64e9e0e1f1efe`는 추가 시험과
`d-membership-aux-1` 각각 전후 동일하다. 후자의 R3 Mutation·S1–S6·문서·LLM/Hub 실환경·
오프라인 SDK 검사도 각각 exit 0이다. 기존 156개 정적 소유자 판정은 유지하고 검토한 변경
근거 파일만 갱신했으며, 새 후보·미해결·오래된 근거·검토 오류는 0이다.

D1-b는 Level 2 로컬 완료다. 이 단계의 영향은 명시적 참여자 저장/참조와 필수 필드 검증이며,
기존 실행·권한·사용량·효과·재개 처리는 변경하지 않았다. 전체 회귀는 D 통합 게이트로 유예한다.
기능 연결·실제 ACK/crash·단일/다중 결과 소비자·병렬/FIFO·공유 quota 검증은 여전히 남아 있다.

## D1/D2 통합 진행: 접수·병렬 실행·기록 보호

이 절은 진행 중인 Level 1/2 집중 증거이며 D 전체 완료·원격 CI 통과가 아니다.
`975913e`의 B/C 원격 CI는 별도 증거다. 기존 공개 API/Hub 소비자 이관, 정적 감사,
전체 LLM/Hub Level 3와 정확한 Push SHA의 CI가 남아 있다.

소유 경계는 RequestAdmission(완전한 대상/binding/Run map/history), RunManager(원자적
fan-out·단일 Dispatcher·Run별 task/취소), Session(참여자·active 집합),
HistoryRetention(참조/누적 사용량 보호와 명시 삭제)다. 접수한 User 하나에 N개의
PENDING Run/Assistant를 하나의 저장 트랜잭션으로 연결한다. 단일 요청도 결과는 목록이다.
같은 Agent는 FIFO, 다른 Agent는 실제 barrier에서 동시에 진입한다. Run별 Tool scope와
취소를 분리하고 전체 active 집합으로 Session 상태를 계산한다. 다른 Agent를 기다리는
PENDING Run의 저장된 quota도 삭제 보호 판단에 포함한다.

공유 User의 접수 map을 부분 삭제로 변조하지 않는다. Run 보관 정리는 같은 요청의 모든
Run을 명시 선택해야 하며, 하나라도 보호되면 형제도 보호한다. Application 보관 정책도
묶음으로 선택하고 공유 User 용량은 한 번만 계산한다. 대화 논리 삭제는 모든 응답을 묶되
원래 접수/Run/영수증은 보존한다. clone은 실행 권한을 복사하지 않고 출처 관찰만 남긴다.
파일 대화의 User/Assistant/Run/history 소실은 복구 전에 거부한다. 메모리 대화의 정상
수명 종료는 보수적으로 중단하며 원본을 재구성하거나 실행을 재생하지 않는다.

| 실행 | 범위·결과 | 확인/수정 |
|---|---|---|
| d-fanout-1 → 2 | 14개 중 5개 오류 → 14 Pass | 기존 실행 시작 `_dispatch`와 새 fan-out 이름 충돌을 분리, 새 fixture 조회 메서드 수정 |
| d-root-nested-1 | 52 Pass | 기존 root/nested 승인·실제 Tool 효과·재개 Assertion 유지 |
| d-crash-1 / diagnostic-1 → 2 | 18개 중 1개 실패 ID / 진단 4개 중 1개 → 56 Pass | 원본 로그 보존과 정상 `session.loaded` 추가만 구분, 실제 데이터·효과 Assertion 유지 |
| d-memory-1 | 16 Pass | 메모리 대화의 실제 부분 쓰기 롤백·수명 종료 후 비재생 |
| d-parallel-1 | 61 Pass | 실제 동시 barrier·같은 Agent FIFO·개별 취소·공유 호출 예산 |
| d-history-1 | 64 Started, 62 Pass, 2 Error | quota 조회의 `Run` import 누락 수정 |
| d-history-2 | 64 Started, 62 Pass, 2 Error | 디렉터리 복원 후 열거 순서가 CAS를 바꾸는 문제를 Run owner 정렬로 수정; pending 삭제 방어를 우회하지 않고 실제 손실 바이트 fixture로 변경 |
| d-history-3 | 64 Started/Pass, 0 Failure/Error/Skip | 실제 Run 삭제 후 실패 시 원본/CAS 복원, 부분 묶음 삭제 거부, User/Assistant 손실 비재생 |

각 실행의 명령·Exit Code·전체 test ID·실패 ID·원본 로그 경로·소스/입력 Hash는
[multi-agent-results.json](multi-agent-results.json)에 추가 보존했다. 모든 실행에서 발견/실행
누락·중복·예상 실패·예상 외 성공은 0이며 소스와 실행 입력은 실행 전후 동일했다.
마지막 실행은 Python 3.12.14, 37.11959509798908초, Exit 0이다.
소스 SHA256은 `3f2286100f0f43a33166998f7462de92d2981af7735c88f13827a2708081b065`,
입력 SHA256은 `df7e23fd1a62d55447de7d90130d0234a367c7fe0360db24cf1e3dc9bf28ef9e`다.
서로 다른 실행을 하나의 전체 회귀로 합산하지 않았다. 이번 통합은 공개 계약·저장 참조·
스케줄링에 영향을 주므로 최종 D 완료/Push 전 Level 3가 필수다.

### 기존 소비자·지시·예약 및 대기 quota 검증

직접 소비자는 `recipients`와 목록 결과를 사용한다. 단일 Agent fixture는 목록에서 자신이
선택한 Run을 꺼내며 원래 효과/승인/한도 Assertion을 유지한다. Session 생성 fixture는
그 시점에 실제 생성한 Agent를 명시 참여자로 저장한다. Backend의 자동 참여나 구 API
alias는 추가하지 않았다. 같은 Agent의 큐 시험은 그 Agent의 대기점을 사용한다.
다른 Agent를 막아 전체 Session을 직렬화하던 fixture 가정은 병렬 계약에 맞게 이관했다.

`d-consumers-1`은 98개 중 94 Pass, 1 Failure/3 Error였다. 목록 반환 소비자·참여자 정렬과
복구의 원래 `invalid_input_reference` 진단을 수정한 `d-consumers-2`는 124/124 Pass,
54.6214103680104초, Exit 0이다. 승인·누적 사용량 삭제 보호·효과 원장·실제 보관 삭제
롤백·Checkpoint 손실·재개 포기 검사를 포함했다. 누락/중복/Skip/xfail/xpass는 0이다.

`d-runtime-consumers-1`(63개 중 58 Pass, 3 Failure/2 Error)에서 상태 조회가 전체 Run
이력을 읽는 회귀를 발견했다. `RunRepository.unfinished`는 기존 카탈로그로 미완료 기록만
읽는다. "history scan 금지" Assertion을 유지했다. 실행 중 지시는 해당 Run의 취소 상태를
확인하며, 실제 실행 수 집계도 모든 Run task를 센다. 큐 한도에는 이미 dispatch되었지만
같은 Agent를 기다리는 요청을 포함한다. 실행 직전 취소 시험은 명시 barrier로 Engine
호출 0회를 검증한다.

`d-runtime-consumers-2`는 새 메서드의 `list` 타입 주석 이름 충돌로 발견 단계에서 Exit 2로
중단했으며 완료로 세지 않았다. 이후 3/4/5 실행은 각각 77개 중 75 Pass(1 Failure/1 Error),
156개 중 128 Pass(28 Error), 156개 중 153 Pass(3 Error)였다. 원인은 남은 단일 결과 및
참여자/접수 fixture, fixture import와 저장 스레드 밖에서 만들어야 하는 런타임 참조였다.
원래 지시 순서, 손상 원본 보존, 모델 호출 횟수, 승인/Tool 효과 검사를 제거하지 않았다.

`d-runtime-consumers-6`은 158개 중 157 Pass, 1 Error였다. 새 대기 quota fixture가 동기
Adapter에 비동기 callable을 주어 대기점에 도달하지 못한 오류다. 제품 코드는 변경하지
않고 fixture를 실제 동기 전송 callable과 thread Event로 수정했다. `d-pending-quota-1`에서
변경된 병렬 모듈 5개가 모두 통과했다(3.172164600007818초, Exit 0). 이전 실행과 달라진
Python 소스는 `tests/llm/test_multi_agent_parallel.py` 하나다. 이전 7개 모듈의 성공은
별도 소스 버전의 집중 증거로 보존하며 하나의 전체 실행으로 합산하지 않는다.

새 시험은 활성 Run에 한도가 없고 대기 recipient만 과거 한도를 보유한 경우에도, Project
정책 편집으로 원본 사용량 영수증을 삭제하지 못함을 확인한다. 병렬 Agent의 실제 Tool
효과도 각각 1회이며 두 번째 효과는 두 Agent 모두 거부한다. 두 Provider 호출 횟수와
실제 fsync 파일 내용, 보존한 원본 바이트를 확인했다. 전체 게이트는 여전히 미실행이다.

### D Hub 소비자와 저장 fixture 이관 중간 기록

Hub는 공개 Agent를 새 Session의 참여자로 명시 저장하고, 제출 시 선택한 Agent를 전달한다.
이력 행은 수신자별 `runs` 목록을 반환한다. 편집은 원래 수신자 전체를 유지하고 재개·추가
지시·재시도 UI는 실제 Run ID를 선택한다. Snapshot은 각 Agent의 Assistant와 모든 미결
승인을 함께 보여 준다. 실제 파일 효과를 사용한 별도 중단, 전체 수신자 편집, 두 승인 중
하나만 재개하는 세 시험을 추가했다.

`d-hub-1`은 36개 중 14 Pass(23 Error), `d-hub-2`는 39개 중 36 Pass(3 Error),
`d-hub-3`은 39개 중 38 Pass(1 Error)였다. 새 Snapshot의 비동기 generator 사용 오류를
명시 반복으로 수정했고 단일 Run 반환·기존 Session 참여자·승인 입력 fixture를 이관했다.
마지막 UI 오류는 선택한 Agent가 참여한 새 Session과 제출 확인 시점을 명시하여 수정했다.
같은 Agent 후속 요청 선택과 다른 Agent 병렬 요청을 모두 검사한다.
`d-hub-ui-4`는 7개 중 6 Pass(1 Failure), `d-hub-ui-5`는 **7/7 Pass**, Exit 0이다.
별도 실행의 성공을 하나의 전체 실행으로 합산하지 않는다.

저장·복구·실행 소비자 11개 모듈의 `d-storage-consumers-1`은 182개 중 151 Pass
(2 Failure/29 Error), `d-storage-consumers-2`는 170 Pass(12 Error)다. 종료한 raw
RunManager를 재사용한 새 fixture 오류, 이전 접수 증거·단일 실행 순서·참여자 설정을
수정 중이다. 원래 파일 보존, 오류 코드, 로그, 삭제/복구 보호 Assertion을 유지한다.
현재 D 전체 완료와 Level 3 성공은 선언하지 않는다. 모든 실행의 명령·Exit·실패 ID·
소스/환경 Hash 및 전후 동일성은 `multi-agent-results.json`에 기록했다.

저장·복구 묶음을 Component까지 확장한 `d-storage-consumers-3`은 **307개 중 284 Pass**,
23 Error, Exit 1이다. 기존 11개 모듈의 182개는 모두 통과했고, 신규 범위에서 생성 전
참여자 선택·단일 결과와 다른 Agent를 이용한 Session 전체 직렬 대기 fixture가 남아 있었다.
수정한 7개 모듈의 `d-component-consumers-1`은 **111/111 Pass**, 49.42837723600678초,
Exit 0이다. Graph 재개를 접수 경계에서 직접 보류하여 원래 Checkpoint 손상·정의 변경·
중복 재개·실제 효과 Assertion을 유지했다. 서로 다른 Agent를 막아 큐를 만드는 방법은 쓰지 않는다.
Hub 추가 소비자 `d-hub-consumers-1`은 **34/34 Pass**, 25.38014212300186초, Exit 0이다.
모든 실행의 Skip/xfail/xpass/누락/중복은 0이며 소스·환경 전후 Hash가 일치한다.

현재 API 문서와 예제는 `Session.agent_ids`, 필수 `recipients`, 목록 결과와 전체 active
Run 조회를 사용한다. 실제 ENOSPC 예제의 실행 전 원자성 장애는 dispatch의 첫 PENDING
Run 저장에 주입한다. 원래 no-orphan·효과 0회·입력 보존 Assertion은 유지한다.
Run 활성화 이후 중단·재개 금지는 별도의 다중 Agent 실제 프로세스 crash 시험으로 검증한다.

D 정적 재검토: 기존 156개 AST 후보의 ID·Owner·규칙은 동일하며 새 예외를 추가하지 않았다.
변경된 근거 파일은 Owner·Reader/Writer·소비자 diff를 재검토하고 정확한 SHA로 갱신했다.
Session 활성 집합은 생성 상태이며 Reader가 필수 필드와 상태 조합을 거부한다. quota는 모든
PENDING/RUNNING 원본 정책을 보호한다. 보관은 완전한 수신자 그룹을 요구하며 복구는 빠진
접수·Run·Assistant를 보충하지 않는다. Hub 선택은 Application의 명시 저장이다. Provider,
승인, 예산 기본값이나 효과 재시도 규칙은 추가하지 않았다. S1/S6 예외 허용도 없다.
정적 재검토는 테스트 통과 증거를 대신하지 않으며 전체 게이트는 별도 수행한다.

최종 집중 소비자 보강: `d-history-probe-consumers-1`은 41개 중 27 Pass(14 Error),
`d-history-probe-consumers-2`는 66개 중 65 Pass(1 Error)였다. 실제 복구 CLI는 통과했으며
남은 오류는 테스트 helper의 목록 소비였다. Graph 모듈을 다시 검증한 `d-graph-consumers-1`은
16/16 Pass다. `d-timing-1`은 5/5 Pass, 공개 필드 검사 `d-naming-1`의 1 Failure는 기존
baseline에 Session 참여자를 두 번 더한 시험 오류였으며 뒤의 66개 실행에서 통과했다.

Hub의 `d-shell-consumers-1`은 11개 중 9 Pass(2 Error), `d-host-consumers-1`은 58개 중
57 Pass(1 Error)였다. 직접 만든 Session에 참여자를 명시하고 단일 결과 helper를 이관했다.
마지막 새 Graph Agent의 명시 참여자 저장 후 `d-resource-consumers-1`은 6/6 Pass다.
승인 전 효과 금지, 저장한 shell 환경으로 재개, 원래 실제 파일 효과 Assertion을 유지했다.

`d-examples-1`은 39개 중 33 Pass(5 Failure/2 Error, 실패 ID 6개)였다. CLI·문서·설정 예제는
통과했으며 Graph RAG의 phase wrapper가 목록을 단일 Run으로 소비하던 오류를 수정했다.
`d-graph-rag-example-2`는 12/12 Pass다. 의미적 요청/전송 재시도 구분·검색 횟수·실패 보고
Assertion은 유지했다. 이들 실행도 Skip/xfail/xpass/누락/중복 0, 소스·환경 전후 동일이다.

`d-integration-aux-1`의 R3 Mutation·S1–S6·문서·LLM/Hub 실제 환경·SDK 환경은 모두 Exit 0이다.
원래 한도 전달과 실제 효과가 통과하고 root/nested 한도 누락 mutation이 실패하는 것을 확인했다.
후속 CLI·예제·문서 및 시험 변경은 새 소스에서 전체 LLM/Hub와 보조 게이트로 함께 확인한다.
현재 집중 실행들의 성공을 전체 통과로 합산하지 않는다.


## D 통합 전체 회귀 1차 실패와 수정 진행

`d-integration-full-1`은 LLM 2206/2206/2096(발견/실행/Pass), 실패 ID 110,
Failure 4/Error 112, exit 1(822.084957초)이다. Hub는 584/584/573, 실패 ID 11,
Failure 0/Error 11, exit 1(981.888421초)이다. 양쪽 Skip/expected failure/
unexpected success/누락/중복/예상 밖 실행은 모두 0이다. source
`838c72f3ec057c95346f24785e680d102960dcfbc0fe94bf42d94c31c06fb5b8`, input
`99d1fc72d351759fc939ca1d0eaf3825884c98bfc36ebdd93312151fcc31d085`는 실행 전후 같다.
동일 source/input의 `d-integration-aux-2`는 여섯 명령 모두 exit 0이다.
실패와 실제 명령·모든 ID·원본 로그는 JSON에 보존했다. D는 완료/커밋/Push하지 않았다.

실패 대부분은 늦게 생성한 Agent의 명시 Session 등록과 목록 반환 소비자 fixture다.
제품 회귀도 발견했다: 종료에서 같은 child에 두 번 cancel하여 정리 대기를 끊음,
Engine 관찰 diagnostic이 실행 code를 덮음, Resume 계획의 Run 목록 중복 조회,
공유 이력 배정 시 실행 정책 오류가 Dispatcher까지 종료함. 기존 Assertion을 유지하여
각각 1회 취소, 기존 Engine 진단 경계, 같은 조회의 snapshot 재사용으로 수정했다.
공유 이력은 dispatch에서 순서/가시성을 고정하고 Run 실행에서 저장된 문맥 예산을 적용한다.
정책 실패도 완전한 fan-out 증거를 유지하며 해당 Run만 실패해야 한다. 실제 파일 효과로
다른 Agent의 계속 실행과 다음 요청 가능성을 검증하는 사례를 추가했다. 수정 후 집중·
관련 안전성 및 상위 전체 게이트가 남아 있다.

`d-full1-followup-llm-1`은 영향 모듈 37개의 491/491/488, 실패 ID 3, Failure 1/Error 2,
exit 1(387.155706초)이다. 종료 소유권·진단 코드·Resume 단일 조회·다른 Agent의
문맥 예산 실패 격리 및 D 실제 crash/효과 시험은 통과했다. 남은 2개 fixture는 Agent 이름과
참여자 등록 오류다. 제품의 Memory 요약 provenance는 첫 경계 User의 옛 run_id에 의존해
첫 원본 Run 참조를 잃었다. 기존 원본 참조 Assertion을 유지하고, 범위 내 실제 Run 연결의
첫/마지막 경계를 보존하도록 수정했다. 원본·전체 ID 목록·저장 설정은 변경하지 않는다.
`d-full1-followup-hub-1`은 8개 모듈 55/55/53, Failure 0/Error 2, exit 1(107.018726초)이며
남은 2개 fixture는 참여자 편집 전에 기존 runtime을 종료하도록 수정했다. 두 실행의
Skip/expected failure/unexpected success/누락/중복/예상 밖 실행은 0이고 실행 전후
source `a4a925acf3195894c99f0b95930dc4c04475df0f818575a372ce2350f47fed34`,
input `92aaeef95a056cca21d1b24c6177022fe7ee292a939c8b3807207a137c4cbbd6`가 같다.
이 결과를 전체 통과로 합산하지 않으며 수정 소비자 검증 후 D 전체 게이트를 다시 실행한다.

수정 후 `d-full1-memory-fixes-2`는 5개 모듈 91/91/91, exit 0(183.684338초),
`d-full1-hub-fixes-2`는 2개 모듈 5/5/5, exit 0(17.098847초)이다. 양쪽 모든
Failure/Error/Skip/expected failure/unexpected success/누락/중복/예상 밖 실행은 0이다.
source `08f426d45876bf1b48c77458256a2771f52ada701dd551472e4a33609ccf69f6`,
input `9410f5998b10bebf7a2339e68c5f10dc095b92e971a72d051bb8171befab6959`는 실행 전후 같다.
제품 Memory provenance 변경의 소비자·Processor·중단/미완료 요약 공개 금지와 기존 원본
참조 Assertion을 재검증했다. 다른 488개 성공과 합쳐 전체 실행으로 표현하지 않는다.
다음 D 통합 전체 게이트는 이 수정과 새 예산 실패 격리 시험까지 포함한다.


### D 2차 전체 게이트와 연결된 런타임의 손실 검증

`d-integration-full-2`는 LLM 2207/2207/2206, Failure 1/Error 0, exit 1(1043.943126초),
Hub 584/584/584, 모든 실패 0, exit 0(982.891991초)이다. 양쪽 Skip/expected failure/
unexpected success/누락/중복/예상 밖 실행은 0이다. source
`787617313e78de56c9b4c40c08c10674dce040170c15cb15ee8e3d57de9f530f`, input
`1fbe68d6fe875911d3b3e235fb398c7a9130d99c964b7f167d1a1bd631e79457`는 실행 전후 같다.
동일 소스의 `d-integration-aux-3` 여섯 명령도 exit 0이다. 유일한 전체 시험 실패는 주입
문맥 생성기의 기존 for_run Mock 호출 횟수다. 공유 snapshot/Run별 예산 선택이라는 현재
경계의 두 호출과 실제 프로젝트 정책 전달을 각각 검증하고 복제·원본 내용 Assertion은 유지했다.

추가 owner 검토에서 연결된 runtime의 다음 접수/dispatch가 재접속 때의 필수 이력 검증을
생략함을 확인했다. 외부 `probe-active-history-loss.py`는 완료 Assistant를 소실시킨 뒤
실제 파일 효과가 1회에서 2회로 늘고 다음 입력에서 원본 응답이 빠짐을 재현했다. 이 probe의
exit 0은 관찰 완료이며 안전성 통과가 아니다. `d-active-history-loss-1`은 실제 회귀 시험
13/13/11, 실패 ID 2, Failure 4/Error 0, exit 1(5.338715초)로 ACK 전 User/Assistant/
admission 소실과 ACK 뒤 dispatch 전 Assistant 소실을 검출했다. 원본 bytes와 실제 효과
보존 Assertion을 유지하며 같은 RequestAdmission owner 검증을 접수·배정 경계에 재사용한다.
이는 제품 안전성 수정이므로 이전 전체 통과 범위를 최종 증거로 승계하지 않는다. 집중·안전성
후 전체 LLM/Hub 게이트를 다시 실행하며 D는 계속 미완료다.

수정 뒤 `d-active-history-loss-2`는 14개 LLM 모듈 184/184/184, `d-history-owner-hub-1`은
3개 Hub 모듈 14/14/14로 각각 exit 0이다. 양쪽 Failure/Error/Skip/expected failure/
unexpected success/누락/중복/예상 밖 실행은 모두 0이며 source/input 실행 전후 동일성을
JSON에 보존했다. 새 손실 사례의 원본 bytes·효과 보존과 기존 승인·예산·원장·Checkpoint·
재개·삭제·메모리 대화 수명·주입 저장소/문맥 owner를 검증했다. 공통 접수 경계가 바뀌어
최종 전체 게이트를 다시 실행한다.
