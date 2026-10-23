# 계약 소유권 전수 적용 실행 기록

## 범위와 기준

기준 SHA `3a32fa0706d5669e9f0e395a04eb40da66655fca`, 작업 브랜치는 `refactor/llm-clean`이다.
전체 제품 `llm/`, `hub/`, 실행 assets를 대상으로 [기존 계약](contract-ownership.md)의
선언·Reader/Writer·변경·실제 소비자 연결과 전수 완료 게이트를 수행한다. 이전 DC1–DC2의
부분 통과는 전체 완료가 아니다. main·refactor/llm-backend·Draft PR #1은 변경하지 않는다.
기존 38개 데이터군 감사와 단계별 실패/성공 증거는 유지하고 실제 영향 경로만 다시 확인한다.

## 사용량 계약의 전체 소비 경로

Owner와 변경:

- `UsagePolicy`: 기존 정책 필드에서 Schema와 실행 snapshot을 파생한다. 원본의 생략/null/값을
  변경하지 않고 Run/Project 한도를 분리한다. 키 접두사 조합으로 정책을 해석하지 않는다.
- `TokenUsage`, `UsageReceipt`: 정규 token 필드와 알려진 사용량/예약/불명 상태, 기간 조회를
  소유한다. 원본 Completion·Component Reader는 계속 필수 기록과 무결성을 검증한다.
  Projection은 저장 Reader를 대체하지 않는다.
- `ModelUsageRecord`: 새로운 예약의 초기 상태와 시작→종료 변경을 소유한다. 내부 처리 중
  타입을 유지하고 Component 저장 경계에서 직렬화한다. identity·예약·종료 불변식은 유지한다.
- `UsageSource`, `UsageCall`, `UsageSummary`: 호출 소유권과 Project의 공개 사용량 응답을 소유한다.
  Run/Component 합계와 기간 계산을 서비스·Hub에서 중복 해석하지 않는다.
- `policies.usage`: 순수 한도 검사와 기간 선택을 담당한다. runtime은 실제 잠금 안에서
  최신 영수증을 읽고 예약·저장·실행을 조율한다. 무제한 호출의 영수증 기록도 유지한다.

연결한 소비자: Run 실행/Completion 예약, Component 모델 관찰·동시 호출·취소, 정책의
counter 검증, Project 사용량 조회, Session/Run/Component 삭제 보호, SDK 진단 코드,
Hub 날짜별 사용량 표시와 설정 Schema. 저장 JSON·공개 응답·SDK 재시도 의미는 변경하지 않는다.

이 영역은 승인·예산·삭제와 직접 연결되므로 집중 검증 후 관련 실제 효과/장애·무결성
검증을 수행하고, 다음 통합 Push 게이트에서 LLM/Hub 전체·정적·R3·문서·실환경을 확인한다.
소단계 커밋은 독립 추적하며, 원격 CI 성공은 정확한 SHA의 실제 결과로만 기록한다.

### 집중 검증

`/workspace/llm-clean-resume/whole-ownership/usage-focus-01.json`: 기존 usage deletion,
Project policy, policy ownership/consumers, execution boundaries 5개 모듈, 발견/실행/pass
50/50/50, exit 0. failure/error/skip/xfail/xpass/누락/중복은 모두 0이다.
명령: `.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --modules tests.llm.test_usage_deletion tests.llm.test_project_policies tests.llm.test_policy_ownership tests.llm.test_policy_consumers tests.llm.test_execution_boundaries --output /workspace/llm-clean-resume/whole-ownership/usage-focus-01.json`.
이 첫 실행에는 별도 전후 manifest가 없어 동일성 재사용 근거로 사용하지 않는다.

`/workspace/llm-clean-resume/b/whole-usage-02`: 같은 범위에 신규 Owner의 독립 wire·원본 보존·
예약/불명 사용량·정책 생략 검증과 Component storage integrity를 추가했다. 7개 모듈,
발견/실행/pass 72/72/72, exit 0, 19.429초. failure/error/skip/xfail/xpass/누락/중복 모두 0.
명령: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-usage-02 tests.llm.test_usage_ownership tests.llm.test_usage_deletion tests.llm.test_project_policies tests.llm.test_policy_ownership tests.llm.test_policy_consumers tests.llm.test_execution_boundaries tests.llm.test_component_storage_integrity`.
source `91a43991c8c3f54a7bcdbe62827e2251ca845ad29a9150f4e6882172c441570d`,
input `a00c1db7267f4e950dc08f3ff931734904322d48ceec575d4aeb43dcafe9fe64`, 각각 실행 전후 동일.
실제 Python은 3.12.14/Clang 22.1.3이다. 이전 GCC 환경의 결과를 환경 동일성 증거로 승계하지 않는다.

위 실행 뒤 확인한 Component의 관찰 가능성 검사와 Hub 표시 소비자 변경은 후속 검증 대상이다.
전체 회귀·소단계 완료·커밋은 아직 선언하지 않는다. 전수 파일/후보 검토도 계속 진행 중이다.

### 관련 안전성 검증과 수정

`whole-usage-safety-03`: 18개 모듈, 발견/실행 192, pass 191, failure 1/error 0, exit 1,
55.660초. Skip/xfail/xpass/누락/중복은 모두 0이다. 실패는
`OperationsSchemaTests.test_corrupt_usage_receipt_blocks_new_calls`로, 손상 영수증은 거부했지만
공통 token 검증기의 오류 문구가 기존 `Invalid complete`와 달랐다. 기존 Assertion을 유지하고
Component/Completion Owner의 진단 문구를 공통 검증기에 전달하도록 수정했다. 검증 규칙이나
실제 효과 횟수는 변경하지 않았다. 기간 조회에서 모든 항목에 동일한 시각을 쓰는 조건도
보강하고 동일 timestamp 경계 시험을 추가했다.

실제 argv·종료 코드·모든 실패 trace는 `/workspace/llm-clean-resume/b/whole-usage-safety-03/`
`llm-execution.json`과 로그에 보존한다. Source
`b6276ee0e23cd42b295582a47ea43747b04d9e259450cb4dc7e7907874a6c191`, input
`2a39bdf1ef0d0b2346dc2c732c4a25fd84269a7542a663ae0078c3f34811a28a`는 각각 전후 동일했다.
이 실행은 실패이며 후속 재검증 결과와 합산하지 않는다.

### 수정 후 소단계 검증

| 실행 | 범위 | 발견/실행/pass | failure/error/skip/xfail/xpass/누락/중복 | Exit |
|---|---|---|---|---|
| whole-usage-safety-04 | LLM Owner·직접 소비자·실제 Component 호출/동시 예약/취소·승인/효과/Checkpoint/RAG·트랜잭션·삭제·정적 근거 18개 모듈 | 193/193/193 | 모두 0 | 0 |
| whole-usage-hub-01 | Hub 사용량·설정·Project authority 3개 모듈 | 19/19/19 | 모두 0 | 0 |

LLM 실제 명령은 앞의 `whole-usage-safety-03`와 같은 모듈 선택에서 실행 이름만
`whole-usage-safety-04`로 변경했다. Hub 명령은
`.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py hub whole-usage-hub-01 tests.hub.test_usage_definitions_system tests.hub.test_settings tests.hub.test_project_authority`.
각 디렉터리의 `*-execution.json`에 실제 전체 argv·exit·엄격 집계가 있고 로그·전후 manifest를 보존한다.
소스 hash는 두 실행 모두 `730b2fa4742e66ea9919dc8cd60b933d995a97e03ae7a9f4f95b49eafee3b8b9`로 전후 동일.
LLM input `4c32fcae2173866b7fdbecafda73f38adcbf6492d3a5a16f0e7bee85777930cc`, Hub input
`26f3e29e0eee9f802e0fe632746223caad2637cd70080a100c6b1c9815c1df54`는 각자 전후 동일하다.
Hub에는 `HUB_TEST_XVFB`·`HUB_TEST_XCLIP`을 명시했다. 서로 다른 실행을 하나의 전체 회귀로 합산하지 않는다.

보조 명령:

- `.venv-linux312/bin/python -m tests.llm.check_budget_mutation`: exit 0. 호출 한도를 의도적으로
  제거한 2개 시험의 기존 실제 효과 Assertion이 실패하여 Mutation을 검출했다.
- `.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --require S6 --require S7 --output /workspace/llm-clean-resume/whole-ownership/usage-static-02.json`: exit 0,
  개별 검토 144, new/stale/required outstanding/review error 0. 변경된 근거와 bool eligibility
  발생만 재검토했으며 전수 기준의 미검토를 승인하지 않았다.
- `.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/whole-ownership/usage-docs-01.json`: exit 0.
- `.venv-linux312/bin/python -m tests.llm.check_environment --suite hub --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/whole-ownership/usage-env-02.json`: exit 0,
  host revision/clean 상태·namespace·X11 확인. 기본 샌드박스의 `usage-env-01`은 `/proc/self/uid_map`
  쓰기를 제한해 exit 1이었다. 같은 smoke test와 검사를 허용된 샌드박스 밖에서 재실행했다.
  실패를 숨기거나 namespace 검증을 비활성화하지 않는다.

이 결과는 사용량 소비 경로 소단계의 Level 2 완료다. 상위 전수 작업은 계속 진행한다.
전체 LLM/Hub 회귀는 관련 다음 소단계들과 묶은 통합 게이트에서 실행하며 성공 전에는 Push하지 않는다.
현재 후보 32,221개/443개 파일의 전수 분류는 완료하지 않았고 전체 완료 게이트는 계속 차단한다.
결과 기록과 inventory 표시 순서만 갱신한 뒤 코드·시험 변경 없이 소단계 커밋으로 보존한다.
