# D 요청 1:N·병렬 실행 검증

기준은 B/C 통합 커밋 `975913e71b413618125b7b770802baaba0d88e52`다.
현재 계약은 [clean-refactor](clean-refactor.md), [검증 정책](verification-policy.md),
[저장 트랜잭션](storage-transactions.md)을 따른다. D1/D2는 아직 완료하지 않았다.

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
