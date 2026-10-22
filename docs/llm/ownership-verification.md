# Owner 정리의 구현·검증 기록

작업 기준은 `bf25ee23621ee490f855bdfa0320daeef0593e5a`, 브랜치는 `refactor/llm-clean`이다.
[정규 계약](contract-ownership.md)과 [O0–O7 작업 목록](ownership-refactor.md)을 따른다.
아래 부분 검증을 상위 단계·최종 전체 게이트·원격 CI 완료로 해석하지 않는다.

## O0 초기 조사와 기존 근거

원격 작업 브랜치가 기준 SHA와 같고 작업 트리가 깨끗함을 확인했다. main은 `94229cb`이며
refactor/llm-backend는 `bb09462`로 보존한다. 기존 데이터군·검증·문서 감사 근거를 재사용한다.

기존 정적 후보 159개와 개별 검토를 연결했다. 보강 문서의 직접 영향은 OW-07의 70건이다.
O1-a 구현 뒤에는 파일 전체 hash를 고정한 의존 근거까지 포함해 105건이 달라졌으며,
159개 기존 후보의 AST identity는 아직 그대로다. 이는 자동 재승인 사유가 아니다.

일반 문자열 키 접근은 llm/hub 215개 파일의 5358개 후보로 수집했다. 해당 수치는 Owner 내부
Reader·외부 규격·UI/사용자 데이터도 포함하며 위반 수가 아니다. 계약별 선언·실제 소비자를
추적해 분류한다. 원자료는 `/workspace/llm-clean-resume/ownership/field-candidates.json`과
`review-queue.json`에 보존하며 새 감사 도구와 후속 구현의 출발점으로 사용한다. O0 전체 감사는 진행 중이다.

## O1-a 기본값·대화 저장 방식

변경한 Owner와 소비자:

- `OutputJournal.DEFAULT_INDEX_STRIDE`가 기본 128을 선언하고 RunRepository·BackendServices가 참조한다.
- `ProjectConversations.DEFAULT_FILE_CACHE_SIZE`가 기술 캐시 기본 32를 선언하고
  BackendServices·SessionManager가 참조한다. 명시 0 비활성은 유지한다.
- Project 도메인의 `ConversationStorage`가 file/memory 값, nullable 검증과 Schema를 소유한다.
  Project·ProjectConversations·서비스 조립·Hub preset 검증이 같은 선언을 사용한다.
  복구·보관·전송·durable admission 소비자도 같은 종류를 참조한다.
- None은 주입 팩토리 계약을 보존한다. JSON의 문자열 값과 공개 설정 형식, 기존 선택·충돌·
  생성/재열기·백업·예산 보호 의미를 변경하지 않는다.

### 집중 검증

Python 3.12.14, 실제 ish host와 잠금 의존성을 사용한다. 환경은 `TERM=xterm-256color`,
`LITELLM_LOCAL_MODEL_COST_MAP=True`, `LITELLM_MODE=PRODUCTION`이다. 다음 실제 명령의
소스·문서·Fixture·환경 전후 hash와 원본 로그는 지정한 결과 디렉터리에 보존했다.

```sh
.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm o1-declarations-l1 tests.llm.test_owner_declarations tests.llm.test_project_conversation_storage tests.llm.test_memory_conversation tests.llm.test_settings_consistency tests.llm.test_project_creation tests.llm.test_configuration_validation
.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm o1-declarations-l1-fixed tests.llm.test_owner_declarations tests.llm.test_project_conversation_storage tests.llm.test_memory_conversation tests.llm.test_settings_consistency tests.llm.test_project_creation tests.llm.test_configuration_validation
```

| 실행 | Exit | 발견/실행/Pass | Failure/Error/Skip | 누락/중복 | 결과 |
|---|---|---|---|---|---|
| o1-declarations-l1 | 1 | 51/51/50 | 0/3/0 | 0/0 | 새 시험 한 메서드의 세 subtest가 비동기 전용 LargeLanguageModel에 동기 with를 사용. 제품 실패와 구분 |
| o1-declarations-l1-fixed | 0 | 51/51/51 | 0/0/0 | 0/0 | 시험을 async with로 수정. 기존 의미·Assertion 유지 |

두 실행 모두 expected failure/unexpected success/예상 외 ID는 0이며, 발견·실행 누락은 없다.
수정 후 source hash는 `b27c20df425681dd3df5c7f0a4ca63207e722abdeb415173a6c5df3660d9cbb0`,
input hash는 `cb6d4fedd5f1cd15506af45ae2292e5d155941518b46b9b96ace49f336e6aa37`로 전후 동일하다.
범위는 설정/Schema·저장소 선택·생성·공개 설정 소비의 Level 1이다. 관련 안전성·통합·정적
재검토와 상위 전체 게이트가 남아 있으므로 O1 완료·Push·원격 CI 통과를 선언하지 않는다.

## O1-b 출력·전이 사유·경로

`OutputVisibility`/`OutputOperation`은 출력 계약의 종류를 선언하고 Engine·Journal·Conversation·
Run·Hub 소비자가 참조한다. `RunTransition`은 기존 전이 사유를 선언한다. 실제 전이 규칙은
`validate_run_transition`에 유지한다. Project/Session/Run/Step의 record와 Run outputs 경로는
해당 Paths 속성에서 선언하고 Repository 소비자는 속성을 사용한다. 저장 형식과 값은 동일하다.

```sh
.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm o1-output-paths-l1 tests.llm.test_owner_declarations tests.llm.test_engine_outputs tests.llm.test_output_integrity tests.llm.test_run_transitions tests.llm.test_domain_state_integrity tests.llm.test_lifecycle_integrity tests.llm.test_checkpoint_integrity tests.llm.test_operation_integrity tests.llm.test_usage_deletion
```

85 발견/실행/Pass, Exit 0. Failure/Error/Skip/expected failure/unexpected success/누락/예상 외/중복 모두 0.
source `5f4f45c3712f6c9c6940ca613058a844c51af53f9eb6f90e8327c5a4f01910c6`,
input `93b27466c24b0fad5ef532511c746c3a8367167f7d1b6f156ccb54bc9bb011e2`가 전후 동일하다.
변경된 출력·전이·경로와 직접 연관된 필수 기록·Checkpoint·효과 원장·사용량 삭제 보호를 실행했다.
Level 1 결과이며 전체 회귀는 관련 단계 통합 게이트로 유예한다.

## O2-a 영수증·조회 투영과 O2-b 설정 설명

- `CompletionRecords`가 Run metadata의 필수 completions 컨테이너, 중복 ID, 생성·조회·갱신·종료를
  소유한다. Reader는 `CompletionResult.from_record`, 갱신은 기존 불변 예약 검증을 사용한다.
  누락 컨테이너는 생성 경계 밖에서 복구하지 않는다. RunRepository·결과·사용량·삭제 보호·Hub가 위임한다.
- `CompletionResult.usage_record`는 unknown usage/예약을 보존한 분리 사본을 제공한다.
  `Run.index_record`는 기존의 저비용 목록 투영을 소유하고 선택된 결과의 정규 Reader 검증을 유지한다.
- `ProjectConfiguration`이 공개 설정 설명을 조합하며 Manager는 잠금·최신 원본·CAS·구현체 검증을
  담당한다. Facade는 형식을 해석하지 않고 위임한다. `ProjectConfigForm`의 필드 선언에서 편집
  값과 form Schema를 파생한다. 공개 dict 형식·M/N/V·effective 비저장·확장 구현체 조회를 유지한다.

```sh
.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm o2-receipts-l1 tests.llm.test_completion_integrity tests.llm.test_configuration_results tests.llm.test_long_running tests.llm.test_usage_deletion tests.llm.test_service_extensions tests.llm.test_extension_contracts tests.llm.test_operation_integrity tests.llm.test_lifecycle_integrity
.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py hub o2-receipts-hub-l1 tests.hub.test_snapshot_projection tests.hub.test_completion_display tests.hub.test_engine_output tests.hub.test_state_owners
.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm o2-projections-l1 tests.llm.test_owner_declarations tests.llm.test_settings_consistency tests.llm.test_settings_contract tests.llm.test_schema_ownership tests.llm.test_project_component_settings tests.llm.test_configuration_objects
.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py hub o2-receipts-hub-l1-fixed tests.hub.test_snapshot_projection tests.hub.test_completion_display tests.hub.test_engine_output tests.hub.test_state_owners
```

| 실행 | Exit | 발견/실행/Pass | Failure/Error/Skip | 누락/중복 |
|---|---|---|---|---|
| o2-receipts-l1 | 0 | 106/106/106 | 0/0/0 | 0/0 |
| o2-receipts-hub-l1 | 1 | 20/20/16 | 0/4/0 | 0/0 |
| o2-projections-l1 | 0 | 60/60/60 | 0/0/0 | 0/0 |
| o2-receipts-hub-l1-fixed | 0 | 21/21/21 | 0/0/0 | 0/0 |

모두 expected failure/unexpected success/예상 외 ID 0. 최초 Hub 오류는 필수 필드가 없는 가상
영수증 4개였으며 실제 저장 형식의 CompletionResult로 Fixture를 보완했다. 독립 표시 기대값·
순서·도구 결과 Assertion은 유지했다. SDK-native thinking dict는 이미 Adapter가 정규 문자열로
변환해야 하는 값이므로 Snapshot Fixture도 정규 형식으로 입력하고, 누락·불완전·native dict
영수증을 거부하는 음성 시험을 추가했다. 원래 오류 로그는 보존한다.

최초 두 실행 source/input은 각각
`100f8ae55f485e833f2791495e22782564da853903e2a584d3eefa82083551ed` /
`0ecfa07bf679be24dbfd2e9dfd6e5dd5de74b4427a58837f417693a958f5bf04`이다.
후속 두 실행은
`96af0d2dedef6ecd36f01dd9b78fa1f6b583ca9dd70a4d18f46732f19fd99744` /
`520a2e8230427a8703ff6cd0e58071ecabdea1f3cf17297a795da69f933f9fba`이다.
각 실행의 소스·환경 전후 동일성을 확인했고 서로 다른 실행을 하나의 전체 통과로 합산하지 않는다.
모든 원본·명령·집계는 `/workspace/llm-clean-resume/b/<실행명>/`에 있다.
현재 Level 1 완료이며 관련 안전성·확장 통합·정적 검토 및 O1–O6/O7 전체 게이트는 남아 있다.


## O3 정책·RAG 검증과 Agent binding

`ArgumentConstraint`가 제약 문법·선택값·Schema 투영·부모 범위 축소를 소유하고 Tool 계층은
원본 Tool Schema와의 교집합만 담당한다. fixed/selectable/bounded는 `ConstraintMode`,
fixed/exponential backoff는 `RetryBackoff`를 참조한다. 기존 RetryPolicy의 시도 계산은 변경하지 않는다.

RAG 검색의 `SearchMethod`/`SearchExpansion`/`RelationRanking`과 `search_schema`에서
API 검증과 검색 Tool Schema를 파생한다. RAGData의 반복된 enum/수치 검증을 위임하고,
명시 API 인자는 모델 호출 전, 해석된 설정은 검색 전 재검증한다. None 생략·양의 정수·bool/float
거부·원본 보존과 Hub의 명시 생성값을 유지한다.

`AgentBinding`은 Run의 원래 binding·request_context·실행 binding envelope와 불변 비교를
소유한다. 저장 Reader/Writer, 요청 dispatch, 실행/재개, Hub 제목 생성이 공개 조회·검증을
사용한다. RunAgentResolver는 최신 Agent 정의·Engine 선택을 계속 검증한다. 중첩 Agent의
선택 binding은 다른 구조이며 무조건 같은 record로 합치지 않는다. `CompletionRecords.validate_update`
역시 저장 시 기존 영수증 제거·예약 변조를 거부한다. EventContext는 Agent 소유 키 변경을 먼저 거부한다.

### 집중 검증 기록

환경은 앞 절과 동일하다. 아래는 각각 독립 실행이며 모두 전후 source/input hash가 동일하다.
실패 실행과 재실행을 합산하지 않는다. 전체 회귀·정적 근거 재검토는 상위 통합 게이트로 유예한다.

`o3-policy-l1`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o3-policy-l1/llm.json --modules tests.llm.test_tool_constraints tests.llm.test_retry tests.llm.test_project_policies tests.llm.test_policy_consumers tests.llm.test_policy_ownership tests.llm.test_interaction_storage tests.llm.test_operation_integrity tests.llm.test_usage_deletion
```

Exit 0; 발견/실행/Pass 83/83/83; Failure/Error/Skip 0/0/0; 누락/중복 0/0; expected failure/unexpected success/예상 외 ID 모두 0.
source `377f5e4987fa231f3d609fd2117c9b824b6a769e569413e6ef511312e89a0e87`,
input `177730e0bf66bb92dfac68cf8c8414fddf63d88c60687e0276e424d5fde4867c`.

`o3-rag-search-l1`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o3-rag-search-l1/llm.json --modules tests.llm.test_tool_constraints tests.llm.test_rag_components tests.llm.test_unified_rag tests.llm.test_rag_storage tests.llm.test_rag_query_evidence tests.llm.test_settings_consistency
```

Exit 0; 발견/실행/Pass 66/66/66; Failure/Error/Skip 0/0/0; 누락/중복 0/0; expected failure/unexpected success/예상 외 ID 모두 0.
source `9df88b699ac19cf148b65691ea9a920db4e9517c28ca1ea0e8d64730d168cb1a`,
input `e0ebe6b8d4326c5dd1dbfc8545cc937ecc3dd49f63325e698e812fb112b847e1`.

`o3-binding-l1`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o3-binding-l1/llm.json --modules tests.llm.test_completion_integrity tests.llm.test_agent_storage tests.llm.test_service_extensions tests.llm.test_extension_contracts tests.llm.test_domain_state_integrity tests.llm.test_run_transitions tests.llm.test_request_context
```

Exit 2: 발견 목록에 없는 `tests.llm.test_request_context`를 명령에 지정하여 러너가 실행 전에 거부했다. 발견/실행/Pass 집계 없음. 성공으로 계산하지 않는다. 실제 Agent/요청 모듈로 바로잡은 후속 명령에 결과를 별도 기록한다.
source `35a7604661cdc0e14f5ded85debe14014a96bd3adfaf9efbd1bfdc01c33ae247`,
input `753289fd0934f11a3de6c78757de0f3f022917e8cf3ec14cd6e74fd5a3b36c99`.

`o3-binding-l1-fixed`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o3-binding-l1-fixed/llm.json --modules tests.llm.test_completion_integrity tests.llm.test_agent_storage tests.llm.test_service_extensions tests.llm.test_extension_contracts tests.llm.test_domain_state_integrity tests.llm.test_run_transitions tests.llm.test_agent_execution tests.llm.test_session_agents tests.llm.test_multi_agent
```

Exit 0; 발견/실행/Pass 111/111/111; Failure/Error/Skip 0/0/0; 누락/중복 0/0; expected failure/unexpected success/예상 외 ID 모두 0.
source `35a7604661cdc0e14f5ded85debe14014a96bd3adfaf9efbd1bfdc01c33ae247`,
input `753289fd0934f11a3de6c78757de0f3f022917e8cf3ec14cd6e74fd5a3b36c99`.

`o3-binding-hub-l1`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/o3-binding-hub-l1/hub.json --modules tests.hub.test_naming tests.hub.test_multi_agent tests.hub.test_agent_selection tests.hub.test_agent_graph
```

Exit 0; 발견/실행/Pass 9/9/9; Failure/Error/Skip 0/0/0; 누락/중복 0/0; expected failure/unexpected success/예상 외 ID 모두 0.
source `35a7604661cdc0e14f5ded85debe14014a96bd3adfaf9efbd1bfdc01c33ae247`,
input `753289fd0934f11a3de6c78757de0f3f022917e8cf3ec14cd6e74fd5a3b36c99`.


## O4 실행 binding과 O5 Hub·Adapter 소비

`ToolRegistry.execution_binding`이 공통 Tool 정의·실행 계약·현재 scope 정책의 binding 투영을
소유한다. Loop·Graph·루트 Agent가 같은 투영을 사용하되 각 Engine의 별도 실행 identity는
그 Owner에 유지한다. Agent의 지역 호출 한도와 부모 Run 한도는 서로 다른 scope이며 합치지
않는다. `ToolExecutionScope.authorize`의 예약-before-await·부모 예산 전파·최신 승인, 공통
`checkpoint_usage`와 Agent별 사용량 선택 검증도 그대로 유지한다. 기존 중첩/확장 감사 근거를
재사용했고 실제 호출 관계와 이번 공통 투영 변경을 대조했다.

Run 오류 vocabulary를 도메인 모델로 옮겨 서비스 구현을 import하지 않아도 같은 선언을
사용하게 했다. Step·Checkpoint·Completion·Usage의 코드 역시 해당 Owner Enum을 참조한다.
Provider capacity의 원래 선언은 ProviderErrorCode에 있다. 다른 의미인 Loop의 tool_timeout
설정 키와 InteractionView의 request_cancelled 사유는 오류 코드와 합치지 않는다.

Hub의 native thinking dict 해석을 제거하고 정규 CompletionResult의 문자열을 사용한다.
Hub가 추가하던 reasoning_effort Schema를 LiteLLM Adapter Schema로 옮겼다. string/null과
최소 길이의 기존 UI 계약을 Adapter가 선언하며 기본값이나 닫힌 SDK allow-list는 만들지 않는다.
주입 Adapter는 자기 Schema를 유지한다. 알려진 SDK 인자 형식은 이제 Backend 설정 검증에도
같은 선언이 적용된다.

오류 코드 참조 교체 중 import 3개가 module docstring 안에 삽입되어 발생한 NameError를
집중 시험이 검출했다. 실제 import로 수정했으며 기존 실패 코드·부분 출력 Assertion은 유지했다.
Hub의 resume 표시 Fixture 하나도 필수 completions=[]를 생략해 오류가 났으므로 실제 저장
형식에 맞게 보완했다. 원래 실패를 제외하거나 성공으로 계산하지 않는다.

`o5-codes-l1`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o5-codes-l1/llm.json --modules tests.llm.test_error_boundaries tests.llm.test_provider_diagnostics tests.llm.test_provider_codec tests.llm.test_common_provider_acceptance tests.llm.test_checkpoint_integrity tests.llm.test_completion_integrity tests.llm.test_policy_consumers
```

Exit 1; 발견/실행/Pass 63/63/60; Failure/Error/Skip 4/0/0; 누락/중복 0/0; expected failure/unexpected success/예상 외 ID 모두 0.
source `113fd7cd4d1148ab7756d9f7c8a063241d649e784f2dce51ea3bcde70e41d4e3`,
input `084770f0cfc0224c52fe12921275a1d8bc92d2bc7b4ebf08021db18b758ef0da`.

`o5-codes-hub-l1`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/o5-codes-hub-l1/hub.json --modules tests.hub.test_error_contract tests.hub.test_backend_diagnostics tests.hub.test_provider_recovery tests.hub.test_completion_display tests.hub.test_interaction_updates tests.hub.test_tool_history
```

Exit 1; 발견/실행/Pass 49/49/48; Failure/Error/Skip 0/1/0; 누락/중복 0/0; expected failure/unexpected success/예상 외 ID 모두 0.
source `113fd7cd4d1148ab7756d9f7c8a063241d649e784f2dce51ea3bcde70e41d4e3`,
input `084770f0cfc0224c52fe12921275a1d8bc92d2bc7b4ebf08021db18b758ef0da`.

`o5-codes-l1-fixed`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o5-codes-l1-fixed/llm.json --modules tests.llm.test_error_boundaries tests.llm.test_provider_diagnostics tests.llm.test_provider_codec tests.llm.test_common_provider_acceptance tests.llm.test_checkpoint_integrity tests.llm.test_completion_integrity tests.llm.test_policy_consumers
```

Exit 0; 발견/실행/Pass 63/63/63; Failure/Error/Skip 0/0/0; 누락/중복 0/0; expected failure/unexpected success/예상 외 ID 모두 0.
source `b82af20b2b11c8bb514ac7113f21d1071f30232b985f59c2885a8ecb2049b882`,
input `426993789817e4b239e33962e96785229d27f847733f46ef031673e3bb0fc62d`.

`o5-codes-hub-l1-fixed`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/o5-codes-hub-l1-fixed/hub.json --modules tests.hub.test_error_contract tests.hub.test_backend_diagnostics tests.hub.test_provider_recovery tests.hub.test_completion_display tests.hub.test_interaction_updates tests.hub.test_tool_history
```

Exit 0; 발견/실행/Pass 49/49/49; Failure/Error/Skip 0/0/0; 누락/중복 0/0; expected failure/unexpected success/예상 외 ID 모두 0.
source `b82af20b2b11c8bb514ac7113f21d1071f30232b985f59c2885a8ecb2049b882`,
input `426993789817e4b239e33962e96785229d27f847733f46ef031673e3bb0fc62d`.

`o4-binding-l2`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o4-binding-l2/llm.json --modules tests.llm.test_agent_execution tests.llm.test_agent_delegation tests.llm.test_graph_agent_contract tests.llm.test_nested_graph tests.llm.test_extension_contracts tests.llm.test_interaction_storage tests.llm.test_operation_integrity tests.llm.test_usage_deletion tests.llm.test_checkpoint_integrity tests.llm.test_test_workspaces
```

Exit 0; 발견/실행/Pass 121/121/121; Failure/Error/Skip 0/0/0; 누락/중복 0/0; expected failure/unexpected success/예상 외 ID 모두 0.
source `c97fbf85d5ff3a376af3e141ef80a11ce62f180a7c769a4aa7a0f569ee4e54ac`,
input `4265d9f35ac57754b4661ffdab6840f065defdaf0b0b089da863e72ff84ea2f1`.

`o5-settings-l1`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/o5-settings-l1/hub.json --modules tests.hub.test_settings tests.hub.test_presets_widgets tests.hub.test_provider_stabilization
```

Exit 0; 발견/실행/Pass 32/32/32; Failure/Error/Skip 0/0/0; 누락/중복 0/0; expected failure/unexpected success/예상 외 ID 모두 0.
source `c97fbf85d5ff3a376af3e141ef80a11ce62f180a7c769a4aa7a0f569ee4e54ac`,
input `4265d9f35ac57754b4661ffdab6840f065defdaf0b0b089da863e72ff84ea2f1`.

R3 mutation 실제 명령: `.venv-linux312/bin/python -m tests.llm.check_budget_mutation`.
Exit 0, 두 대상 모두 원래 실제 효과 Assertion으로 변이를 검출했다(의도한 failure 2, error/skip 0).
이는 일반 회귀의 실패를 허용하는 것이 아니라 결함 주입 시 두 번째 효과가 검출되는지 확인한
별도 음성 검증이다. `/workspace/llm-clean-resume/ownership/o4-budget-mutation.log`에 원본을
보존한다. o4-binding-l2와 같은 source/input의 변경 없는 실행 구간에 수행했다.

모든 실행은 소스·Fixture·환경 전후 동일성을 확인했다. O4는 관련 통합 안전성 집합의 통과이고
전체 LLM/Hub·정적·문서 게이트 완료는 아니다. 추가 선언/소비자 감사와 상위 Level 3를 계속한다.


## O3 추가 선언·Reader 검증과 O5 소비자 정리

RunPolicy·ContextPolicy·OutputBuffer의 dataclass 필드가 Schema와 검증을 소유하도록 연결했다.
Loop/Graph의 명시 옵션은 각 Engine의 같은 Schema로 검사하고, Graph 실행 직전 deadline
재검증은 유지했다. CompletionPolicy 생성자도 동일 설정 검증을 호출한다. FileTools의
파일 크기 인자와 LexicalCache 용량은 해당 Owner Schema를 재사용한다. ReviewPolicy의
hook_timeout 최소값도 한 선언을 공유한다. 기존 None·0·정확한 int·명시 옵션 의미를 보존한다.

공유 도메인 storage_version 검사는 `validate_domain_version` 한 곳에 모았다. 파일 Reader,
atomic Writer와 직접 모델 Reader가 같은 검사를 호출한다. 저장 직전 원본 재검증과
트랜잭션 순서는 유지한다. 직접 모델 Reader에도 잘못된 버전이 들어갈 수 없음을 독립
원본 보존 Assertion으로 보강했다.

Tool 오류 코드는 의존성이 없는 `core.tool_codes.ToolErrorCode`, Hub 관리/가져오기 종류는
`hub.components.ManagerKind`/`ImportKind`가 선언한다. 실행·프로세스·진단·화면이 참조한다.
독립 Adapter에는 SDK reasoning_effort 필드를 주입하지 않는 시험을 추가했다.

아래 실행은 각각의 source/input 전후가 동일하다. 성공 실행의 Failure/Error/Skip,
expected failure/unexpected success/누락/예상 외/중복은 모두 0이다. 실행을 합쳐 하나의
전체 회귀로 표현하지 않는다. 관련 선언·소비자 검증의 Level 1/2이며 상위 전체 게이트가 남는다.

`o3-policy-fields-l1`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o3-policy-fields-l1/llm.json --modules tests.llm.test_owner_declarations tests.llm.test_project_policies tests.llm.test_policy_ownership tests.llm.test_policy_consumers tests.llm.test_service_extensions tests.llm.test_settings_consistency
```

Exit 0; 발견/실행/Pass 67/67/67; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `02fc1da074d351ca1cf93aa1aebb622f916683dbdb9626d0dd0b810324fe8ecd`,
input `76ce56307277f077d3de157157210d6de94abc90f3ef8830e87fcf7fb34418df`.

`o5-adapter-schema-l1`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/o5-adapter-schema-l1/hub.json --modules tests.hub.test_settings tests.hub.test_presets_widgets
```

Exit 0; 발견/실행/Pass 23/23/23; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `02fc1da074d351ca1cf93aa1aebb622f916683dbdb9626d0dd0b810324fe8ecd`,
input `76ce56307277f077d3de157157210d6de94abc90f3ef8830e87fcf7fb34418df`.

`o3-declared-limits-l1`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o3-declared-limits-l1/llm.json --modules tests.llm.test_owner_declarations tests.llm.test_project_policies tests.llm.test_policy_ownership tests.llm.test_policy_consumers tests.llm.test_operational_storage tests.llm.test_engine_outputs tests.llm.test_loop tests.llm.test_graph tests.llm.test_settings_consistency
```

Exit 2; 발견 단계 미완료. 실행/통과 수를 선언하지 않는다.
명령의 `tests.llm.test_graph`가 존재하지 않아 Exit 2. 실제 `test_graph_engine`·`test_langgraph_engine`으로 수정했다. Graph._deadline의 실행 직전 호출을 확인하고 Schema 위임 메서드를 유지한 뒤 후속 실행을 수행했다.
source `46e6d613f2e15153a3df5878f0ce5a0e8c33053cb51a3186fa40344ece596d20`,
input `ee5572a678f7fda361111141d04f5f5e8c4d304af5ee35bd68d64b03dffa140b`.

`o3-declared-limits-l1-fixed`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o3-declared-limits-l1-fixed/llm.json --modules tests.llm.test_owner_declarations tests.llm.test_project_policies tests.llm.test_policy_ownership tests.llm.test_policy_consumers tests.llm.test_operational_storage tests.llm.test_engine_outputs tests.llm.test_loop tests.llm.test_graph_engine tests.llm.test_langgraph_engine tests.llm.test_settings_consistency
```

Exit 0; 발견/실행/Pass 141/141/141; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `468c7db4c3b3ee8da45ee2673ee37272b2aae5178fd828730d8276a2265ced8f`,
input `9fc94119c3ae040ba9a52e511f7259cbd4d0dceb314de1d9df8e6f7ec59b9698`.

`o3-storage-limits-l2`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o3-storage-limits-l2/llm.json --modules tests.llm.test_owner_declarations tests.llm.test_domain_state_integrity tests.llm.test_repositories tests.llm.test_transactions tests.llm.test_builtin_tools tests.llm.test_schema_ownership tests.llm.test_output_integrity
```

Exit 0; 발견/실행/Pass 106/106/106; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `5510ab9308b436d3a3214181dbf2853150a5c99a6b2bd5d1faea0d869fd45002`,
input `987798c50cbceaf0868cf3500bc7a1492a89c439b2f2f784a0d99b7fab4306b4`.

`o3-policy-errors-l1`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o3-policy-errors-l1/llm.json --modules tests.llm.test_policy_consumers tests.llm.test_completion_processing tests.llm.test_completion_review tests.llm.test_completion_review_crash tests.llm.test_tool_workers tests.llm.test_tool_declarations
```

Exit 0; 발견/실행/Pass 68/68/68; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `d92af946a87188caf07e940ad9a5b26dde70beb89a70205e4f915152c0bc0977`,
input `5a04b3d484f8436c52c1cc0ed9e0d8b5ea7818abf36f64daff20514cad5723de`.

`o5-catalog-l1`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/o5-catalog-l1/hub.json --modules tests.hub.test_component_catalog tests.hub.test_component_managers tests.hub.test_failure_diagnosis tests.hub.test_error_contract
```

Exit 0; 발견/실행/Pass 26/26/26; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `d92af946a87188caf07e940ad9a5b26dde70beb89a70205e4f915152c0bc0977`,
input `d97b6524c6f42c0b32fa6bc2fcdc200e69419351a57abf4300b5f5df52936c9a`.


## O6 감사 보강과 OW-07 개별 재검토

S7은 기본 인덱스 간격·대화 저장 종류·출력 종류·정확한 Paths 속성·Completion 컨테이너·
Project Facade의 중복 설명 조합을 검출한다. 같은 파일의 다른 심볼에 선언을 옮겨도 걸리는
음성 시험을 추가했다. S6에는 native thinking 접근을 추가했고 Adapter의 정규 모델 소비를
확인한다. CI의 기존 전체 LLM/Hub·R3 Job을 유지하고 S7도 필수 검사에 추가했다.

기존 159건을 강화된 계약으로 대조했다. 발생이 제거된 24건은 승인 목록에서도 제거했고,
남은 135건은 실제 Owner·소비자·Schema·기존 검증 범위를 다시 확인했다. 공유 도메인 버전
guard로 이동한 3건은 새 위치와 직접 Reader 시험으로 별도 분류했다. 현재 138건의 상세
판단은 `tests/llm/fixtures/architecture_reviews.json`에 각 ID별로 기록한다. 이전 판단은
기준 커밋에 보존한다. 자동 baseline 갱신이나 파일 전체 면제를 추가하지 않았다.

S1–S7 strict와 문서 검사(143 문서·183 Python fence·1060 상대 링크·36 anchor)는 Exit 0.
정적 후보 분류는 동적 호출 관계나 일반 필드 접근 5358건 전체에 대한 완료 증거가 아니다.
원본/잔여 항목의 비교 원자료는 `/workspace/llm-clean-resume/ownership/review-decisions.json`이다.

`o6-static-reader-l2`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o6-static-reader-l2/llm.json --modules tests.llm.test_architecture_checks tests.llm.test_current_contracts tests.llm.test_session_domain tests.llm.test_domain_state_integrity tests.llm.test_rag_components
```

Exit 1; 발견/실행/Pass 87/87/60; Failure/Error/Skip 0/27/0; 누락/중복 0/0.
source `80bbeff0188b3f5b38a5930d49bf7a3d0bc64ba98ec22977f9cfaae36ecd08e1`,
input `e4cee3cad05d38680b5817dc65bb2804d9bbe579d004d9335bc615e465653545`.

`o6-static-reader-l2-fixed`:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o6-static-reader-l2-fixed/llm.json --modules tests.llm.test_architecture_checks tests.llm.test_current_contracts tests.llm.test_session_domain tests.llm.test_domain_state_integrity tests.llm.test_rag_components
```

Exit 0; 발견/실행/Pass 87/87/87; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `08dcef995f96db5a1b7ae91a74823aa083f10afea5ea0a680fa971fb877a9739`,
input `0ee74b9e7352a5d64b2a46c8f1c94612ca2ee905207ff6ec6abbc01663c9a626`.

최초 27 errors는 RAG describe_config의 LexicalCache import 누락 한 원인이었다. import를
수정하고 동일 87개 집합을 재실행해 통과했다. Assertion·원본 보존·중단/재개·캐시 무효화
검증은 유지했다. 두 실행 모두 소스/환경 전후 동일, xfail/xpass/예상 외 ID는 0이다.
다음 게이트는 현재 O1–O6 통합 제품 소스의 전체 LLM/Hub·R3·정적·문서·실환경 검증이다.


## O1–O6 추가 소비 경로와 최초 통합 실패의 수정

최초 통합 실행 `o1-o6-integrated-l3`은 실패했다. 실패를 제외하거나 부분 실행을 합쳐
전체 성공으로 기록하지 않았다.

- LLM의 main_domain_optimization 두 시험은 주입 Journal이 받는 state-only Paths 구조에
  새 `.outputs` 속성을 요구해 실패했다. `RunPaths.output_path(state)`에 파일명 선언을
  두고 Repository가 기존 state 경계로 호출하도록 수정했다. 기존 두 시험은 변경하지 않았다.
- Hub message_timing의 네 시험 메서드(7 Error)는 합성 Run의 필수 completions 컨테이너,
  reasoning 영수증의 필수 필드가 없었다. 정상 Run에는 명시 빈 컨테이너를, 합성 영수증에는
  정규 CompletionResult 기록을 제공했다. 원래 시간·실제 표시 Assertion과 제품의 손상 거부를 유지했다.
- 최초 통합과 같은 source/input의 `o1-o6-integrated-aux`는 R3 두 실제 효과 mutation 검출,
  S1–S7, 문서, LLM/Hub host·격리·X11, offline SDK 검사 모두 Exit 0이었다. 이 결과는 실패한
  전체 회귀를 대체하지 않는다. 원본 로그와 보조 명령은 해당 디렉터리의 `execution.json`에 보존한다.

추가 Owner 연결과 영향 범위:

| 단계 | 정규 Owner와 실제 소비자 | 보존한 계약·검증 선정 |
|---|---|---|
| O1/O2 | RunPaths.output_path → RunRepository·주입 Journal | 기존 구조적 Paths 확장, 스트림 종료·손상 출력·목록 비용 |
| O2 | ResumeRecord/RestartRecord → RunManager·Graph/Loop·Agent·History·Hub | 계보 필드·사본 Projection, 승인·필수 증거·예산은 최신 실행 경계에서 검증 |
| O3 | CheckpointEnvelope/CheckpointOperation → Scope·Repository·Graph·Loop·Agent | 공통 envelope와 child opaque payload 분리, private 이름·원본·중첩 재개·효과 횟수 |
| O1/O5 | ResourceKind/StepKind, ActivityEventType, PlanBlockerCode → 실행·계획·Hub | 내장 종류만 참조; 확장 kind는 열려 있음. 표시 Tool/Reasoning과 Backend Step은 서로 다른 Owner |
| O5 | HubOutputBlock/HubTraceItem/OperationCode → parser·renderer·오류 UI | 허용된 사용자 hub-* 출력 확장·표시 의미 보존. SDK·Markdown 표준의 다른 의미와 혼합하지 않음 |
| O2/O5 | ProjectConfiguration.view/with_values/validate_edit, ProjectConfigForm → 설정·Preset·RAG 설정·채팅 Project UI | 원본과 edit preview 분리·CAS 보존, host-fixed 비교 이관, effective 비저장·M/N/V·custom Adapter |
| O3 | ControlNodeType/ConditionOperator/LoopLimit/JoinWait → Workflow Schema/검증·Graph 실행 | 제어 종류·비교 연산·반복 한도 의미 공유, 사용자 action handler는 열린 경계 유지 |
| O5 | ErrorCategory/ErrorReason/Confidence/DiagnosticCode·StepErrorCode → Diagnostic 분류·Provider 힌트·Hub | 같은 관찰 분류를 재선언하지 않음. 알 수 없는 code/metadata는 보존하고 분류로 재실행을 허가하지 않음 |
| O5 | RecoveryState → ProviderRecovery·오류 표시, MemoryConflictError.code → UI | 앱 소유의 명시적 복구 결과만 표시. Backend retry·승인·예산 규칙을 변경하지 않음 |

아래 Level 1/2 범위는 해당 Owner의 계약과 직접 소비자·영향 통합 시험이다. 전체 회귀는
현재 통합 소스를 사용하는 O7로 묶었다. 미검증 소단계를 완료로 승격하지 않는다.
기록된 모든 실행은 source/input 전후 동일성을 확인했다. expected failure, unexpected success,
예상 외 test ID, 누락, 중복, Skip은 모두 0이다. Error 수는 subtest별 집계일 수 있으므로
실패한 메서드 수와 구분한다. 환경은 앞서 명시한 Python 3.12.14와 실제 host 고정 SHA다.

### o1-o6-integrated-l3

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/o1-o6-integrated-l3/hub.json
```

Exit 1; 발견/실행/Pass 586/586/582; Failure/Error/Skip 0/7/0; 누락/중복 0/0.

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o1-o6-integrated-l3/llm.json
```

Exit 1; 발견/실행/Pass 2273/2273/2271; Failure/Error/Skip 0/2/0; 누락/중복 0/0.
source `08dcef995f96db5a1b7ae91a74823aa083f10afea5ea0a680fa971fb877a9739`.
input `114ae4d80565d7442376ebffac24ed3c5c6d5506bf81980a70ab3376b3e92014`.

### o1-output-extension-l2-fixed

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o1-output-extension-l2-fixed/llm.json --modules tests.llm.test_main_domain_optimization tests.llm.test_owner_declarations tests.llm.test_output_integrity tests.llm.test_extension_contracts
```

Exit 0; 발견/실행/Pass 46/46/46; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `72dbca4d588705560011828c1edcabb145a7d2b89be2aba7c5a648d207e62ff7`.
input `d7f781b1c4e0c8e8d4654ea9aca1dd36eab8e46a8d8a8b67c2f5542fa60d881c`.

### o2-timing-receipts-l1-fixed

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/o2-timing-receipts-l1-fixed/hub.json --modules tests.hub.test_message_timing tests.hub.test_snapshot_projection tests.hub.test_completion_display
```

Exit 0; 발견/실행/Pass 12/12/12; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `72dbca4d588705560011828c1edcabb145a7d2b89be2aba7c5a648d207e62ff7`.
input `d7f781b1c4e0c8e8d4654ea9aca1dd36eab8e46a8d8a8b67c2f5542fa60d881c`.

### o2-continuations-l2

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o2-continuations-l2/llm.json --modules tests.llm.test_owner_declarations tests.llm.test_architecture_checks tests.llm.test_checkpoint_lifecycle tests.llm.test_checkpoint_integrity tests.llm.test_graph_checkpoints tests.llm.test_nested_graph tests.llm.test_loop tests.llm.test_agent_execution tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_cross_session_requests
```

Exit 0; 발견/실행/Pass 194/194/194; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `ec613650ec54a6ae211a8fa5a08610fc526245080d320ac3d49edbc915a269f5`.
input `2b9e970365c394e2f0e49d62b6eab7e0a75ff4fce552bba12902091cacdb987f`.

### o2-continuations-hub-l2

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/o2-continuations-hub-l2/hub.json --modules tests.hub.test_request_history tests.hub.test_context tests.hub.test_turn_deletion tests.hub.test_approval_continuation
```

Exit 0; 발견/실행/Pass 26/26/26; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `ec613650ec54a6ae211a8fa5a08610fc526245080d320ac3d49edbc915a269f5`.
input `2b9e970365c394e2f0e49d62b6eab7e0a75ff4fce552bba12902091cacdb987f`.

### o3-checkpoint-envelope-l2

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o3-checkpoint-envelope-l2/llm.json --modules tests.llm.test_owner_declarations tests.llm.test_architecture_checks tests.llm.test_nested_graph tests.llm.test_graph_checkpoints tests.llm.test_loop tests.llm.test_loop_steering tests.llm.test_agent_execution tests.llm.test_checkpoint_integrity tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity
```

Exit 0; 발견/실행/Pass 183/183/183; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `da83159efe7b416490738d2b6d643a4f9484c8c5c99277110fe5017a26e03571`.
input `0a8a2fea84e0078e2c64613ab072535abf7bf569dfd0436a8f1d87eeee740c75`.

### o1-domain-kinds-l2

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o1-domain-kinds-l2/llm.json --modules tests.llm.test_owner_declarations tests.llm.test_architecture_checks tests.llm.test_project_activity tests.llm.test_configuration_results tests.llm.test_checkpoint_lifecycle tests.llm.test_rag_job_state_integrity tests.llm.test_engine_outputs tests.llm.test_extension_contracts
```

Exit 0; 발견/실행/Pass 123/123/123; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `543053e062b88aae67b3315d2aa516d7d1ad822726dd2d0344e5704179f8a7f7`.
input `f09c57e83b6662bbae9d1efbfd20fea1d075056a320869e6b547e9ed9cddd109`.

### o5-presentation-kinds-l2

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/o5-presentation-kinds-l2/hub.json --modules tests.hub.test_output tests.hub.test_output_interaction tests.hub.test_snapshot_projection tests.hub.test_error_contract tests.hub.test_failure_diagnosis tests.hub.test_provider_activity tests.hub.test_tool_history
```

Exit 0; 발견/실행/Pass 40/40/40; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `543053e062b88aae67b3315d2aa516d7d1ad822726dd2d0344e5704179f8a7f7`.
input `f09c57e83b6662bbae9d1efbfd20fea1d075056a320869e6b547e9ed9cddd109`.

### o2-config-view-l2

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o2-config-view-l2/llm.json --modules tests.llm.test_owner_declarations tests.llm.test_settings_consistency tests.llm.test_settings_contract tests.llm.test_configuration_results tests.llm.test_project_component_settings tests.llm.test_configuration_objects tests.llm.test_extension_contracts
```

Exit 0; 발견/실행/Pass 82/82/82; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `6e25b73ff593e0d08e92acfacdc6ac0ca48b6af8edf7e1aab7433a44c72e0b22`.
input `cdbd710279f391479ef53f2bdeddaa2076bfc8a4225b760400a98b5b3fd03d81`.

### o5-config-view-l2

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/o5-config-view-l2/hub.json --modules tests.hub.test_settings tests.hub.test_settings_tabs tests.hub.test_presets_widgets tests.hub.test_settings_json tests.hub.test_settings_project_flow tests.hub.test_component_managers
```

Exit 0; 발견/실행/Pass 42/42/42; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `6e25b73ff593e0d08e92acfacdc6ac0ca48b6af8edf7e1aab7433a44c72e0b22`.
input `cdbd710279f391479ef53f2bdeddaa2076bfc8a4225b760400a98b5b3fd03d81`.

### o3-workflow-kinds-l2

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o3-workflow-kinds-l2/llm.json --modules tests.llm.test_workflow_format tests.llm.test_graph_decisions tests.llm.test_graph_engine tests.llm.test_langgraph_engine tests.llm.test_nested_graph tests.llm.test_graph_checkpoints tests.llm.test_agent_workflow tests.llm.test_graph_agent_contract
```

Exit 0; 발견/실행/Pass 125/125/125; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `d5f38f2055f3dbcd7ec4c94ba0e65dd6c19e7bc85fe1afe137050a3e8e1b777b`.
input `bb6b46e5593f8ce23d3f8369d609331ea207483947ef12f1a6803c2dbf2b7d3c`.

### o5-project-error-l2

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/o5-project-error-l2/hub.json --modules tests.hub.test_settings_project_flow tests.hub.test_error_contract tests.hub.test_failure_diagnosis
```

Exit 0; 발견/실행/Pass 19/19/19; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `d5f38f2055f3dbcd7ec4c94ba0e65dd6c19e7bc85fe1afe137050a3e8e1b777b`.
input `bb6b46e5593f8ce23d3f8369d609331ea207483947ef12f1a6803c2dbf2b7d3c`.

### o5-diagnostics-l2

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o5-diagnostics-l2/llm.json --modules tests.llm.test_error_boundaries tests.llm.test_provider_diagnostics tests.llm.test_engine_outputs tests.llm.test_owner_declarations
```

Exit 0; 발견/실행/Pass 51/51/51; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `d16f7a7ee2a2776046ee681c4aaa7bb40796eb6ec3d9d0b616c5615df32432f0`.
input `55915a39de9c6527d64481fa9a74a075dd590f95bde8b666bf65595418ebb2dc`.

### o5-diagnostics-hub-l2

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/o5-diagnostics-hub-l2/hub.json --modules tests.hub.test_error_contract tests.hub.test_failure_diagnosis tests.hub.test_provider_recovery tests.hub.test_retry_observations tests.hub.test_retry_ui tests.hub.test_tool_retry
```

Exit 0; 발견/실행/Pass 62/62/62; Failure/Error/Skip 0/0/0; 누락/중복 0/0.
source `d16f7a7ee2a2776046ee681c4aaa7bb40796eb6ec3d9d0b616c5615df32432f0`.
input `55915a39de9c6527d64481fa9a74a075dd590f95bde8b666bf65595418ebb2dc`.

진단 분류 이관 후 UI의 raw reason 허용값 후보 한 건이 추가로 제거되었다. 현재 정적 검토는
기존 159건 중 제거 25건·유지 134건, 새 공통 버전 guard 3건을 합한 137건이다. 앞의
138건 기록은 당시 실행의 실제 수치로 보존한다. S7의 bounded AST 규칙은 동적 호출이나
사용자 확장 payload를 완전히 증명하지 않으므로 아래 Owner 연결표와 실제 통합 검증을 함께 본다.

## O0 종결 대조: 선언에서 실제 소비 경로까지

OW-01–07과 조사 중 확인한 공통 계약은 아래 연결표로 추적한다. 기존 38개 데이터군의
필수 기록·참조·손상·삭제 검증은 [H2 종결 대조](lifecycle-verification.md#h2-종결-대조와-남은-단계)와
H3–H5/P1/A–F의 후속 결과를 재사용했다. 당시 외부 원감사 REPORT.md가 없는 사실도
유지한다. 38개 원행렬을 새로 읽거나 다시 작성했다고 주장하지 않는다.

| 계약군 | 선언·검증/저장 Owner | 실제 소비·책임 경계와 판정 |
|---|---|---|
| 기술 기본값 | OutputJournal, ProjectConversations, FileCatalog, BaseEngine/GraphEngine | Repository·BackendServices·SessionManager가 선언 참조. 서로 다른 pool/buffer의 같은 숫자는 합치지 않음 |
| Project 대화 저장 선택 | ConversationStorage | Project·Schema·storage factory·Hub preset·전송/보관 소비. None은 주입 팩토리 계약 |
| Project 설정·설명 | ProjectConfig, ProjectConfigForm, ProjectConfiguration | Manager는 잠금/CAS, Facade는 위임, Hub는 detached view. 형식·host-fixed 의미 중복 제거 |
| Run/Step 상태와 오류 | RunStatus/StepStatus, validate_*_transition, RunTransition/RunErrorCode/StepErrorCode | Manager의 최신 전이 검사와 Repository의 불변 갱신 검사 유지. UI 상태는 별도 표시 계약 |
| 내장 리소스·Step 종류 | ResourceKind, StepKind | Engine·서비스·Hub가 참조. ResourceRef의 custom kind를 닫지 않음 |
| 도메인 기록·버전 | dataclass 필드, validate_domain_version, _stored_fields | 파일 Reader·atomic Writer·직접 from_record가 같은 선언을 사용. 개별 Component 형식 버전과 합치지 않음 |
| 저장 경로·출력 인덱스 | Project/Session/Run/StepPaths, OutputJournal | Repository만 저장 구조를 소비. state-only 주입 Paths, 인덱스 0 비활성·tail 손상 보존 |
| Completion 영수증·사용량 | CompletionResult, CompletionRecords | RunRepository·결과·사용량·보관·Hub Projection. missing≠empty, unknown≠0, 중복 ID 거부 |
| 출력·Trace | EngineOutput/OutputVisibility/OutputOperation, HubOutputBlock/HubTraceItem | 공통 Backend 출력과 Hub 렌더링 종류를 구분. parser/renderer는 해당 표시 Owner 참조 |
| 실행 정책·제약 | RunPolicy/ContextPolicy/OutputBuffer, ArgumentConstraint, CompletionPolicy/ReviewPolicy | Schema와 검증이 선언에서 파생. 서로 다른 deadline·scope·host None override를 합치지 않음 |
| RAG 검색·캐시 | SearchMethod/Expansion/RelationRanking, LexicalCache | Schema·설정·검색·retrieval 도구가 같은 허용값/범위 사용. identity/active pointer/Job 저장 의미 변경 없음 |
| Agent binding | AgentBinding, RunAgentResolver, RequestAdmission | 신규·Run 저장·admission·Hub context는 공개 projection/검증. child Agent의 다른 실행 binding은 별도 계약 |
| Tool 실행 binding | ToolRegistry.execution_binding, ToolExecutionScope.binding | Loop/Graph/Agent가 동일 tools/contracts/policy projection 사용. root scope 필수와 child scope narrowing 유지 |
| 승인·예산·실제 효과 | InteractionRepository, ToolExecutionScope.authorize, ToolExecutor, OperationRepository | 생성/응답/실행 직전/재개에서 최신 증거 재검증. 같은 검증 호출을 제거하지 않음. 예약은 await 전, 효과는 승인 뒤 |
| Resume/Restart 계보 | ResumeRecord/RestartRecord, RunManager admission | History·Engine·Hub는 Owner field/projection 사용. descriptor는 실행 권한이 아니며 최신 digest·승인·영수증 검사는 admission에 유지 |
| 공통 Checkpoint envelope | CheckpointEnvelope/CheckpointOperation, EngineCheckpointScope | Repository는 공통 저장, Engine/handler는 private payload. Graph/Loop 이름·format은 각 Owner, 불명확한 효과 재실행 금지 |
| Workflow 제어 구조 | ControlNodeType/ConditionOperator/LoopLimit/JoinWait, WorkflowGraph | Schema·구조 검증·Graph 실행 연결. action handler의 자체 config/입출력 Schema·검증은 선택 구현체에 위임 |
| Provider/SDK | ModelRequest/Response, LiteLLMAdapter와 providers의 전송/codec 협력 구현 | SDK 필드는 Adapter 경계에서만 해석. Engine/Hub는 공통 모델/Adapter Schema. 원본 생략·명시 retry·주입 client 계약 유지 |
| 진단·오류 분류 | Diagnostic, ErrorCategory/ErrorReason/Confidence, 해당 ErrorCode Owner | Hub는 Backend 분류를 표시. 알려지지 않은 code는 원본 보존. recovery 권한을 분류에서 만들지 않음 |
| Application 복구 | ProviderRecovery/RecoveryState, RetrySummary | Hub의 명시 선택과 관찰 상태. Backend RetryPolicy·SDK 내부 retry와 별개. 취소/partial 응답/실제 HTTP 횟수 시험 유지 |
| 계획·거부·관리 기능 | PlanBlockerCode, 각 계획 Owner, Hub OperationCode/ManagerKind/ImportKind | 계획 조회가 실행 허가 원본이 되지 않음. 실행 시 revision·참조·활성 작업 재검증 |
| Component 전용 기록 | 각 Component의 Schema·Reader/Writer·참조/백업 검증 | Memory/Refinement/RAG/Tool/Skill 등의 내부 협력 객체는 해당 도메인 Owner. 공통 인프라로 업무 의미를 옮기지 않음 |
| 공통 원자성·수명 | StorageIO/TransactionManager, stream/scope/task 생성 Owner | 잠금·CAS·rollback/after_commit 순서 유지. aclosing/finally와 취소 후 await 종료 유지, 자동 cleanup retry 추가 없음 |
| 외부 규격·UI 자체 데이터 | JSON Schema/Markdown/X11/CLI 경계, Hub Form/locale/renderer | 외부 표준을 해석하는 경계와 표시 Owner의 선언. 동일 철자만으로 Backend 필드와 합치지 않음 |

5358개 일반 키 접근 후보는 위반 건수나 완료율의 분모가 아니다. Component 내부 Reader,
공개 Schema 순회, opaque 사용자 metadata, 외부 프로토콜, UI 전용 표시 자료를 함께 포함한다.
파일명·발생 횟수만으로 면제하지 않고 Owner와 실제 계약 의미로 판단했다. 확인된 외부 재선언은
위 구현으로 연결했고, 독립 wire Fixture의 리터럴은 기대값 원본으로 유지했다. 같은 Owner의
협력 모듈에서 수행하는 기록 검증·직렬화와 외부 소비자의 별도 부분 Reader를 구분한다.

정적 S7은 명시한 리터럴 패턴에만 적용한다. 동적 registry/getattr, 선택된 custom 구현의
private payload, 새 SDK 옵션 전체를 정적으로 증명하지 않는다. 이 경계는 열린 계약과 실제
확장·주입·중첩·원본 보존 시험으로 확인하며, 새 계약 값이 추가되면 Owner 연결표와 검출
범위를 다시 점검해야 한다. 이는 현재 발견한 미해결 안전성 실패를 면제하는 규칙이 아니다.

O0의 현재 OW 항목·확인된 공통 계약 연결은 정리했고 O1–O6 구현과 집중 검증을 마쳤다.
전체 기능/안전성 완료는 현재 소스의 O7 Level 3 및 정확한 Push SHA 원격 CI 결과와 구분한다.


## O7 최종 통합 Level 3

O1–O6의 추가 변경을 모두 포함한 같은 통합 소스를 고정하여 전체 LLM과 Hub를 각각
한 번 실행했다. 서로 다른 부분 실행의 결과를 합친 전체 통과 수치가 아니다.

| Suite | 발견/실행/Pass | Failure/Error/Skip | 누락/중복 | 시간 | Exit |
|---|---|---|---|---|---|
| llm | 2280/2280/2280 | 0/0/0 | 0/0 | 1144.610초 | 0 |
| hub | 586/586/586 | 0/0/0 | 0/0 | 1010.389초 | 0 |

expected failure/unexpected success/예상 외 ID도 모두 0이다. R3의 두 mutation은 각각
원래 실제 파일 효과 Assertion으로 검출되었다. S1–S7 미해결 0·현재 검토 137건, 문서와
LLM/Hub host·격리·X11 및 실제 SDK offline 전송 검증 6개 보조 명령도 모두 Exit 0이다.

source hash는 `d16f7a7ee2a2776046ee681c4aaa7bb40796eb6ec3d9d0b616c5615df32432f0`이며
전체 회귀와 보조 검증의 실행 전후가 모두 같다.

input hash는 `d4ba29041458578db81655f175e860b9fd3425651cb16c3dc038a5d5d92391a1`이며
전체 회귀와 보조 검증의 실행 전후가 모두 같다.

전체 및 보조 게이트의 실제 명령:

```sh
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/o7-final-l3/llm.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/o7-final-l3/hub.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_budget_mutation
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --require S6 --require S7 --output /workspace/llm-clean-resume/b/o7-final-aux/static.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/b/o7-final-aux/docs.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_environment --suite llm --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/b/o7-final-aux/env-llm.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_environment --suite hub --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/b/o7-final-aux/env-hub.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_provider_environment --output /workspace/llm-clean-resume/b/o7-final-aux/provider-environment.json
```

원본 결과·발견 ID·실패 이력·환경 fingerprint는 `/workspace/llm-clean-resume/b/o7-final-l3/`,
`o7-final-aux/`와 앞의 각 소단계 디렉터리에 보존한다. 제품 코드·테스트·Fixture·lock·실행
환경은 이 결과 이후 변경하지 않고, 결과 기록을 위한 문서 변경만 수행한다. 그 차이를
별도 input snapshot으로 확인하고 정적·문서 검사를 재실행한다. 이 문서 수정 뒤 동일 제품
소스의 전체 회귀를 다시 실행했다고 표현하지 않는다.

### 시작 커밋의 원격 실패와 현행 증거

원격 기준 `bf25ee23621ee490f855bdfa0320daeef0593e5a`의
[기존 CI](https://github.com/E1der0wL/ish-llm/actions/runs/39786348023)는 실패 상태였다.
두 Job의 정적 검사와 LLM의 `test_scan_covers_both_product_trees_and_ratchet`가
`Changed review evidence requires re-review: docs/llm/clean-refactor.md`로 실패했다.
당시 LLM은 2261 발견/실행·2260 Pass·1 Error, Hub는 584/584 Pass였다. 이번 O0/OW-07의
개별 검토와 현재 정적 게이트가 이 근거 갱신을 수행했다. 과거 실패를 성공으로 덮어쓰지 않는다.

### Commit/Push와 원격 CI의 확인 경계

이 기록을 작성하는 시점에는 O7 로컬 통합 게이트가 통과했고 Push 뒤 원격 CI 확인이 남아 있다.
원격 CI는 Push된 정확한 head SHA의 `Linux backend contracts`에서 LLM/Hub 두 Job과
전체 regression·필수 static/R3/environment 검사를 실제 확인해야 한다. 문서에 기록된
로컬 성공을 원격 성공으로 승계하지 않는다. 커밋 이후 생성되는 SHA·실제 실행 URL·최종
결과는 해당 커밋의 GitHub Checks와 작업 완료 보고에서 대조한다.
