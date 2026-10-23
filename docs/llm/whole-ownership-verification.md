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

## 묶음 1의 고정 SHA Hub 재검증

Checkpoint 로컬 커밋은 `a887583`이다. `/workspace/ish-llm-group1-validation`을 이 SHA의
분리된 검증 checkout으로 만들고 동일 Python·호스트·고정 의존성·namespace/X11 환경에서
`/workspace/ish-llm/.venv-linux312/bin/python /workspace/llm-clean-resume/whole-ownership/run-suite.py hub whole-owners-group1-hub-corrected-l3`
를 실행했다. **586/586/586, 모든 bad count 0, exit 0**. Source
`2747f1ba3367773a49066d6f412c58d6cab1be42fe3c3b62a924ca3de92c2750`, input
`63bfe7bdc8841d2f75b651c6cf5a78cec6bfc237696bbd47e3d9ca3467af93af`는 전후 동일하다.
실제 import 경로도 해당 검증 checkout의 `llm`·`hub`임을 확인했다. 작업 checkout의 다음 변경이
이 시험에 섞이지 않았다. 앞서 기록한 LLM 전체와 보정된 감사 시험의 재사용 조건·각 소스 구분을
유지한다. 다음 추가 지시 소단계와 묶어 Push/원격 CI를 진행하며 이 결과는 전수 작업 완료가 아니다.

## 추가 지시 이벤트·채널·Message 영수증 Owner

`SteeringEventData`/`SteeringOperation`은 Engine 요청과 중첩 scope 연결, `SteeringNotification`은
부분 변경 알림의 생략/빈 배열 구분을 소유한다. `SteeringChannel`은 Run 채널·예약 경로·실행 대상
등록과 종료를 소유한다. `InstructionMessageRecord`/`InstructionReceipt`/`InstructionApplication`은
Message 내부 영수증·상태 집계·반영 이력·예약 연결을 소유한다. RunManager·Graph·서비스·Hub는
속성/변경 API와 공개 `RunInstruction`을 사용한다. 원본 Message 본문이나 실행 기록의 새 저장소를
만들지 않았다. 원래 JSON 모양·초기 필드 생략·미결합 null·opaque 확장 데이터와 저장 ACK 순서는 유지한다.
공통 Checkpoint records 교체도 Owner 경계에서 원본 필수 증거와 교체값을 검사하고 사본에만 적용한다.

이 과정에서 기존 복제 Writer의 결함을 독립 재현했다. 일부 대상은 applied, 다른 대상은 pending인
지시를 복제하면 대상별 영수증은 남지만 전체 상태를 unapplied로 만들어 현행 Reader가 거절했다.
새 복제본에서 pending 대상만 cloned 사유로 닫고 같은 Owner로 부분 반영 상태를 계산하도록 수정했다.
원본 및 적용 영수증·사용 이력은 바꾸지 않으며 기존 손상 기록을 복구하거나 재실행하지 않는다.
새 회귀는 집계의 정합성뿐 아니라 노드 전용 지시가 후속 실행의 일반 문맥으로 유출되지 않음을 확인한다.

실패와 보정 이력:

- `whole-steering-channel-01`: 기존 장애 주입 helper가 typed SteeringTarget을 dict로 읽어
  두 번째 대상의 의도적 장애 전에 TypeError를 일으켰다. `.scope` 접근으로 변경했고, 실제로
  두 번째 대상에서만 주입됐다는 Assertion을 추가했다. 기존 부분 반영/재개/효과 Assertion은 유지했다.
- `whole-instruction-clone-repro`: 위 복제 결함이 현재 Reader의 aggregate status 검사로 재현됐다.
- `whole-instruction-clone-fixed`: 복제본 상태/영수증 Assertion은 통과한 뒤 새 시험의 후속 요청
  fixture에 필수 admission이 없어 실패했다. 기존 admitted_message helper로 실제 접수 증거를
  구성했고 권한 검사를 완화하지 않았다. 보정 재현 시험은 `whole-instruction-clone-fixed-02`로 통과했다.
- `whole-steering-receipts-03`: 재시작 회복 시험의 Writer fixture가 typed 영수증을 JSON 변환 없이
  저장하려 했다. 해당 경계에 `to_dict()`만 추가했다. 원본 독립 필드값·재시작/무실행 Assertion은 유지했다.
- `whole-steering-hub-01`: 존재하지 않는 test_steering 모듈을 지정해 발견 단계에서 exit 2로
  중단됐다. 시험을 실행하지 않았으며 성공/Skip으로 처리하지 않는다. 실제 모듈 test_features를
  확인해 `whole-steering-hub-02`에서 실행했다.

| 실행 | 발견/실행/pass | 실패 ID / failure / error | Skip·xfail·xpass·누락·중복 | Exit |
|---|---|---|---|---|
| whole-steering-channel-01 | 128/128/127 | 1 / 1 / 0 | 모두 0 | 1 |
| whole-steering-reservations-02 | 29/29/29 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-instruction-clone-repro | 1/1/0 | 1 / 0 / 1 | 모두 0 | 1 |
| whole-instruction-clone-fixed | 70/70/69 | 1 / 0 / 1 | 모두 0 | 1 |
| whole-instruction-clone-fixed-02 | 1/1/1 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-steering-receipts-03 | 140/140/139 | 1 / 0 / 1 | 모두 0 | 1 |
| whole-steering-receipts-04 | 140/140/140 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-steering-safety-05 | 143/143/143 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-steering-hub-01 | 실행 전 중단 | 집계 없음 | 집계 없음 | 2 |
| whole-steering-hub-02 | 29/29/29 | 0 / 0 / 0 | 모두 0 | 0 |

각 실행은 별도 증거이며 하나의 전체 실행으로 합산하지 않는다. 명령·소스/입력 hash:

- `whole-steering-channel-01`: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-steering-channel-01 tests.llm.test_steering_values tests.llm.test_engine_steering tests.llm.test_loop_steering tests.llm.test_instruction_reservations tests.llm.test_graph_checkpoints tests.llm.test_checkpoint_integrity tests.llm.test_conversation_turns`.
  source `95717dbb7de825c3d4556e32ca59ed7a930fc6ecb5a7a56bfb6476be1734c8f8` 전후 동일.
  input `962c394e1fd02e587cdbd18151d01e22958c4345aa067d1055de215fb00e6a7b` 전후 동일.
- `whole-steering-reservations-02`: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-steering-reservations-02 tests.llm.test_instruction_reservations tests.llm.test_steering_values`.
  source `bf0c97422b0efa7a9938d945c581315755436b9fc31130ca9c2a75f70d63aaa3` 전후 동일.
  input `94f9093988d9a2312dcf72d5ff63b092d33cf936957f71815472cb7658c25318` 전후 동일.
- `whole-instruction-clone-repro`: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-instruction-clone-repro tests.llm.test_instruction_values`.
  source `ede4d41e76a138f0c98e549c8afd2b3897f8aaa95f626b1ae749d961fc834536` 전후 동일.
  input `429e3bf295faf3686f9b952040d4ee60e5075a1e7d0704267e002b4aaa775297` 전후 동일.
- `whole-instruction-clone-fixed`: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-instruction-clone-fixed tests.llm.test_instruction_values tests.llm.test_loop_steering tests.llm.test_instruction_reservations tests.llm.test_conversation_turns`.
  source `0bbe9a84c0fe46e0ab2f2e77f803c3fa3bb7eb5b1f1ec1b77fcf82facbf6dd27` 전후 동일.
  input `034a7122a307b8829c5fbc5e1c6a864f4c2d49faa1a1c6e231699bc7e3c56fd4` 전후 동일.
- `whole-instruction-clone-fixed-02`: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-instruction-clone-fixed-02 tests.llm.test_instruction_values`.
  source `c93134654a85abcf548c9337152ce24099afc83036242140c4d77640b30fbcf5` 전후 동일.
  input `42a5d02724dda02b1675ade3e49538ca2392450761a32394ba0ed99ed6e660c0` 전후 동일.
- `whole-steering-receipts-03`: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-steering-receipts-03 tests.llm.test_instruction_values tests.llm.test_steering_values tests.llm.test_loop_steering tests.llm.test_instruction_reservations tests.llm.test_conversation_turns tests.llm.test_engine_steering tests.llm.test_checkpoint_values tests.llm.test_checkpoint_integrity tests.llm.test_graph_checkpoints`.
  source `b9c26d75f02ab714345ff571fc203992fe7eb416ed769274919b632d88594e0b` 전후 동일.
  input `4e160690c12eec3b709dd37bde787f187bb527980f359e7dae1e7b6646c7690e` 전후 동일.
- `whole-steering-receipts-04`: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-steering-receipts-04 tests.llm.test_instruction_values tests.llm.test_steering_values tests.llm.test_loop_steering tests.llm.test_instruction_reservations tests.llm.test_conversation_turns tests.llm.test_engine_steering tests.llm.test_checkpoint_values tests.llm.test_checkpoint_integrity tests.llm.test_graph_checkpoints`.
  source `424d2acff6a6a86531a8a1face4f1ed4bb757bcc5cdb974fd5c411dd9774327e` 전후 동일.
  input `5901dd09c8422620e5854e525076039446e9a909dee302ec35b54068f43d568c` 전후 동일.
- `whole-steering-safety-05`: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm whole-steering-safety-05 tests.llm.test_instruction_values tests.llm.test_steering_values tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_transactions tests.llm.test_runtime_reliability tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`.
  source `1837b72cc45d7b089ae3d1372eaf8eb3d75f1ef4df59407abbf1cdc6b2b338c4` 전후 동일.
  input `37f2cc4220effbd5f18ced9d3699ed7a34121d8fd765249be3f6f982bfcdca87` 전후 동일.
- `whole-steering-hub-01`: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py hub whole-steering-hub-01 tests.hub.test_message_timing tests.hub.test_snapshot_projection tests.hub.test_request_history tests.hub.test_steering`.
  source `1837b72cc45d7b089ae3d1372eaf8eb3d75f1ef4df59407abbf1cdc6b2b338c4` 전후 동일.
  input `ab05f9411c94b99f3a3727cd3cd938000221eb02039942d3e6807027c63e9f56` 전후 동일.
- `whole-steering-hub-02`: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py hub whole-steering-hub-02 tests.hub.test_message_timing tests.hub.test_snapshot_projection tests.hub.test_request_history tests.hub.test_features`.
  source `1837b72cc45d7b089ae3d1372eaf8eb3d75f1ef4df59407abbf1cdc6b2b338c4` 전후 동일.
  input `ab05f9411c94b99f3a3727cd3cd938000221eb02039942d3e6807027c63e9f56` 전후 동일.

안전성 범위는 승인·예산 삭제·Tool 효과·Checkpoint·RAG·수명/삭제·실제 트랜잭션·런타임 장애와
감사/범위 검사이며, 직접 Hub 범위는 Message 타이밍·snapshot·재개 거부/CAS·추가 지시다.
S1–S7은 변경된 근거 26건과 새 Owner/시험의 연결을 재검토해 146건, new/stale/outstanding/error 0
(`steering-static-02.json`, exit 0). R3 Mutation은 실제 효과 Assertion 두 개로 검출했으며
`steering-mutation-01.log` exit 0이다. 기존 필수 검사와 실패 재현을 삭제하지 않았다.

전수 감사에는 실제로 전체 내용을 읽은 docstring 전용 package marker 27개를
[파일별 근거](contract-coverage-reviews.json)에 기록했다. 구현·값·Reader/Writer·동적 소비자가
없는 파일만 해당하며 주변 구현 파일은 포함하지 않는다. [현재 inventory](contract-coverage-index.json)는
447개 파일 중 420개와 31,739개 후보가 여전히 미검토/미분류다. 기존 S1–S7 검토를 전수 승인으로
전환하지 않았다. 새 시험은 검토 hash·분류와 현재 inventory 일치를 확인한다.

이 기록은 추가 지시 소단계의 Level 2 증거다. 다음 검증 checkout에서 현재 통합 소스의 전체
LLM/Hub·보조 검증을 실행하고 성공한 SHA만 Push한다. 원격 CI와 전수 잔여 0은 아직 완료하지 않았다.

## 요청 접수·배포 이력의 전체 소비 경로

`llm/services/runtime/requests.py`의 `RequestAdmission`/`RequestDispatch`가 정규 필드·진단·
버전·수신자 선택·미배포 상태를 선언하고 검증된 값 객체를 반환한다. RunManager, RequestHandle,
Agent 삭제 검사, 전송 검사, Conversation 복제, Hub 이력/작성자 Projection이 속성과 API로
소비한다. 원시 dict 인덱싱을 제공하는 호환 실행 경로는 없다. 고정 필드는 Owner 경계에서만
직렬화하며 Repository/공개 응답의 JSON 모양은 유지한다.

수신자별 원래 Agent binding, 정확한 Run/Assistant 전체 집합, 원문 history revision, Message
소유자·상태, 원본 request context를 계속 검사한다. history digest는 원시 저장 기록으로 계산하며
typed Message projection의 재직렬화 결과로 대체하지 않는다. 조회한 수신자·binding·이력·Run map을
수정해도 내부 증거는 바뀌지 않는다. 복제 provenance는 관찰 자료로만 남고 실행 admission이 아니다.
전체 수신자의 Run 및 이력 저장 후 dispatch하는 잠금·트랜잭션·ACK 순서는 변경하지 않았다.

새 `test_request_values` 4개는 독립 wire 값, 중첩 사본, missing/null, 원문 digest 변경,
복제의 실행 권한 미승계를 검사한다. 기존 손상 테스트는 원시 fixture를 계속 변조한다.
기존 시험 5파일의 Assertion 361개를 AST로 비교해 값 객체의 `to_dict()` 경계 외에는 모두
유지했음을 확인했다(`admission-assertion-preservation.json`). 효과 횟수나 장애 단언은 삭제하지 않았다.

개발 중 최초 smoke 명령 `.venv-linux312/bin/python -m unittest tests.llm.test_request_admission -q`는
classmethod로 전환한 `identifiers`의 인자를 잘못 남겨 setUp에서 8개 모두 error(exit 1)였다.
인자를 복구한 같은 smoke는 8개 통과(exit 0). 두 로그를 `admission-draft/smoke.log`,
`smoke-02.log`에 보존했다. 별도 source/input manifest를 수집하지 않은 smoke는 공식 검증의
재사용 근거로 쓰지 않는다. 아래 strict runner 실행에서 제품·시험·환경의 동일성을 다시 확인했다.

| 실행 | 발견/실행/pass | Failure / Error / Skip | xfail·xpass·누락·추가·중복 | Exit |
|---|---|---|---|---|
| whole-admission-01 | 169/169/169 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-admission-safety-02 | 187/187/187 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-admission-hub-01 | 29/29/29 | 0 / 0 / 0 | 모두 0 | 0 |

실제 명령과 실행 전후 hash(서로 다른 실행을 하나의 전체 회귀로 합산하지 않음):

- `whole-admission-01`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-admission-01/llm.json --modules tests.llm.test_request_values tests.llm.test_request_admission tests.llm.test_multi_agent tests.llm.test_multi_agent_parallel tests.llm.test_multi_agent_crash tests.llm.test_cross_session_requests tests.llm.test_cross_session_crash tests.llm.test_facade_requests tests.llm.test_session_agents tests.llm.test_agent_domain tests.llm.test_conversation_turns tests.llm.test_turn_deletion tests.llm.test_loop_steering tests.llm.test_service_extensions`.
  source `1b0177ac23214e24cfcc9619dd5962d95b850022538461ecf9b5f0f6dfec022a`, input `2a9b3600e439adf38d5682eb30f57897972bb2f9c1334dc856f907633cd46e75` 각각 전후 동일.
- `whole-admission-safety-02`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-admission-safety-02/llm.json --modules tests.llm.test_request_values tests.llm.test_request_admission tests.llm.test_multi_agent tests.llm.test_multi_agent_parallel tests.llm.test_multi_agent_crash tests.llm.test_cross_session_requests tests.llm.test_cross_session_crash tests.llm.test_instruction_reservations tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_transactions tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`.
  source `5b83b55225b789d32e44cdcbe9493174ecec07436a11c51eda7368b8f011d40f`, input `9157471c5478461384c56643515536209a3a38bb82025f30d99de79dc852b651` 각각 전후 동일.
- `whole-admission-hub-01`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-admission-hub-01/hub.json --modules tests.hub.test_message_timing tests.hub.test_snapshot_projection tests.hub.test_request_history tests.hub.test_features`.
  source `5b83b55225b789d32e44cdcbe9493174ecec07436a11c51eda7368b8f011d40f`, input `6c8fa70a97dbc972ffda5e0a9017cd9941895e3a69282c588c457309e2e99bff` 각각 전후 동일.

첫 집중 범위는 접수/배포·다중 Agent·실제 중단/복구·전송·Facade·참여자·Agent 삭제·대화/turn
삭제·추가 지시·서비스 확장이다. 이후 선언/slot 정리까지 반영한 최종 안전성 범위는 원본 접수,
병렬/중단/전송, 예약, 승인, 예산 삭제, Tool 효과, Checkpoint, RAG, 수명/삭제, 원자적 저장,
감사와 inventory다. Hub는 Message 타이밍·snapshot·request 이력·기능 소비자를 검사했다.
Python 3.12.14 Clang 22.1.3, host `d833790959a76ac457fdeea3f21a6bea1eecfe12`를 유지했다.

S1–S7은 변경된 근거 13건을 재검토한 후 146건, new/stale/outstanding/error 0
(`admission-static-02.json`, exit 0). 앞선 검사는 변경된 source hash를 거절했으며
(`admission-static-01.json`, exit 1), 기존 면제값을 자동 갱신하지 않았다. R3 Mutation은
원래 실제 효과 Assertion 두 개로 검출(`admission-mutation-01.log`, exit 0)했다.
명령은 `python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --require S6 --require S7 --output <위 JSON>`과
`python -m tests.llm.check_budget_mutation`이다.

전체 파일/동적 소비자를 검토한 요청 접수 Owner 1개, 후보 64개를 개별 근거로 추가했다.
현재 inventory 447개 중 419개 파일/31,603개 후보는 미검토/미분류로 남는다. 주변 파일 전체를
완료 처리하지 않았으며 전수 완료 게이트는 아직 통과하지 않는다.
이 구간은 Level 2 로컬 완료다. 전송/후속 구간과 묶은 현재 통합 소스에서 다음 Level 3를 수행한다.
앞선 `967f6dd` SHA에서 실행 중인 전체 게이트는 이 새 요청 접수 변경의 전체 통과 증거로 재사용하지 않는다.
문서 검사는 `python -m tests.llm.check_docs --output /workspace/llm-clean-resume/whole-ownership/admission-docs-01.json`으로 exit 0이었다.

## 묶음 2의 전체 게이트와 원격 Push

검증 전용 checkout `/workspace/ish-llm-group2-validation`의 정확한
`967f6dd933f201232ef2d1896687a7f4b62415df`에서 `whole-owners-group2-l3`를 실행했다.
LLM 2,344/2,344, Hub 586/586 모두 pass/exit 0이며 Failure/Error/Skip/xfail/xpass/누락/추가/중복은
모두 0이다. 전체 LLM은 1,246.60초, 전체 Hub는 1,070.62초였다. 두 suite가 같은 고정 소스에서
실행됐고 source `1837b72cc45d7b089ae3d1372eaf8eb3d75f1ef4df59407abbf1cdc6b2b338c4`,
input `9c4f9ecfdfa62c84304792075bd2172ca5f8960fb7685f05f7cee8cd93695bcd`는 전후 동일했다.
Python/host는 앞서 기록한 Clang 3.12.14 및 `d833790959a76ac457fdeea3f21a6bea1eecfe12`다.

실제 명령은 해당 checkout에서 `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-owners-group2-l3/llm.json` 및
같은 명령의 `--suite hub --output /workspace/llm-clean-resume/b/whole-owners-group2-l3/hub.json`이다.
감독 명령 `python /workspace/llm-clean-resume/whole-ownership/gate.py whole-owners-group2-l3`도 exit 0.
`TERM=xterm-256color`, LiteLLM local cost map/production, 기존 Xvfb/xclip 경로를 적용했다.
실제 bwrap namespace 검증이 가능한 동일 호스트 권한에서 실행했다.

같은 SHA의 `group2-auxiliary/`에 S1–S7(146개 검토, 잔여 0), 문서, R3 실제 효과 Mutation,
실제 SDK offline transport 검사를 보존했으며 각각 exit 0이다. 환경 검사 최초 명령의 잘못된
`--require-runtime` 인자는 실행 전 CLI에서 exit 2였다(`environment.log`, `results.json`).
정확한 `--host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --suite llm|hub --output <suite-environment.json>`으로
환경/격리 검사를 다시 실행해 둘 다 exit 0을 확인했다(`environment-results.json`).
첫 명령을 성공으로 간주하거나 시험 Skip으로 집계하지 않는다.

원격 main `94229cb865374d99f20ec48c5e48269488041a4e`, refactor/llm-backend
`bb0946257ab6bcabb9a3ef04d3d854e5333b07d5`, 작업 브랜치의 기존
`3a32fa0706d5669e9f0e395a04eb40da66655fca`를 먼저 확인했다.
`git push origin 967f6dd933f201232ef2d1896687a7f4b62415df:refs/heads/refactor/llm-clean`이 exit 0으로
완료됐다. main/이전 브랜치/Draft PR #1은 변경하지 않았다.
[해당 SHA의 원격 실행](https://github.com/E1der0wL/ish-llm/actions/runs/39901306599)은 현재 두 필수
Job의 전체 회귀가 진행 중이다. 아직 원격 성공을 선언하지 않으며 후속 결과를 별도 기록한다.
새 요청 접수 및 전송 변경은 위 SHA 이후 작업으로, 이 전체 게이트의 완료 증거에 포함하지 않는다.

## 세션 전송 영수증·원장·조회 경로

`runtime/transfer_values.py`가 Source/Intent/Receipt/Inventory, action/direction, deadline,
content digest/idempotency identity, 권한 callback 및 공개 조회 Projection을 소유한다.
`runtime/transfers.py`는 검증된 속성·변경 API를 사용해 권한·접수·조회·취소·재개를 조정하고,
TransferLedger가 파일 I/O를 소유한다. SessionRepository 주입 경계는 기존 JSON/CAS를 유지한다.
원래 revision을 가진 변경 입력은 커밋된 원장이 아니며 Writer만 새 revision을 만든다.
원장 Reader는 원본 또는 필수 필드가 없으면 실패한다. 원래 source/target/Message의 트랜잭션,
ACK 뒤 큐 등록, idempotency·만료·Resume·승인·누적 사용량·효과 보장은 그대로다.

Run metadata 복사와 Conversation clone도 Receipt Owner로 이동했다. Resume/restart의 원본
Run ID는 RunLink.require_source_id로 필수 증거를 읽으며 누락 시 fallback을 사용하지 않는다.
공개 snapshot은 원래 JSON이고 내부 취소/재개는 typed Run view를 사용한다. 인가 callback에
전달하는 접수 전 Intent를 완료된 영수증으로 해석하지 않는다. [현행 계약](session-transfers.md)에
Owner와 직렬화 경계를 명시했다.

첫 집중 검증 `whole-transfer-01`은 71개 중 70개 pass, error 1이었다. 만료 시간을 주입하는 기존
시험이 이전 module 함수 경로를 patch해서 AttributeError가 발생했다. 실제 새 Owner의
TransferDeadline.remaining으로 patch 대상을 바꾸고 세 Resume 경로 모두 clock 검사를 거쳤음을
추가 단언했다. 원래 승인/예산/효과 횟수/저장 보존 단언은 유지했다. 원래 두 시험 파일의 Assertion
125개를 AST로 비교했고 직렬화 경계 외에는 유지됐으며 새 단언 1개만 추가했다(`transfer-assertions.json`).
새 test_transfer_values는 독립 wire/null, 불변 중첩 증거, 원래 CAS revision, 누락/다른 소유자,
권한/clone Projection, exact version type을 검증한다.

| 실행 | 발견/실행/pass | Failure / Error / Skip | xfail·xpass·누락·추가·중복 | Exit |
|---|---|---|---|---|
| whole-transfer-01 | 71/71/70 | 0 / 1 / 0 | 모두 0 | 1 |
| whole-transfer-02 | 76/76/76 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-transfer-safety-03 | 173/173/173 | 0 / 0 / 0 | 모두 0 | 0 |

각 실행의 실제 명령과 source/input hash:

- `whole-transfer-01`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-transfer-01/llm.json --modules tests.llm.test_cross_session_requests tests.llm.test_cross_session_crash tests.llm.test_repositories tests.llm.test_conversation_turns tests.llm.test_multi_agent tests.llm.test_multi_agent_parallel tests.llm.test_multi_agent_crash`.
  source `c8f9971463a779220625be3c720b73b116ce4f2cb969eb269af332fc0d6ecb19`, input `e60bf3411002fcd4e1adb5e669b36204a2b2824933204044dcffca423c1ddc88` 각각 전후 동일.
- `whole-transfer-02`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-transfer-02/llm.json --modules tests.llm.test_transfer_values tests.llm.test_cross_session_requests tests.llm.test_cross_session_crash tests.llm.test_repositories tests.llm.test_conversation_turns tests.llm.test_multi_agent tests.llm.test_multi_agent_parallel tests.llm.test_multi_agent_crash`.
  source `92f7be3255dadb17b8e2a98fb14965e3b702584cd308c7276d942bbf845aa3b7`, input `fbab812474de608d1aab30271740515dd8c4478ea1a7b8939ae2567a205e2b46` 각각 전후 동일.
- `whole-transfer-safety-03`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-transfer-safety-03/llm.json --modules tests.llm.test_transfer_values tests.llm.test_cross_session_requests tests.llm.test_cross_session_crash tests.llm.test_repositories tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_transactions tests.llm.test_runtime_reliability tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`.
  source `e116c7bf0d6cec7e9712dc39c176db18200e3a7ab9aed50fa66e5689dedfbd91`, input `c4193ae85fe01b80764fda8d76e4c5ca091824470f94a92859644123f9598bfa` 각각 전후 동일.

집중 범위는 전송·실제 프로세스 중단/ACK·Repository 주입·대화/복제·다중 Agent/병렬 실행이다.
최종 안전성 범위는 전송·CAS·승인·예산 삭제·Tool 효과·Checkpoint·RAG·수명/삭제·트랜잭션·
런타임 장애·정적 감사·전수 inventory다. 환경은 위와 같다. 다른 source의 결과를 하나의 전체
실행으로 합산하지 않는다. 직접 Hub 소비자의 공개 JSON은 보존했고 다음 묶음의 전체 Hub에서 검증한다.

`transfer-auxiliary.json`의 실제 명령에서 S1–S7·R3·문서가 각각 exit 0이다. 변경된 개별 감사 근거
13건을 재검토했다. 새 S4 한 건은 JsonValue의 exact int 검증이 Owner의 version 비교보다 먼저
실행됨을 확인하고 bool/float/null/string/다른 int 거부 시험에 연결했다. 총 147건의 개별 검토,
new/stale/outstanding/error 0이며 Reader에 중복 타입 검증이나 호환 경로를 추가하지 않았다.
R3는 실제 효과 횟수 단언 두 개로 mutation을 검출했다.

전송 두 파일의 전체 내용·동적 callback/patch·실제 소비자를 검토해 후보 115개를 개별 분류했다.
전수 inventory는 448개 파일, 418개 미검토 파일, 31,307개 미분류 후보다. 이는 전수 완료가 아니다.
이 소단계는 Level 2 완료이며, 요청 접수와 전송을 묶은 고정 SHA에서 전체 LLM/Hub 및 필수 보조
검증을 실행한다. 전체 성공 전 새 소단계를 원격 완료로 기록하거나 Push하지 않는다.


## 묶음 3 전체 게이트·CI 시간 예산

고정 checkout `ef25aa0353bd52fc2be681401356a741960af318`의 `whole-owners-group3-l3`에서
LLM 2,354/2,354와 Hub 586/586이 모두 pass/exit 0이었다. 각각 1,263.68초, 1,074.05초이며
Failure/Error/Skip/xfail/xpass/누락/추가/중복은 모두 0이다. source
`e116c7bf0d6cec7e9712dc39c176db18200e3a7ab9aed50fa66e5689dedfbd91`, input
`314f080fb70828aa150075d3665d2463824a327a7dbff954d6a808cda4674d77`는 전후 동일했다.
실제 명령은 `python -m tests.llm.run_regression --suite llm|hub --output /workspace/llm-clean-resume/b/whole-owners-group3-l3/<suite>.json`이며 각 suite를 독립 실행했다.
`group3-auxiliary/results.json`의 S1–S7(147건)·문서·R3·SDK·LLM/Hub 환경 검사는 각각 exit 0이다.
로컬 환경은 Python 3.12.14 Clang 22.1.3 및 위에 고정한 host다.

기존 Push `967f6dd`의 [원격 실행](https://github.com/E1der0wL/ish-llm/actions/runs/39901306599)은
Hub 586/586 success였으나 LLM Job이 **30분 제한 초과로 cancelled**였다. LLM의 최종 집계는
없으므로 성공으로 처리하지 않는다. GitHub annotation의 실제 원인은 `The job has exceeded the maximum execution time of 30m0s`다.
로그 archive 요청은 HTTP 403이었으며 성공 로그로 대체하지 않았다. 원격 상태·공개 summary·annotation을
`ci/967f6dd933f201232ef2d1896687a7f4b62415df/`에 보존했다.

`ae418a7fa073d4f477d5cd903c52292bb9243e7e`는 위 전체 통과 제품/시험에 CI deadline 30→60분과
[시간 예산 기록](ci-execution-budget.md)만 추가했다. 두 tree의 차이가 workflow와 새 문서뿐이고
제품·시험·fixture·의존성은 동일함을 확인했다. YAML 비교에서도 deadline 한 값 외 matrix/step/env/명령은
동일했다(`ci-budget-validation.json`). 이 SHA의 별도 checkout에서 S1–S7·문서가 exit 0이었다.
동일 제품에 대한 위 로컬 전체 결과를 제한적으로 재사용하며 새 로컬 전체 실행으로 합산하지 않는다.
`git push origin ae418a7fa073d4f477d5cd903c52292bb9243e7e:refs/heads/refactor/llm-clean` exit 0.
main/이전 브랜치 SHA는 이전 기록과 같으며 수정하지 않았다.
[새 정확한 SHA의 CI](https://github.com/E1der0wL/ish-llm/actions/runs/39905057801)는 현재 실행 중이다.
CI Python은 3.12.14 GCC 13.3.0으로 로컬 compiler 환경과 다르며 원격 결과를 별도로 확인한다.

## 재개·재시작·실행 진입 증거

`RunLink`/`ResumeRecord`/`RestartRecord`의 단일 Reader·생성·속성·자식 투영·직렬화를 연결했다.
RunRepository/RunManager, Tool 승인, Agent/중첩 Graph/Loop, 전송 lineage, Hub 이력/표시가
같은 Owner를 사용한다. 필수 원본 ID·digest·checkpoint·선택 목록 유실을 빈 선택으로 채우지 않는다.
원본 request와 opaque metadata를 보존하고 부분 출력 옵션의 생략/명시 false도 유지한다.
`RunExecutionEvidence`는 필드 부재를 unknown, 정확한 저장 false만 미실행으로 해석한다.
숫자/null을 false로 변환하지 않는다. `RestartEvidence`/`ResumePlanEvidence`는 기존 hash 입력과
중첩 원문 값을 보존하는 메모리 Projection이다. 추가 저장본이나 실행 권한을 만들지 않는다.

동일 Agent binding의 중복 raw 비교는 기존 `AgentBinding.same_request`로 일원화했다.
이 함수는 전체 검증 binding과 Missing/Null request context를 비교한다. 원본 상태·Step·digest·
현재 정책·승인·누적 예산·효과·Checkpoint 검사는 유지한다. [Checkpoint 계약](checkpoints.md)에
Owner와 누락 증거 처리를 추가했다.

첫 Hub 집중 검증은 기존 context fixture가 source ID만 가진 부분 resume라 35개 중 34 pass/1 error였다.
독립 wire helper로 긍정 fixture의 필수 필드를 명시했고 재실행 35개가 모두 통과했다.
기존 실패·효과·권한 Assertion은 유지했다. `test_owner_declarations`의 null resume 오류 종류는
우연한 AttributeError에서 Owner의 ValueError로 변경했으며 거부 Assertion은 유지했다.
손상 음성 fixture를 정상화하지 않았다. 새 6개 시험은 필수 필드 유실·null/숫자 거부·중첩 사본·
자식 투영·M/N/V·unknown 실행 진입·revision 원문 보존을 독립 기대값으로 검증한다.

| 실행 | 발견/실행/pass | Failure / Error / Skip | xfail·xpass·누락·추가·중복 | Exit |
|---|---|---|---|---|
| whole-continuation-01 | 267/267/267 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-continuation-hub-01 | 35/35/34 | 0 / 1 / 0 | 모두 0 | 1 |
| whole-continuation-hub-02 | 35/35/35 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-continuation-safety-03 | 148/148/148 | 0 / 0 / 0 | 모두 0 | 0 |

각 실행의 실제 명령과 source/input hash(각각 실행 전후 동일):

- `whole-continuation-01`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-continuation-01/llm.json --modules tests.llm.test_continuation_values tests.llm.test_owner_declarations tests.llm.test_checkpoint_lifecycle tests.llm.test_conversation_turns tests.llm.test_turn_deletion tests.llm.test_cross_session_requests tests.llm.test_cross_session_crash tests.llm.test_nested_graph tests.llm.test_graph_checkpoints tests.llm.test_extension_contracts tests.llm.test_interaction_decisions tests.llm.test_tool_constraints tests.llm.test_observability tests.llm.test_builtin_tools tests.llm.test_loop_steering`.
  source `d226106b9a94583b74098ef5d14c28ba9a74bc8397e6a4e5e64a9a3fde44a377`.
  input `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.

- `whole-continuation-hub-01`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-continuation-hub-01/hub.json --modules tests.hub.test_context tests.hub.test_approval_continuation tests.hub.test_request_history tests.hub.test_tool_history tests.hub.test_features`.
  source `d226106b9a94583b74098ef5d14c28ba9a74bc8397e6a4e5e64a9a3fde44a377`.
  input `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.

- `whole-continuation-hub-02`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-continuation-hub-02/hub.json --modules tests.hub.test_context tests.hub.test_approval_continuation tests.hub.test_request_history tests.hub.test_tool_history tests.hub.test_features`.
  source `d226106b9a94583b74098ef5d14c28ba9a74bc8397e6a4e5e64a9a3fde44a377`.
  input `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.

- `whole-continuation-safety-03`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-continuation-safety-03/llm.json --modules tests.llm.test_continuation_values tests.llm.test_backend_contracts tests.llm.test_steering_values tests.llm.test_agent_execution tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_transactions tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`.
  source `d226106b9a94583b74098ef5d14c28ba9a74bc8397e6a4e5e64a9a3fde44a377`.
  input `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.

초기 LLM 범위는 직접 Owner, 소유 선언, checkpoint 수명, 대화/turn 삭제, 전송/실제 중단,
중첩 Graph, Engine 확장, 승인/Tool 제약·실제 명령 효과·관측·Loop steering이다.
최종 안전성 범위는 실제 backend restart, Step 없는 custom Engine, Agent/추가 지시,
승인·예산 삭제·Tool 원장·Checkpoint·RAG·수명/삭제·트랜잭션·감사/inventory다.
Hub 범위는 context/승인 continuation/request history/Tool history/features다.
마지막 공통 digest 선언 정리 이후 최종 LLM 안전성을 실행했다. 다른 소스의 결과를 하나의
전체 실행으로 합산하지 않는다. 환경은 앞서 고정한 로컬 Python/host와 동일하다.

S1–S7 최초 검사는 변경된 근거 hash를 거절했다(exit 1). 51건의 개별 의존 근거를 실제 변경
의미별로 재검토했고 새 정적 발생은 없었다. 최종 `continuation-auxiliary/results.json`의
S1–S7(147건)·문서·R3·SDK·양 suite 환경 검사 모두 exit 0이다. 실제 명령은 위 묶음 3 보조
검사와 같고 output 디렉터리만 `continuation-auxiliary/`다. R3의 원래 실제 효과 단언을 유지했다.
기존 전송 파일 전체 검토는 presence API 변경 두 곳만 재대조했고 후보 목록은 동일했다.
새 continuation 파일의 후보 31개와 실제 소비자를 개별 분류했다. 전수 inventory는 448개 파일,
417개 미검토 파일/31,276개 미분류 후보다. 부분 파일의 상단·다른 함수까지 완료로 승인하지 않았다.
이 소단계는 Level 2 로컬 완료다. 현 통합 SHA에서 다음 전체 게이트를 수행하며 전체 리팩터링을 계속한다.


## 영속 도메인 Record·경로 선언

Project/Session/Message/Run/Step Reader가 자신의 dataclass slot 선언에서 키 이름을 파생한다.
공통 DomainRecord는 필드 목록·paths 제외·직렬화·정확한 현재 버전 검사를 담당한다.
기존 Project의 root record 선언이 공통 storage_version/paths 이름을 제공하며 Reader가
별도 문자열 목록을 재선언하지 않는다. 원래 모델 필드·생성자 순서·JSON 값은 변경하지 않았다.
Project.to_record가 설정과 Component identity 배열을 직렬화하고 전체 Reader로 검증한다.
ProjectRepository는 그 결과를 기존 트랜잭션/atomic Writer에 전달한다. RunIndex는 불변 정렬
Projection이며 선택한 원본의 전체 Reader 검증을 대체하지 않는다.

각 Paths 클래스의 파일명·디렉터리 요소를 메서드 앞 선언부에 모았다. retention의 Run 경로
검사도 SessionPaths를 사용한다. 구조적 state 경로를 주입하는 기존 RunPaths.output_path를
보존했다. 다른 workspace/Provider 로그 루트는 도메인 경로와 같은 철자만으로 합치지 않는다.
새 독립 시험은 기존 JSON shape·Component 수명·사본·버전 exact type·캐시 Projection과
전체 Reader 구분·기존 파일 배치를 검증한다. 원본 손상·누락·실제 삭제 안전성 시험도 유지한다.

첫 직접 smoke 명령에 존재하지 않는 tests.llm.test_storage를 지정해 ImportError/exit 1이었다.
11개 Owner 시험의 pass와 이 발견 오류를 정식 집중 통과로 합산하지 않았다. 실제 존재하는
persistence/repositories 모듈을 확인하여 아래 정식 실행을 수행했다.

| 실행 | 발견/실행/pass | Failure / Error / Skip | xfail·xpass·누락·추가·중복 | Exit |
|---|---|---|---|---|
| whole-domain-records-01 | 60/60/60 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-domain-records-safety-02 | 124/124/124 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-domain-records-audit-03 | 16/16/16 | 0 / 0 / 0 | 모두 0 | 0 |

실제 명령과 전후 동일한 hash:

- `whole-domain-records-01`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-domain-records-01/llm.json --modules tests.llm.test_domain_record_values tests.llm.test_owner_declarations tests.llm.test_persistence tests.llm.test_repositories tests.llm.test_lifecycle_integrity tests.llm.test_history_retention_integrity tests.llm.test_project_conversation_storage`.
  source `d226106b9a94583b74098ef5d14c28ba9a74bc8397e6a4e5e64a9a3fde44a377`.
  input `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.

- `whole-domain-records-safety-02`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-domain-records-safety-02/llm.json --modules tests.llm.test_domain_record_values tests.llm.test_repositories tests.llm.test_lifecycle_integrity tests.llm.test_history_retention_integrity tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_deletion_integrity tests.llm.test_transactions tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`.
  source `d226106b9a94583b74098ef5d14c28ba9a74bc8397e6a4e5e64a9a3fde44a377`.
  input `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.

- `whole-domain-records-audit-03`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-domain-records-audit-03/llm.json --modules tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`.
  source `d226106b9a94583b74098ef5d14c28ba9a74bc8397e6a4e5e64a9a3fde44a377`.
  input `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.

집중 범위는 Owner 선언·영속 모델·Repository 주입·Component 수명·retention·Project 대화 저장소다.
안전성 범위에는 승인·누적 예산 삭제·Tool 효과·Checkpoint·RAG·부모 삭제·트랜잭션·감사를 포함했다.
로컬 환경은 앞서 고정한 Python 3.12.14 Clang 22.1.3 및 host SHA다. 원본 파일 효과와 장애
주입을 포함했다. 세 실행을 하나의 전체 회귀로 표현하지 않는다.

정적 최초 검사는 변경된 소스 근거와 바뀐 발생을 거절했다. 30건 의존 근거를 의미별 재검토하고,
더는 존재하지 않는 3개 발생의 이전 근거는 외부 로그에 보존했다. 새 S4는 필드 집합 membership이며,
실제 값 검사는 DomainRecord.validate_version의 exact int→현재 값 비교다. 최초 새 검토의 owner
이름이 실제 함수 심볼과 달라 보조 static이 exit 1이었다. owner를 _stored_fields로 바로잡은
`domain-records-static-03.json`은 145건 검토, new/stale/outstanding/error 0, exit 0이다.
정적 수정 뒤 감사/inventory 16개도 다시 통과했다. 제품 코드는 안전성 실행과 동일하다.
`domain-records-auxiliary/results.json`에서 문서·R3·SDK·LLM/Hub 환경 검사는 각각 exit 0이다.
명령은 위 보조 게이트와 같고 디렉터리만 domain-records-auxiliary다. 최초 static 실패는 보존했다.

Paths 파일 후보 29개와 실제 소비 경로만 전수 완료로 분류했다. 전체 inventory는 448개 파일,
416개 미검토 파일/31,186개 미분류 후보다. models.py의 설정·metadata 등 남은 작업을 전체
파일 검토로 승인하지 않았다. 이 소단계는 Level 2이며 다음 관련 통합 게이트에서 전체 검증한다.
현재 2f13412에서 실행 중인 전체 게이트는 그 뒤의 이 변경을 포함하지 않는다.


## 묶음 4 전체 게이트

고정 SHA `2f134127c502cf761d032b66aa43234b1a729858`의 `whole-owners-group4-l3`에서
LLM 2,360/2,360(1,278.67초), Hub 586/586(1,082.02초)이 pass/exit 0이었다.
Failure/Error/Skip/xfail/xpass/누락/추가/중복 모두 0이다. source
`954b55e6b82c8c93f41c8dc0e6eec299b3ca652505a4f528ba7af96fcae7a088`, input
`9e81c1d29e3986813c0692820459d6a79cde8ba6d688e825d68bbe6fca286441`는 전후 동일했다.
각 suite의 실제 명령은 `python -m tests.llm.run_regression --suite llm|hub --output /workspace/llm-clean-resume/b/whole-owners-group4-l3/<suite>.json`이다.
`group4-auxiliary/results.json`의 정적 S1–S7(147건), 문서, R3, SDK, 양 suite 환경은 각각 exit 0.
로컬 Python/host/실제 namespace 환경은 앞서 고정한 것과 같다. 이 SHA 이후 도메인 Record와
설정 변경은 이 전체 게이트에 포함하지 않는다. 아직 이 SHA의 원격 성공으로 보고하지 않는다.

## 공통 설정 조회·출처·편집 가능성

`configuration_views.py`의 ConfigurationView/Resolution, Override, Engine key, runtime
관측 타입이 공통 고정 구조를 소유한다. Resolver는 Owner로 직렬화하고 Engine/Component,
Agent/registry의 동적 resolver, Hub 모델 선택·Tool 인자 재시도·설정 페이지/위젯은 검증된
속성을 소비한다. 같은 관측을 여러 번 파싱하던 Loop/Hub 경로도 작업 내부에서 재사용한다.
ConfigurationSource/Pointer가 출처 이름과 경로 인코딩을 공유한다. RAG 확장 관측과 실제
구현별 config/policy 해석은 해당 Owner에 남으며 이 구간의 전체 완료로 승인하지 않는다.

선택적 query 관측 부재는 독립된 빈 조회값을 제공하되 원래 JSON의 생략 상태는 보존한다.
필수 저장 증거의 누락을 이 규칙으로 복원하지 않는다. 명시적 null/false, 열린 SDK 옵션,
원본 Project/Session/Agent/host 값과 unknown extension을 보존한다. 새 5개 독립 시험은
wire·JSON pointer escaping·override 원문·중첩 사본·생략·잘못된 타입 거부·runtime query가
원본 설정을 수정하지 않음을 검증한다. [현재 설정 감사](configuration-audit.md)에 반영했다.

`whole-configuration-01`은 존재하지 않는 tests.llm.test_graph 지정으로 발견 단계 exit 2였다.
발견/실행/pass 수를 성공 집계하지 않았다. 실제 모듈 목록을 확인하여 nested_graph를 지정했고,
첫 정상 LLM 179개/Hub 29개를 통과했다. 이후 위젯 소비자와 resolver 식별자/Owner 모듈 분리를
추가한 최종 통합 소스에서 안전성 및 Hub를 다시 검증했다.

| 실행 | 발견/실행/pass | Failure / Error / Skip | xfail·xpass·누락·추가·중복 | Exit |
|---|---|---|---|---|
| whole-configuration-02 | 179/179/179 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-configuration-hub-01 | 29/29/29 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-configuration-safety-03 | 174/174/174 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-configuration-hub-02 | 37/37/37 | 0 / 0 / 0 | 모두 0 | 0 |

실제 명령과 각 실행 전후 동일한 source/input:

- `whole-configuration-02`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-configuration-02/llm.json --modules tests.llm.test_configuration_values tests.llm.test_configuration_validation tests.llm.test_configuration_objects tests.llm.test_configuration_results tests.llm.test_configuration_workflow_example tests.llm.test_settings_contract tests.llm.test_settings_consistency tests.llm.test_explicit_configuration tests.llm.test_project_component_settings tests.llm.test_rag_models tests.llm.test_rag_retry_presence tests.llm.test_loop tests.llm.test_nested_graph`.
  source `d226106b9a94583b74098ef5d14c28ba9a74bc8397e6a4e5e64a9a3fde44a377`.
  input `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.

- `whole-configuration-hub-01`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-configuration-hub-01/hub.json --modules tests.hub.test_settings tests.hub.test_settings_project_flow tests.hub.test_tool_retry tests.hub.test_live`.
  source `d226106b9a94583b74098ef5d14c28ba9a74bc8397e6a4e5e64a9a3fde44a377`.
  input `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.

- `whole-configuration-safety-03`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-configuration-safety-03/llm.json --modules tests.llm.test_configuration_values tests.llm.test_configuration_validation tests.llm.test_settings_contract tests.llm.test_settings_consistency tests.llm.test_explicit_configuration tests.llm.test_agent_execution tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_transactions tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`.
  source `d226106b9a94583b74098ef5d14c28ba9a74bc8397e6a4e5e64a9a3fde44a377`.
  input `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.

- `whole-configuration-hub-02`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-configuration-hub-02/hub.json --modules tests.hub.test_settings tests.hub.test_settings_project_flow tests.hub.test_settings_json tests.hub.test_settings_usability tests.hub.test_tool_retry tests.hub.test_live`.
  source `d226106b9a94583b74098ef5d14c28ba9a74bc8397e6a4e5e64a9a3fde44a377`.
  input `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.

직접 범위는 설정 객체·설명·저장/복원·workflow 예제·명시 옵션·Component·RAG 모델/retry·
Loop/중첩 Graph이고, 최종 안전성은 설정 narrowing/host 고정값·Agent·승인·예산 삭제·효과·
Checkpoint·RAG·수명/삭제·트랜잭션·감사다. Hub는 settings/project flow/JSON 편집/사용성,
Tool 재시도와 실제 Backend live 소비자다. 환경은 앞서 고정한 로컬 Python/host와 동일하다.
다른 소스의 통과를 하나의 전체 실행으로 합산하지 않는다.

정적 검사는 변경된 근거 hash를 먼저 거절했다. 24건 의존 근거를 실제 의미별로 재검토했으며
새 정적 발생은 없다. 최종 `configuration-auxiliary/results.json`에서 S1–S7(145건,
new/stale/outstanding/error 0), 문서, R3, SDK, 양 suite 환경 모두 exit 0이다. 실제 명령은
위 보조 게이트와 같고 output 디렉터리만 configuration-auxiliary다. 원래 실제 효과/실패
Assertion을 제거하지 않았다. query Owner 파일 후보 18개와 실제 동적 소비 경로만 개별
완료로 분류했다. 전수 inventory는 449개 파일, 416개 미검토 파일/31,113개 미분류 후보다.
이 소단계는 Level 2 완료이며 도메인 Record·후속 공통 검증 변경과 다음 전체 게이트로 묶는다.


## 묶음 3 원격 성공·묶음 4 Push

`ae418a7fa073d4f477d5cd903c52292bb9243e7e`의
[원격 CI](https://github.com/E1der0wL/ish-llm/actions/runs/39905057801)를 2026-10-23 05:53:36 UTC에
다시 조회했다. 정확한 head SHA와 두 필수 Job의 모든 필수 step success를 확인했다.
LLM 2,354/2,354(1,735.80초), Hub 586/586(1,386.94초), Failure/Error/Skip/xfail/xpass/누락/추가/중복 0.
정적·문서·R3·SDK·환경·증거 보존 step도 실제 success다. 증거는
`ci/ae418a7fa073d4f477d5cd903c52292bb9243e7e/status.json`이며 verified=true다.
원격 Python은 GCC 13.3.0 빌드로 로컬 Clang 환경과 구별한다. 60분 deadline 조정 후 전체 Job이
완료됐으며 기존 필수 검사·실행 범위를 줄이지 않았다. 앞선 967f6dd의 cancelled 기록은 유지한다.

원격 세 브랜치 SHA를 다시 확인했고 main/이전 브랜치는 원래 값과 동일했다.
`git push origin 2f134127c502cf761d032b66aa43234b1a729858:refs/heads/refactor/llm-clean`이 exit 0이었다.
이는 위 묶음 4 전체 통과 SHA다. 그 뒤 Record/설정/JSON 변경은 이 Push에 포함하지 않는다.
새 SHA의 원격 CI는 별도 확인하며 ae418a7의 원격 통과를 승계하지 않는다.

## 열린 JSON 문법 검증 책임

`JsonObject.validate`가 JSON 객체/문자열 key/재귀 값/유한 숫자를 검증한다. ProjectConfig는
Project 설정 의미를 소유하고 기존 공개 validate_json은 단일 JsonObject 구현으로 위임한다.
36개 직접 소비자에서 JSON 문법 검사를 위한 ProjectConfig 의존을 제거했다. 승인·효과·예약·
상태·필수 필드·version/revision·Component 정책 검증은 해당 Owner에 남는다.
[JSON 책임 계약](json-data.md)에 경계를 명시했다. 값 변환·누락 필드 추가·두 번째 Reader는 없다.

`json-ownership-equivalence.json`은 최종 36개 소비자의 실행 AST가 validator 호출/import만
정규화했을 때 동일함을 기록한다. 기존 import의 함수/클래스 scope도 각각 동일하다.
JsonObject 본문은 선언 참조를 펼치면 기존 ProjectConfig.validate_json 본문과 AST가 동일하다.
따라서 오류 종류·문구·JSON scalar·유한 숫자·unknown key 처리도 그대로다. 특히 worker는
sys.path/protocol FD 설정 뒤의 원래 지연 import를 유지했다. 새 독립 4개 시험은 원문/수치 타입,
잘못된 컨테이너/key/value/NaN/무한대, 공개 위임 API, JSON 유효성과 도메인 필수 증거의 구분을 검증한다.

| 실행 | 발견/실행/pass | Failure / Error / Skip | xfail·xpass·누락·추가·중복 | Exit |
|---|---|---|---|---|
| whole-json-01 | 108/108/108 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-json-safety-02 | 129/129/129 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-json-hub-01 | 13/13/13 | 0 / 0 / 0 | 모두 0 | 0 |

실제 명령과 각각 실행 전후 동일한 source/input:

- `whole-json-01`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-json-01/llm.json --modules tests.llm.test_json_data tests.llm.test_contracts tests.llm.test_typed_contracts tests.llm.test_configuration_values tests.llm.test_configuration_objects tests.llm.test_tool_workers tests.llm.test_isolated_tools tests.llm.test_tool_constraints tests.llm.test_vision tests.llm.test_continuation_values tests.llm.test_request_values tests.llm.test_transfer_values`.
  source `d226106b9a94583b74098ef5d14c28ba9a74bc8397e6a4e5e64a9a3fde44a377`.
  input `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.

- `whole-json-safety-02`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-json-safety-02/llm.json --modules tests.llm.test_json_data tests.llm.test_completion_processing tests.llm.test_memory_processing_config tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_transactions tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`.
  source `d226106b9a94583b74098ef5d14c28ba9a74bc8397e6a4e5e64a9a3fde44a377`.
  input `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.

- `whole-json-hub-01`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-json-hub-01/hub.json --modules tests.hub.test_history_retention tests.hub.test_settings_project_flow tests.hub.test_settings_json`.
  source `d226106b9a94583b74098ef5d14c28ba9a74bc8397e6a4e5e64a9a3fde44a377`.
  input `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.

직접 범위는 공통 typed contract/설정 객체, 실제 격리 Tool worker/인자 제약, Vision, 재개·접수·전송이다.
최종 안전성은 Completion/Memory 처리, 승인·예산 삭제·효과·Checkpoint·RAG·수명/부모 삭제·
트랜잭션·정적 감사/inventory다. Hub 직접 변경은 설정 저장/JSON 편집과 history retention이다.
로컬 Python/host/namespace 환경은 앞서 고정한 것과 같다. 실제 파일 효과·장애 주입 Assertion을
그대로 유지했다. 서로 다른 실행의 pass를 하나의 전체 실행으로 합산하지 않는다.

최초 정적 검사는 변경된 source hash를 거절했다. 위 동일성 증거와 각 실제 호출을 검토하여
80건 의존 근거를 갱신했다. 새 정적 발생은 없고 `json-auxiliary/results.json`의 S1–S7(145건,
new/stale/outstanding/error 0), 문서, R3, SDK, 양 suite 환경이 각각 exit 0이었다.
실제 보조 명령은 위와 동일하고 output 디렉터리만 json-auxiliary다. 기존 전수 검토 4개 파일은
동일 literal 후보와 변경된 validator 호출만 재검토했다. 그 외 변경 파일 전체를 완료로 승인하지 않았다.
새 JSON Owner 후보 6개를 분류했다. 현재 inventory는 450개 파일, 416개 미검토 파일,
31,108개 미분류 후보다. 이 구간은 Level 2 완료이며 도메인 Record·설정·JSON의 현 통합 SHA를
묶은 Level 3를 수행한다. 전수 소유권 작업은 계속 진행한다.

## 재개·재시작 SHA 원격 게이트와 묶음 5 전체 게이트

`2f134127c502cf761d032b66aa43234b1a729858`의
[원격 CI](https://github.com/E1der0wL/ish-llm/actions/runs/39908324282)를
2026-10-23 06:27:09 UTC에 실제 확인했다. LLM 2,360/2,360/2,360, Hub 586/586/586,
모든 실패·오류·skip·xfail/xpass·누락·추가·중복 0, 양 Job 및 필수 정적·문서·R3·SDK·환경·
증거 단계 success였다. 원격 Python 3.12.14 GCC 13.3.0과 로컬 Clang 환경은 구분한다.
증거는 `whole-ownership/ci/2f134127c502cf761d032b66aa43234b1a729858/status.json`이다.

도메인 Record·설정 조회·JSON 문법 Owner를 묶은 현재 SHA
`d43c82a5ee619f7b99d49bf6a4de05533725a4bb`는 별도 고정 checkout
`/workspace/ish-llm-group5-validation`에서 Level 3를 실행했다.
`/workspace/ish-llm/.venv-linux312/bin/python /workspace/llm-clean-resume/whole-ownership/gate.py whole-owners-group5-l3`
의 실제 각 suite 명령과 결과:

- `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-owners-group5-l3/llm.json`: 2374/2374/2374, 모든 bad count 0, exit 0, 1346.719초.
- `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-owners-group5-l3/hub.json`: 586/586/586, 모든 bad count 0, exit 0, 1089.120초.
  source `9004813735d6f464bf53b4dadade018a5746cc596413f0be745c8951e2f2e853` 전후 동일.
  input `ba47fc9c9a86cc7ea7a33f229c9673be82f57da4ffab29cadbcc9e072e1a2ae5` 전후 동일.

동일 SHA의 `group5-auxiliary/results.json`에서 S1–S7 145건(new/stale/outstanding/error 0),
문서, R3, Provider SDK, LLM/Hub 실제 환경 검사가 각각 exit 0이었다. Python·고정 host·
namespace/X11은 앞서 명시한 로컬 환경과 같다. `d43c82a`를 작업 브랜치에 Push했다.
그 SHA의 원격 CI는 이 기록 시점에 아직 확인 중이며 전체 작업 완료로 선언하지 않는다.
main과 refactor/llm-backend의 원격 SHA는 기존 값과 동일했다. Draft PR #1은 변경하지 않았다.

## Schema·ParameterLayout·구현체 외형 Owner

`SchemaKeyword/Type/Boolean/Extension/Traversal/Reference/Narrowing`의 선언에서 JSON Schema
구조·탐색·참조·narrowing 문법을 관리한다. `ImplementationParameters`가 config/policy의
공통 외형을 읽고 `ParameterLookup`이 경로 존재와 값을 구분한다. `ParameterLayout`은
이 Owner들을 사용하며 원본 설정·SDK 옵션·명시적 null/false/0·부분 확장·정책 우회 거부를
보존한다. Component별 중첩 정책과 Agent policy/Project config는 서로 합치지 않았다.

`JsonPointer`는 설정 관측 경로·Schema reference 작성·Workflow 엄격 binding 파서를 소유한다.
관측 경로의 기존 해석과 영속 binding의 엄격 검증을 구분하고, 루트·빈 키·이스케이프·누락의
기존 의미를 독립 시험으로 검증했다. 외부 참조·암묵적 default·안전하게 재배치할 수 없는
제약·지원하지 않는 narrowing 선언은 계속 거부한다. Schema의 annotation/const 데이터는
구조로 오인하지 않는다. Schema 함수 11개의 선언 확장 AST가 기존과 동일했고, 나머지 두
함수는 같은 envelope 검증 위임 및 동일 오류 template 이동이다. 비교 증거는
`schema-draft/schema-ast-comparison.json`과 각 차이 파일에 보존한다.

실패 이력: `whole-parameters-01`/`whole-parameters-hub-01`은 모듈 이름에 tests.llm/tests.hub
prefix를 빠뜨려 discovery exit 2, summary incomplete였다. 통과로 집계하지 않았다.
해당 모듈들은 실제 존재하며 올바른 qualified 이름으로 아래 검증을 다시 실행했다.
간이 unit 실행 23개는 모두 통과했으나 공식 발견·누락 검사를 대신하지 않는다.

| 실행 | 발견/시작/통과 | Failure / Error / Skip | xfail/xpass·누락/추가/중복 | exit |
|---|---:|---|---|---:|
| whole-parameters-02 | 194/194/194 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-parameters-hub-02 | 38/38/38 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-parameters-safety-03 | 189/189/189 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-parameters-hub-03 | 8/8/8 | 0 / 0 / 0 | 모두 0 | 0 |

실제 명령과 각 실행 전후 동일한 소스/입력:

- `whole-parameters-02`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-parameters-02/llm.json --modules tests.llm.test_parameter_values tests.llm.test_settings_contract tests.llm.test_configuration_values tests.llm.test_configuration_validation tests.llm.test_configuration_objects tests.llm.test_configuration_results tests.llm.test_settings_consistency tests.llm.test_explicit_configuration tests.llm.test_schema_ownership tests.llm.test_workflow_format tests.llm.test_agent_workflow tests.llm.test_goals tests.llm.test_refinement tests.llm.test_memory_processing_config tests.llm.test_mcp_runtime tests.llm.test_vision tests.llm.test_project_component_settings`.
  source `a68d25e28d613f08095104747d234fb90b4236c7e142d4b775f3170b5daa2b7e`.
  input `16aa13e1685755ac0b53732a3028e45762b70f4a1dac66c8d937f01a6d82c003`.
- `whole-parameters-hub-02`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-parameters-hub-02/hub.json --modules tests.hub.test_settings tests.hub.test_settings_json tests.hub.test_settings_project_flow tests.hub.test_tool_retry tests.hub.test_rag_extensions tests.hub.test_rag_defaults`.
  source `a68d25e28d613f08095104747d234fb90b4236c7e142d4b775f3170b5daa2b7e`.
  input `16aa13e1685755ac0b53732a3028e45762b70f4a1dac66c8d937f01a6d82c003`.
- `whole-parameters-safety-03`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-parameters-safety-03/llm.json --modules tests.llm.test_parameter_values tests.llm.test_goals tests.llm.test_refinement tests.llm.test_memory_processing tests.llm.test_rag_components tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_transactions tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`.
  source `f41f286af9402e335d2d609a0ad7879772065a71d69852f6e61b3b5b719ea77b`.
  input `c289503815d4d1c2f477c7b8675cc1ce7a990f3c96981e096ae5ac8a020d3d7b`.
- `whole-parameters-hub-03`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-parameters-hub-03/hub.json --modules tests.hub.test_naming tests.hub.test_resource_tools`.
  source `f41f286af9402e335d2d609a0ad7879772065a71d69852f6e61b3b5b719ea77b`.
  input `c289503815d4d1c2f477c7b8675cc1ce7a990f3c96981e096ae5ac8a020d3d7b`.

첫 집중 실행 후 D14 용어 일관성을 위해 세 메서드의 지역 변수 settings를 parameters로
변경했다. `schema-draft/local-name-proof.json`에서 alpha-equivalent AST를 확인했다.
그 외 제품·시험 변경 없이 해당 첫 집중 결과를 제한적으로 재사용하며, 최종 소스에서는
새 Owner·Goal·Refinement·Memory·RAG와 승인·예산·효과·Checkpoint·원본 보존·삭제·트랜잭션·
정적 감사/inventory의 안전성 집합을 실행했다. Hub는 설정 UI·JSON·프로젝트 저장·재시도·
RAG 설정에 더해 자동 제목과 리소스 Tool 실제 경로를 검증했다. 다른 실행을 하나의 전체
실행으로 합산하지 않는다. 환경·고정 의존성·host·실제 namespace/X11은 동일하게 유지했다.

정적 검사는 변경된 근거 hash를 먼저 거절했다. 실제 호출과 AST/시험을 대조해 83건의
의존 근거를 재검토했고, 기존 exact-True 정책 비교 두 발생은 새 envelope 접근의 ID로
이동했다. 기존 명시적 허가·평가 필요 조건과 Assertion은 유지했다. S1–S7은 145건,
new/stale/outstanding/error 0이다. `parameters-auxiliary/results.json`의 정적·문서·R3·SDK·
양 suite 환경 6개 검사는 모두 exit 0이며 각 실제 명령을 같은 파일에 보존했다.

현재 inventory는 제품 451개, 미검토 파일 414개, 미분류 후보 30,804개다. Schema 어휘와
구현체 외형의 남은 소비 경로 두 계약은 명시적으로 incomplete이며 dynamic 검토도 완료로
표시하지 않았다. 선언 후보 분류가 전체 소비자 완료를 뜻하지 않는다. 이 구간은 Level 2로
보존하고, 이어 Project/Session 설정 대상 및 Schema 소비자 전환과 묶어 현 통합 소스의
Level 3를 수행한다. 전체 게이트는 실행 시점을 묶었으며 필수 안전성 기준을 낮추지 않았다.

## Project/Session 설정 대상과 변경 경계

앞선 Schema/envelope 소단계는 `cb52b83`으로 보존했다. 이어 `ParameterTargets`,
`SessionConfiguration`, `ResolvedEngineConfiguration`을 도입해 Project/Session 대상 선택과
공통 외형의 생성·조회·변경을 연결했다. ProjectConfig가 section 선언·갱신·저장 전 검증을
소유하며, ComponentData/ProjectManager는 잠금·CAS·수명·삭제 안전성·저장 순서를 유지한다.
대상 이름은 열린 mapping이고 각 envelope의 내부 의미는 선택 구현체가 소유한다.
필수 Project 저장 section과 실행의 engine 선택 필드는 누락 시 계속 오류다.

Engine/Component registry·설정 resolve·Component 설정/영구 삭제·실제 CLI와 Hub 생성,
부트스트랩, preset, 제목 Session, 설정 폼, JSON 편집/내보내기를 같은 경계로 전환했다.
`ProjectFormSchemas`는 일반·Engine·Component Schema를 독립 사본으로 투영한다.
`ProjectConfigForm`은 동일 필드 선언을 가진 slots dataclass가 되어 직렬화 이름과 경로가
그 선언을 참조한다. 공개 JSON과 사용자 값·explicit null·host 고정값·생략 상태는 유지한다.

JSON 내보내기 redaction은 편집 중 원본의 모양을 보존하며 저장 검증을 대신하지 않는다.
숨겨진 제목 설정은 기존과 같이 부모 Schema 검증 뒤에 복원한다. Project/Session 정책
권한 범위, Component 원본/영수증 삭제 보호, Hub가 명시하던 생성 기본값과 사용자 우선
순위는 바꾸지 않았다. 다른 의미의 Tool.parameters/Agent catalog engines와 합치지 않았다.

`project-config-draft/config-equivalence.json`에서 이전 cb52b83의 ProjectConfig를 독립
namespace로 로드해 생성·Session 검증·Engine 선택 5,040개 조합의 결과와 오류 타입/문구가
현재 구현과 일치함을 확인했다. 이는 실제 저장·실행 안전성 검증의 대체가 아니다.
새 독립 시험은 대상 존재/빈 값/사본, 잘못된 변경의 원본 보존, Session 권한, 실제 wire,
필수 선택 필드 누락, 편집 redaction의 비승인 성격, Schema 투영 사본·경로를 검사한다.

개발 중 smoke-05는 Graph의 새 ProjectConfig 상수 참조에 import를 빠뜨려 NameError로
실패했다. import를 추가한 smoke-06 이후와 아래 최종 공식 검증은 통과했다. 이 실패를
성공으로 집계하지 않았다. 최초 migration helper의 잘못된 소스 경계 탐색도 외부 로그에
보존했으며 해당 초기 smoke 결과를 공식 검증 근거로 사용하지 않았다.

| 실행 | 발견/시작/통과 | Failure / Error / Skip | xfail/xpass·누락/추가/중복 | exit |
|---|---:|---|---|---:|
| whole-config-targets-01 | 126/126/126 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-config-targets-hub-01 | 32/32/32 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-config-targets-02 | 175/175/175 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-config-targets-hub-02 | 42/42/42 | 0 / 0 / 0 | 모두 0 | 0 |

실제 명령과 실행별 전후 동일한 source/input:

- `whole-config-targets-01`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-config-targets-01/llm.json --modules tests.llm.test_configuration_targets tests.llm.test_configuration_values tests.llm.test_configuration_objects tests.llm.test_configuration_results tests.llm.test_configuration_validation tests.llm.test_settings_contract tests.llm.test_settings_consistency tests.llm.test_explicit_configuration tests.llm.test_project_component_settings tests.llm.test_component_storage_integrity tests.llm.test_schema_ownership`.
  source `955bba2f4908c1d3cea401a679e1b878a7a526351fe3da1ea913e5184f9b5bb4`.
  input `e3e6cc34b5eee02dcf246809adf5b3748ad591434b98fdfa2d5ca59311d5ecf7`.
- `whole-config-targets-hub-01`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-config-targets-hub-01/hub.json --modules tests.hub.test_presets_widgets tests.hub.test_settings_project_flow tests.hub.test_settings_json tests.hub.test_settings tests.hub.test_naming`.
  source `955bba2f4908c1d3cea401a679e1b878a7a526351fe3da1ea913e5184f9b5bb4`.
  input `e3e6cc34b5eee02dcf246809adf5b3748ad591434b98fdfa2d5ca59311d5ecf7`.
- `whole-config-targets-02`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-config-targets-02/llm.json --modules tests.llm.test_configuration_targets tests.llm.test_parameter_values tests.llm.test_settings_contract tests.llm.test_configuration_validation tests.llm.test_project_component_settings tests.llm.test_graph_engine tests.llm.test_graph_agent_contract tests.llm.test_linux_platform tests.llm.test_plugin tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_transactions tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`.
  source `dae8fa0f05db4114c8c616882aa069fae17f1a6c0c139a8a45d05bc0a779825f`.
  input `711553057c75c0fdf5b1d79d7fcb6c00442af0d6b3e565f7728737c73b7b2fb9`.
- `whole-config-targets-hub-02`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-config-targets-hub-02/hub.json --modules tests.hub.test_presets_widgets tests.hub.test_settings_project_flow tests.hub.test_settings_json tests.hub.test_settings tests.hub.test_naming tests.hub.test_bootstrap tests.hub.test_defaults tests.hub.test_rag_defaults`.
  source `dae8fa0f05db4114c8c616882aa069fae17f1a6c0c139a8a45d05bc0a779825f`.
  input `711553057c75c0fdf5b1d79d7fcb6c00442af0d6b3e565f7728737c73b7b2fb9`.

01은 첫 대상 전환 소스, 02는 추가 Hub 생성/JSON 복원/폼/제목 및 실제 CLI까지 포함한
최종 소스다. 서로 다른 실행을 전체 1회로 합산하지 않는다. 최종 LLM은 새 Owner와 설정,
Graph·CLI 및 승인·예산 삭제·효과·Checkpoint·RAG·수명/부모 삭제·트랜잭션·감사/inventory를
포함한다. 최종 Hub는 preset·설정 UI/JSON/Project 저장·제목·bootstrap·생성/RAG 기본값을
검증했다. 실제 파일 효과와 장애 주입 Assertion을 유지했다. Python 3.12.14 Clang, 고정 host
및 namespace/X11·의존성은 이전 로컬 환경과 같고 각 입력 manifest에 보존했다.

변경된 정적 근거 84건을 호출 경계와 위 결과/동일성 근거로 재검토했다. 새 정적 발생은 없다.
`config-targets-auxiliary/results.json`의 S1–S7 145건(new/stale/outstanding/error 0), 문서,
R3 Mutation, Provider SDK, 양 suite 실제 환경 6개 검사는 각각 exit 0이며 실제 명령도 그
파일에 보존했다. 전체 검토한 새 target 파일은 후보 21개를 개별 분류했고, 기존 envelope
파일의 변경 API 추가는 같은 후보임을 대조한 뒤 hash를 갱신했다.

현재 inventory는 452개 제품 파일, 414개 미검토 파일, 30,679개 미분류 후보다. 공통 envelope와
Schema 어휘의 남은 소비 경로 두 계약은 여전히 incomplete다. 이 Level 2 커밋은 전수 완료가
아니며, cb52b83과 현재 통합 SHA를 묶은 Level 3를 다음 고정 checkout에서 실행한다.
원격 d43c82a CI는 이 기록 시점에 실행 중이며 아직 성공으로 선언하지 않았다.

## 묶음 5 원격 CI 확인

`d43c82a5ee619f7b99d49bf6a4de05533725a4bb`의
[원격 CI](https://github.com/E1der0wL/ish-llm/actions/runs/39911703976)를
2026-10-23 06:59:35 UTC에 실제 확인했다. LLM 2,374/2,374/2,374(1,557.354초),
Hub 586/586/586(1,235.949초), 모든 failure/error/skip/xfail/xpass/누락/추가/중복 0,
양 Job과 필수 보조 단계 success다. 증거는
`whole-ownership/ci/d43c82a5ee619f7b99d49bf6a4de05533725a4bb/status.json`이다.
원격 GCC 환경은 로컬 Clang 환경과 구분한다. 후속 설정 대상은 `8136897`으로 커밋했고
고정 checkout `/workspace/ish-llm-group6-validation`에서 별도 Level 3를 실행 중이다.
그 전체 결과는 아직 완료로 기록하지 않는다.

## 오류 관측의 단일 분류와 소비자 중복 제거

`CodedError.read_code`가 명시적 분류 권한과 유효한 code 판정을 소유하고,
`ErrorObservation`이 선택적 속성 실패 처리·관측 없음·기존 32개 chain 상한을 선언한다.
`stable_error_code`의 바깥쪽 우선/cause/context/순환 차단은 유지한다. 일반 관측 예외는
원래 실패를 대체하지 않고, 취소 등 BaseException은 계속 전파한다.

검토 중 중복 소비의 실제 결함 두 건을 독립 재현했다.

- Provider는 공통 분류 뒤 code getter를 다시 읽어, 권한 없는 SDK 예외의 getter가 실패하면
  분류 자체가 깨졌다. trusted getter가 첫 관측 뒤 바뀌면 이미 선택한 관측을 다시 해석했다.
- 실제 Linux Tool worker는 분기 조건과 전송 레코드에서 같은 예외의 code를 두 번 관측해
  첫 코드 observed_1 대신 observed_2를 전송했다.

`error-observation-draft/repro.log`의 정확한 unittest 대상은
`tests.llm.test_provider_diagnostics.DiagnosticTests.test_provider_classification_reads_trusted_code_once_and_ignores_vendor_code_getter`
및 `tests.llm.test_tool_workers.WorkerTests.test_coded_error_is_observed_once_across_real_worker_boundary`다.
실제 명령은 `.venv-linux312/bin/python -m unittest <위 두 ID>`이며 exit 1, 실행 2개,
subtest를 포함한 failure event 3개, error/skip 0이었다. 해당 Provider/worker 소비자 소스는
8136897과 byte-identical임을 `repro-evidence.json`에 기록했고 전체 소스/입력 manifest도
`repro-source.json`/`repro-input.json`에 보존했다. 기존 오류 Owner를 정리하던 중 재현한
결과이며 별도 과거 SHA에서 실행한 전체 테스트로 표현하지 않는다.

Provider의 중복 getter/trust 검사를 제거하고 Tool worker는 한 번 선택한 코드를 재사용했다.
동일 재현 두 개와 기존 FailureContractTests를 포함한 `repro-fixed.log`는 6개 pass, exit 0이다.
기존 HTTP/timeout/연결 분류, ToolExecutionError 효과·재시도·복구 가능성, 미분류 오류의
불확실한 효과, 프로세스 취소/정리와 원본 실패 Assertion은 유지했다. 신규 시험은 취소를
삼키지 않고 untrusted getter를 실행 분류용으로 읽지 않는 것까지 검증한다.
계약 설명은 [오류 관측](error-observations.md)에 정리했다.

| 실행 | 발견/시작/통과 | Failure / Error / Skip | xfail/xpass·누락/추가/중복 | exit |
|---|---:|---|---|---:|
| whole-error-observation-01 | 224/224/224 | 0 / 0 / 0 | 모두 0 | 0 |
| whole-error-observation-hub-01 | 6/6/6 | 0 / 0 / 0 | 모두 0 | 0 |

실제 명령 및 각 실행 전후 동일한 source/input:

- `whole-error-observation-01`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-error-observation-01/llm.json --modules tests.llm.test_error_boundaries tests.llm.test_execution_boundaries tests.llm.test_backend_contracts tests.llm.test_provider_diagnostics tests.llm.test_provider_runtime tests.llm.test_tool_workers tests.llm.test_model_protocol tests.llm.test_rag_models tests.llm.test_graph_agent_contract tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_transactions tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`.
  source `8f3e5088c5d0d67ab3e91fe619ec4002887260c7a3b08425a8306caba914a30d`.
  input `e363fd261cf3ea483c4cfc436875008c88f930579a9689b82e0cb27d6cb0f23c`.
- `whole-error-observation-hub-01`: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-error-observation-hub-01/hub.json --modules tests.hub.test_tool_history tests.hub.test_page_layout`.
  source `8f3e5088c5d0d67ab3e91fe619ec4002887260c7a3b08425a8306caba914a30d`.
  input `e363fd261cf3ea483c4cfc436875008c88f930579a9689b82e0cb27d6cb0f23c`.

LLM 범위는 실제 오류/실행 경계, Provider/SDK, 실제 격리 worker, 모델 사본·RAG client·
Graph 위임, 승인·예산 삭제·효과·Checkpoint·RAG 저장·수명·실제 트랜잭션과 감사다.
Hub는 여러 승인과 재시작을 거친 실제 Tool 영수증/trace 및 공통 레이아웃을 검증했다.
Python·host·namespace/X11·고정 의존성은 앞선 로컬 환경과 동일하다. 수정 전 실패를 제외하거나
다른 실행의 pass를 합산해 전체 검증으로 표현하지 않는다.

변경된 근거 86건을 재검토했다. `error-observation-auxiliary/results.json`에서
S1–S7 145건(new/stale/outstanding/error 0), 문서, R3 Mutation, SDK, 양 suite 환경이
각각 exit 0이었다. 실제 명령은 그 파일에 보존한다. 안전성 기준과 기존 Assertion을 낮추지 않았다.

또한 실제 전체 내용·소비자를 읽은 `errors.py`, `execution/trace.py`, Graph `agent.py`,
`policies/errors.py`, Provider `parameters.py`, `ui/layout/__init__.py` 6개를 개별 전수 검토했다.
뒤의 5개는 이미 typed TraceItem/AgentExecution/Diagnostic/copy_model/직접 class export를
사용하고 있어 제품 변경이 필요 없었다. runtime client 동일성, 동적 ID mapping, inherited
실행과 Python export를 고정 wire의 중복 해석으로 혼동하지 않았다. 인접 파일은 함께
승인하지 않았다. 현재 inventory는 452개 파일, 미검토 408개, 미분류 30,669개,
incomplete 계약 2개이며 전체 완료는 계속 차단된다.

이 소단계는 Level 2 통과다. 오류 관측 수정은 실행 중인 8136897 전체 게이트에 포함되지
않았으므로 그 결과를 재사용해 최종 완료하지 않는다. 다음 관련 Tool 계약 정리와 묶은
현재 통합 소스의 Level 3 및 정확한 Push SHA 원격 CI까지 계속 진행한다.


## 설정 대상 전체 게이트 실패와 Tool 선언 Owner (2026-10-23)

고정 SHA `8136897991d54d4d3d3a49e57e2eff0ced8f9343`의 `whole-owners-group6-l3`는
LLM 2383/2383/2383 pass, exit 0, 1351.696128초였으나 Hub 586/586/584 pass,
Failure 0 / Error 2 / Skip 0, exit 1, 1101.192820초로 **실패**했다.
두 suite의 xfail/xpass·누락/추가/중복은 모두 0이다. 전체 성공이나 Push 근거로 사용하지 않았다.
source `dae8fa0f05db4114c8c616882aa069fae17f1a6c0c139a8a45d05bc0a779825f`,
input `cb409a603a74e347180266491d7bea27d90a0d8ef6861878bbe96595e3c822f1`는 전후 동일하다.
실제 명령/전체 로그/개별 결과는 `/workspace/llm-clean-resume/b/whole-owners-group6-l3`에 보존한다.

실패한 `test_tool_manager_import_list_open_delete_and_validation` 및
`test_builtin_selection_persists_and_approval_resumes`는 모두
`hub/backend/tools/management.py`의 ImplementationParameters import가 future import보다
앞에 놓여 발생한 SyntaxError다. import 순서를 수정하고 전체 제품 Python 파일을 compile했다.
집중 재검증에는 두 실제 Tool 관리·승인 경로를 모두 포함했다. 기존 Assertion을 제거하지 않았다.

Tool 실행 선언을 [정규 Owner](tool-declaration-owner.md)로 옮겼다. 등록부·공개 package export·
Application·Component·Provider·Hub의 22개 파일 import는 같은 타입과 기존 import scope를
유지한다. runtime 승인 판단은 닫힌 ToolContract Reader의 속성을 사용한다. 2,160개 입력에서
이전 constructor/Reader와 현재 출력·오류가 동일하다. 공개 export의 클래스 identity와 독립
wire 기대값을 추가 검증했다. 원본 JSON·descriptor 지문·명시 선택·효과 순서는 보존한다.

첫 집중 명령 `whole-tool-contract-01` 및 `whole-tool-contract-hub-01`은 존재하지 않는
모듈 이름을 포함해 discovery exit 2로 거부됐다. 이를 pass로 집계하지 않았다. 실제 모듈을
확인한 뒤 아래 명령을 실행했다. 각 실행의 failure/error/skip/xfail/xpass·누락/추가/중복은 0이다.

- `whole-tool-contract-02`: 발견/시작/pass 121/121/121, exit 0, 94.688318초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-tool-contract-02/llm.json --modules tests.llm.test_tool_declarations tests.llm.test_tool_constraints tests.llm.test_tool_workers tests.llm.test_tool_packages tests.llm.test_agent_execution tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity`.
  source `c2e2a8b6c3a179cac53601d46ac4eb100dc19d4e17b99c2efdf800a97bf030d8`, input `13333852129dfff19282c21c8ea7c9c7d41801012fc066cd8f2256fe88cd2ce8`.

- `whole-tool-contract-hub-02`: 발견/시작/pass 31/31/31, exit 0, 153.863280초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-tool-contract-hub-02/hub.json --modules tests.hub.test_interaction_updates tests.hub.test_question_tools tests.hub.test_tool_advice tests.hub.test_tool_history tests.hub.test_resource_tools`.
  source `c2e2a8b6c3a179cac53601d46ac4eb100dc19d4e17b99c2efdf800a97bf030d8`, input `13333852129dfff19282c21c8ea7c9c7d41801012fc066cd8f2256fe88cd2ce8`.

LLM은 선언/제약·실제 worker/패키지·Agent 실행·승인 저장·사용량 삭제·효과·Checkpoint·RAG·
수명 무결성, Hub는 실패했던 Tool 관리·질문 승인·실제 Component 도구·분류 조언·재개 영수증을
검증했다. 같은 Python 3.12.14/Clang, 고정 host/의존성, 실제 Linux namespace와 X11 환경이다.
변경된 정적 근거 28건을 개별 재검토하고 동일 ToolContract predicate 1건의 위치를 갱신했다.
`tool-contract-auxiliary/results.json`의 실제 6개 명령(정적 S1–S7 145건, 문서, R3 Mutation,
SDK, LLM/Hub 환경)은 모두 exit 0이다. 새 선언 Owner만 전수 검토했으며 주변 파일은 포함하지 않았다.
현재 파일 453개, 미검토 408개, 미분류 30640개,
incomplete 계약 2개로 전체 완료는 계속 차단된다.

이 소단계는 Level 2다. 수정된 현재 통합 소스로 Level 3를 새로 실행하며, 앞선 실패와
다른 실행을 합쳐 하나의 전체 pass로 표현하지 않는다. 성공한 정확한 Push SHA의 CI를 별도로
확인하고 Tool worker/정책·나머지 제품 전수 작업을 이어간다.


## Tool IPC와 공통 프로세스 시작 계약 (2026-10-23)

[worker 프로토콜](tool-worker-protocol.md)의 요청·응답·descriptor·소스 binding을 Owner 타입으로
연결했다. 부모와 자식이 고정 필드를 중복 조립/해석하지 않는다. 실패 효과 검증은 기존
ToolExecutionError로 위임한다. worker 실패 코드는 ToolErrorCode를 관측 소비자까지 공유한다.
일반 명령과 Tool interpreter의 ProcessLaunch도 동일 Owner로 연결했다. 시작 인자·부모 사망
검사·None payload/메모리 의미를 유지하고 Reader의 중첩 입력 사본을 검증했다.

기존 worker bootstrap FD/root/plugin/Component import 순서를 보존했다. 새 프로토콜 Owner는
stdlib-only이며 독립 -I/-S 프로세스에서도 SDK/Component를 import하지 않는다. 신규 요청 및
응답의 독립 wire/필수 필드·효과 분류 누락·descriptor 지문 검증을 추가했다. 기존 실제 실행
횟수·승인 거부·fresh approval·자식 회수·취소·출처 Assertion은 유지하고 mock 요청의 키 접근만
typed 속성으로 변경했다. 이전 290c576의 decoder와 3,858개 오류/결과 사례가 동일했다.
기존 실패 envelope 확장 허용과 성공 envelope 확장 거부도 바꾸지 않았다.

초기 실제 worker/선언 smoke 22개, worker/격리 smoke 34개, wire/bootstrap smoke 4개가 exit 0이었다.
이 결과는 뒤의 엄격한 discovery 집계와 합치지 않는다. 공식 집중 실행은 아래와 같다.
각 실행의 failure/error/skip/xfail/xpass·누락/추가/중복은 모두 0이며 전후 manifest가 동일하다.

- `whole-worker-protocol-01`: 발견/시작/pass 160/160/160, exit 0, 122.075496초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-worker-protocol-01/llm.json --modules tests.llm.test_tool_worker_protocol tests.llm.test_tool_workers tests.llm.test_tool_packages tests.llm.test_tool_package_integrity tests.llm.test_isolated_tools tests.llm.test_tool_constraints tests.llm.test_execution_boundaries tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity`.
  source `501f1fd6f62863da5bcb15a369115f3ad8af9866262b55e062c9b9dc217b51ec`, input `4644970f73cd6970bed71f906b9955a18b461227a74e0b259cf78ea59902cfb0`.

- `whole-worker-protocol-hub-01`: 발견/시작/pass 31/31/31, exit 0, 216.730455초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-worker-protocol-hub-01/hub.json --modules tests.hub.test_question_tools tests.hub.test_tool_retry tests.hub.test_tool_history tests.hub.test_resource_tools`.
  source `501f1fd6f62863da5bcb15a369115f3ad8af9866262b55e062c9b9dc217b51ec`, input `4644970f73cd6970bed71f906b9955a18b461227a74e0b259cf78ea59902cfb0`.

- `whole-worker-observability-01`: 발견/시작/pass 26/26/26, exit 0, 7.374887초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-worker-observability-01/llm.json --modules tests.llm.test_observability tests.llm.test_tool_worker_protocol`.
  source `b1a49a97e5f5468163c077ee28594de75746cdf76fff78bb76f1494a23939c7b`, input `22868ab973d348ab5043277236ce3d36573020060ec4e2639c62959abef1462e`.

- `whole-worker-values-02`: 발견/시작/pass 23/23/23, exit 0, 9.332947초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-worker-values-02/llm.json --modules tests.llm.test_tool_worker_protocol tests.llm.test_isolated_tools`.
  source `82fd104e97aa82034feba184f933b6783c856148afa01fb98ecaa254c2b27191`, input `6820a7ff8802b347ad3e6d1fa3b97b01761c5d964784cbfc0b5c55ab64b69c04`.

첫 LLM/Hub 검증은 실제 worker·패키지 무결성·격리·인자 제약·실행 경계와 승인 저장·예산 삭제·
효과 원장·Checkpoint·RAG·수명 안전성, Hub 승인/재시도/재개/Component Tool 소비자를 포함한다.
후속 관측 코드 연결은 26개로, 시작 요청 Reader의 중첩 사본 보강은 wire 및 실제 격리 23개로
추가 검증했다. 각 변경 후 해당 소스에서 실행했으며 서로 다른 실행을 하나의 전체 pass로
표현하지 않는다. 로컬 Python 3.12.14/Clang·고정 host/의존성·namespace/X11은 유지했다.

변경된 정적 근거 113건을 개별 재검토했고 발생 ID의 추가/삭제는 없었다.
`worker-protocol-auxiliary/results.json`의 정적 S1–S7 145건, 문서, R3 Mutation, SDK,
LLM/Hub 실제 환경 6개 명령은 모두 exit 0이었다. 코드/실제 소비를 검토한 Tool IPC Owner,
ProcessLaunch/ProcessGate, ToolErrorCode 3개 파일만 전수 판정을 추가했다.
현재 파일 454개, 미검토 406개, 미분류 30463개,
incomplete 계약 2개이며 전체 완료는 계속 차단된다.

이 소단계는 Level 2다. 실행 중인 290c576 고정 전체 게이트에는 이번 IPC 변경이 포함되지
않았다. Tool 패키지/선택 설정과 묶은 후속 현재 통합 소스의 Level 3 및 정확한 SHA CI를
수행한다. 전체 대상·핵심 안전성·후속 단계 완료 기준은 유지한다.


## 290c576 전체 게이트와 원격 Push

`290c57665bf4ef6cef4466dc4ab66d342b7118e0` 고정 소스의 `whole-owners-group7-l3`는
LLM 2387/2387/2387 pass, 1392.063513초와 Hub 586/586/586 pass, 1119.631919초로
각각 exit 0이었다. failure/error/skip/xfail/xpass·누락/추가/중복 모두 0이다.
source `c2e2a8b6c3a179cac53601d46ac4eb100dc19d4e17b99c2efdf800a97bf030d8`,
input `1a5b3101d3f3316aac6a371ca5f86f8397d4594c520c51f15c7794d91914a09e`는 전후 동일했다.
실제 명령과 실행 증거는 `/workspace/llm-clean-resume/b/whole-owners-group7-l3`에 보존한다.
`group7-auxiliary/results.json`의 정적 145건·문서·R3·SDK·양 suite 환경도 모두 exit 0이다.

원격 상태를 재확인한 뒤 `git push origin 290c576:refs/heads/refactor/llm-clean`이 exit 0이었다.
main `94229cb865374d99f20ec48c5e48269488041a4e`와 refactor/llm-backend
`bb0946257ab6bcabb9a3ef04d3d854e5333b07d5`는 그대로다. Draft PR #1도 변경하지 않았다.
[정확한 SHA CI](https://github.com/E1der0wL/ish-llm/actions/runs/39920714762)는
2026-10-23T07:52:51Z 확인에서 실행 중이었다. 아직 원격 통과를 선언하지 않았다.
후속 IPC 커밋 `831ea60`과 아래 패키지 변경은 이 전체 게이트의 검증 범위에 포함되지 않는다.

## Python Tool 정의·digest·manifest Owner (2026-10-23)

[패키지 계약](tool-package-records.md)의 Source·digest·manifest·PEP 508 Reader를 분리하고
Repository inventory, CRUD, worker, dependency 준비, Hub 설명까지 같은 Owner로 연결했다.
source/requirements 원본 파일이 계속 정규 정의이며 새 JSON 정의나 호환 Reader를 만들지 않는다.
Source Reader의 이전/현재 결과·오류는 130개 입력에서 동일했다. 생략·null·빈 문자열,
UTF-8/줄바꿈, unknown 필드와 금지 dependency 문법을 포함한다.

초기 smoke는 54개 중 error 1, exit 1이었다. 기존
`tests.llm.test_tool_package_integrity.ToolPackageLossTests.test_package_semantics_are_checked_even_when_manifest_hashes_match`
재현 fixture가 잘못된 requirements의 checksum을 만들던 과정에서 실패했다. 체크섬 API에
정의 의미 검증을 추가한 것이 원인이었다. ToolPackageDigests의 checksum projection과
ToolPackageSource의 의미 검증 책임을 분리했고, 원래 실패 Assertion을 그대로 유지한 단일
재현은 1 pass/exit 0이었다. 이후 값/무결성 smoke 18개도 pass/exit 0이었다.
실패 및 수정 로그는 `tool-package-draft/smoke.log`, `repro-fixed.log`, `smoke-02.log`에 보존한다.

다음 공식 집중 실행은 각각 failure/error/skip/xfail/xpass·누락/추가/중복 0이며 전후 manifest가 동일하다.

- `whole-tool-package-01`: 발견/시작/pass 122/122/122, exit 0, 99.509865초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-tool-package-01/llm.json --modules tests.llm.test_tool_package_values tests.llm.test_tool_packages tests.llm.test_tool_package_integrity tests.llm.test_tool_data tests.llm.test_tool_workers tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity`.
  source `c567f0c2fad694bf735c4cb8b2bf7e3075e35c641dc31734a6bba745f17e82ec`, input `3070d5a6b1b39d41e1f5c17d8bdaa122de01730d51089bfefb4f1e2d8a1716bc`.

- `whole-tool-package-hub-01`: 발견/시작/pass 17/17/17, exit 0, 47.306820초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-tool-package-hub-01/hub.json --modules tests.hub.test_interaction_updates tests.hub.test_question_tools`.
  source `c567f0c2fad694bf735c4cb8b2bf7e3075e35c641dc31734a6bba745f17e82ec`, input `3070d5a6b1b39d41e1f5c17d8bdaa122de01730d51089bfefb4f1e2d8a1716bc`.

- `whole-tool-package-values-02`: 발견/시작/pass 34/34/34, exit 0, 8.944547초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-tool-package-values-02/llm.json --modules tests.llm.test_tool_package_values tests.llm.test_tool_package_integrity tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`.
  source `a1472bdea9d02512d4907a1d35e293b321a3714b88f96a84c1a38e4826be4c70`, input `aecf8437307d4a59ebde498fd0af74d6429ddde6199b122fd90659e778e97438`.

첫 LLM은 실제 패키지/파일·누락·손상·중단/원자적 복원·선택 CRUD·worker 및 승인·예산 삭제·
효과·Checkpoint·RAG·수명 안전성, Hub는 실제 import/list/open/delete와 built-in 승인 경로다.
마지막 실행은 checksum 구현 공통화 후 현재 소스의 값/실제 무결성과 감사 fail-closed 계약을
검증했다. 서로 다른 실행을 합산해 전체 pass로 표현하지 않는다. 로컬 Python 3.12.14/Clang,
고정 host/의존성·namespace/X11은 앞선 실행과 동일하다.

기존 정적 의존 근거 4건과 새 S4 후보 2건을 검토했다. 두 후보는 Owner Reader의 exact int
version 및 단일 VERSION 비교이며, boolean/누락/잘못된 identity를 거부하는 기존 계약이다.
`tool-package-auxiliary/results.json`의 정적 S1–S7 147건·문서·R3·SDK·양 suite 환경은
각각 exit 0이다. 원본 Assertion이나 필수 job을 제외하지 않았다.
현재 455개 파일, 미검토 406개, 미분류 30375개,
incomplete 계약 2개로 전수 완료는 계속 차단된다.

이 소단계는 Level 2이며 전체 회귀는 다음 Tool 선택/관련 소비자와 묶은 현재 통합 Level 3에서
수행한다. 현재 IPC·패키지 변경은 이미 통과한 290c576 전체/원격 결과에 승계하지 않는다.


## Tool 활성화 선택과 실제 소비 경로 (2026-10-23)

[선택 계약](tool-selection.md)의 필드 metadata에서 Schema와 layout, Reader를 파생했다.
ToolData의 잠긴 add/remove/replace, Python/builtin Component, Hub 관리/생성 기본값이 같은
Owner를 사용한다. 기존 목록의 순서와 유일성·생략/빈 배열/null·원본 사본·정책 envelope를
보존한다. Component의 중복 검증은 동일 Schema에 위임했고 package 존재/잠금/수명/CAS/
트랜잭션/승인 binding은 그대로다. Hub 기본 선택은 계속 명시적인 Application 선택이다.

Schema 어휘에 enum/uniqueItems만 추가했으며 공통 Schema의 나머지 AST는 동일하다.
이전 eb00e14의 두 Component Schema 직렬화가 일치하고 126개 검증 결과 및 오류 순서도
동일했다. 초기 실제 선택 CRUD/builtin smoke는 38개, 새 Owner 포함 smoke는 41개 pass,
exit 0이었다. 공식 집중 실행과 중복 합산하지 않는다.

- `whole-tool-selection-01`: 발견/시작/pass 161/161/161, exit 0, 101.514572초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-tool-selection-01/llm.json --modules tests.llm.test_tool_selection tests.llm.test_tool_data tests.llm.test_builtin_tools tests.llm.test_tool_constraints tests.llm.test_settings_contract tests.llm.test_tool_packages tests.llm.test_tool_workers tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity`.
  source `0c5b3a7f6378552e6d70cb7754d0a25a7f0baaa8332eba5bc85cd8dd8c1edd89`, input `29b77531021068751011e33b936cd0fdccf4cb7774d1ef5451aaa55ca0d8684f`.

- `whole-tool-selection-hub-01`: 발견/시작/pass 36/36/36, exit 0, 183.153139초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-tool-selection-hub-01/hub.json --modules tests.hub.test_defaults tests.hub.test_bootstrap tests.hub.test_interaction_updates tests.hub.test_question_tools tests.hub.test_resource_tools tests.hub.test_tool_history`.
  source `0c5b3a7f6378552e6d70cb7754d0a25a7f0baaa8332eba5bc85cd8dd8c1edd89`, input `29b77531021068751011e33b936cd0fdccf4cb7774d1ef5451aaa55ca0d8684f`.

각 실행의 failure/error/skip/xfail/xpass·누락/추가/중복은 모두 0이다. LLM은 실제 선택 CRUD,
builtin·인자 제약·설정 계약·패키지/worker와 승인 저장·예산 삭제·효과·Checkpoint·RAG·수명
안전성이다. Hub는 기본값/초기화·관리 UI·승인·실제 Resource Tool·재개 영수증 경로다.
로컬 Python 3.12.14/Clang과 고정 host/의존성·namespace/X11은 동일했다.

기존 정적 근거 32건을 재검토했다. 두 Component layout의 enabled 리터럴 후보는 정규 필드
참조로 전환돼 사라졌으며 기존 검토를 `removed-findings.json`에 보존했다. 새 후보는 없다.
`tool-selection-auxiliary/results.json`의 정적 S1–S7 145건·문서·R3·SDK·양 suite 환경은 모두
exit 0이다. Schema 전수 검토의 기존 분류를 동일 AST 발생에 연결하고 새 선언 2개만 추가했으며,
그 공통 계약의 기존 incomplete 상태는 유지했다.
현재 456개 파일, 미검토 406개, 미분류 30325개,
incomplete 계약 2개로 전체 완료를 선언하지 않는다.

이 소단계는 Level 2다. IPC·패키지·활성화 선택을 묶은 현재 통합 소스로 다음 Level 3를
실행한다. 이후에도 나머지 제품 파일의 선언/Reader/Writer/소비 연결과 정확한 SHA 원격 CI
확인을 계속하며, 앞선 소단계 통과를 전체 완료로 대체하지 않는다.


## 290c576 정확한 SHA 원격 CI 확인

2026-10-23T08:35:34Z 확인에서 [CI 39920714762](https://github.com/E1der0wL/ish-llm/actions/runs/39920714762)의
head SHA는 `290c57665bf4ef6cef4466dc4ab66d342b7118e0`였고 두 필수 job 및 모든 필수 step이
실제 success였다. LLM 2387/2387/2387 pass, 1962.161375초;
Hub 586/586/586 pass, 1373.940361초였다.
failure/error/skip/xfail/xpass·누락/추가/중복 모두 0이다. 환경·문서·정적 감사·R3 Mutation·
실제 SDK 진단·실패 포함 증거 보존 step도 실제 결과로 확인했다.
원격 Python 3.12.14/GCC 13.3.0은 로컬 Clang 환경과 구분한다. 이 증거를 이후 커밋에 승계하지 않는다.

## 실행 문맥 수명과 Tool 준비 제한 (2026-10-23)

[문맥 계약](execution-contexts.md)의 ToolCallContext가 부모/child의 진입·조회·finally 복원을
소유한다. private ContextVar 직접 접근을 제거했다. 공개 current_tool_call()의 deepcopy와
UsageScope/current_usage()의 동일 scope/counter 객체 반환은 유지했다. 두 문맥을 하나의
전역 실행 상태로 합치거나 권한·예산·효과 정책을 추가하지 않았다.

ToolData.prepare는 두 Owner의 존재 판정으로 기존 제한을 적용한다. 실제 import가 파일을
만드는 fixture로 Tool 문맥만/사용량 문맥만/둘 다 있을 때 효과 0, 문맥 종료 후 명시적 준비의
실제 효과 발생을 검증했다. 동시 Task 분리·중첩/취소 복원·공개 조회 사본도 신규 검증했다.
기존 worker/usage/ToolData smoke 30개, 신규 문맥 smoke 4개는 각각 pass/exit 0이었다.
공식 집중 실행과 중복 합산하지 않는다.

- `whole-execution-context-01`: 발견/시작/pass 153/153/153, exit 0, 118.035729초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-execution-context-01/llm.json --modules tests.llm.test_execution_contexts tests.llm.test_execution_boundaries tests.llm.test_tool_workers tests.llm.test_tool_data tests.llm.test_usage_ownership tests.llm.test_agent_execution tests.llm.test_nested_graph tests.llm.test_component_storage_integrity tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity`.
  source `b90770f38d32c4695fa88027baa697ec78b1593047fb924c9a6e40f49a14da41`, input `59db93b471fdc8c8d7184dd88de3b7542dafb25c06e238227db3d0154476b610`.

- `whole-execution-context-hub-01`: 발견/시작/pass 21/21/21, exit 0, 155.073767초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-execution-context-hub-01/hub.json --modules tests.hub.test_question_tools tests.hub.test_resource_tools tests.hub.test_tool_history`.
  source `b90770f38d32c4695fa88027baa697ec78b1593047fb924c9a6e40f49a14da41`, input `59db93b471fdc8c8d7184dd88de3b7542dafb25c06e238227db3d0154476b610`.

각 실행의 failure/error/skip/xfail/xpass·누락/추가/중복은 모두 0이다. LLM은 실제 worker·ToolData,
사용량·Agent/중첩 Graph·실행 경계·Component 저장 및 승인·예산 삭제·효과·Checkpoint·RAG·
수명 안전성, Hub는 질문 승인·Resource Tool·재개 영수증이다. 전후 source/input은 각각
동일했고 로컬 Python 3.12.14/Clang·고정 host/의존성·namespace/X11을 유지했다.

정적 의존 근거 22건을 재검토했고 발생 추가/삭제는 없다. `execution-context-auxiliary/results.json`의
정적 S1–S7 145건·문서·R3·SDK·양 suite 환경은 모두 exit 0이다. 전체 내용을 읽고 실제 소비를
연결한 ToolCallContext 및 ToolData만 전수 판정을 추가했다. ToolData의 남은 None 후보 3개는
-> None 반환 타입 주석이며 실행 상태 비교/기본값/필수 기록 fallback이 아니다.
현재 457개 파일, 미검토 405개, 미분류 30317개,
incomplete 계약 2개로 전체 완료는 계속 차단된다.

이 소단계는 Level 2다. 실행 중인 1e29e66 고정 전체 게이트에는 이번 문맥 변경이 포함되지
않았으며, 이후 관련 조회/실행 계약과 묶은 현재 통합 Level 3에서 검증한다. 소단계 완료로
전체 대상 작업을 종료하지 않는다.


## 1e29e66 통합 게이트 실패와 실제 Host 자동완성 준비 검증 (2026-10-23)

고정 SHA `1e29e66aacf276e53b37ae64d6628f7c27dfc747`의 group8 전체 회귀는 실패했다.
LLM 발견/시작/pass 2397/2397/2397, exit 0, 1384.799290초였지만 Hub는
586/586/585, error 1, exit 1, 1124.586482초였다. 실패 이외의 failure/skip/xfail/xpass·
누락/추가/중복은 0이다. 실패한 `test_actual_host_focus_keys_and_reinstallation`은
Tab 이후 완료 상태 해제를 기다리다 timeout이 발생했다. 이 전체 실행을 통과로 기록하거나 Push하지 않았다.
전후 source `0c5b3a7f6378552e6d70cb7754d0a25a7f0baaa8332eba5bc85cd8dd8c1edd89`,
input `af7ee79b9db9e6fa31e9f4c758c9ea7c3091cb47d45e9e498257107fadbf3b16`은 동일했다.
group8-auxiliary의 정적·문서·R3·SDK·양 suite 환경은 각각 exit 0이었다.

같은 고정 소스의 Host 집중 재현 `whole-host-repro-01`은 5개 pass/exit 0이었다.
이 재실행을 실패 취소 근거로 삼지 않았다. 설치된 prompt_toolkit Buffer는 threaded 후보를
읽기 전에 빈 CompletionState를 공개한다. 기존 테스트는 상태 존재만으로 후보 준비를 판단해
후보가 없는 상태에서 Down/Tab을 보낼 수 있었다. 제품의 입력 처리·완료 기준은 변경하지 않고
테스트가 실제 후보를 기다리도록 수정했다. 동일 실제 Host 시험에서 Event로 후보 생성을
의도적으로 중단하고 빈 상태를 확인한 다음 해제해 재현 조건을 강제했다. 기존 shell dispatch
방지·실제 키 입력·완성 문자열·재설치/초점 복원 Assertion은 모두 유지했다.

초기 `whole-host-completion-01`은 존재하지 않는 모듈명을 지정해 discovery exit 2였다.
성공으로 집계하지 않으며 원본 로그를 보존한다. 실제 모듈을 확인해 다음 명령으로 검증했다.

- 명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-host-completion-02/hub.json --modules tests.hub.test_ish_integration tests.hub.test_completion_display tests.hub.test_popup_completion tests.hub.test_input_contract`.
- 발견/시작/pass 13/13/13, exit 0, 10.159593초.
- source: `a5e66b86307746319ef7700a6a0521cef3919aa45c3a5af90c36e8ced6c0805b`.
- input: `69d4f242ff5b9a172b8b1b5593d3491929892435967022de9fe0ccd2757cf335`.

failure/error/skip/xfail/xpass·누락/추가/중복은 모두 0이다. Python 3.12.14/Clang과
고정 Host·의존성·실제 입력 환경은 이전 실행과 같다. `host-completion-auxiliary/results.json`의
정적 S1–S7 145건·문서·R3·SDK·양 suite 환경도 각각 exit 0이다. 테스트 준비 동기화의
Level 2 검증이며 앞선 전체 실패를 지우지 않는다. 실행 문맥 변경까지 포함하는 새 고정
통합 Level 3를 실행하고 통과 후에만 작업 브랜치에 Push한다.


## 편집 snapshot Owner와 Component·Agent·Hub 소비 연결 (2026-10-23)

[편집 관찰 계약](editable-snapshots.md)의 EditableSnapshot과 AgentSnapshot이 공개 봉투의
필드·Reader·Writer·사본 경계를 소유한다. Hub의 생성 결과는 같은 필드를 상속한
CreatedResource Projection을 사용한다. Component/Agent 저장·동기/비동기 공개 응답은
동일한 JSON이며 Repository load의 공개 snapshot 호출도 유지했다. 잘못된 필수 필드·
타입/unknown·Agent 정의/지문 불일치는 거부한다. 공통 Component version은 불투명한
문자열로 보존하고 빈 문자열을 임의의 새 버전으로 바꾸거나 재계산하지 않는다.

실제 소비는 Hub Tool·Record·Skill 관리/Vision 실패 정리, Skill 참조·Agent 영향/child
조회, Refinement binding, Memory 추출/원본 파일까지 연결했다. 기존 CAS·잠금·원본
보존·효과/예산/삭제 판단 순서는 유지했다. 초기 Owner·Agent 저장·Skill 참조 smoke는
30개 pass/exit 0이었다. 신규 시험은 중첩 조회 사본과 실제 오래된 버전의 save/delete가
현재 파일을 바꾸지 못함을 확인한다.

- `whole-editable-snapshot-01`: 발견/시작/pass 173/173/173, exit 0, 272.105543초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-editable-snapshot-01/llm.json --modules tests.llm.test_editable_snapshots tests.llm.test_agent_storage tests.llm.test_agent_domain tests.llm.test_component_record_integrity tests.llm.test_settings_contract tests.llm.test_skill_references tests.llm.test_memory_processing tests.llm.test_memory_compaction_integrity tests.llm.test_refinement tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity`.
  source `b783781df96f09e1f64c19fc02c3b32e944a52e2953ffddf81cf9ca219818cc9`, input `f6a32220d3559e0d93e0cd68663ed90d5d2085e88410ea6f8782ff778b78395e`.

- `whole-editable-snapshot-hub-01`: 발견/시작/pass 29/29/29, exit 0, 178.810726초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-editable-snapshot-hub-01/hub.json --modules tests.hub.test_component_managers tests.hub.test_resource_tools tests.hub.test_interaction_updates tests.hub.test_question_tools`.
  source `b783781df96f09e1f64c19fc02c3b32e944a52e2953ffddf81cf9ca219818cc9`, input `f6a32220d3559e0d93e0cd68663ed90d5d2085e88410ea6f8782ff778b78395e`.

두 실행 모두 failure/error/skip/xfail/xpass·누락/추가/중복 0이며 전후 manifest가 같다.
LLM은 모든 직접 변경 소비자와 실제 저장 무결성·중단/재개·승인·예산 삭제·효과·Checkpoint·
RAG·수명 안전성, Hub는 실제 관리 화면·Resource Tool·Tool 관리·질문 승인 경로를 검증했다.
Python 3.12.14/Clang·고정 Host·의존성·namespace/X11은 이전 환경과 동일하다.

기존 정적 근거 16건의 변경 의존성을 재검토했다. S4 발생 3개는 dict 조회에서 타입 속성으로
변경한 동일 CAS/고정 참조 비교이며 각각 연결했고 과거 행은 removed-findings.json에 보존했다.
`snapshot-auxiliary/results.json`의 S1–S7 145건·문서·R3·SDK·양 suite 환경은 모두 exit 0이다.
감사 fail-closed 자체 시험 `python -m unittest tests.llm.test_architecture_reviews
 tests.llm.test_contract_coverage`는 16개 pass/exit 0이었다. 위 집중 실행과 합산하지 않는다.
현재 460개 파일, 미검토 405개, 미분류 30278개,
incomplete 계약 2개이며 이번에는 새 Owner 3개만 전수 검토했다.

이 소단계는 Level 2다. 진행 중인 9067faa 고정 통합 게이트에는 이 변경이 포함되지 않는다.
참조/삭제 보호와 관련 후속 소단계를 묶은 현재 소스의 다음 Level 3에서 전체를 검증한다.
payload의 Prompt·Memory·파일 의미, 참조 프로토콜과 Manager 고정 응답은 계속 후속 작업이다.


## 공통 참조와 Skill 삭제 관찰 Owner (2026-10-23)

[참조 계약](resource-references.md)의 대상·출처·선택적 경로를 ResourceTarget/
StoredResourceReference로 읽고 ComponentRegistry와 ProjectReferences의 중복 일치 판정을
통합했다. Component·Agent·Engine·Workflow handler는 여전히 자기 정의를 해석하며 공통
계층이 사유 정책을 해석하지 않는다. Component 이름과 Agent 참조 namespace는 각 Owner를
참조한다. AgentUse와 Skill의 impact/dependencies Projection도 타입으로 연결했다.

원래 공개 JSON·path 생략/null·확장 근거·비활성 정의·필수 Owner 누락 거부·잠금/CAS/
트랜잭션을 보존한다. 필수 참조 필드를 기본값으로 만들지 않는다. 신규 시험의 432개
component/identifier/path 조합이 이전 판정과 같았고, 실제 저장에서 필수 출처 누락과
비활성 참조 Owner가 삭제를 차단하며 파일 전체가 보존됨을 확인했다. 기존 직접 소비 smoke
67개, 신규 시험 4개는 각각 pass/exit 0이며 공식 실행과 중복 합산하지 않는다.

- `whole-resource-reference-01`: 발견/시작/pass 149/149/149, exit 0, 116.797363초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-resource-reference-01/llm.json --modules tests.llm.test_resource_references tests.llm.test_agent_resource_integrity tests.llm.test_agent_workflow tests.llm.test_skill_references tests.llm.test_refinement tests.llm.test_memory_compaction_integrity tests.llm.test_component_dependencies tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity`.
  source `67a66155ddfadb9ee683b777f77ef4e7d1ae319188742a3adec2287e643da7d4`, input `d48b85d024c435b3a5ff6ea51c0bee72028a8b70d0e4214faa8bae238f2696cd`.

- `whole-resource-reference-hub-01`: 발견/시작/pass 13/13/13, exit 0, 131.278023초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-resource-reference-hub-01/hub.json --modules tests.hub.test_component_managers tests.hub.test_resource_tools tests.hub.test_agent_graph`.
  source `67a66155ddfadb9ee683b777f77ef4e7d1ae319188742a3adec2287e643da7d4`, input `d48b85d024c435b3a5ff6ea51c0bee72028a8b70d0e4214faa8bae238f2696cd`.

두 실행 모두 failure/error/skip/xfail/xpass·누락/추가/중복 0이다. LLM은 실제 Agent/Workflow·
Skill·Refinement·Memory compaction·Component 의존/삭제와 승인·예산 삭제·효과·Checkpoint·
RAG·수명 안전성, Hub는 관리·Resource Tool·Agent Graph 소비 경로를 검증했다. source/input은
전후 동일했고 Python 3.12.14/Clang·고정 Host·의존성·namespace/X11을 유지했다.

기존 정적 근거 27건의 의존성을 재검토했고 발생 추가/삭제는 없다. Agent view의 이전 후보는
동일 AST 발생에 연결해 기존 판정을 유지하고 새 Projection만 검토했다. 새 참조 Owner와
ProjectReferences, SkillDependencies만 전수 검토했다. 감사 fail-closed 자체 시험
`python -m unittest tests.llm.test_architecture_reviews tests.llm.test_contract_coverage`는
16개 pass/exit 0이었다. `references-auxiliary/results.json`의 S1–S7 145건·문서·R3·SDK·
양 suite 환경은 각각 exit 0이다.
현재 462개 파일, 미검토 404개, 미분류 30201개,
incomplete 계약 2개로 전체 완료는 여전히 차단된다.

이 소단계는 Level 2다. 이전 편집 snapshot과 묶은 현재 소스에서 다음 Level 3 전체 게이트를
수행한다. 실행 중인 9067faa 전체 검증에는 두 소단계가 포함되지 않는다. Component 내부
정의, history_references와 나머지 고정 구조/리터럴 전환도 계속 진행한다.


## 9067faa 통합 게이트 실패와 실제 실행 문맥 재현 보강 (2026-10-23)

고정 SHA `9067faa`의 group9 전체 실행은 실패했다. LLM 발견/시작/pass
2401/2401/2399, error 2, exit 1, 1399.690179초; Hub는 586/586/586 pass,
exit 0, 1130.669857초였다. 다른 failure/skip/xfail/xpass·누락/추가/중복은 0이다.
전후 source `a5e66b86307746319ef7700a6a0521cef3919aa45c3a5af90c36e8ced6c0805b`,
input `cfc9a348954063fbe1ae06f94b6f8bcbb10c5fe208e2392d4c64330c9e5be332`은 동일했다.
group9-auxiliary의 정적·문서·R3·SDK·양 suite 환경은 각각 exit 0이었다.

오류는 `tests.llm.test_tool_packages.PackageTests`와 이를 상속한 `DependencyTests`의
`test_prepare_validates_without_dependency_or_run`이었다. 실행 문맥 Owner 전환 후에도 시험이
이전 current_tool_call 함수만 mock하여 실제 문맥을 만들지 못했다. 준비 금지의 원래
RuntimeError Assertion은 유지하고 유효한 ToolCall로 ToolCallContext.scope에 진입하도록
fixture를 수정했다. 기존 실제 import 파일 효과 0·Tool/usage 각각의 문맥·중첩/취소 복원
시험도 유지한다. 현재 패키지 경계 smoke 역시 47개 중 같은 error 2/exit 1이었으며, 수정 후
해당 2개와 실행 문맥 4개는 6 pass/exit 0이었다. 실패와 수정 로그를
package-boundaries-draft/smoke.log 및 context-repro-fixed.log에 각각 보존한다.
이 실패한 전체 실행은 Push하지 않으며 이후 성공 결과로 덮어쓰지 않는다.

## Tool 패키지 저장·공용 dependency 경계의 선언 소유 (2026-10-23)

[경계 계약](package-boundaries.md)에 따라 경로/파일/진단은 ToolPaths, 설치된 배포판과
marker/Host 해석은 ToolDependencies가 소유한다. SDK marker extra는 정규 필드에서
직렬화한다. 기존 주입점은 단일 구현의 직접 별칭이며 새 Reader나 호환 실행 분기는 없다.
저장/manifest 커밋·원자적 복원·공유 설치 pin/constraints·명시적 prepare·최종 worker
검사 순서는 같다. 이전 구현과 336개 dependency 입력에서 결과/오류·입력 비변경이 같았다.

- `whole-package-boundary-01`: 발견/시작/pass 123/123/123, exit 0, 100.458329초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-package-boundary-01/llm.json --modules tests.llm.test_tool_packages tests.llm.test_tool_package_integrity tests.llm.test_tool_data tests.llm.test_tool_workers tests.llm.test_execution_contexts tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity`.
  source `2c2bd8d70f15bf758039b8b3034fbd134bf53757492afaa0481b5d748de2daa4`, input `d56a37639d632363dcb922c28b1db215424addfa09e523088c8f77d4e31ed23c`.

- `whole-package-boundary-hub-01`: 발견/시작/pass 17/17/17, exit 0, 45.956046초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-package-boundary-hub-01/hub.json --modules tests.hub.test_interaction_updates tests.hub.test_question_tools`.
  source `2c2bd8d70f15bf758039b8b3034fbd134bf53757492afaa0481b5d748de2daa4`, input `d56a37639d632363dcb922c28b1db215424addfa09e523088c8f77d4e31ed23c`.

각 실행의 failure/error/skip/xfail/xpass·누락/추가/중복은 모두 0이다. LLM은 실제 패키지
저장·장애 주입·공용 dependency 설치/충돌·ToolData/worker·실행 문맥과 승인·예산 삭제·
효과·Checkpoint·RAG·수명 안전성, Hub는 실제 패키지 관리·질문 승인 경로를 검증했다.
전후 source/input은 동일했으며 로컬 Python 3.12.14/Clang·고정 Host·의존성·namespace/X11을
유지했다. 서로 다른 실행 결과를 전체 통과로 합산하지 않는다.

기존 정적 근거 2건을 재검토했고 발생 추가/삭제는 없다. 감사 자체 시험 16개는 pass/exit 0이며
package-boundary-auxiliary의 S1–S7 145건·문서·R3·SDK·양 suite 환경도 각각 exit 0이다.
packages.py의 전체 구현과 실제 소비를 검토했으며 남은 실행 후보 4개는 dataclass 생성 옵션이다.
현재 462개 파일, 미검토 403개, 미분류 30154개,
incomplete 계약 2개로 전체 완료는 계속 차단된다.

이 소단계는 Level 2다. 실행 문맥 시험 수정과 편집 snapshot·공통 참조·패키지 경계를 모두
포함한 고정 현재 소스에서 다음 전체 Level 3를 실행한다. 성공 후 작업 브랜치에 Push하고
그 정확한 SHA의 실제 원격 CI를 확인한다. 이후 미전환 전체 범위도 계속 진행한다.


## Python Tool 함수 메타데이터·인자 Schema·제약 (2026-10-23)

[함수 선언 계약](tool-function-contract.md)에 따라 decorator attribute는 ToolMetadata, 허용
타입 문법과 진단은 FunctionAnnotations, Schema 어휘는 기존 SchemaKeyword가 소유한다.
strict 필드는 ToolDefinition 선언을 참조하고 함수 소비자가 metadata dict를 반복 해석하지
않는다. 원래 함수 identity·ToolContract·options 불변성·strict 생략/false/true와 signature
기본값의 정확한 JSON/Schema 검증을 유지했다.

인자 제약은 기존 ArgumentConstraint를 그대로 사용해 allOf 교집합과 필수 입력을 만든다.
fixed만 유일한 명시 선택값을 채우며 원래 Schema와 입력은 보호한다. JsonObject가 보장하는
객체 타입의 도달 불가능한 중복 검사만 제거했다. 초기 선언/제약/패키지 smoke 48개는
pass/exit 0이며 아래 공식 실행과 중복 합산하지 않는다.

- `whole-tool-signature-01`: 발견/시작/pass 136/136/136, exit 0, 113.078686초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/whole-tool-signature-01/llm.json --modules tests.llm.test_tool_declarations tests.llm.test_tool_constraints tests.llm.test_tool_packages tests.llm.test_tool_package_integrity tests.llm.test_tool_workers tests.llm.test_agent_execution tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity`.
  source `e0da74d83463ccf4d1377af9504aa6d8e03a64d5ce9191dfcaec08a1fc2912d9`, input `f544bc2ac8eb2d7dcc674921298bc36fcedebf13de18ffb748da5f55aa1f982f`.

- `whole-tool-signature-hub-01`: 발견/시작/pass 21/21/21, exit 0, 56.419639초.
  명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/whole-tool-signature-hub-01/hub.json --modules tests.hub.test_interaction_updates tests.hub.test_question_tools tests.hub.test_tool_advice`.
  source `e0da74d83463ccf4d1377af9504aa6d8e03a64d5ce9191dfcaec08a1fc2912d9`, input `f544bc2ac8eb2d7dcc674921298bc36fcedebf13de18ffb748da5f55aa1f982f`.

각 실행의 failure/error/skip/xfail/xpass·누락/추가/중복은 모두 0이다. LLM은 독립 wire,
잘못된 타입/기본값·원본 Schema·실제 패키지/worker·Agent 및 승인·예산 삭제·효과·Checkpoint·
RAG·수명 안전성, Hub는 실제 Tool 관리·질문 승인·인자 수정 경로를 확인했다. 전후 manifest가
같고 Python 3.12.14/Clang·고정 Host·의존성·namespace/X11은 이전 환경과 동일하다.

정적 발생 및 기존 분류의 의존 변경은 0건이며 모든 구현과 실제 소비를 검토한 세 파일만
전수 판정을 추가했다. 남은 실행 리터럴 후보는 typing 인자 tuple 또는 fixed의 단일 값에
접근하는 순수 인덱스다. 감사 자체 시험 16개는 pass/exit 0이며 tool-signature-auxiliary의
S1–S7 145건·문서·R3·SDK·양 suite 환경도 각각 exit 0이다.
현재 462개 파일, 미검토 400개, 미분류 30109개,
incomplete 계약 2개로 전체 완료를 선언하지 않는다.

이 소단계는 Level 2다. 9adb2b3의 실행 중인 전체 게이트에는 이 변경이 포함되지 않는다.
남은 Tool 등록/실행 binding 계약과 관련 소비자를 묶어 다음 현재 통합 Level 3를 진행한다.
전체 대상의 미전환 항목과 정확한 SHA 원격 CI까지 계속 확인한다.
