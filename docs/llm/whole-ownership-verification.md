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

## 대화 삭제 표식과 Session 설정 응답

사용량 소단계는 `19b132b`로 로컬 커밋했다. 후속 변경은 `Message`의 논리 삭제 표식 조회·patch
생성과 `ResumeAbandonment`의 사유 선언/생성, `SessionConfigurationView`의 설정 응답 생성이다.
대화 조회·문맥·turn 분류·재개·cross-Session 전달 검증과 삭제 Writer가 해당 Owner를 사용한다.
주입 Conversation에는 기존 `update_metadata` API를 계속 사용하며 새 필수 인터페이스를 요구하지
않는다. Session 설정 응답의 사본과 생략/null·유효값 출처, 이미 실행 중인 Run snapshot은 보존한다.

`whole-conversation-01`: 발견/실행 141, pass 125, 실패한 test ID 16개, failure 1/error 19,
exit 1. 일부 subtest 오류로 error 이벤트 수가 실패 ID 수보다 크다. 원인은 Session 삭제 Writer의
새 `Message.deletion_metadata()` 호출에 필요한 import 누락이었다. 프로세스 중단 시험도 같은
NameError 때문에 목표 장애 주입 지점에 도달하지 못했다. import를 수정했고 기존 rollback·
중단 복구·원본 보존 Assertion을 변경하지 않았다. source
`be64afd05983f6b0aca024dbdaee0333aa0872e92829449e032b2e8be7b6baeb`, input
`4d8a415985bddd2c6ec34217624b7dbccaaf2a492a7680644794239d401f4b17`는 전후 동일했다.

수정 후 결과:

| 실행 | 범위 | 발견/실행/pass | failure/error/skip/xfail/xpass/누락/중복 | Exit |
|---|---|---|---|---|
| whole-conversation-02 | turn 삭제·대화·cross-Session/실제 중단·Checkpoint 수명·설정/명명·주입 서비스·감사 11개 모듈 | 141/141/141 | 모두 0 | 0 |
| whole-conversation-safety-03 | 사용량 삭제·수명 삭제·승인·효과·Checkpoint 무결성·실제 트랜잭션 6개 모듈 | 69/69/69 | 모두 0 | 0 |

실제 명령은 각각 `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-conversation-02 tests.llm.test_turn_deletion tests.llm.test_conversation_turns tests.llm.test_cross_session_requests tests.llm.test_cross_session_crash tests.llm.test_checkpoint_lifecycle tests.llm.test_naming_contract tests.llm.test_configuration_results tests.llm.test_settings_consistency tests.llm.test_service_extensions tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`,
`.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-conversation-safety-03 tests.llm.test_usage_deletion tests.llm.test_lifecycle_deletion_integrity tests.llm.test_interaction_storage tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_transactions`다.
각 실행 디렉터리의 `llm-execution.json`·로그·전후 manifest에 증거를 보존한다. Source
`2bf876651be16f69875fb47f5ad172b771022cc608213493c6a4fad30a648e0b`, input
`8839a960aa562d816a8d055790f2a72efc894e363f6dcdecc32c32ed9694298c`는 두 실행에서 같고 각자 전후 동일하다.
서로 다른 실행을 하나의 전체 회귀로 합산하지 않는다. Level 2 소단계 결과이며 전체 작업은 계속한다.

보조 검증은 기존과 동일한 명령의 S1–S7 정적 검사(`conversation-static-02.json`, 검토 144,
new/stale/outstanding/error 0), `check_budget_mutation`(`conversation-mutation-01.log`, 실제 효과
Assertion 검출), `check_docs`(`conversation-docs-01.json`)가 모두 exit 0이다. 전체 회귀는
Checkpoint 소비 경로를 포함하는 다음 통합 게이트에서 수행하며 아직 Push하지 않는다.

## Checkpoint 저장·이벤트·승인 연결

대화·Session 설정 소단계는 `d7f6d84`로 로컬 커밋했다. 후속 변경은 공통 저장 외피의
`CheckpointManifest`·`CheckpointSnapshot`·`CheckpointRecord`, Run의 선언/삭제 증거,
`CheckpointWrite` 이벤트와 `InteractionBinding`이다. 필드는 해당 타입 또는 Owner 선언에서
정의하고 Repository·Run·Engine·Tool·승인·복구·보존·Agent 삭제 소비자가 속성/API로 읽는다.
Engine 고유 header/record payload는 해당 Engine이 계속 소유하며 저장 형식을 새로 만들지 않는다.

Manifest와 개별 record는 **읽은 원본 JSON**의 digest를 검사한다. 필수 header/records 누락을
빈 데이터로 복원하지 않으며 초기 실행의 checkpoint 자체 부재만 허용한다. 초기화 이벤트의
records 생략/명시 빈 객체 차이를 보존한다. 삭제 기록의 승인 사본은 조회 증거이며 재개 권한이
아니다. 선언·revision 동반 증거, 저장 ACK 순서, 중첩 Agent/Run Tool 예산 영수증 부착,
승인 fingerprint·갱신 binding·실제 효과 횟수와 장애 주입 Assertion을 유지했다.

독립 시험은 저장 wire·버전의 정확한 정수 타입·필수 필드·identity·사본·원본 불변성·선택 값
동일성·삭제된 승인 연결·이벤트 생략 상태·예산 증거를 확인한다. 직접 소비자 검증은 다음과 같다.
각 실행은 별도 결과이며 합쳐서 전체 회귀로 표현하지 않는다.

| 실행 | 발견/실행/pass | failure/error/skip/xfail/xpass/누락/중복 | Exit |
|---|---|---|---|
| whole-checkpoint-01 | 99/99/99 | 모두 0 | 0 |
| whole-checkpoint-02 | 162/162/162 | 모두 0 | 0 |
| whole-checkpoint-consumers-03 | 200/200/200 | 모두 0 | 0 |
| whole-checkpoint-writers-04 | 201/201/201 | 모두 0 | 0 |

재현 명령·소스와 환경 증거:

- `whole-checkpoint-01`: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-checkpoint-01 tests.llm.test_checkpoint_integrity tests.llm.test_checkpoint_lifecycle tests.llm.test_interaction_storage tests.llm.test_graph_checkpoints tests.llm.test_agent_execution tests.llm.test_loop_steering`.
  source `5048691894dcfbd9ffd5c955e0af38ff1efb289864d61e32bfa08f0b157152ad` 전후 동일.
  input `3e5591aa6bcdfbd453826217c8295001077e087c7484d9d50e24a773458a9943` 전후 동일.
- `whole-checkpoint-02`: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-checkpoint-02 tests.llm.test_checkpoint_values tests.llm.test_checkpoint_integrity tests.llm.test_checkpoint_lifecycle tests.llm.test_interaction_storage tests.llm.test_graph_checkpoints tests.llm.test_agent_execution tests.llm.test_loop_steering tests.llm.test_turn_deletion tests.llm.test_conversation_turns tests.llm.test_history_retention_integrity tests.llm.test_service_extensions`.
  source `d702ed6e7692064f5e96521e86d237144eb43502ff951d75529a598dbb303bf9` 전후 동일.
  input `edd0b073d1fa99de1033f05c05978831ac977900ca681b5b23f487e7d9943d6a` 전후 동일.
- `whole-checkpoint-consumers-03`: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-checkpoint-consumers-03 tests.llm.test_checkpoint_values tests.llm.test_common_boundaries tests.llm.test_engine_steering tests.llm.test_agent_execution tests.llm.test_nested_graph tests.llm.test_loop_steering tests.llm.test_graph_checkpoints tests.llm.test_checkpoint_integrity tests.llm.test_checkpoint_lifecycle tests.llm.test_history_retention_integrity tests.llm.test_agent_storage tests.llm.test_execution_boundaries`.
  source `2e184f102e1a83ced3512955563dd894f545de7b086a46a5f6302ad99bbdf774` 전후 동일.
  input `086eb2f80c7a55bdd59520442857dfe8f7a59907a906a0dce88a619f514be5ec` 전후 동일.
- `whole-checkpoint-writers-04`: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-checkpoint-writers-04 tests.llm.test_checkpoint_values tests.llm.test_common_boundaries tests.llm.test_engine_steering tests.llm.test_agent_execution tests.llm.test_nested_graph tests.llm.test_loop_steering tests.llm.test_graph_checkpoints tests.llm.test_checkpoint_integrity tests.llm.test_checkpoint_lifecycle tests.llm.test_history_retention_integrity tests.llm.test_agent_storage tests.llm.test_execution_boundaries`.
  source `0f1020f3983d42db045ba4ca44ee3035bc315f9b5f988976f599ebe0a4879c8d` 전후 동일.
  input `ba87f2c7761e78d7ae7c01e7b79cac30bcceaaf4dad3e713fcdd7f40eab301f6` 전후 동일.

최종 201개 집중 시험 이후 사용하지 않는 import만 제거했다. 정적 S4는 새 저장 Owner의 버전
검증 두 곳과 영향받는 기존 근거 40건을 개별 재검토했다. 전수 inventory는 444개 파일,
31,994개 후보가 아직 미분류다. 이 수치는 위반 수나 완료 수가 아니다.
사용량·대화·Checkpoint의 통합 상태에 대해 다음 Level 3에서 전체 LLM/Hub·정적·R3·문서·
실환경을 검증한다. 이 소단계 및 앞선 로컬 결과만으로 원격 CI나 전체 전수 작업 완료를 선언하지 않는다.

## 사용량·대화·Checkpoint 묶음: 전체 게이트와 Hub 시험 대역 보정

`TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip .venv-linux312/bin/python /workspace/llm-clean-resume/whole-ownership/gate.py whole-owners-group1-l3`
로 실제 namespace 실행 환경에서 두 전체 suite를 실행했다. 실행 전후 source
`a0f55075fccf28a26a1efb1f27257a8338776a3ce8f750bf026fc6e5c449bf53`, input
`b5967292ef441cf78a4dc10dd3d0547204af0b23d972d7234568228dd1bed99d`가 각각 동일하다.

| Suite | 발견/실행/pass | 실패 ID / failure / error | Skip·xfail·xpass·누락·중복 | Exit |
|---|---|---|---|---|
| LLM 전체 | 2334/2334/2334 | 0 / 0 / 0 | 모두 0 | 0 |
| Hub 전체 | 586/586/576 | 10 / 0 / 14 | 모두 0 | 1 |

Hub 오류는 `test_message_timing`, `test_snapshot_projection`, `test_request_history`의
Message 대역 10곳이 `SimpleNamespace`여서 `conversation_deleted` 속성을 제공하지 않은
것이다. Conversation 공개 반환 계약의 실제 `Message` 생성자로 교체했다. 제품 소스는
변경하지 않았고 전체 Assertion AST가 변경 전후 동일함을 확인했다. Backend 거부·CAS 충돌·
새 요청으로 우회 금지와 타이밍/표시 Assertion을 보존했다. 이 실패는 전체 게이트 실패로 기록한다.

보정 후 `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py hub whole-hub-message-fixtures-01 tests.hub.test_message_timing tests.hub.test_snapshot_projection tests.hub.test_request_history`
는 22/22/22, exit 0이다. Hub 실행에는 위 전체 환경 변수를 동일하게 적용했다.
변경된 시험 근거를 참조하는 `ChatMessage`·`TraceItem`·`SessionView`의 정적 근거 3건도
재검토했다. `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-hub-fixture-guards-01 tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`
는 15/15/15, exit 0이다. 두 집중 실행 모두 failure/error/skip/xfail/xpass/누락/중복 0,
source `2747f1ba3367773a49066d6f412c58d6cab1be42fe3c3b62a924ca3de92c2750` 전후 동일하다.
각 입력 hash는 Hub `49730680f603ddf512c7d0c53f96e8a0a3982bc263ee6f2c42a0f49ebf1295f0`,
LLM `559eeaeb6087a874debdb69f9c19df2b0c03475fc07d5b1caad209bff3e7d7b9`로 각각 전후 동일하다.

`group1-reuse-proof.json`은 원래 전체 실행과 보정 Hub 실행 사이의 차이가 위 Hub 시험 3개와
정적 검토 fixture뿐임을 확인한다. 제품·LLM 시험 소스/발견 수·의존성·호스트·환경은 동일하다.
영향받는 검토 fixture 시험은 위와 같이 다시 실행했고 LLM 전체 결과는 나머지 동일 범위의
증거로 재사용한다. 이들을 하나의 새로운 전체 LLM 실행으로 합산하지 않는다.

보조 검증: S1–S7 `checkpoint-static-02.json` 146개 검토/new·stale·outstanding·error 0,
문서 `checkpoint-docs-01.json`, R3 `checkpoint-mutation-01.log`, 실제 환경 `group1-env.json`,
실제 SDK 오프라인 진단 `group1-providers.json` 모두 exit 0. R3의 의도적 mutation은 실제 효과
Assertion 두 개가 실패하여 검출됐다. 검사를 통과한 제품 결함과 혼동하지 않는다.

Checkpoint 소단계와 Hub 대역 보정을 로컬 커밋으로 보존하고 그 SHA를 고정한 검증 디렉터리에서
Hub 전체를 재실행한다. 통합 게이트는 아직 완료가 아니며 원격 Push/CI도 아직 하지 않았다.
상위 전수 작업은 이어서 진행한다. 이전 전체·집중 로그와 실패 증거를 삭제하지 않는다.
