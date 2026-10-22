# 검증 계층과 전체 회귀 실행 정책

이 문서는 H2 잔여 작업부터 H3–H5, P1 잔여 작업, A–F에 적용하는 검증 실행의 단일 기준이다.
이후 [계약 소유권·책임 경계 정리 O0–O7](ownership-refactor.md)에도 같은 계층과 안전성 기준을 적용한다.
기능 완료 조건과 승인·권한·예산·외부 효과·데이터 무결성 계약은 그대로 유지한다.
과거 검증 기록의 명령·결과·실패·실행 순서는 변경하지 않는다. 당시의 매 소단계 전체 회귀·Push
표현은 이후 작업의 실행 빈도를 규정하지 않는다. [구현 순서](clean-refactor.md)와
[수명주기 계약](data-lifecycle.md)의 완료 조건을 이 정책과 함께 적용한다.

## 실행 계층

| 계층 | 시점 | 필수 범위와 완료의 의미 |
|---|---|---|
| Level 1 | 개별 변경·결함 수정 직후 | 변경 owner의 단위·Writer/Reader·상태 전이·무결성, 결함 재현, 직접 소비자. 원칙적으로 전체 회귀를 실행하지 않는다. 실패 원인을 수정하고 해당 범위를 재검증한다. |
| Level 2 | H2-a 같은 독립 소단계 완료 | 해당 소단계의 모든 계약, 직접 의존하는 Repository·Component·Engine·Application 소비자, 영향 범위 통합, 아래 핵심 안전성, 정적 감사·필요한 문서 검사, 실제 원본 보존·장애 주입. 범위가 입증되고 모두 통과하면 로컬 소단계 완료·독립 커밋이 가능하다. 상위 단계나 원격 CI 완료를 뜻하지 않는다. |
| Level 3 | 상위 단계·통합 게이트 | H1–H5 각각, P1 전체, A–F 각각의 최종 완료, 관련 소단계를 묶는 게이트, 최종 병합·배포, 영향 범위가 불명확한 변경. 현재 통합 소스의 전체 LLM/Hub, 정적 감사, R3 mutation, 문서 검사, 필요한 실제 환경 검증을 수행한다. |

Owner 정리의 O1–O6도 상위 단계 완료와 관련 소단계 묶음에서 Level 3를 적용하며 O7은 최종
통합 게이트다. 문서에 계약·작업 목록을 추가한 사실은 해당 제품 단계의 구현·검증 완료가 아니다.

영향 범위는 실제 호출 관계, 공통 계약, 저장 형식, 공개 API 소비자로 판단한다. 파일 수나 변경
줄 수만으로 정하지 않는다. 공유 Reader/Writer·Schema·트랜잭션·원자적 확정 경계, 승인·권한·
예산 예약·Tool 실행, 영수증·재시도·Resume, 삭제·격리·복원, 공통 API·Hub 계약 변경은
전체 회귀 승격을 우선 검토한다. 제한된 범위가 입증되면 관련 통합 검증을 사용할 수 있지만,
안전성을 충분히 증명하지 못하면 한 파일 변경도 Level 3으로 승격한다. 판단 근거를 남긴다.

## 매 소단계의 핵심 안전성

| 영역 | 유지할 보장 |
|---|---|
| 승인·권한 | 손상·만료·취소 승인으로 실행 불가, 승인 전 worker/handler 및 외부 효과 0 |
| 예산 | Resume·삭제·재시작으로 호출 한도나 누적 예약·사용량 초기화 불가 |
| Tool 효과 | 영수증 소실·불확실한 결과로 중복 실행 불가 |
| Checkpoint | 필수 기록 소실 시 잘못된 재개 불가 |
| RAG | 포인터·identity 손상으로 원본 삭제 불가 |
| 데이터 | 필수 기록 누락을 빈 데이터·기본값으로 복원 불가 |
| 삭제 | 참조·원장·누적 사용량 보호 우회 불가 |
| 트랜잭션 | 실패·중단 후 원본 또는 완전한 새 상태 유지 |

아래 명령은 현재 존재하는 모듈을 재사용하는 공통 안전성 시작 집합이다. 이 집합의 성공만으로
모든 변경의 안전성을 입증하지 않는다. 변경 owner와 소비자의 시험을 추가하고, 새로 발견한
결함 재현을 반드시 포함한다. R3 정상 시험과 한도 누락 mutation도 유지한다.
실제 파일 효과 횟수와 실패 재현 assertion, 실제 자식 프로세스 중단·재열기 검증을 예외 mock으로
대체하거나 제거하지 않는다. 테스트 선택은 제품의 실행 정책·저장 형식을 바꾸는 근거가 아니다.

```sh
python -m tests.llm.run_regression --suite all --modules \
  tests.llm.test_interaction_storage tests.llm.test_usage_deletion \
  tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity \
  tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity \
  tests.llm.test_nested_graph tests.llm.test_transactions \
  --output artifacts/safety.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_architecture --require S1 --output artifacts/architecture.json
python -m tests.llm.check_docs --output artifacts/docs.json
```

`--modules`는 지정한 suite의 전체 discovery 목록에서 정확한 모듈을 선택한다. 클래스·메서드
일부 선택이나 glob은 지원하지 않는다. 존재하지 않거나 빈 모듈, 중복 지정, discovery 오류·중복은
실행 전에 실패한다. JSON의 `scope`, `modules`, `available`, `discovered`, `started`로 발견 범위와
선택·실행 범위를 구별한다. 선택하지 않은 시험은 성공이나 Skip으로 집계하지 않는다.
선택된 필수 시험의 Skip·expected failure·unexpected success·누락·중복·실패는 성공이 아니다.
`--modules`를 생략한 기존 전체 실행과 CI 배정은 유지한다.

## 전체 게이트와 원격 CI

```sh
python -m tests.llm.run_regression --suite llm --output artifacts/llm.json
python -m tests.llm.run_regression --suite hub --output artifacts/hub.json
python -m tests.llm.check_architecture --require S1 --output artifacts/architecture.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_docs --output artifacts/docs.json
```

단계별 추가 정적 종료 규칙과 실제 환경 검증도 충족해야 한다. 기존 정적 부채의 new/stale 0은
모든 부채 해결이나 `--strict` 통과를 뜻하지 않는다. 환경 검사는
`tests.llm.check_environment --suite llm|hub --host-revision <검증할 SHA> --output <JSON>`을
각 suite에 실행한다. Python·고정 의존성·실제 ish host·X11·sandbox 조건을 보존한다.
sudo로 시험을 실행하거나 격리를 낮추어 통과시키지 않는다.

소단계는 Level 2 성공 후 문서를 먼저 최신화하고 독립 커밋으로 보존할 수 있다. 관련 소단계가
모인 Level 3 게이트에서 전체 검증 후 `refactor/llm-clean`에 묶어 Push하는 방식을 우선한다.
매 소단계 Push가 필요한 경우에도 CI 전체 검증은 필수다. 동일 소스의 로컬 전체 실행을 중복하지
않도록 아래 증거 재사용 조건을 적용한다. 미검증 변경을 완료로 기록하거나 묶음에 숨기지 않는다.
main·refactor/llm-backend·Draft PR #1과 기존 브랜치 보호·작업 제한은 유지한다.

[GitHub Actions](../../.github/workflows/tests.yml)는 Push의 정확한 SHA에서 LLM/Hub 전체와
필수 검사를 실행한다. 부분 시험으로 대체하거나 Job·assertion을 비활성화하지 않는다.
원격 run의 head SHA·Job·실제 결과를 조회한 경우에만 CI 통과를 선언한다. 로컬 완료, Push 성공,
CI 진행 중, CI 성공을 구별하며 원격 실패가 있으면 해당 게이트는 완료가 아니다.

## 검증 증거 재사용

재사용할 각 시험에 대해 다음을 모두 확인하고 근거를 기록한다.

- 영향을 주는 제품 코드와 실제 소비 경로가 동일하다.
- 테스트 코드·Fixture·제품 자산과 관련 의존성·실행 환경이 동일하다.
- 해당 소스의 hash 또는 동일성이 확인되며 실행 전후 변경이 없다.
- discovery 목록과 필수 배정 범위가 유지된다.

새 계약·새 시험·변경된 범위는 추가로 실행한다. Python 파일 hash만으로 다른 Fixture·자산·
환경의 동일성을 추정하지 않는다. 변경 영향의 동일성을 입증하지 못하면 해당 범위를 재실행한다.
서로 다른 실행의 성공을 하나의 전체 실행으로 표현하지 않는다. 각 명령의 원래 exit와 실패를
보존하고, 분할 검증이면 어떤 시험이 어느 소스에서 실행됐는지 명시한다. 변경 후 현재 통합 상태의
필수 범위가 모두 검증돼야 상위 게이트를 통과할 수 있다.

## 실패와 진행 차단

기존 확정 계약으로 해결 가능하면 원인을 분석하고 구현체·테스트를 수정한다. 원래 안전성 assertion을
유지한 채 집중·통합·필수 안전성을 재검증하고 계속한다. 승인·권한 우회, 예산 초기화, 중복 외부 효과,
원본 손실, 영수증·Checkpoint 소실에 따른 잘못된 재개, 손상 기록을 정상 상태로 복원하는 결함이
남으면 후속 단계로 가지 않는다. 계약 충돌·새 설계 결정이 필요하면 근거와 선택지를 보고한다.
핵심 실패를 집계에서 제외하거나 검증 범위를 줄여 완료를 선언하지 않는다.

## 소단계와 상위 게이트 보고 양식

각 결과는 [수명주기 검증 기록](lifecycle-verification.md) 또는 해당 단계 기록에 남긴다.

| 항목 | 기록할 증거 |
|---|---|
| 변경·영향 | 코드·소유 구현체, Writer/Reader 계약, 실제 호출 관계와 Repository·Component·Engine·Application·Hub 소비자 |
| Owner 정리 | 작업 ID, 정규 선언·의미 해석·검증·변경 책임, Schema/Reader/Writer/Projection/실제 소비 연결, 조사 범위와 미분류·확정 위반·적법한 선언의 근거 |
| 검증 결정 | Level, 선택한 모듈·계약·통합·안전성 범위와 선정 이유, 누락된 범위와 보완 계획 |
| 실행 | 실제 명령·작업 경로·환경, 각 exit code, 로그·JSON 위치 |
| 결과 | discovered/started/pass/failure/error/skip, expected failure/unexpected success, missing/unexpected/duplicate |
| 안전성 | 장애 주입 도달·프로세스 exit·원본 보존·실제 효과 횟수, R3 mutation 별도 결과 |
| 동일성 | 기준 commit와 미커밋 변경, 실행 전후 소스·시험·Fixture·의존성 hash, host/Python/환경, 재사용한 실행과 근거 |
| 전체 회귀 | 수행 명령 또는 명시적 유예 이유. 부분 성공을 전체 실행으로 합산하지 않음 |
| 상태·후속 | 문서 갱신, 독립 commit, Push SHA·원격 CI 실제 상태, 남은 위험·다음 Level 3 게이트 |

실제 인증 모델, 커널 short-write, 전원 손실·손상 디스크·네트워크 파일시스템은 해당 실행 증거가
확보되기 전까지 미검증으로 남긴다. 테스트 횟수보다 기존 안전성을 보장하는 증거를 우선한다.

계약 소유권 작업에서는 같은 규칙의 중복 구현을 제거하되 저장·실행·재개 시 최신 증거를
검증하는 호출은 유지한다. 공개 형식의 독립 Fixture와 실제 효과 Assertion을 제품 상수에서
생성하는 기대값으로 대체하지 않는다. 정적 후보 0건·개별 분류·회귀 통과는 각각의 증거이며,
미검출 경로와 실제 Owner/소비 연결을 확인하지 않은 범위를 전수 완료로 선언하지 않는다.
