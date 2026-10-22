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
