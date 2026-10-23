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
