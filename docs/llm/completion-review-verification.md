# F 최종 후보 검토 검증

E의 로컬 Level 3 통과 뒤 `refactor/llm-clean`에서 이어간다. F의 로컬 Level 3 통합 게이트는 통과했다.
Push와 정확한 SHA의 원격 CI는 실제 결과 확인 뒤 별도로 보고한다.
[현재 계약](completion-review.md), [검증 정책](verification-policy.md)을 적용한다.

## 범위와 근거

소유 구현체는 CompletionReview/ReviewPolicy, CompletionPipeline, LoopReview/LoopEngine이다.
소비자는 기존 Processor/Memory/Goal, root·Graph Agent, Run/Checkpoint/usage/Tool 승인·출력과 Hub다.
후보 공개·재개·저장 증거에 걸친 변경이므로 최종 완료에는 LLM/Hub 전체 회귀를 실행한다.
공통 트랜잭션·승인·예산·효과·Checkpoint·Resume 정책은 완화하지 않는다.

기존 backend `bb0946257ab6bcabb9a3ef04d3d854e5333b07d5`에서 필요한 검토 알고리즘과 실패
시나리오만 읽었다. 분리 관찰·verdict 우선순위·checkpoint 예약·서명 보존 continuation·실제 강제 종료
시나리오를 현재 Owner/API로 재구현했다. 구형 숨은 기본 예산·default accept·자동 활성화·Agent 설정/
호환 Reader 및 전체 파일 복사는 채택하지 않았다. 과거 Run 사용량 합산·원래 timeout 유지 정책도
현재 [Resume 범위](long-running.md)와 달라 도입하지 않았다. 과거 결과는 이번 통과 증거가 아니다.

## E 검증 중 분리 작업공간의 개발 시험

E의 소스·환경 guard를 유지하기 위해 같은 현재 소스와 동일 Python/dependency/ish 심볼릭 링크를
사용한 별도 디렉터리에서 F를 작성했다. 아래 결과는 **독립 개발 시험이며 작업 브랜치 전체 검증이 아니다**.
명령은 `.venv-linux312/bin/python /workspace/llm-clean-resume/f/run-trial.py <name> <modules...>`다.
원본 argv, 실행된 ID 전체, source 전후 동일성, exit, 실패 trace/log hash는
[기계 판독 기록](completion-review-results.json)에 보존한다. 환경의 전체 input hash는 이후 작업
브랜치 검증에서 별도로 수집한다. 서로 다른 소스의 통과를 하나의 전체 실행으로 합산하지 않는다.

| 실행 | 발견/실행/Pass | Failure/Error/Skip | exit |
|---|---|---|---|
| trial-focus-1 | 60/60/60 | 0/0/0 | 0 |
| trial-focus-2 | 39/39/39 | 0/0/0 | 0 |
| trial-crash-3 | 2/2/0 | 0/6/0 | 1 |
| trial-integration-4 | 21/21/19 | 0/2/0 | 1 |
| trial-integration-5 | 68/68/68 | 0/0/0 | 0 |
| trial-write-6 | 26/26/26 | 0/0/0 | 0 |
| trial-owner-7 | 24/24/24 | 0/0/0 | 0 |

각 실행 expected failure/unexpected success/누락/중복/예상 밖 실행은 0이다. crash-3의 6 Error는
두 test method의 여섯 subtest다. 자식 `-m`이 시험 Adapter의 module을 `__main__`으로 만들어 재개
binding이 바뀐 것이 원인이었다. canonical module import로 시험 진입점만 수정해 원래 동일성 거부
계약을 유지했다. integration-4의 두 오류는 새 fixture의 WorkflowGraph.connect API 및 정렬된
Session.agent_ids 사용 오류였다. 제품 계약과 Assertion을 축소하지 않고 fixture를 수정했다.

실제 fsync 파일 효과, continue 뒤 승인 0→1/Resume 영수증 재사용, 모델·토큰·Tool 예산, 미정산
Project 예약, Reviewer revision/attempt 한도, 검토 중 취소·Run timeout, private 후보/최종 공개,
Graph 범위·병렬 Agent 격리, Memory/Goal Tool pair, live instruction과 credential 배제를 검사했다.
verdict 파일 쓰기 직후 OSError에서는 롤백 후 명시 Resume가 후보·기존 Tool 효과를 재사용한다.
여섯 실제 `os._exit(94)` 경계는 response/attempt/computed/continue/accept/publication이며,
복구만으로 모델·Reviewer를 실행하지 않고 명시 Resume 뒤 실제 효과는 한 번이다.

## 작업 브랜치 검증

분리 시험에서 변경한 F 파일만 현재 E 소스 동일성을 검사해 반영했다. 기존 필수 안전성 모듈과
직접 소비자를 집중·통합 검증한 뒤 현재 통합 소스의 Level 3 및 R3·S1–S6·문서·실환경을 실행한다.
실제 명령/exit/발견·실행·Pass/비정상 집계/소스·환경 hash와 후속 게이트는 실행 후 이곳에 추가한다.

Reviewer의 임의 Python I/O를 차단하는 sandbox나 외부 유료 모델 서비스의 실시간 검증은 포함하지
않는다. 실제 로컬 파일·프로세스·현재 host 검증과 순수 Reviewer 계약의 범위를 구분한다.

집중·관련 안전성 검증 결과:

- `f-safety-1`: 발견/실행/Pass 255/255/255, 117.301118초, exit 0. Failure/Error/Skip 및 나머지 비정상 집계 모두 0.
- `f-hub-1`: 발견/실행/Pass 39/39/39, 75.150685초, exit 0. Failure/Error/Skip 및 나머지 비정상 집계 모두 0.

source `aa514fe56d0f915c1e487ddc58a6528445912c7d29eee6af9a43c8d19585d919`, input `6339896cb247f1cd699aaa59d813db6c364c44ebd179302d1d8b826571614c04`가 두 실행에서 같고 각각 전후 동일하다.
LLM 17개 모듈은 새 Review/강제 종료와 Processor·독립 Provider·정책·설정·Loop/Graph 지시·승인 저장·
사용량 삭제·operation/checkpoint/RAG/lifecycle·runtime·E 전송·D 병렬을 포함한다.
Hub 7개 모듈은 병렬/요청 이력/turn 삭제/승인 continuation/실행/설정 재시작/context 소비자를 포함한다.
Python 3.12.14와 고정 의존성, ish d833790959a76ac457fdeea3f21a6bea1eecfe12 및 실제 bwrap/X11 환경이다.

초기 정적 검사는 변경된 소스 pin과 새 유효 범위 선언을 검토하도록 거부했다. 세 새 occurrence는
ReviewPolicy의 Reader/schema 공통 최소 유효값 선언이며 숨은 runtime default가 아니다.
검토 metadata의 owner를 실제 AST 클래스 이름으로 맞춘다. 검사기·면제·안전성 기준은 변경하지 않는다.
전체 게이트는 이 통합 소스의 LLM/Hub 및 R3·S1–S6·문서·실환경을 실행한다.

## 첫 전체 게이트의 닫힌 필드 목록 실패

`f-integration-full-1`: LLM 2261/2261/2260, Failure 1/Error 0/Skip 0, exit 1,
1116.556998초. Hub 584/584/584, 모든 비정상 집계 0, exit 0, 999.504439초.
expected failure/unexpected success/누락/중복/예상 밖 실행은 두 suite 모두 0이다.
source `aa514fe56d0f915c1e487ddc58a6528445912c7d29eee6af9a43c8d19585d919`, input
`a676225f13899f8bd1280da2e9c4c2f4414ec6979a622fd04420f937c007464b`가 전후 같다.
같은 입력의 auxiliary 여섯 검사도 exit 0이다. 첫 실행을 전체 성공으로 기록하지 않는다.

실패는 NamingContractTests.test_current_implementation_fields_are_closed_and_owned의
LoopEngine 정책 기대 목록에 새 final_review가 빠진 것이다. 새 계약의 필드 한 개만 명시 목록에
추가하며 닫힌 schema/소유자/원본 Missing 보존/unknown field 거부 Assertion을 유지한다.
제품 코드·Hub 시험·Fixture·의존성은 변경하지 않는다. 관련 집중 검증 후 LLM 전체를 다시 실행한다.
Hub는 변경된 LLM 명명 시험을 소비하지 않으며 관련 소스·전체 발견 목록·환경 동일성을 검증해
첫 전체 실행 결과를 제한적으로 재사용한다. 서로 다른 실행의 결과를 한 번의 전체 실행으로 합치지 않는다.

`f-naming-2`는 명명 계약·최종 검토·정책 소유·명시 설정 4개 모듈 68/68/68,
모든 비정상 집계 0, exit 0, 22.491980초다. source
`74399d6ad1b65476c71804d1e07d34eb17f3d9fae869ff9671adda013b5cc12f`, input
`40d0939a8d8c3bc6c4caf6d9e9e3b4428a60e9dd561e8a3c762d79d1bbdae20d`가 전후 같다.

## 수정 후 Level 3 통합 게이트

| 실제 실행 | 발견/실행/Pass | Failure/Error/Skip | exit | 초 |
|---|---|---|---|---|
| f-integration-llm-2 LLM 전체 | 2261/2261/2261 | 0/0/0 | 0 | 1109.917521 |
| f-integration-full-1 Hub 전체 (동일성 확인 후 재사용) | 584/584/584 | 0/0/0 | 0 | 999.504439 |

각 실행의 expected failure/unexpected success/누락/중복/예상 밖 실행도 0이다.
LLM 재실행 source `74399d6ad1b65476c71804d1e07d34eb17f3d9fae869ff9671adda013b5cc12f`, input `b1797b3baffcf1f20f24c51641af3ab6ea3484f0e8ecefea084b48b254d27fea`가 전후 같다.
Hub는 위 첫 전체 실행의 원래 source/input과 결과를 유지하며 새 실행으로 표현하지 않는다.
재사용 증거는 결과 JSON의 hub_reuse에 있다. 모든 제품 Python/Hub 시험과 Fixture/공통 의존성/
Python 실행 파일/환경 변수/커널/host revision 및 상태/bwrap/X11 바이너리가 같다.
유일한 Python 변경은 독립 LLM 명명 시험의 명시 정책 필드 한 개이며 다른 모듈이 이를 import하지
않는다. Hub 전체 발견 ID 584개도 원래 실행과 정확히 같다. 나머지 변경은 문서·정적 검토 hash다.
서로 다른 실행을 한 번의 전체 회귀로 합산하지 않았다. 첫 LLM 실패도 원본대로 보존한다.

현재 입력의 f-integration-aux-2에서 R3 Mutation, S1–S6(159개 후보), 문서, 실제 ish host
LLM/Hub 환경, offline Provider 검증 모두 exit 0이다. 이 근거로 F의 상위 게이트를 완료했다.
검증 후 변경은 결과 문서와 문서 hash 검토 기록뿐이며 제품·시험 소스는 같음을 다시 확인한다.
H1–H5 → P1 → A–F 로컬 게이트 완료와 원격 CI 결과는 구분한다.
