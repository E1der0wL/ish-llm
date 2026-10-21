# P1 구현·검증 기록

현재 및 후속 작업의 검증 범위·실행 시점·커밋과 Push는
[Level 1–3 검증 정책](verification-policy.md)을 따른다. 아래 과거 실행의 명령·결과·실패와
당시 완료 조건은 역사적 증거로 보존하며, 소단계마다 전체 회귀를 반복하는 지침으로 적용하지 않는다.

P0의 [고정 조합과 최신 허용 조합 검증](p0-sdk-transition.md)이 통과한 뒤
`refactor/llm-clean`에서 진행한다. P1-a에서 누적 Tool 사용량 소실을 재현해 중단한 뒤,
사용자의 보강·재개 요청에 따라 **책임 분리와 필수 상태 복원을 보강하고 P1-a 검증을 완료**했다.
P1-b와 P1-c의 첫 두 계약군은 검증·Push를 완료했다. P1-c에서 만료 필드 누락 후
실제 Tool 효과를 재현하여 기능 단계 진행을 중단한 뒤 H1 승인·RAG 포인터·삭제/예산을 보강했다.
이 문서에는 원래 실패와 감사를 보존하며, 이후 결과는 [수명주기 검증](lifecycle-verification.md)을
따른다. 세 번째 Provider 계약군은 별도 커밋으로 보존한다. A–F는 미착수이며 P1 전체 완료를 뜻하지 않는다.
main·이전 backend 브랜치·Draft PR #1은 변경하지 않는다. H1–H5 완료 후 P1와 A–F에서는
이전 backend 브랜치를 필요한 구현/시험의 읽기 전용 참고로만 사용하며 현재 계약에 맞춰 이식한다.
전체 merge·무검증 cherry-pick·구형 형식/호환 계층 복원 및 과거 통과 결과 승계는 하지 않는다.

## P1-c20: 지시 상태의 기존 owner 참조 — 로컬 Level 2

기존 InstructionStatus의 네 값은 그대로 두고 RunInstruction Reader, 접수/예약/반영/종료
Writer, RunManager의 초기 영수증, Session clone 소비자 4개 파일의 문자열 41곳을 enum 참조로
전환했다. AST에서 이 참조를 원래 값으로 되돌리고 추가 import를 제거하면 기존 AST와 같다.
대상 상태와 전체 집계의 차이, 별도 checkpoint input 상태, 영수증·재개·예산·트랜잭션 계약은
유지했다. 기존 실제 모델 효과·저장 실패·예약/부분 반영·clone 시험을 재사용하며 시험 소스와
Assertion은 바꾸지 않았다. 현재 지시 계약 문서에도 선언 소유권을 반영했다.

S3 23건이 실제 제거됐고 새 후보는 없다. Run 소스 의존성이 바뀐 정확한 review 6건은
초기 지시 영수증 두 필드의 diff와 기존 owner 판정을 재확인하고 enum 선언 hash를 추가했다.
해당 review의 모든 기존 참조 시험을 이번 추가 실행에서 검증했다. 과거 backend의 enum 선언만
읽기 전용으로 확인했으며 같은 구현이 이미 현재에 있어 알고리즘 이식·과거 성공 승계는 없다.
치환 보조 검사는 첫 실행에서 AST를 검사 중 수정하는 순서 오류로 exit 1이었으나 제품 쓰기 전
차단됐다. 원본 helper/오류를 c20-before에 보존하고 검사 사본을 분리한 뒤 구조 검사를 통과했다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 집중 LLM(재사용) | 79/79 | 79 | 0/0/0 | 28.353초 | 0 |
| 추가 LLM | 225/225 | 225 | 0/0/0 | 76.986초 | 0 |
| 추가 Hub | 33/33 | 33 | 0/0/0 | 67.783초 | 0 |

expected failure·unexpected success·missing·unexpected·duplicates도 모두 0이다. 추가 LLM은
공통 안전성 9개 모듈, audit 소비자 및 Graph/Run/usage/catalog 직접 소비자를 포함한다.
Hub는 지시·실행 중 복구·승인 연속 실행을 검증했다. R3 실제 효과 mutation True/outer exit 0,
정적 raw 382·reviewed 76·미해결 306(S2 5·S3 283·S6 18), new/stale/review 오류와
S1/S4/S5 미해결 0/exit 0, 문서 132/182/959/34·오류 0이다.

집중 실행과 추가 실행의 HEAD는 `2bf3c5edc4cfa0f37eb7b0c313597b7f85bd014e` 및 c20 변경이다.
701 Python 소스 hash `c48e6d4f012797ea45d9f12f65033dbd40e28393e31bbabd518f36da89104a29`가 같고,
863개 입력 중 바뀐 것은 audit reviews/debt 두 개뿐이다. 나머지 861개 제품·시험·Fixture·문서와
Python 3.12.14·136 의존성·env 5개·host d833790959a76ac457fdeea3f21a6bea1eecfe12·커널·
바이너리가 같다. 두 실행 구간의 전후 manifest도 각각 동일했다. 전체 발견 목록 LLM 2101이
같고 집중 79개의 실제 pass ID는 추가 실행과 겹치지 않는다. 참조 시험의 실제 실행 여부,
report hash와 test ID 목록은 c20-level2/reuse-proof.json에 있다.
**각 실행은 selected이며 결과를 하나의 전체 회귀로 합산하지 않는다.** 동일 값의 참조만 바뀌고
관련 소비자/핵심 안전성이 통과하여 전체 회귀·Push는 다음 묶음 Level 3까지 유예한다.
P1 최종 S1–S5 및 A–F는 미완료다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c20-focused/llm.json --modules tests.llm.test_loop_steering tests.llm.test_engine_steering tests.llm.test_instruction_reservations
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c20-level2/llm.json --modules tests.llm.test_architecture_checks tests.llm.test_architecture_reviews tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_nested_graph tests.llm.test_transactions tests.llm.test_catalog_presence tests.llm.test_common_boundaries tests.llm.test_graph_checkpoints tests.llm.test_long_running tests.llm.test_operations_schema tests.llm.test_stability_refinement
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c20-level2/hub.json --modules tests.hub.test_features tests.hub.test_provider_recovery tests.hub.test_approval_continuation
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S4 --require S5 --output /workspace/llm-clean-resume/p1/c20-static.json
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c20-docs-final.json
.venv-linux312/bin/python /workspace/llm-clean-resume/p1/verify-c20-focused-reuse.py
```

집중 입력 hash `59d73a1e36614b3a9f0a1aec6939d3ee7423edea74613b29de1b225c08a658e7`, 추가 입력 hash
`d1db6a5ef5f061989cfbecfbf7e404f7a921920cf427d171b6e9b430082c289d`를 기록했다. 명령·exit·원시 로그·
manifest는 c20-focused와 c20-level2에 있다. 문서 갱신 후 소스-커밋 동일성과 staged diff를
검사하고 로컬 커밋한다. 원격 마지막 확인 e160b0e의 CI 실제 결과는 미확인이다.
기존 실제 환경 검증 한계는 유지한다.

## P1-c19: Hub의 명시된 시작 설정 경로 보존 — 로컬 Level 2

Hub의 시작 설정 경로 선택 6곳을 None 기준으로 바꿨다. bootstrap·새 Project 프리셋 선택·
PresetService·SettingsService·재시작이 명시된 Path를 그대로 사용한다. 설정 catalog는 완전한
현재 general section의 workspace_root가 정확히 빈 문자열일 때만 시작 경로를 표시한다.
빈 경로의 기존 상속·실행 중 데이터 위치 유지·A→B→C 재시작·프리셋 초기값·저장 형식은 같다.

실제 파일 시험 4개에서 기존 구현은 1 pass·3 failure·error/Skip 0, 0.200초/exit 1이었다.
FalseyPath 입력을 버려 설정/프리셋을 활성 데이터 위치에 쓰고, 선택한 빈 Component 프리셋을
무시해 tools/demo를 다시 선택했으며 재시작 때 다른 시작 경로를 읽었다. 수정 전후 시험 byte가
동일하고 원본·로그는 c19-before에 있다. 정확한 파일 위치/내용, 원래 설정 파일 보존,
명시 빈 components, 재시작 후 Project/Session 선택 해제와 None 경로의 정상 동작을 보호한다.

현재 호출 관계와 각 변경 함수의 diff를 확인했다. S2 7건이 실제 제거됐고 새로운 후보는 없다.
변경 파일을 참조하던 생성 프리셋 9건과 표시 조건 2건의 근거를 다시 확인했다. 생성·schema
의미는 그대로이고 소비 경로만 수정됐으며 기존 owner 시험과 새 실제 파일/catalog 시험을
실행했다. 이 11개 review의 해당 hash와 시험 근거만 갱신했다. 새로운 허용 판정이나 AST
완화는 없다. 과거 backend도 같은 truthiness 선택을 써서 구현을 차용하지 않았다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 집중 Hub | 29/29 | 29 | 0/0/0 | 36.777초 | 0 |
| Level 2 LLM | 139/139 | 139 | 0/0/0 | 52.878초 | 0 |
| Level 2 Hub | 72/72 | 72 | 0/0/0 | 125.930초 | 0 |

expected failure·unexpected success·missing·unexpected·duplicates도 모두 0이다. LLM은 공통
안전성 9개 모듈과 audit 소비자를, Hub는 경로/프리셋/생성/재시작·설정 폼·실제 factory와 RAG
설정 소비자를 검증했다. R3 실제 효과 mutation True/outer exit 0이다. 정적 raw 405·reviewed 76·
미해결 329(S2 5·S3 306·S6 18), new/stale/review 오류·S1/S4/S5 미해결 0/exit 0이며
문서 132/182/959/34·오류 0이다. **이번은 selected 실행이며 전체 회귀는 수행하지 않았다.**
변경이 Hub의 명시 경로 선택에 한정되고 위 owner·직접 소비자·핵심 안전성이 통과하여
다음 관련 소단계 묶음 Level 3까지 전체 회귀/Push를 유예한다. P1 전체와 A–F는 미완료다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c19-focused/hub.json --modules tests.hub.test_workspace_presence tests.hub.test_workspace_settings tests.hub.test_global_preferences tests.hub.test_creation_contracts tests.hub.test_presets_widgets
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c19-level2/llm.json --modules tests.llm.test_architecture_checks tests.llm.test_architecture_reviews tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_nested_graph tests.llm.test_transactions
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c19-level2/hub.json --modules tests.hub.test_workspace_presence tests.hub.test_workspace_settings tests.hub.test_global_preferences tests.hub.test_creation_contracts tests.hub.test_presets_widgets tests.hub.test_defaults tests.hub.test_general_options tests.hub.test_rag_defaults tests.hub.test_response_reliability tests.hub.test_settings tests.hub.test_settings_project_flow tests.hub.test_settings_restart tests.hub.test_settings_navigation tests.hub.test_factory_presence
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S4 --require S5 --output /workspace/llm-clean-resume/p1/c19-static.json
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c19-docs-final.json
```

기준 HEAD `e160b0e5fc291f16825fe36d0c90dc18086f0f53`에 c19 변경을 더한 소스에서 실행했다.
701 Python 소스 hash `4e871a9488137a6d423f3e28359e3426ca23e9e2ab3310defd0f5a95a81d1867`와
863 입력/환경 hash `36fbecf3c0f05a6d5de33f2adde5b43fc6a949551a00191adbd816f7cdf916e3`는 각각 전후 동일하다.
Python 3.12.14·136 의존성·env 5개·host d833790959a76ac457fdeea3f21a6bea1eecfe12·커널·
바이너리의 c16–c18 전체 게이트와 동일성을 확인했다. 이전 pass를 이번 실행 수에 합산하지 않는다.
전체 발견 목록은 LLM 2101/Hub 567이며 새 Hub 시험 4개가 추가됐다. 명령·exit·ID·원시 로그와
manifest는 `/workspace/llm-clean-resume/p1/c19-level2/`에 있다. 결과 문서를 먼저 갱신한 뒤 로컬 커밋한다.
원격 마지막 확인은 e160b0e이며 해당 CI 실제 결과는 미확인이다. 기존 실제 환경 검증 한계는 유지한다.

## P1-c18: 명시 RAG 재시도 허가 — c16–c18 묶음 Level 3

RAGData.arun_job의 retry는 호출자가 주는 재실행 허가이며 자동 Provider 정책이 아니다.
기존 내부 실행기는 truthiness로 판단하여 문자열 `"false"`로 실패한 Job을 다시 실행했다.
새 실제 파일 시험 3개 중 2개 ID에서 9개 failure를 재현했다(1 pass·error/Skip 0,
4.963초, exit 1). 잘못된 값 8종은 대기 Job에서 모델 효과를
발생시켰고, 실패 Job의 문자열 재시도는 실제 모델 호출 파일을 1회에서 2회로 늘렸다.
이 실패 로그·제품 원본·새 시험 원본을 c18-before에 보존했다.

RAGJobs.run은 전달된 retry가 정확한 bool인지 lease 획득 전에 검사한다. 공개 arun_job만
정상 대기 실행의 False 기본값을 소유하고 내부 실행기는 인자를 필수로 받는다. 내부 잠금 취소
시험의 유일한 직접 호출에 False를 명시했으며 반복 취소·descriptor 닫힘 Assertion은 유지했다.
claim의 Revision/CAS·재개 계획·포기·취소·구성·usage/receipt 검증과 저장 형식은 바꾸지 않았다.
새 시험은 잘못된 값의 효과 0과 모든 Project 파일 byte 보존, 기본/False 정상 실행,
실패 뒤 False 거부, 현재 resume-plan revision과 명시 True의 정상 재시도를 검사한다.
**수정 전후 새 시험 파일은 byte가 동일하다.**

내부 중복 기본값 S2 하나를 제거하고 공개 command 기본값 하나만 정확한 발생 ID와 owner·
계약·소스/Fixture hash·구체적 시험으로 판정했다. 변경 의존성을 가진 기존 RAG review 두 건은
API diff와 실제 consumer 결과를 재검토하고 test_rag_components/configuration_fixtures 및
필요한 checkpoint helper 근거를 보강했다. prepare의 graph 재사용/best_effort 의미는 같다.
AST 규칙이나 실패 판정은 완화하지 않았다. 원격 backend의 해당 retry 서명/claim만 참고했으며
같은 truthiness 구현이므로 이식하지 않았고 이전 검증 결과도 승계하지 않았다.

이번 변경은 재시도 허가 경계이므로 집중 검증 뒤 c16 Interaction·c17 audit와 함께 현재 통합
소스를 Level 3으로 검증했다. 별도의 반복 Level 2 전체 실행은 추가하지 않았다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 수정 후 집중 | 90/90 | 90 | 0/0/0 | 29.942초 | 0 |
| 전체 LLM | 2101/2101 | 2101 | 0/0/0 | 737.909초 | 0 |
| 전체 Hub | 563/563 | 563 | 0/0/0 | 715.317초 | 0 |

expected failure·unexpected success·missing·unexpected·duplicates도 모두 0이다. 각 전체 suite는
한 번의 full 실행이며 집중 또는 과거 결과를 합산한 수가 아니다. 공통 승인·사용량/삭제·효과·
Checkpoint·RAG·수명주기·중첩 Agent·트랜잭션의 실제 효과/장애 주입 시험이 모두 포함됐다.
R3 실제 효과 mutation True/outer exit 0, 실제 LLM/Hub 환경 검사 각각 ok/exit 0이다.
정적 raw 412·reviewed 76·미해결 336(S2 12·S3 306·S6 18), new/stale/review 오류와
S1/S4/S5 미해결 0/exit 0이다. P1 최종 S1–S5 및 A–F는 아직 미완료다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c18-focused/llm.json --modules tests.llm.test_rag_retry_presence tests.llm.test_rag_components tests.llm.test_rag_resilience tests.llm.test_rag_graph_checkpoint tests.llm.test_rag_extraction_repair tests.llm.test_rag_optimization
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c16-c18-level3/llm.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c16-c18-level3/hub.json
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_environment --suite llm --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/p1/c16-c18-level3/env-llm.json
.venv-linux312/bin/python -m tests.llm.check_environment --suite hub --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/p1/c16-c18-level3/env-hub.json
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S4 --require S5 --output /workspace/llm-clean-resume/p1/c18-static.json
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c18-docs-final.json
```

실행 HEAD는 `fd53ddd85535f423bc41568375670f7d7befad20`와 당시 c18 미커밋 변경이다. 700 Python 소스 hash
`b13ff913b34bf21cb639684c6ed527386dd7210d47c5cb1a4948dbbc60440f48`, 862개 입력/환경 manifest hash
`7721d13deb8e73630f689780bd9268877a682c0d76371bc9b94b353d371edbcf`가 실행 전후 각각 동일했다.
Python 3.12.14·136 의존성·env 5개·고정 host `d833790959a76ac457fdeea3f21a6bea1eecfe12` 및 바이너리를 기록했다.
전체 실행 중 소스·시험·Fixture·문서를 고정했고, 전후 대조 뒤 결과 문서를 먼저 갱신한다.
명령·exit·발견/실행 ID·원시 로그와 manifest는 `/workspace/llm-clean-resume/p1/c16-c18-level3/`에 있다.
문서 검사는 132문서·182 Python fence·959링크·34 anchor, 오류 0이다.
소스-커밋 동일성 및 staged diff 검사 후 c16–c18을 작업 브랜치에 묶어 Push한다.
원격 CI 실제 결과는 기존 접근 제한으로 미확인이며 로컬 전체 성공과 구분한다.
main·backend·Draft PR #1을 변경하지 않으며 기존 실제 인증 모델·커널 장애 검증 한계는 유지한다.

## P1-c17: 조건식·관찰용 입력 8건의 판정 — 로컬 Level 2

제품·시험·AST 규칙을 바꾸지 않고 S2 발생 8건을 개별 판정했다. RAG의 동일 내용/지문과 graph
완성 여부에 따른 재사용, 명시 best_effort 실패 분류, BaseEngine의 수신 후/예산 소진 시 재시도
거부, Provider timeout 분류, Run/Component 사용량의 명시 한도별 영수증 조회·토큰 예약 조건이다.
이 식들은 조건으로만 소비되며 설정값을 대체하지 않는다. Observability.record의 retry=False는
이미 일어난 실행의 관찰 태그이며 재시도 정책·권한·영수증을 생성하지 않는다. 물리 시도 집계와
sink 실패 격리는 기존 owner 계약이다. 각 발생의 정확한 ID·현재 계약·소스/의존성 hash·존재하는
구체적 시험 ID를 기록했다. RAG 오류 코드의 S3 선언과 Provider S6 경계는 별도로 계속 남는다.

바뀐 audit fixture 소비자인 검사기의 성공/실패 사례를 새로 실행했다. RAG의 실제 model receipt·
원본 보존/재시도·extraction 실패, Provider transport/stream/cancellation, Component의 예약·
불확실 사용량·opaque 요청, 공통 안전성 9개 모듈 및 Hub RAG/재시도 관찰을 추가 검증했다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 새 llm | 255/255 | 255 | 0/0/0 | 78.932초 | 0 |
| 새 hub | 26/26 | 26 | 0/0/0 | 40.146초 | 0 |

expected failure·unexpected success·missing·unexpected·duplicates도 모두 0이다.
R3 실제 효과 mutation True/outer exit 0, 정적 raw 413·reviewed 75·미해결 338(S2 14·S3 306·S6 18),
new/stale/review 오류·S1/S4/S5 미해결 0/exit 0, 문서 132/182/959/34·오류 0/exit 0이다.

### 검증 결과 재사용

기준 c16 실행 HEAD `5f30933add12ee2f38efd7fe64ef0a474455b169`와 당시 미커밋 제품/시험은 현재
HEAD `48012d79f6880d5f7f6558836e5da227352464cb`의 해당 소스와 동일하다. 699 Python 소스 hash는 모두
`dc7fe434e2847287659499ac31781e416b6050cb13a3279a949ef3b95d2f691f`다. 861개 입력 중
달라진 파일은 audit reviews/debt와 이 결과 문서 세 개뿐이며, 나머지 858개 제품·시험·Fixture·자산,
Python 3.12.14·136 의존성·바이너리·커널·env 5개·깨끗한 host d833790959a76ac457fdeea3f21a6bea1eecfe12가
같다. c16의 기본/추가 helper 시험 모두 그 전후 manifest 구간에서 실행됐고, 그 byte 동일성을
다시 대조했다. c17의 전체 발견 ID도 LLM 2098/Hub 563으로 정확히 같다.

| 재사용한 모듈 | 이전 pass | 원래 실행 |
|---|---:|---|
| tests.llm.test_long_running | 23 | c16-level2 |
| tests.llm.test_retry | 21 | c16-level2 |
| tests.llm.test_observability | 22 | c16-observation |
| tests.hub.test_retry_ui | 10 | c16-level2 |

LLM 66개·Hub 10개는 새 실행과 test ID가 겹치지 않는다. 참조한 모든 owner 시험이 새 실행
또는 위 재사용 범위의 실제 통과 목록에 있음을 검증했다. 각 test ID·원래/새 report hash·기준
manifest·변경 파일 목록은 c17-level2/reuse-proof.json에 있다. **원래 실행과 새 실행 모두
selected이며 이 성공들을 하나의 전체 회귀로 합산하지 않는다.** 이번 전체 회귀는 audit 자료만
변경되고 위 동일성이 성립하여 유예하며, 다음 묶음 Level 3에서 현재 통합 소스를 전체 검증한다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c17-level2/llm.json --modules tests.llm.test_architecture_checks tests.llm.test_architecture_reviews tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_nested_graph tests.llm.test_transactions tests.llm.test_rag_resilience tests.llm.test_rag_optimization tests.llm.test_rag_graph_checkpoint tests.llm.test_rag_extraction_repair tests.llm.test_provider_runtime tests.llm.test_operations_schema tests.llm.test_common_boundaries
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c17-level2/hub.json --modules tests.hub.test_rag_jobs tests.hub.test_rag_extensions tests.hub.test_retry_observations
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S4 --require S5 --output /workspace/llm-clean-resume/p1/c17-static.json
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c17-docs-before.json
.venv-linux312/bin/python /workspace/llm-clean-resume/p1/verify-c17-reuse.py
```

새 실행 전후 입력/환경 hash `0299def3fe01125a227a5c7af3df6727e7fe3172e0bdd7a8948ff09f7ad317b1`과
위 소스 hash가 각각 동일했다. 명령·exit·discovery·원본 로그·전후 manifest는
`/workspace/llm-clean-resume/p1/c17-level2/`에 있다. 현재 계약의 조건 판정이므로 과거 backend
알고리즘을 가져오거나 이전 브랜치의 통과 결과를 승계하지 않았다. 문서를 먼저 갱신하고 로컬
커밋하며, Push는 다음 전체 게이트 뒤 묶어서 한다. P1 최종 S1–S5와 A–F는 미완료다.
원격 마지막 확인은 5f30933이고 CI 실제 결과는 미확인이다. 기존 실제 환경 미검증 한계는 유지한다.

## P1-c16: Interaction 선언과 명시 요청 보존 — 로컬 Level 2

InteractionOption이 공통 effect, InteractionRequest가 kind·priority·생성 category 초기값을
ClassVar로 한 번 선언하고 검증기·ToolExecutor·Graph·InteractionRepository·Hub 질문/거절
소비자가 참조한다. 저장 필드는 늘지 않으며 effect/category의 Application 확장, 선택지 ID와
effect의 구분, closed Reader·지문·만료·필수 영수증·승인/권한/예산 판단을 유지한다.
현재 계약 문서에도 owner와 생성/저장 경계의 구분을 보강했다.

입력 보존 결함을 새 시험 4개로 먼저 재현했다. 유효한 falsey InteractionRequest가 새 기본
승인 요청으로 바뀌었고, falsey source 객체는 버려졌다. source/action의 잘못된 falsey 입력
10종도 빈 객체로 교체되어 타입 거부를 우회했다. 결과는 1 pass·3 failed ID·12 failure·error/Skip 0이다.
approval_request의 source/action과 ToolApprovalRequired의 명시 요청 선택 세 곳을 None 기준으로
수정하여 기존 owner 검증에 원래 값을 전달한다. ID·source·action·지문·사용자 deny 선택과
재열기 roundtrip을 보호하며, 임의 effect나 표시 ID로 승인을 만들지 않는 시험도 포함한다.
수정 전후 새 시험 파일의 byte가 동일하며 원본 실패/제품 소스는 보존했다.

8개 제품 파일의 AST를 대조했다. 세 Presence 수정 외에는 owner 선언·import·같은 값의 참조만
변경됐다. 이는 영향 범위의 구조 근거이며 실제 안전성 시험을 대체하지 않는다. S3 19건이 실제
제거됐고 새 raw 후보는 없다. 무효화된 9개 review는 각 변경 파일의 diff·기존 CAS/조건 계약과
소비자를 재확인하고 새 owner 선언 hash를 추가했다. 전역 hash 갱신/AST 완화/새 분류는 없다.
사전 정적 exit 1로 변경 근거를 거부한 기록도 보존한다.

| 실행 | discovered/started | pass | failed ID/failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| c16-before llm | 4/4 | 1 | 3/12/0/0 | 0.006초 | 1 |
| c16-focused llm | 82/82 | 82 | 0/0/0/0 | 14.967초 | 0 |
| c16-level2 llm | 344/344 | 344 | 0/0/0/0 | 104.342초 | 0 |
| c16-level2 hub | 73/73 | 73 | 0/0/0/0 | 83.893초 | 0 |
| c16-observation llm | 22/22 | 22 | 0/0/0/0 | 5.520초 | 0 |

모든 실행의 expected failure·unexpected success·missing·unexpected·duplicates는 0이며,
재현 이후 완료 실행의 failure/error/Skip도 모두 0이다. Level 2는 owner의 Writer/Reader,
Naming, 실제 승인/거절·Tool 선언·MCP·재시도·Graph/중첩 재개·공통 안전성 9개 모듈과 감사
검사기, Hub 질문/응답·거절·재개·Tool 관리·이력의 직접 소비 경로를 포함한다. 추가 조사에서
직접 Tool helper의 승인/binding 소비 경로도 확인해 test_observability 22개를 같은 고정 소스로
실행했다. Level 2 LLM 본 실행과 test ID가 겹치지 않고 전체 발견 목록도 정확히 같다.
실행들은 각각 selected이며 합쳐서 전체 회귀라고 하지 않는다.

R3 실제 효과 mutation True/outer exit 0, 정적 raw 413·reviewed 67·미해결 346(S2 22·S3 306·S6 18),
new/stale/review 오류·S1/S4/S5 미해결 0/exit 0, 문서 132/182/959/34·오류 0/exit 0이다.
원본·효과 횟수·프로세스 중단/재개·손상 거부 Assertion은 유지했다. 변경 제품 범위는 과거 통과
결과를 재사용하지 않고 실행했다. 생성 입력의 제한된 수정과 상수 참조임을 입증하여 이번 전체
회귀는 유예하며, 다음 관련 소단계 묶음 Level 3에서 현재 통합 소스 전체 LLM/Hub 후 Push한다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c16-before/llm.json --modules tests.llm.test_interaction_presence
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c16-focused/llm.json --modules tests.llm.test_interaction_presence tests.llm.test_interactions tests.llm.test_interaction_decisions tests.llm.test_interaction_storage tests.llm.test_naming_contract tests.llm.test_current_contracts
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c16-level2/llm.json --modules tests.llm.test_interaction_presence tests.llm.test_interactions tests.llm.test_interaction_decisions tests.llm.test_interaction_storage tests.llm.test_naming_contract tests.llm.test_current_contracts tests.llm.test_tool_declarations tests.llm.test_mcp_runtime tests.llm.test_retry tests.llm.test_long_running tests.llm.test_graph_checkpoints tests.llm.test_checkpoint_lifecycle tests.llm.test_explicit_configuration tests.llm.test_error_boundaries tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_nested_graph tests.llm.test_transactions tests.llm.test_architecture_checks tests.llm.test_architecture_reviews
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c16-level2/hub.json --modules tests.hub.test_question_tools tests.hub.test_question_card tests.hub.test_interaction_updates tests.hub.test_output_interaction tests.hub.test_approval_continuation tests.hub.test_tool_advice tests.hub.test_paused_stop tests.hub.test_retry_ui tests.hub.test_tool_history tests.hub.test_request_history
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c16-observation/llm.json --modules tests.llm.test_observability
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S4 --require S5 --output /workspace/llm-clean-resume/p1/c16-static.json
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c16-docs-before.json
.venv-linux312/bin/python /workspace/llm-clean-resume/p1/verify-c16-structure.py
```

기준 HEAD 5f30933add12ee2f38efd7fe64ef0a474455b169와 미커밋 변경이다. Level 2 및 추가 helper
실행 전후 699 Python 소스 hash `dc7fe434e2847287659499ac31781e416b6050cb13a3279a949ef3b95d2f691f`,
861 입력/환경 hash `fedb0b385acf30707c054992c9447482a40568bddff7f70a0b8bb748cd54297a`가 각각
동일하다. Python 3.12.14·136 의존성·깨끗한 host d833790959a76ac457fdeea3f21a6bea1eecfe12·
env 5개는 c15 전체 게이트와 같다. 명령·exit·발견 목록·원본 로그·구조 증거·전후 manifest는
`/workspace/llm-clean-resume/p1/`의 c16-before, c16-focused, c16-level2, c16-observation에 있다.
이전 backend의 core/interactions 35–64행만 읽기 전용 참고했다. 선언 값은 이미 현재에 있고
그 Reader에는 현재 closed 필드 검증이 없어 이식하지 않았다. 이전 시험 결과도 승계하지 않는다.
문서를 먼저 최신화하고 로컬 커밋으로 보존한다. P1 최종 및 A–F는 남아 있고, 원격 마지막 확인은
5f30933이며 CI 실제 결과는 미확인이다. 인증 모델·커널 short-write 등 기존 미검증 한계도 유지한다.

## P1-c15: Checkpoint 상태의 단일 선언과 c13–c15 묶음 Level 3

Loop·Graph·EngineCheckpointScope·Agent 위임·InteractionRepository의 체크포인트 상태 문자열
28곳을 기존 core.checkpoints.CheckpointStatus 참조로 바꿨다. owner의 다섯 값은 그대로이며
공통 수명과 다른 Run/Step·Tool 효과·추가 지시 input 상태를 합치지 않았다. Writer와 비교/재개
소비자가 같은 선언을 사용한다. literal로 되돌린 AST는 변경 전과 동일함을 5개 파일별로 확인했다.
이는 구조 비교 근거이며 실제 실행 시험을 대체하지 않는다. 저장 형식·필수 증거·Revision/CAS·
트랜잭션·승인·예산·재시도/Resume 규칙과 시험/Fixture는 바꾸지 않았다.

25개 S3 발생이 실제 제거됐고 새 raw 후보는 없다. 달라진 Graph/Loop 파일을 참조하는 review
2건은 상수 참조만 바뀐 diff와 기존 후보/소비자 계약을 재확인하고 owner 선언 hash도 추가했다.
변경된 근거를 사전 정적 검사가 exit 1로 거부한 기록도 보존했다. 최종 정적 raw 432·reviewed 67·
미해결 365(S2 22·S3 325·S6 18), new/stale/review 오류·S1/S4/S5 미해결 0/exit 0이다.
기본 gate의 성공은 P1 최종 S1–S5 완료를 뜻하지 않는다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| c15-focused llm | 111/111 | 111 | 0/0/0 | 47.304초 | 0 |
| c13-c15-level3 llm | 2094/2094 | 2094 | 0/0/0 | 731.807초 | 0 |
| c13-c15-level3 hub | 563/563 | 563 | 0/0/0 | 723.022초 | 0 |

모든 완료 실행의 expected failure·unexpected success·missing·unexpected·duplicates도 0이다.
집중 범위는 현재 Writer/Reader·완료 기록 유실·manifest 저장 실패·실제 프로세스 종료·중첩
예산·승인 영수증·명시적 재개·삭제다. 여러 Engine/승인 소비자의 공통 경계이므로 집중 통과 후
별도 Level 2 중복 없이 Level 3으로 승격했다. 이번 **동일한 현재 통합 소스**의 전체 실행에
c13 audit 자료, c14 FileCatalog 수정/새 시험, c15 상태 참조를 모두 포함했다. 이전 결과나
부분 실행을 전체 결과에 합산하지 않았다. 실제 파일 효과·원본 보존·장애 주입 Assertion을 유지한다.
R3 실제 효과 mutation True/outer exit 0, 실제 LLM·Hub 환경 검사 각각 exit 0, 문서
132/182/959/34·오류 0/exit 0을 확인했다. 인증 모델·커널 short-write 등 기존 미검증 한계는 남는다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c15-focused/llm.json --modules tests.llm.test_current_contracts tests.llm.test_checkpoint_integrity tests.llm.test_graph_checkpoints tests.llm.test_nested_graph tests.llm.test_interaction_storage tests.llm.test_checkpoint_lifecycle
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c13-c15-level3/llm.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c13-c15-level3/hub.json
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S4 --require S5 --output /workspace/llm-clean-resume/p1/c15-static.json
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_environment --suite llm --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/p1/c13-c15-level3/env-llm.json
.venv-linux312/bin/python -m tests.llm.check_environment --suite hub --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/p1/c13-c15-level3/env-hub.json
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c15-docs-before.json
```

기준 HEAD `8ac9fce43aed40e3a1a25854981e03a164453153`와 미커밋 c15 변경에서 실행했다. 실행 전후 698 Python 소스 hash
`3eb841be0fc8b5ea7533cba30e2f0a6664f5a94da6a746c2f4d77babd7b3f9e5`, 860 입력/환경 hash
`2f3a479e35c923a6d9ffd7814601c3e00fc172e8d4ac114c8180310c9c847d03`가 각각 동일하다. Python 3.12.14·136 의존성·깨끗한 실제 host
d833790959a76ac457fdeea3f21a6bea1eecfe12와 env 5개는 c12와 같다. 실제 명령·exit·test ID·
원본 로그·전후 manifest는 `/workspace/llm-clean-resume/p1/c13-c15-level3/`, 집중 결과는
c15-focused, 이전 파일/구조 비교는 c15-before에 보존했다. 이전 backend의 graph/checkpoints
63–87행만 읽기 전용 비교했고 이미 현재에 있는 동작이므로 가져오지 않았다. 과거 통과를 승계하지 않는다.

전체 성공 뒤 문서를 먼저 최신화하고 c13–c15를 작업 브랜치에 묶어 Push한다.
main·refactor/llm-backend·Draft PR #1은 유지한다. 원격 CI는 기존 조회 403으로 실제 결과
미확인이다. 이후 남은 P1 owner/Presence와 P1 최종 gate를 마친 뒤 A–F로 진행한다.

## P1-c14: FileCatalog 주입값과 캐시 선언 — 로컬 Level 2

FileCatalog가 `projection or default`로 명시적인 falsey callable을 버렸다. 실제 record 파일로
재현하면 요청한 projection의 결과 대신 기본 상태를 반환하고 호출 증거 파일도 생성되지 않았다.
잘못된 falsey/noncallable 입력 6종도 생성자에서 거부하지 않았다. 새 시험 4개 중 1개만 통과했고,
7개 Assertion 실패·2개 error(호출되지 않은 projection의 증거 파일 부재)를 원본 로그와 함께 보존했다.
이번 수정은 None만 기본 projection을 선택하고 명시한 나머지 값은 callable인지 검증한다.
시험·Assertion은 수정 전후 동일하다. 타입/identity/version 손상은 projection 전에 거부하며
실제 원본 byte 보존, 캐시 hit의 단일 호출, 0 비활성/1 eviction과 Run/Step 선택값을 검사한다.

기술 캐시 초기값 4096은 FileCatalog.DEFAULT_MAX_ENTRIES가 한 번 선언하고 RunRepository와
StepRepository 생성자가 소비한다. 저장 원본·실행 한도·정책·캐시 동작은 바꾸지 않았다.
음수 거부의 0은 현재 계약에 명시된 비음수 캐시 용량의 수학적 경계로 개별 S3 판정했다.
변경으로 무효화된 Graph/ToolRuntime review 2건은 runs.py의 생성자 기본 인자 한 곳만 바뀐
diff와 관련 소비자 시험을 확인하여 갱신했다. 정적 검사의 사전 exit 1은 이 변경을 실제로 탐지했다.
BaseEngine.step의 c13 근거 문구도 실제 None 분기와 math.isfinite 검증으로 정확히 고쳤다.
정적 AST 규칙·기존 Assertion·필수 CI 배정은 유지한다.

| 실행 | discovered/started | pass | failed ID/failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| c14-before llm | 4/4 | 1 | 3/7/2/0 | 0.005초 | 1 |
| c14-focused llm | 57/57 | 57 | 0/0/0/0 | 6.868초 | 0 |
| c14-level2 llm | 329/329 | 329 | 0/0/0/0 | 82.792초 | 0 |
| c14-level2 hub | 35/35 | 35 | 0/0/0/0 | 46.014초 | 0 |

모든 실행에서 expected failure·unexpected success·누락·중복·unexpected는 0이며, 재현 이후
완료 실행의 failure/error/Skip도 0이다. 집중 범위는 조회/캐시·서비스·출력 저장이다. Level 2는
Run/Step 공개 API·상태 전이·참조·최적화 조회, Graph/불확실 효과, 공통 승인·예산·효과·Checkpoint·
RAG·수명주기/삭제·중첩 Graph·트랜잭션, Hub 이력·승인·삭제의 실제 소비 경로를 포함한다.
R3 실제 효과 mutation True/outer exit 0, 정적 raw 457·reviewed 67·미해결 390(S2 22·S3 350·S6 18),
new/stale/review 오류·S1/S4/S5 미해결 0/exit 0, 문서 132/182/959/34·오류 0/exit 0이다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c14-before/llm.json --modules tests.llm.test_catalog_presence
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c14-focused/llm.json --modules tests.llm.test_catalog_presence tests.llm.test_stability_refinement tests.llm.test_service_extensions tests.llm.test_operational_storage
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c14-level2/llm.json --modules tests.llm.test_catalog_presence tests.llm.test_stability_refinement tests.llm.test_service_extensions tests.llm.test_operational_storage tests.llm.test_optimized_queries tests.llm.test_run_api tests.llm.test_run_transitions tests.llm.test_run_reference_integrity tests.llm.test_runtime tests.llm.test_long_running tests.llm.test_graph_checkpoints tests.llm.test_base_engine tests.llm.test_architecture_checks tests.llm.test_architecture_reviews tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_nested_graph tests.llm.test_transactions
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c14-level2/hub.json --modules tests.hub.test_request_history tests.hub.test_tool_history tests.hub.test_history_retention tests.hub.test_approval_continuation tests.hub.test_history_alerts
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S4 --require S5 --output /workspace/llm-clean-resume/p1/c14-static.json
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c14-docs-before.json
```

기준은 c13 로컬 75cfd99와 위 미커밋 변경이다. Level 2 전후 698 Python 소스 hash
`2ad91de87fdfcc3d385e08a3b32c747c620e1f80d60e0e0855111299875dc672`, 860 입력/환경 hash
`9c89779be4d66ee172501dff5594ba936af236b49adacec56dcd81ae81f98564`가 각각 동일했다.
Python 3.12.14·136 의존성·깨끗한 host d833790959a76ac457fdeea3f21a6bea1eecfe12·env 5개는
c12와 같다. 명령/exit/discovery/원본 로그·이전 제품/시험·전후 manifest는 c14-before,
c14-focused, c14-level2에 보존했다. 변경된 제품 범위는 이전 통과를 재사용하지 않고 실행했다.
이전 backend의 FileCatalog 100–128행만 읽기 전용 비교했다. 같은 truthiness 문제와 제거된
reader hook이 있어 가져오지 않았다. 현재 알고리즘·closed Reader와 기존 실패 사례를 유지한다.

범위가 캐시와 직접 소비자로 입증되어 이번 전체 회귀는 유예한다. 문서를 먼저 최신화하고 로컬
독립 커밋하며, c13 이후 관련 소단계를 묶은 다음 Level 3에서 전체 LLM/Hub 후 Push한다.
P1 최종 S1–S5와 A–F는 남아 있다. 원격 마지막 확인은 568af3f, CI 실제 결과는 미확인이다.

## P1-c13: 검증·조건식 11건의 개별 판정 — 로컬 Level 2

제품·시험 Python·실행 정책·저장 형식·AST 규칙은 그대로다. S2 후보 11건을 현행 계약에 따라
개별 판정하고 정확한 발생 ID·owner·근거·소스/의존성/계약/시험 hash를 기록했다.
ordered_processors, ToolContract, BaseEngine.step, GraphEngine.validate_resume, OutputBuffer,
ToolRuntime, ToolExecutor의 조건은 필수 속성·타입·범위·중복·명시적 재시도 노드 집합 검증이다.
DomainLogger는 검증된 boolean 스위치, RecoveryPolicy는 Backend가 이미 허용한 Tool의
복구 선택, GlobalPage/SchemaForm은 widget·도움말 표시 조건이다. 입력값을 대신하는 기본값이
아니며 누락·None·False·빈 값이나 승인·효과 증거를 복구하지 않는다. 파일/rule 단위 허용은 없다.
ToolRuntime의 실제 불확실 효과 시험을 모든 잘못된 입력의 완전한 coverage로 주장하지 않는다.
현재 알고리즘의 판정이므로 이전 backend 구현/형식/통과 결과를 가져오지 않았다.

변경된 audit fixture의 실제 소비 경로를 검색했다. check_architecture와 그 성공/실패 시험
test_architecture_checks·test_architecture_reviews만 직접 소비한다. 두 모듈을 새로 실행했고,
공통 승인·누적 예산·효과·Checkpoint·RAG·수명주기/삭제·중첩 Graph·트랜잭션의 실제 파일/장애
시험과 Hub 오류/재시도 소비자를 추가했다. 아래 새 실행은 모두 selected 범위다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 새 llm | 139/139 | 139 | 0/0/0 | 49.150초 | 0 |
| 새 hub | 14/14 | 14 | 0/0/0 | 0.139초 | 0 |

새 실행의 expected failure·unexpected success·missing·unexpected·duplicates도 모두 0이다.
R3 실제 효과 mutation은 의도된 내부 Assertion 실패를 검출(True), outer exit 0이다.
정적 검사는 raw 458·reviewed 66·미해결 392(S2 23·S3 351·S6 18), new/stale/review 오류 및
필수 S1·S4·S5 미해결 0/exit 0이다. 문서 검사는 132문서·182 Python fence·959링크·34 anchor,
오류 0/exit 0이다. 남은 S2/S3를 판정 없이 통과시킨 것이 아니며 P1 최종 게이트는 아직 미완료다.

### 재사용한 결과와 동일성

검증 정책의 재사용 조건을 적용했다. 직전 c10–c12 Level 3은 기준 HEAD
`aa24c041aa026d7ffa737b6cccb6ec5ce247da72`에 당시 미커밋 c12 시험을 포함한 현재 소스였고, c13 기준 HEAD는
`568af3fdada3aba620e6e9a43c7cbd0ffe11ed2b`다. 서로 다른 HEAD를 같다고 하지 않는다.
697개 Python 소스 hash는 두 실행 모두
`4e33a87d7d74e73672706eed9cb10672f0cb011cea7aadae38ce1199404e7a83`이다.
859개 입력 중 달라진 것은 architecture_reviews.json·architecture_debt.json·이 결과 문서
세 파일뿐이다. 나머지 856개 제품/시험/Fixture/자산, Python 3.12.14·136개 의존성,
실행 바이너리·커널·환경 5개·깨끗한 실제 host d833790959a76ac457fdeea3f21a6bea1eecfe12가
모두 같다. 새 입력 hash `32c427c20a0d321358fb43dedc451e6c8ba71c5ef0c1c50632c80223cef91e78`와
소스는 새 실행 전후에도 각각 byte 단위로 동일했다. 전체 discovery ID 목록도 LLM 2090/Hub 563으로
같고 모든 새 review 참조 ID가 실제 이전 통과 목록에 유일하게 존재한다.

다음 owner/직접 소비자 모듈의 **이전 전체 실행 결과**만 재사용했다(LLM 192개·Hub 17개).
새 실행과 중복한 test_error_contract는 재사용 수에서 제외했다. 제품·시험·계약은 변경되지 않았고
audit fixture를 읽는 경로는 새로 검증했으므로 이 범위의 근거는 유효하다.

| 원래 c10–c12 Level 3에서 통과한 모듈 | 재사용 pass |
|---|---:|
| tests.llm.test_base_engine | 24 |
| tests.llm.test_completion_processing | 19 |
| tests.llm.test_error_boundaries | 10 |
| tests.llm.test_explicit_configuration | 24 |
| tests.llm.test_graph_checkpoints | 22 |
| tests.llm.test_lifecycle_logging | 17 |
| tests.llm.test_long_running | 23 |
| tests.llm.test_operational_storage | 25 |
| tests.llm.test_service_extensions | 22 |
| tests.llm.test_tool_declarations | 6 |
| tests.hub.test_general_options | 5 |
| tests.hub.test_settings | 11 |
| tests.hub.test_settings_project_flow | 1 |

각 test ID·원래 report hash·현재 report hash·환경 비교와 변경 파일 목록은
`/workspace/llm-clean-resume/p1/c13-level2/reuse-proof.json`에 있다. 원래 명령/exit 0과 전체
원본 로그는 c10-c12-level3에 보존했다. **이전 결과와 새 부분 실행을 하나의 전체 실행으로
합산하지 않는다.** 이번 소단계 전체 회귀는 제한된 audit 자료 변경과 위 동일성 근거로 유예하며,
다음 관련 소단계 묶음 또는 P1 최종 Level 3에서 현재 통합 상태 전체를 다시 검증한다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c13-level2/llm.json --modules tests.llm.test_architecture_checks tests.llm.test_architecture_reviews tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_nested_graph tests.llm.test_transactions
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c13-level2/hub.json --modules tests.hub.test_error_contract
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S4 --require S5 --output /workspace/llm-clean-resume/p1/c13-static.json
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c13-docs-before.json
.venv-linux312/bin/python /workspace/llm-clean-resume/p1/verify-c13-reuse.py
```

명령 작업 경로·env 5개는 c12 전체 게이트와 동일하다. 요약·로그·선택 목록·전후 manifest는
c13-level2에 보존했다. 소단계 문서를 먼저 갱신하고 로컬 독립 커밋으로 보존하며, Push는 후속
Level 3 뒤 묶어서 한다. 원격 마지막 확인은 568af3f이고 CI는 실제 결과 미확인이다.
실제 인증 모델·커널 short-write 등 기존 미검증 한계는 그대로이며 안전성 시험을 줄이지 않았다.

## P1-c12: 현재 Naming·Schema 계약과 c10–c12 묶음 Level 3

제품 코드는 바꾸지 않고 test_naming_contract의 과거 Schema/binding digest 동결을 현재 계약
시험으로 대체했다. 삭제된 retention Schema를 다시 넣고 현재 Provider flag·MCP alias·Memory
필드를 빼던 정규화는 제거했다. 현재 12개 구현체의 config/policy 필드와 닫힌 외형, Provider의
두 boolean flag와 정책 필드, RAG cache의 비음수 정수와 폐기 retention 거부, MCP alias의 필수
server/tool·unknown 거부, Memory mode/prompt/대상 참조, Project 정책의 None/False/0/빈 목록과
유효 한도를 명시적으로 확인한다. 기존의 해당 필수 필드·nullable·closed·허용 enum/최솟값
Assertion을 현재 형식에 직접 적용하며 이전 값으로 변환한 성공과 섞지 않는다.

Loop binding도 adapter를 빼고 과거 digest와 비교하는 대신, 현재 **전체 payload**의 필드와
값 및 공개 bind_execution의 canonical token을 비교한다. Graph는 폐기 agents catalog를
제외한 현재 전체 payload를 명시했다. 저장된 settings/tool_policy 어휘와 실행 identity는
그대로 보호한다. 다른 저장 도메인/Workflow 필드·parameter_key·폐기 API·get/resolve/describe
시험은 유지했다. naming_contract.json·retired_retention_schemas.json과 역사적 결과는
byte/hash가 같은 원본으로 보존했으며 새 snapshot digest를 생성해 통과시키지 않았다.

현재 owner/Provider/Schema/Naming 및 실제 forwarding 시험을 재사용했다. Schema 집중 후
확대 명령에서 존재하지 않는 test_provider_adapters를 잘못 지정하여 discovery 단계에서 시험 시작 전에
거부했다(`c12-focused-final`, exit 2, summary incomplete). 실행 집계가 없으며 성공·Skip 또는
누락 0으로 재분류하지 않는다. 원본 명령/로그는 보존했고 실제 발견된 test_adapter_boundaries로
바로잡아 다시 실행했다. 이 거부를 제외한 아래 완료 실행들의 failure/error/Skip·expected failure·
unexpected success·누락·중복·unexpected는 모두 0이다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| `c12-schema-focused` llm | 42/42 | 42 | 0/0/0 | 4.783초 | 0 |
| `c12-focused-corrected` llm | 121/121 | 121 | 0/0/0 | 22.123초 | 0 |
| `c10-c12-level3` llm | 2090/2090 | 2090 | 0/0/0 | 735.642초 | 0 |
| `c10-c12-level3` hub | 563/563 | 563 | 0/0/0 | 715.229초 | 0 |

c10 Hub 팩토리 수정, c11 CAS/참조 증거, c12 현재 계약 시험이 포함된 **하나의 현재 통합 소스**로
전체 LLM/Hub를 각각 실행했다. 별도 c12 Level 2를 중복 실행하지 않았고 다른 실행의 성공을
하나의 전체 결과로 합치지 않았다. 승인·누적 예산·Tool 실제 효과·중단/재개·Checkpoint·RAG
원본/identity·삭제·트랜잭션 장애·실제 host/X11/bwrap 필수 범위를 포함했다.
R3 실제 효과 Mutation 검출 True/outer exit 0, 실제 환경 검사 LLM·Hub 각각 exit 0,
문서 검사 132/182/959/34·오류 0/exit 0이다. 정적 검사 new/stale/review 오류/S1·S4·S5 미해결 0,
reviewed 55·미해결 403, exit 0이다. S2 34·S3 351·S6 18은 남아 있으며 P1 최종 완료가 아니다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c12-schema-focused/llm.json --modules tests.llm.test_naming_contract tests.llm.test_schema_ownership tests.llm.test_current_contracts
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c12-focused-corrected/llm.json --modules tests.llm.test_naming_contract tests.llm.test_schema_ownership tests.llm.test_current_contracts tests.llm.test_settings_contract tests.llm.test_configuration_validation tests.llm.test_loop tests.llm.test_graph_engine tests.llm.test_adapter_boundaries
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c10-c12-level3/llm.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c10-c12-level3/hub.json
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S4 --require S5 --output /workspace/llm-clean-resume/p1/c12-static.json
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_environment --suite llm --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12
.venv-linux312/bin/python -m tests.llm.check_environment --suite hub --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c12-docs-before.json
```

전체 실행 전후 697개 Python 소스 hash `4e33a87d7d74e73672706eed9cb10672f0cb011cea7aadae38ce1199404e7a83`,
859개 입력/환경 hash `359fc9ab95427541732587c731dca25363982036c48dc3e8657fadd5973ff294`가 각각 동일했다.
Python 3.12.14·136개 의존성·깨끗한 실제 host `d833790959a76ac457fdeea3f21a6bea1eecfe12`와
`TERM=xterm-256color`, `LITELLM_LOCAL_MODEL_COST_MAP=True`, `LITELLM_MODE=PRODUCTION`,
`HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb`, `HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip`
환경을 사용했다. 정확한 명령·exit·discovery·원본 로그·이전 시험·fixture hash는
`/workspace/llm-clean-resume/p1/c12-before/`, `c12-schema-focused/`, `c12-focused-final/`,
`c12-focused-corrected/`, `c10-c12-level3/`에 보존했다.
이 전체 통과 뒤 문서를 먼저 갱신하고 c10–c12를 refactor/llm-clean에 Push한다.
main·이전 backend 브랜치·Draft PR #1은 변경하지 않으며 원격 CI는 기존 조회 403으로 미확인이다.
현재 계약 시험이 목적이므로 이전 알고리즘/형식을 재도입하거나 과거 통과 결과를 승계하지 않았다.
P1 나머지 owner 선언·Presence를 정리한 후 P1 최종 S1–S5와 전체 게이트를 통과해야 A–F로 진행한다.

## P1-c11: CAS·참조와 형식 버전의 구분 — 로컬 Level 2

제품 코드·저장 형식·CAS 이름/비교·승인/실행 정책은 변경하지 않았다. S4 후보 9건을
현행 Revision/Schema/참조 계약과 실제 소비자에 따라 개별 분류했다. MCP의 호출 전 snapshot,
발견 결과 publication, catalog 조회 3건은 정의 hash와 connector revision을 비교한다.
Skill 대상 참조 1건, 복구·이력 정리 plan 2건, Hub Tool 삭제·builtin 선택 2건도 현재 content/
설정 CAS다. 이 이름들은 naming 계약에서 보존하도록 정한 것이며 형식 호환 Reader가 아니다.
ProjectActivityEvent 1건은 Run/Step source에 component/version을 허용하지 않는 참조 variant
검사다. 해당 기존 공개 계약을 data-contracts에 명시했다.

개별 ID·owner·사유·계약·소스/소비자/시험/Fixture/의존성 hash를 기록했다. raw 후보 458건을
그대로 남기며 reviewed 55·미해결 403, new/stale/review 오류/S1·S4·S5 미해결 0, exit 0이다.
AST 규칙 완화나 파일 면제는 없다. 남은 S2 34·S3 351·S6 18과 현재 Naming/Schema 잔여 작업을
유지하며 P1 최종 완료로 보고하지 않는다.

기존 CAS/실제 효과/장애 시험을 재사용하고 부족한 증거만 보강했다. 새 MCP 시험은 발견 연결이
열린 동안 정의 또는 connector revision을 바꿔 publication 거부, 이전 catalog와 **Project 전체
파일 byte** 보존, stale 조회 거부, Tool·모델 호출 0, 연결 정리를 확인한다. 기존 Activity 손상
시험에는 otherwise-valid JSON의 component/version 참조를 추가해 거부·원문 비노출·원본 파일
보존을 확인했다. 기존 손상 사례와 모든 Assertion은 유지했다.

첫 집중 실행의 실패 2건(test ID 1개)은 새 전체 파일 비교가 정상적인 project.loaded 조회 로그도
변경 금지로 잡았기 때문이다. catalog 비교는 통과했다. 실제 로그를 별도 Application FileLogSink에
보존하여 전체 Project byte Assertion을 유지했다. 두 번째 실패 2건은 새 로그 확인에 `.failed`
이벤트를 잘못 기대한 시험 오류였다. 실제 기록은 정상 조회 로그였다. 이를 조회 기록의 증가와
실제 event 확인으로 고쳤으며 로그를 끄거나 원본/효과 검사를 줄이지 않았다. 두 원래 fixture와
실패 로그를 보존했다. 이 실패들을 제품 안전성 결함이나 성공으로 재분류하지 않는다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| `c11-evidence` llm | 67/67 | 66 | 2/0/0 | 30.114초 | 1 |
| `c11-evidence-corrected` llm | 67/67 | 66 | 2/0/0 | 29.938초 | 1 |
| `c11-focused-final` llm | 24/24 | 24 | 0/0/0 | 12.668초 | 0 |
| `c11-level2` llm | 218/218 | 218 | 0/0/0 | 70.632초 | 0 |
| `c11-level2` hub | 53/53 | 53 | 0/0/0 | 89.337초 | 0 |

모든 실행의 Skip·expected failure·unexpected success·누락·중복·unexpected는 0이다.
Level 2 LLM 18개 모듈은 해당 owner·검사기·Component/Agent 참조·이력/복구 소비자와 공통 승인·
예산·효과·Checkpoint·RAG·삭제·트랜잭션 안전성을 포함한다. Hub 7개는 stale 삭제/선택·승인 후
실제 파일 효과·이력 보존·활동 조회·Component 관리·오류 계약·자원 실행 소비자다. 실제 프로세스
중단·장애 주입과 원본 보존을 유지했다. R3 실제 효과 Mutation 검출 True/outer exit 0,
문서 검사 132/182/959/34·오류 0/exit 0이다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c11-evidence/llm.json --modules tests.llm.test_mcp_runtime tests.llm.test_project_activity tests.llm.test_skill_references tests.llm.test_history_retention_integrity tests.llm.test_operations_schema
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c11-evidence-corrected/llm.json --modules tests.llm.test_mcp_runtime tests.llm.test_project_activity tests.llm.test_skill_references tests.llm.test_history_retention_integrity tests.llm.test_operations_schema
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c11-focused-final/llm.json --modules tests.llm.test_mcp_runtime tests.llm.test_project_activity
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c11-level2/llm.json --modules tests.llm.test_mcp_runtime tests.llm.test_skill_references tests.llm.test_project_activity tests.llm.test_history_retention_integrity tests.llm.test_operations_schema tests.llm.test_architecture_checks tests.llm.test_architecture_reviews tests.llm.test_component_record_integrity tests.llm.test_component_storage_integrity tests.llm.test_agent_resource_integrity tests.llm.test_transactions tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c11-level2/hub.json --modules tests.hub.test_interaction_updates tests.hub.test_question_tools tests.hub.test_history_retention tests.hub.test_activity tests.hub.test_component_managers tests.hub.test_error_contract tests.hub.test_resource_tools
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S4 --require S5 --output /workspace/llm-clean-resume/p1/c11-static-final.json
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c11-docs-before.json
```

Level 2 전후 697개 Python 소스 hash `344cf9c631024ed6671f42af2f20c068aa72f8bf7da81dcd065d420ce89e1de7`,
859개 입력/환경 hash `a189555de7cba00dc06edd197f5e3aa49a97344b0f7ee782bc5b5238c071d99a`가 각각 동일했다.
Python 3.12.14·136개 의존성·깨끗한 실제 host `d833790959a76ac457fdeea3f21a6bea1eecfe12`와
c7–c9 게이트의 TERM/LiteLLM/Xvfb/xclip 환경을 유지했다. 명령·exit·JSON/원본 로그·오류 fixture는
`/workspace/llm-clean-resume/p1/c11-evidence/`, `c11-evidence-corrected/`, `c11-focused-final/`,
`c11-level2/`에 보존한다. 현재 검증된 CAS 알고리즘을 그대로 사용해 과거 구현을 다시 이식하지 않았다.
제품 동작이 바뀌지 않은 개별 정적 분류와 증거 보강 범위이므로 전체 LLM/Hub는 이번 소단계에서
유예하며, c10 이후 P1 묶음 Level 3에서 현재 통합 소스로 실행한 뒤 Push한다. 로컬 소단계 완료를
P1 전체나 원격 CI 완료로 간주하지 않는다.

## P1-c10: Hub 팩토리 Presence — 로컬 Level 2

HubConfig와 create_backend의 Engine/Component 팩토리 입력 두 경로를 정리했다. 내용이 있지만
False로 평가되는 dict를 넘기면 지정 모델 대신 기본 모델이 호출됐고, 같은 성질의 list/tuple은
명시한 Component 교체가 누락됐다. `or {}`/`or ()`를 None 전용 선택으로 바꿨다.
None·빈 컬렉션은 기본 등록을 유지하며 명시한 nonempty 컬렉션은 truthiness와 무관하게 쓴다.
HubConfig는 Engine dict·문자열 이름·callable, Component list/tuple·callable을 구성 전에 검증한다.
list는 기존 실제 소비자가 사용하는 형식이므로 유지하고 annotation에 명시했다. 잘못된 Engine
입력의 AttributeError 6건과 조용히 수락하던 Component 입력 7건은 정상 입력 오류로 거부한다.
팩토리 생성은 기존대로 backend thread가 담당하며 검증 때 실행하지 않는다.

새 시험 4개에서 실제 모델 파일 효과가 수정 전 **기본 1/명시 0**, 수정 후 **기본 0/명시 1**임을
검증했다. falsey list/tuple의 팩토리 실제 파일 효과·동일 Component·실제 Prompt 저장/조회,
잘못된 입력의 무생성 거부, None/빈 컬렉션의 기본 Engine/Component 등록도 확인했다.
수정 전 실패 10·error 6은 실패 test ID 3개의 subtest 결과다. Assertion이나 파일 효과 검사를
완화하지 않았고 원래 제품 코드·시험·로그를 `c10-before/`에 보존했다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| `c10-before` hub | 4/4 | 1 | 10/6/0 | 9.498초 | 1 |
| `c10-focused` hub | 25/25 | 25 | 0/0/0 | 64.373초 | 0 |
| `c10-level2` llm | 277/277 | 277 | 0/0/0 | 71.179초 | 0 |
| `c10-level2` hub | 84/84 | 84 | 0/0/0 | 225.863초 | 0 |

모든 실행의 Skip·expected failure·unexpected success·누락·중복·unexpected는 0이다.
Level 2 LLM 21개 모듈은 감사 검사기·현재 설정/Schema·Loop/Graph/Agent Workflow·정책 소유권/
합성·Tool 제약과 공통 승인·누적 예산·효과·Checkpoint·RAG·삭제·트랜잭션 안전성을 포함한다.
Hub 12개 모듈은 생성/재열기·설정·모델/Component 주입·실제 RAG/자원 Tools·승인 질문·재시도/
Provider 복구 소비자다. 실제 효과·중단/재개·원본 보존과 장애 주입 검증을 유지했다.

Backend 저장 형식·실행 정책은 변경하지 않았다. 기존 None·일반 dict/list/tuple의 등록 조합은
같으며 잘못된 입력과 falsey nonempty 컬렉션의 처리가 변경 범위다. Hub의 언어 상속·작업 경로
선택 등 다른 Presence 후보까지 완료했다고 보고하지 않는다. 이 범위와 필수 관련 안전성이
통과했으므로 이번 소단계의 전체 LLM/Hub는 유예한다. 다음 P1 묶음 Level 3에서 현재 통합 소스의
전체 검증 후 Push한다. 앞선 c7–c9 전체 통과를 변경된 c10의 전체 결과로 재사용하지 않는다.

정적 검사 new/stale/review 오류/S1·S5 미해결 0, reviewed 46·미해결 412, exit 0이다.
실제 수정된 create_backend의 두 S2 발생만 제거했고 AST 규칙·기존 분류·다른 후보는 유지했다.
R3 실제 효과 Mutation 검출 True/outer exit 0, 문서 검사 132/182/959/34·오류 0/exit 0이다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c10-before/hub.json --modules tests.hub.test_factory_presence
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c10-focused/hub.json --modules tests.hub.test_factory_presence tests.hub.test_bootstrap tests.hub.test_creation_contracts tests.hub.test_defaults tests.hub.test_rag_defaults tests.hub.test_retry_observations
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c10-level2/llm.json --modules tests.llm.test_architecture_checks tests.llm.test_architecture_reviews tests.llm.test_current_contracts tests.llm.test_configuration_validation tests.llm.test_schema_ownership tests.llm.test_settings_contract tests.llm.test_agent_workflow tests.llm.test_graph_engine tests.llm.test_loop tests.llm.test_project_policies tests.llm.test_policy_consumers tests.llm.test_policy_ownership tests.llm.test_tool_constraints tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_transactions
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c10-level2/hub.json --modules tests.hub.test_factory_presence tests.hub.test_bootstrap tests.hub.test_creation_contracts tests.hub.test_defaults tests.hub.test_rag_defaults tests.hub.test_components tests.hub.test_resource_tools tests.hub.test_question_tools tests.hub.test_tool_retry tests.hub.test_retry_observations tests.hub.test_provider_recovery tests.hub.test_settings
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S5 --output /workspace/llm-clean-resume/p1/c10-static-final.json
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c10-docs-before.json
```

Level 2 전후 697개 Python 소스 hash `d5f60784afb1329a302a583b3435585269af5324572a5ccb8782b85c6c960f1b`,
859개 입력/환경 hash `9c1a4a46089cf23acf453a31f19078606f184fe77ac1e3a8b1cd7e3d4253dece`가 각각 동일했다.
Python 3.12.14·136개 의존성·실제 host `d833790959a76ac457fdeea3f21a6bea1eecfe12`와 바로 앞
c7–c9 게이트의 TERM/LiteLLM/Xvfb/xclip 환경을 유지했다. 실제 명령·exit·discovery·원본 로그는
`/workspace/llm-clean-resume/p1/c10-before/`, `c10-focused/`, `c10-level2/`에 보존한다.
이전 backend 브랜치의 bootstrap 30–65행·HubConfig 19–61행만 읽기 전용 대조했다.
동일한 truthiness 구현이 있어 가져오지 않았으며 현재 Presence 계약에 따라 수정했다.
P1 전체·A–F·원격 CI 완료를 뜻하지 않는다.

## P1-c9: Hub 표시·생성 선언 분류와 c7–c9 묶음 Level 3

제품 코드·AST 검출 규칙·실행 정책은 변경하지 않았다. 개별 후보 33건을 실제 소유자와
호출 경로에 따라 분류했다. 번역 MESSAGES의 24건은 설정 탭/설명·X11/실행 오류 안내·
Component 명령 설명·관찰된 재시도 대상/횟수의 표시 문자열이다. Language와 실제 UI 소비자를
통하며 제한값·오류 분류·재시도 권한을 생성하지 않는다. 생성 defaults의 9건은 provider_defaults,
rag_defaults, create_project가 한 곳에서 소유하는 초기값이다. 명시값을 우선하여 새 Project에
저장하고 기존 Project 선택은 생성 전에 반환한다. 이들은 현재 Hub 생성 계약에 명시된 선언이며,
Backend의 Schema·승인·예산·효과 규칙을 대신하지 않는다.

각 발생의 정확한 ID·개별 사유·계약·전체 후보 파일·필요한 소비자/검증기·시험/의존성 hash를
고정했다. 파일 전체 면제나 자동 baseline 갱신은 없고 원본 후보 460건은 보고서에 남는다.
reviewed 46·미해결 414, new/stale/review 오류/S1·S5 미해결 0, exit 0이다.
미해결에는 S2 36·S3 351·S4 9·A에서 처리할 S6 18이 남아 있다. P1 최종 S1–S5 통과가 아니다.

기존 c1의 생성 감사와 현재 계약을 재사용했다. 새 중복 시험 대신 현재 검사기의 엄격한 성공/
실패 사례와 Hub 생성·설정·재열기·사용자 값 보존·backend 한도 거부·RAG 실제 검색·재시도 표시/
권한·X11 실제 소비자 10개 모듈을 실행했다. 이후 c7·c8과 **같은 현재 통합 소스**에서 전체
LLM/Hub를 각각 한 번 실행했으므로 별도 c9 Level 2를 반복하지 않았다. c7/c8의 과거 집중 결과를
이번 전체 실행의 성공으로 합산하지 않는다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| `c9-focused` llm | 23/23 | 23 | 0/0/0 | 4.012초 | 0 |
| `c9-focused` hub | 72/72 | 72 | 0/0/0 | 108.398초 | 0 |
| `c7-c9-level3` llm | 2086/2086 | 2086 | 0/0/0 | 731.456초 | 0 |
| `c7-c9-level3` hub | 559/559 | 559 | 0/0/0 | 713.366초 | 0 |

모든 실행의 expected failure·unexpected success·누락·중복·unexpected도 0이다.
전체 게이트는 승인·누적 예산·실제 Tool 효과·중단/Resume·Checkpoint·RAG identity/원본·삭제·
트랜잭션 장애 주입과 실제 host/X11/bwrap 필수 시험을 포함한다. R3 실제 효과 Mutation 검출
True/outer exit 0, 실제 환경 검사 LLM·Hub 각각 exit 0, 문서 검사 132/182/959/34·오류 0/exit 0.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c9-focused/llm.json --modules tests.llm.test_architecture_checks tests.llm.test_architecture_reviews
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c9-focused/hub.json --modules tests.hub.test_creation_contracts tests.hub.test_defaults tests.hub.test_rag_defaults tests.hub.test_general_options tests.hub.test_settings tests.hub.test_response_reliability tests.hub.test_error_contract tests.hub.test_retry_ui tests.hub.test_component_managers tests.hub.test_x11_clipboard
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c7-c9-level3/llm.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c7-c9-level3/hub.json
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S5 --output /workspace/llm-clean-resume/p1/c9-static.json
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_environment --suite llm --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12
.venv-linux312/bin/python -m tests.llm.check_environment --suite hub --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c9-docs-before.json
```

전체 실행 전후 696개 Python 소스 hash `15d3340325b0c57b61aa372de7133c9ea352689769b51cf64d7b8e336b5df1f0`,
858개 입력/환경 hash `320572f5156f2a2c48e02b1fd072834cbbc0592a673b1561d16ac1d8dd7c7fe6`가 각각 동일했다.
Python 3.12.14·136개 의존성·깨끗한 실제 host `d833790959a76ac457fdeea3f21a6bea1eecfe12`와
`TERM=xterm-256color`, `LITELLM_LOCAL_MODEL_COST_MAP=True`, `LITELLM_MODE=PRODUCTION`,
`HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb`, `HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip`
환경을 사용했다. 명령·exit·discovery JSON·원본 로그·동일성 증거는
`/workspace/llm-clean-resume/p1/c9-focused/`와 `c7-c9-level3/`에 보존했다.

P1 나머지 owner·현재 Schema/Naming과 A–F는 미완료다. 이 묶음 전체 통과 뒤 문서를 먼저
갱신하고 c7–c9를 작업 브랜치에 Push한다. 기존 원격 CI 조회의 403 차단이 해소되지 않았으므로
원격 CI 통과를 선언하지 않는다. 이후 Push의 정확한 SHA도 기존 전체 필수 CI 대상이다.

## P1-c8: Workflow 현재 형식 선언 — 로컬 Level 2

WorkflowGraph.SCHEMA_VERSION과 MANAGED_FIELDS를 Schema·순수 Reader·빌더 Writer가 공유한다.
현재 version=1, 최상위/중첩 body의 정확한 int 검사, 관리 필드의 생성자 거부, 닫힌 Schema와
실행 알고리즘은 그대로다. 공개 Schema 전체가 변경 전 JSON과 동일함을 확인했다. 호환 Reader,
자동 변환, 별도 helper 계층을 추가하지 않았다. 버전/관리 필드 계약군만 정리했으며 Workflow의
다른 제어 값이나 Graph Engine 전체의 선언까지 완료했다고 보고하지 않는다.

새 시험 3개는 명시 current roundtrip·관리 필드 거부·사본 독립성, 최상위/중첩의 bool/float/text/
null/미지원/누락 버전 거부와 입력 불변, 실제 create/save 실패 후 **Project 모든 파일 byte** 보존을
확인한다. 첫 실행의 error 2건은 새 중첩 fixture에 필수 on_limit을 빠뜨린 시험 오류였다.
수정 뒤 실패 2건(실패 test ID 1개)은 누락된 중첩 버전이 이미 상위 닫힌 Schema에서 거부됐으나
새 시험이 schema_version 문구만 기대했기 때문이다. 실제 nodes/loop 경로를 반영했다.
원래 거부·입력 불변·전체 byte Assertion은 유지했고, 초기 제품/fixture/로그를 보존했다.
이 실패들을 제품의 새 안전성 결함 또는 성공으로 재분류하지 않는다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| `c8-contract-before` llm | 3/3 | 1 | 0/2/0 | 0.088초 | 1 |
| `c8-contract-corrected` llm | 3/3 | 2 | 2/0/0 | 0.196초 | 1 |
| `c8-focused-1` llm | 38/38 | 38 | 0/0/0 | 9.955초 | 0 |
| `c8-level2` llm | 332/332 | 332 | 0/0/0 | 140.145초 | 0 |
| `c8-level2` hub | 15/15 | 15 | 0/0/0 | 65.737초 | 0 |

모든 실행의 Skip·expected failure·unexpected success·누락·중복·unexpected는 0이다.
최종 Level 2의 LLM 26개 모듈은 모든 test_graph 모듈·LangGraph·nested/Agent Workflow·실제
예제·설정/Schema/Naming·Component 저장 소비자·감사 검사기와 승인·예산·효과·Checkpoint·RAG·
삭제·트랜잭션 안전성을 포함한다. Hub 4개는 실제 작성/실행·Component 관리·설정·Project 권한
소비자다. 원본·실제 효과·중단/재개 검증을 유지했다. 공개 형식/알고리즘이 같은 좁은 owner 선언
변경이며 위 필수 범위를 통과했으므로 이번 소단계의 전체 LLM/Hub는 유예한다. 후속 P1 묶음
Level 3에서 전체 통합 검증 후 Push한다. 소단계 결과를 P1 전체/원격 CI 완료로 보고하지 않는다.

정적 검사 new/stale/review 오류/S1·S5 미해결 0, reviewed 13, 미해결 447, exit 0이다.
기존 Workflow 후보 6건을 정리했다. 실제 중복 version·관리 필드 목록은 owner 참조로 바꿨고,
남은 정확한 type/version guard 두 발생은 실제 Reader/Writer 시험과 선언/Schema/소비자/계약/
시험 hash로 개별 분류했다. 기존 11개 분류도 유효하다. 외부 함수에서 클래스 선언을 참조하는
이 두 guard를 위해 AST 규칙을 완화하지 않았다. R3 실제 효과 mutation 검출 True/outer exit 0,
문서 검사 132/182/959/34·오류 0/exit 0이다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c8-level2/llm.json --modules tests.llm.test_agent_workflow tests.llm.test_architecture_checks tests.llm.test_architecture_reviews tests.llm.test_checkpoint_integrity tests.llm.test_component_record_integrity tests.llm.test_component_storage_integrity tests.llm.test_configuration_validation tests.llm.test_configuration_workflow_example tests.llm.test_graph_agent_contract tests.llm.test_graph_checkpoints tests.llm.test_graph_decisions tests.llm.test_graph_engine tests.llm.test_graph_rag_example tests.llm.test_interaction_storage tests.llm.test_langgraph_engine tests.llm.test_lifecycle_deletion_integrity tests.llm.test_lifecycle_integrity tests.llm.test_naming_contract tests.llm.test_nested_graph tests.llm.test_operation_integrity tests.llm.test_rag_storage tests.llm.test_schema_ownership tests.llm.test_settings_contract tests.llm.test_transactions tests.llm.test_usage_deletion tests.llm.test_workflow_format
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c8-level2/hub.json --modules tests.hub.test_component_managers tests.hub.test_project_authority tests.hub.test_resource_tools tests.hub.test_settings_project_flow
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S5 --output /workspace/llm-clean-resume/p1/c8-static-final.json
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c8-docs.json
```

Level 2 전후 696개 Python 소스 hash `15d3340325b0c57b61aa372de7133c9ea352689769b51cf64d7b8e336b5df1f0`,
858개 입력/환경 hash `0e5e044a99fa6a12ab24bcec9ec1099770747fa02e1f7efc0b3164c5e79fe98a`가 각각 동일했다.
Python 3.12.14/136개 의존성·실제 host `d833790959a76ac457fdeea3f21a6bea1eecfe12`와 기존
TERM/LiteLLM/Xvfb/xclip 환경을 유지했다. 실행별 정확한 명령·exit·JSON/로그·원래 fixture는
`/workspace/llm-clean-resume/p1/`에 보존했다. 현재의 검증된 Workflow/Graph 계약을 사용했고
동일한 과거 알고리즘을 다시 조사하거나 이식하지 않았다. P1 나머지 owner·현재 Schema/Naming과
A–F는 여전히 미완료다.

## P1-c7: 근거가 있는 개별 정적 분류 — 로컬 Level 2

P1-c4–c6 전체 게이트/Push `8058c71bfc5dd11bc701b69033e42408ffa454fe` 뒤 검토된 기술적
후보를 엄격 게이트에서 구분하도록 검사 절차를 보강했다. 제품 코드·저장·실행 정책은 변경하지
않았다(`git diff --quiet HEAD -- llm hub examples`, exit 0). 기존 AST 검출 규칙도 그대로다.
[개별 분류 계약](architecture-reviews.md)에 따라 정확한 발생 ID·owner·개별 사유·현재 계약·
후보/의존 소스/시험/Fixture hash·구체적 시험 ID를 별도 파일에 기록한다. 신규·이동·중복·삭제·
근거 변경은 기본 검사에서도 실패한다. 구 API S1과 미완료 Provider 경계 S6은 승인할 수 없다.
분류의 시험 ID는 검증 범위이며 과거 시험 성공 증거가 아니다. 실제 회귀·R3·CI gate는 유지한다.

이미 c5에서 확인한 Memory 6건(엄격 bool 조건 3, 주기 산술 1, 실제 ParameterLayout 선언 2),
공유 domain Reader/Writer의 정확한 int+canonical version guard 4건, 표준 logging.FileHandler의
필수 handleError 콜백 1건만 개별 분류했다. CAS·Workflow 등 다른 미해결 후보는 승인하지 않았다.
후보 파일 전체와 실제 선언/검증/계약/시험/Fixture를 pin하며, blanket owner/file/rule 예외나
자동 hash/baseline 갱신 기능은 없다. 원본 464개 finding은 모두 보고서에 남고 reviewed 11개와
미해결 453개를 구분한다. 미해결 목록에서는 이 11개만 제거했다.

새 검사기 시험 8개는 유효한 정확한 발생만 인정하고, 원본 finding 가시성·새 발생·변경/이동·
복제·의존 파일/계약/시험 변경·빈 근거·없는 시험·중복 review·중복 debt·없는 명시 파일·S1/S6
승인 시도를 거부함을 검사한다. 기존 모든 AST 양성/음성·미해결 strict 실패 Assertion을 보존했다.
새 저장 시험은 bool/float/text/null/미지원/누락 version의 Reader·기존/신규 Writer를 검사하며
원본 전체 byte 보존과 신규 파일 미생성을 확인한다. 새 표준 handler 시험은 실제 파일의 write에
OSError를 주입하여 failed_writes 1회·원본 byte 동일·stderr 없음과 후속 정상 append를 확인한다.
기존 파일/프로세스/효과 Assertion을 제거하지 않았다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| `c7-checker-1` llm | 23/23 | 23 | 0/0/0 | 3.117초 | 0 |
| `c7-evidence-1` llm | 79/79 | 79 | 0/0/0 | 10.878초 | 0 |
| `c7-focused-final` llm | 114/114 | 114 | 0/0/0 | 57.147초 | 0 |
| `c7-level2` llm | 276/276 | 276 | 0/0/0 | 91.586초 | 0 |
| `c7-level2` hub | 17/17 | 17 | 0/0/0 | 83.309초 | 0 |

모든 실행의 Skip·expected failure·unexpected success·누락·중복·unexpected는 0이다.
새 근거 시험과 분류 파일을 추가한 시점을 구분하여 각 집중 실행을 기록했다. 최종 Level 2는
21개 LLM 모듈의 분류 근거 owner·설정/Schema/Naming·회귀 runner·저장 소비자와 승인·예산·
효과·Checkpoint·RAG·삭제·트랜잭션 공통 안전성을 포함한다. Hub 3개는 Provider 로깅/활동/진단
소비자다. 제품과 CI Job은 그대로이고 검사기와 정확한 근거 파일의 영향 범위가 한정되므로 이번
소단계에서 전체 LLM/Hub는 실행하지 않았다. c6 전체 결과를 새 실행으로 재표현하지 않는다.
후속 P1 묶음 Level 3에서 전체 통합 검증 후 Push한다. 현재는 c7 로컬 완료만 뜻한다.

정적 `--require S1 --require S5`는 new/stale/review 오류/필수 미해결 0, reviewed 11,
미해결 debt 453, exit 0이다. P1 종료용 S1–S5 검사는 필수 미해결 435로 **exit 1**을 확인했다.
이는 알려진 잔여 작업 확인이며 P1 완료가 아니다. 원본 후보를 숨겨 엄격 gate를 통과시키지 않았다.
R3 mutation 검출 True/outer exit 0, 문서 검사 132/182/958/34·오류 0/exit 0이다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c7-level2/llm.json --modules tests.llm.test_architecture_checks tests.llm.test_architecture_reviews tests.llm.test_checkpoint_integrity tests.llm.test_configuration_validation tests.llm.test_current_contracts tests.llm.test_interaction_storage tests.llm.test_lifecycle_deletion_integrity tests.llm.test_lifecycle_integrity tests.llm.test_memory_processing tests.llm.test_memory_processing_config tests.llm.test_naming_contract tests.llm.test_operation_integrity tests.llm.test_operational_storage tests.llm.test_provider_runtime tests.llm.test_rag_storage tests.llm.test_regression_runner tests.llm.test_schema_ownership tests.llm.test_session_domain tests.llm.test_settings_contract tests.llm.test_transactions tests.llm.test_usage_deletion
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c7-level2/hub.json --modules tests.hub.test_backend_diagnostics tests.hub.test_provider_activity tests.hub.test_provider_stabilization
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S5 --output /workspace/llm-clean-resume/p1/c7-static.json
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --output /workspace/llm-clean-resume/p1/c7-p1-pending.json
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c7-docs.json
```

Level 2 전후 695개 Python 소스 hash `09f4580c4e368bf511c3e60f91368d5f1f616e1e3422a9a3ebff368248ae17c1`,
857개 입력/환경 hash `e459a32de914926f6c3efafe0ebc160804084f4a2e5242924453480fd48bc8ad`가 각각 동일했다.
Python 3.12.14/136개 의존성·실제 host `d833790959a76ac457fdeea3f21a6bea1eecfe12`와 기존
TERM/LiteLLM/Xvfb/xclip 환경을 유지했다. 실행별 명령·exit·JSON/로그는
`/workspace/llm-clean-resume/p1/`에 보존했다. 과거 브랜치/38개 자료군 감사를 다시 수행하지 않고
현재의 검증된 계약과 c5의 개별 판정을 재사용했다. P1 나머지 owner·현재 Schema/Naming 및
A–F·원격 CI는 아직 완료되지 않았다.

## P1-c6: Tool 실패 분류 선언과 c4–c6 묶음 Level 3

c4 `0af28d0`·c5 `7a51b88`의 독립 로컬 검증/커밋에 이어 ToolExecutionError가 NO_EFFECT,
UNCERTAIN_EFFECT, EFFECTS와 기존 code를 소유하도록 정리했다. ToolExecutor 재시도/중단 기록,
Loop 복구, Worker 인코더/디코더, MCP·파일 Tool 소비자가 같은 선언을 쓴다. IPC의 오류 code와
효과 분류 값, 엄격한 bool·무효과 증거 요구, 승인·누적 예산·재개·불확실 효과의 의미는 그대로다.
안전한 재시도 여부의 OR는 같은 순서의 명시 조건 분기로 표현했다. 검사 예외나 새 호환 계층은 없다.
InspectionError는 별도 검사 경계가 소유한 오류 선언으로 유지하며 실행 효과 권한으로 쓰지 않는다.

이전 backend `bb0946257ab6bcabb9a3ef04d3d854e5333b07d5`의 해당 ToolExecutionError 생성자와
retry의 unknown/uncertain·수정 예산·취소 시험 이름만 선택적으로 비교했다. 같은 알고리즘/시험이
현재에도 있어 다시 이식하거나 복사하지 않았다. 기존 실제 Worker taxonomy·IPC 오류·파일 효과·
중단 후 재시작·승인 전 spawn 금지·retry/repair/Resume 시험을 그대로 재사용했다. 상수 이동을
흉내 내는 시험은 추가하지 않았고 기존 Assertion·효과 횟수·실패 주입은 모두 유지했다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| `c6-focused-1` llm | 107/107 | 107 | 0/0/0 | 48.378초 | 0 |
| `c4-c6-level3` llm | 2073/2073 | 2073 | 0/0/0 | 733.084초 | 0 |
| `c4-c6-level3` hub | 559/559 | 559 | 0/0/0 | 713.474초 | 0 |

모든 실행의 Skip·expected failure·unexpected success·누락·중복·unexpected는 0이다.
c6 집중 5개 모듈 뒤 공통 Tool 경계의 모든 직접/간접 소비자를 함께 검증하기 위해 Level 3으로
승격했다. 별도 c6 Level 2를 중복 실행하지 않고 **현재 통합 소스의 전체 LLM/Hub**가 해당 계약·
통합·안전성 범위를 포함하도록 했다. c4/c5의 서로 다른 소스 결과를 합산한 전체 성공이 아니다.
새 전체 게이트는 Memory와 Tool 실패 선언의 통합 상태를 검증한다. P1의 나머지 owner·현재 Schema/
Naming 정리는 남아 있으며, 이 게이트를 P1 전체·A–F 완료로 보고하지 않는다.

정적 c6-static-final은 new/stale/S1 미해결 0, debt 464, exit 0이다. 실제 해결한 19개 항목만
기준에서 제거했다. c4–c6 전체에서 54개를 해결했으며 다른 owner/계약의 후보를 예외로 승인하지
않았다. Runtime의 남은 승인 종류·관측 상태·checkpoint 레코드/키·범위 선언은 후속 P1 범위다.
R3 실제 효과 mutation 검출 True, outer exit 0이며 의도한 inner failure를 보존했다.
문서 검사 131/182/955/34, 오류 0, exit 0이며 실제 host/UID/bwrap 및 Hub X11 환경 검사도 각각
성공했다. GitHub 필수 전체 job과 test ID 배정을 변경하지 않았다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c4-c6-level3/llm.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c4-c6-level3/hub.json
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/p1/c6-static-final.json
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c6-docs.json
.venv-linux312/bin/python -m tests.llm.check_environment --suite llm --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/p1/c4-c6-level3/environment-llm.json
.venv-linux312/bin/python -m tests.llm.check_environment --suite hub --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/p1/c4-c6-level3/environment-hub.json
```

전체 게이트 전후 694개 Python 소스 hash `1da2970bc5ed13df1da89b6e5e99ce66df914db119f2a3c848e5e6747dc452d1`,
854개 입력/환경 hash `b36c0bcdf8bd03a73da4494d0690d828cdd5a18c43523a3541b303b780bbce61`가 각각 동일했다.
Python 3.12.14/136개 의존성, 실제 host `d833790959a76ac457fdeea3f21a6bea1eecfe12`, 기존
TERM/LiteLLM/Xvfb/xclip 환경을 유지했다. 실행별 명령·exit·JSON/로그·해결한 후보는
`/workspace/llm-clean-resume/p1/`에 보존했다. 검증 후 문서를 먼저 갱신하고 소스 동일성을
다시 확인하여 작업 브랜치에 묶음 Push한다. main·이전 backend·Draft PR #1은 변경하지 않는다.
원격 CI는 해당 Push SHA의 실제 결과를 확인하지 못했으므로 미확인이다.

## P1-c5: Memory 처리 설정·압축 상태 선언 — 로컬 Level 2

c4 로컬 커밋 `0af28d0` 다음 계약군이다. MemoryProcessor의 처리 옵션 Schema를 Component의
설정/편집 경계와 런타임 검증이 공유한다. 기존 공개 Schema 전체와 비교해 동일함을 확인했다.
정확한 int 검사를 Schema의 정수 필드에서 유도하여 누락된 max_output_chars도 보호한다.
기존 닫힌 Schema가 금지한 모드/null/알 수 없는 처리 필드도 런타임 경계에서 거부한다.
기능 활성화의 필수 입력·Provider 선택·추가 호출 정책은 기존 계약을 따른다.

새 시험은 max_output_chars=1.0이 실제 설정 파일에 저장되는 실패와 런타임의 잘못된 한도·
모드 수락을 재현했다. baseline의 실패 test ID는 3개, 실패 Assertion은 20건이다. 원래 제품/
fixture와 실패 로그를 보존했다. 수정 후 잘못된 저장 입력마다 Project 아래 **모든 파일 byte**가
동일하고 보조 모델 호출은 0이며, 명시 False/0/nullable 값과 유효한 최솟값은 유지된다.

Compactions는 Tool 결과 모드와 상태·재개/활성/미완료 집합·명시 action을 단일 선언으로 소유한다.
MemoryData·Tool 결과 처리·Component 참조 보호가 동일 선언을 사용한다. Run 상태는 RunStatus,
Message 상태는 MessageStatus, Memory 레코드 상태/범위는 MemoryComponent를 사용한다.
요약 형식/실패 모드는 MemoryProcessor, 처리 실패 코드·부분 참조 표시는 MemorySession이 소유한다.
저장 값·Schema·CAS/트랜잭션·상태 전이·재개/취소/삭제 허용 의미는 변경하지 않았다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| `c5-baseline` llm | 4/4 | 1 | 20/0/0 | 0.214초 | 1 |
| `c5-focused-1` llm | 72/72 | 72 | 0/0/0 | 50.504초 | 0 |
| `c5-focused-2` llm | 78/78 | 78 | 0/0/0 | 63.432초 | 0 |
| `c5-level2` llm | 529/529 | 529 | 0/0/0 | 215.537초 | 0 |
| `c5-level2` hub | 64/64 | 64 | 0/0/0 | 116.732초 | 0 |

모든 실행에서 Skip·expected failure·unexpected success·누락·중복·unexpected는 0이다.
첫 집중 검증 뒤 압축 상태 선언과 소비자를 정리하여 두 번째 집중 검증을 수행했다. 이 두 결과를
동일 소스의 전체 회귀로 합치지 않는다. Level 2는 최종 통합 소스에서 새로 수행했다.
LLM 40개 모듈은 Memory 전체·작업 상태·대형 Tool/Data·설정/Schema/Naming·Agent 소비자와
승인·예산·효과·Checkpoint·RAG·삭제·트랜잭션 안전성을 포함한다. Hub 15개는 Memory·문맥·
Component·Project 및 모든 settings 이름의 설정 화면 시험이다. 원본 보존·중단·취소·재개·
실제 효과 횟수 시험을 재사용했고 제거/축소하지 않았다. 변경은 Memory 소유 선언과 검증 경계로
한정되므로 전체 LLM/Hub는 유예했다. P1 관련 소단계를 묶는 Level 3에서 전체 검증 후 Push한다.

정적 검사 `c5-static-final`은 new/stale/S1 미해결 0, debt 483, exit 0이다. 실제 해결한 35개
Memory 항목만 기준 목록에서 제거했으며 새 예외를 추가하거나 검사 규칙을 바꾸지 않았다.
초기 정적 실행의 새 boolean 비교 후보 4건은 기존 boolean 조건으로 되돌렸다. 타입을 먼저 검증한
조건 OR는 fallback이 아니라 활성 여부의 합성이다. 최종 처리 owner의 남은 후보는 다음과 같다.

| 남은 후보 | 현재 판정/후속 범위 |
|---|---|
| processing의 S2 OR 3개 | Schema가 bool만 허용한 summarize/extract/nested_processing의 조건 합성. 값 대체 없음. 기존 후보를 보존하고 개별 의미를 기록함 |
| processing recall_every 나머지 비교 1개 | 첫 준비 이후 주기 판정의 산술 0. 정책값을 만들지 않으며 명시 0은 Schema에서 허용한 비활성 의미 |
| Component ParameterLayout config/policy 2개 | 해당 owner가 선언한 필드 위치. Schema와 pack/unpack이 소비하는 실제 단일 선언 |
| processing S6 4개·tool_results S6 1개 | SDK Tool wire 해석. A에서 정규 Adapter 경계로 이관할 미완료 항목이며 예외로 승인하지 않음 |

이 계약군 밖의 Memory 기록 종류·그 밖의 owner/Schema/Naming 후보까지 정리했다고 보고하지 않는다.
R3 실제 효과 mutation 검출 True, outer exit 0이며 의도한 inner 실패는 별도 보존했다.
문서 검사 131문서/182 Python fence/955상대 링크/34 anchor, 오류 0, exit 0이다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c5-level2/llm.json --modules tests.llm.test_agent_delegation tests.llm.test_agent_workflow tests.llm.test_architecture_checks tests.llm.test_checkpoint_integrity tests.llm.test_completion_integrity tests.llm.test_completion_processing tests.llm.test_component_record_integrity tests.llm.test_component_storage_integrity tests.llm.test_configuration_validation tests.llm.test_current_contracts tests.llm.test_data_component tests.llm.test_data_quarantine tests.llm.test_goals tests.llm.test_interaction_storage tests.llm.test_large_data tests.llm.test_lifecycle_deletion_integrity tests.llm.test_lifecycle_integrity tests.llm.test_long_running tests.llm.test_memory_compaction_integrity tests.llm.test_memory_component tests.llm.test_memory_consolidation_integrity tests.llm.test_memory_conversation tests.llm.test_memory_history_integrity tests.llm.test_memory_processing tests.llm.test_memory_processing_config tests.llm.test_memory_runtime_choices tests.llm.test_memory_summary_integrity tests.llm.test_naming_contract tests.llm.test_nested_graph tests.llm.test_operation_integrity tests.llm.test_project_authority tests.llm.test_project_policies tests.llm.test_provider_runtime tests.llm.test_rag_storage tests.llm.test_schema_ownership tests.llm.test_settings_contract tests.llm.test_tool_data tests.llm.test_transactions tests.llm.test_usage_deletion tests.llm.test_work_state
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c5-level2/hub.json --modules tests.hub.test_component_managers tests.hub.test_context tests.hub.test_memory_manager tests.hub.test_popup_settings_updates tests.hub.test_project_authority tests.hub.test_settings tests.hub.test_settings_editor tests.hub.test_settings_json tests.hub.test_settings_navigation tests.hub.test_settings_project_flow tests.hub.test_settings_restart tests.hub.test_settings_tabs tests.hub.test_settings_usability tests.hub.test_settings_viewport tests.hub.test_workspace_settings
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/p1/c5-static-final.json
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c5-docs.json
```

Level 2 전후 694개 Python 소스 hash `04dc0607042a37a0e176312c5ea1ce09e60a44f4f6e3927ad751db8bf3b4f5ad`,
854개 입력/환경 hash `55c436053b26eb80acc518a2bdd4e9fe6f562e1d7baae9df5336339b041e269f`가 각각 동일했다.
Python 3.12.14/136개 의존성, 실제 host `d833790959a76ac457fdeea3f21a6bea1eecfe12`와 기존
TERM/LiteLLM/Xvfb/xclip 환경을 유지했다. 정확한 명령·exit·JSON/로그·해결한 후보별 근거는
`/workspace/llm-clean-resume/p1/`에 보존했다. 이전 backend 알고리즘은 c4에서 비교한 동일
Memory 흐름을 재사용하며 불필요하게 재독해/재이식하지 않았다. 과거 결과는 현재 증거가 아니다.
이 단계는 c5 로컬 완료이며 P1 전체·A–F·원격 CI 완료가 아니다.

## H1–H5 이후 P1-c4: Memory 런타임 주입 — 로컬 Level 2

H1–H5 로컬 상위 게이트와 Push `a2b78e17b7ae0814e4b1ed6fc237d173c86a73e2` 뒤 P1-c 잔여를
재개했다. 아래 과거 중단/검증 기록은 수정하지 않는다. 이번은 c4의 제한된 로컬 완료이며 P1
전체/A–F/원격 CI 완료가 아니다. 나머지 owner 선언·현재 Schema 계약은 계속 남아 있다.

MemoryProcessor가 callable 객체의 truthiness로 completion_fn을 선택하여 명시한 falsey 모델을
기본 함수로 바꾸는 결함을 재현했다. 실제 호출 파일에 injected='' / default='unexpected default\n',
auxiliary_calls=1/main_calls=1이 남았다. 잘못된 모델·계수기·priority도 생성 시 거부하지 않았다.
수정은 None일 때만 기본 함수를 선택하고 callable 또는 None, 정확한 int priority를 owner에서
검증한다. falsey callable identity, 명시한 0/음수 우선순위는 보존한다. 미설정 중립 정렬값은
MemoryProcessor.UNCONFIGURED_PRIORITY 하나로 선언하고 Component resolver가 재사용한다.
활성 기능의 명시 priority 요구·저장 형식·승인·예산·효과 알고리즘은 변경하지 않았다.

새 4개 시험은 직접 owner 입력과 실제 Memory 추출을 검증한다. 수정 뒤 intended 파일에
auxiliary 1줄, 기본 함수 파일 없음, 주/보조 모델 각 1회와 저장 설정 동일성을 확인한다.
초기 baseline은 실패 test ID 3개/Assertion 19건이다. 원래 효과 횟수와 거부 Assertion,
초기 제품/fixture·로그를 보존했다.

| 실행 | discovered/started | pass | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| `c4-baseline` llm | 4/4 | 1 | 19/0/0 | 0.365초 | 1 |
| `c4-focused-1` llm | 68/68 | 68 | 0/0/0 | 50.796초 | 0 |
| `c4-level2` llm | 411/411 | 411 | 0/0/0 | 175.151초 | 0 |
| `c4-level2` hub | 25/25 | 25 | 0/0/0 | 62.356초 | 0 |

모든 실행의 Skip·expected failure·unexpected success·누락·중복·unexpected는 0이다.
Level 2의 30개 LLM 모듈은 Memory 전체 owner/저장·문맥/실제 모델·Agent·설정 소비자와
승인·예산·효과·Checkpoint·RAG·삭제·트랜잭션을 포함한다. Hub 6개는 Memory 관리·문맥·
Component·설정·Project 소비자다. 변경은 Memory 런타임 선택 경계로 한정되며 위 통합/안전성
검증이 통과했으므로 전체 LLM/Hub는 이번 소단계에서 실행하지 않았다. P1 관련 소단계를 묶는
상위 Level 3에서 현재 통합 소스 전체를 검증한 뒤 Push한다.

정적 c4-static-1은 new/stale/S1 미해결 0, 기존 debt 518, exit 0이다. 기준은 바꾸지 않았다.
이 결함은 기존 AST 후보에 없었으며 실제 호출 관계로 발견했다. 다른 owner의 미분류 후보를
해결했다고 주장하지 않는다. R3 실제 효과 mutation 검출 True, outer exit 0이며 의도한 inner
실패도 보존한다. 현재 계약 문서 검사는 131/182/955/34, 오류 0, exit 0이다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/p1/c4-level2/llm.json --modules tests.llm.test_agent_delegation tests.llm.test_agent_workflow tests.llm.test_checkpoint_integrity tests.llm.test_completion_integrity tests.llm.test_completion_processing tests.llm.test_component_record_integrity tests.llm.test_component_storage_integrity tests.llm.test_configuration_validation tests.llm.test_current_contracts tests.llm.test_goals tests.llm.test_interaction_storage tests.llm.test_lifecycle_deletion_integrity tests.llm.test_lifecycle_integrity tests.llm.test_memory_compaction_integrity tests.llm.test_memory_component tests.llm.test_memory_consolidation_integrity tests.llm.test_memory_conversation tests.llm.test_memory_history_integrity tests.llm.test_memory_processing tests.llm.test_memory_runtime_choices tests.llm.test_memory_summary_integrity tests.llm.test_nested_graph tests.llm.test_operation_integrity tests.llm.test_project_authority tests.llm.test_project_policies tests.llm.test_provider_runtime tests.llm.test_rag_storage tests.llm.test_settings_contract tests.llm.test_transactions tests.llm.test_usage_deletion
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/p1/c4-level2/hub.json --modules tests.hub.test_memory_manager tests.hub.test_context tests.hub.test_component_managers tests.hub.test_settings tests.hub.test_settings_project_flow tests.hub.test_project_authority
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/p1/c4-static-1.json
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/p1/c4-docs.json
```

Level 2 전후 693개 Python 소스 hash `640525a4da7276a270ff4e28c560ba73c703529fe0ea0adb849acfae9fa77853`,
853개 입력/환경 hash `46fa56619429e17026cf1d1be9da54005973887d2bb2a8e225a9d04b19d9447b`가 각각 동일했다.
Python 3.12.14/136개 의존성, 실제 host `d833790959a76ac457fdeea3f21a6bea1eecfe12`와 기존
TERM/LiteLLM/Xvfb/xclip 환경을 유지했다. 실행별 정확한 명령·exit·JSON/로그·원래 fixture는
`/workspace/llm-clean-resume/p1/`에 보존했다. 다른 실행을 하나의 전체 실행으로 합산하지 않는다.

이 단계에서 처음 이전 backend `bb0946257ab6bcabb9a3ef04d3d854e5333b07d5`를 읽기 전용으로
참고했다. Memory processing의 설정/생성자와 test_memory_processing의 요약·중단·재시작·
잘못된 설정 시나리오만 조회했다. 동일 알고리즘/시험은 현재에도 있어 중복 이식하지 않았다.
옛 Adapter 객체의 직접 주입은 identity 보존의 참고 근거이며 A의 Adapter 전환을 앞당겨 복사하지
않았다. 옛 provider or {}는 현재 strict None/타입 계약과 달라 채택하지 않았다. 브랜치 merge,
cherry-pick, 파일 통째 복사·구형 Schema/호환 계층 복원은 없으며 과거 결과를 현재 증거로 승계하지 않는다.

## 중단 원인: 전체 사용량 필드 누락을 빈 이력으로 처리

실제 Tool이 파일에 `first`를 쓴 뒤 다음 모델 요청을 실패시켜 체크포인트를 남겼다.
저장된 모든 실행 기록에서 `runtime_tool_usage` 필드 전체를 제거하고 백엔드를 다시 열어
Resume했다. 현재 정책의 `max_calls=1`은 그대로인데도 두 번째 Tool이 `second`를 썼다.
모델 응답은 scripted fixture이며 **두 파일 쓰기는 실제 효과**다. 실제 인증 모델 호출을
검증한 것으로 해석하지 않는다. 정상 체크포인트가 자동으로 손상된다는 뜻도 아니다.
필수 사용량이 없는 체크포인트를 명시 거부해야 하는 계약의 실패다.

원인은 [RunManager._context()](../../llm/services/runtime/runs.py)의 조건부 추출이다.
`if "runtime_tool_usage" in value`가 누락 기록을 검사 전에 제외한다. 모든 사용량 필드가
빠지면 `ToolExecutionScope.restore([])`가 호출되고 새 scope의 `calls=0`이 유지된다.
상한 자체가 없어지는 R3와는 경로가 다르지만, 재개 계보의 누적 사용량이 사라져
`max_calls=1`에서 실제 호출 2회를 허용한다.

중단 재현 시점까지 이 추출 코드는 변경하지 않았고 P0 커밋 `2284464`에도 동일하게 존재한다.
이번 재현 실행은 P1 작업 트리에서 수행했다. P0 체크아웃에서 동일한 효과 시험을 실행한
것으로 보고하지 않는다. P1에서 추가한 개별 usage 필드 검사는 전체 필드가 추출에서
제외되는 경우까지 보호하지 못한다.

[ResumeUsageContracts](../../tests/llm/test_current_contracts.py)에 두 경우를 분리했다.

| 실제 저장 파일에 주입한 실패 | 결과 |
|---|---|
| usage 내부 `calls` 누락 | 재시작 후 모델·Tool·새 Run 전에 거부, 통과 |
| `runtime_tool_usage` 전체 누락 | `first`와 `second`가 모두 기록됨, 실패 |

최종 재현 명령:

```bash
TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION \
  /workspace/.setup/ish-llm/verified-next/bin/python -m unittest -v \
  tests.llm.test_current_contracts.ResumeUsageContracts
```

실제 결과는 **2개 실행 / 1 pass / 1 failure / 0 error / 0 Skip, 0.527초, exit 1**이다.
로그는 `/workspace/llm-clean-p1/resume-envelope-final.log`다. assertion과 실패 주입을
유지했으며 Skip이나 호환 fallback을 추가하지 않았다.

사용자의 “안전성 문제가 발생하면 중단하고 보고하세요” 원칙에 따라 당시 자동 진행을 중단했다.
제품 변경과 실패 시험을 작업 트리에 보존했고 검증된 단계로 커밋·Push하지 않았다.
중단 당시 HEAD와 원격 `refactor/llm-clean`은 P0 커밋
`228446489a06d5a8a12f8199f10d81833875b985`다.

후속 수정은 Checkpoint의 사용량 기록 소유권과 admission 검증을 먼저 확정해야 한다.
`steering.py`는 사용량을 포함하지 않는 instruction 제어 기록도 쓰므로, 모든 record에
무조건 usage를 요구하는 수정은 정상 경로를 깨뜨릴 수 있다. 실행 기록·제어 기록을
구별하고 필수 누적 사용량의 누락을 Tool/모델 실행 및 새 Run 생성 전에 거부해야 한다.
Agent usage의 전체 누락은 당시 아직 재현하지 않았다. 이후 보강 과정의 결과는 아래에 기록한다.
해결 뒤 정상/손상 Resume, 승인·영수증·실제 효과, 전체 회귀를 다시 검증하기 전에는
P1-b/c 및 A–F로 진행하지 않는다.

## 재개 요청 후 보강

[재개 조정과 독립 판단](checkpoints.md#재개-조정과-독립-판단)에 기준을 명시했다.
RunManager는 검증을 조정하고, 사용량·승인·효과의 근거는 각 소유자가 해석한다.

- ToolExecutionScope가 공통 필드명과 타입 검사를 소유한다. 모든 서비스 checkpoint record에
  사용량을 기록하고, 추가 지시 선택 기록도 동일한 Writer 계약을 따른다.
  누락된 기록을 조건부 목록 추출로 제거하지 않는다.
- 계획 조회·접수·실행 시 같은 검증을 사용한다. 필수 사용량 오류는 `tool_usage_corrupt`로,
  계획 상태는 `corrupt`로 구분한다. 예산 소진·승인 거절로 표시하지 않는다.
- AgentExecution이 자신의 scope에 속한 자식 기록과 실행 중 소유 기록을 검사한다.
  정상 instruction payload는 instruction 소유자의 검증으로 구분한다.
  전체 usage·개별 counter·최신 usage만 누락된 경우를 거부한다.
- Graph는 사용량 저장 callback을 재개 가능한 container 여부와 분리한다.
  Agent가 사용자 정의 Engine을 실행해도 논리 사용량을 저장한다. 이 변경이 사용자 정의
  Engine에 checkpoint 재개 기능이나 외부 효과 재실행 권한을 부여하지는 않는다.
- `ResumeState`는 계획 계약이, `CheckpointStatus`는 checkpoint 수명 계약이 선언한다.
  기존의 설정 binding 변경 거부·현재 호스트 권한 재검사·승인 binding·효과 원장을 유지한다.

추가 Agent 실패 주입의 수정 전 결과는 **2개 실행 / failure 2 / error 0 / Skip 0, 1.374초**다.
전체 Agent usage 제거는 실제 `first`·`second` 효과를 만들었고, counter 제거는 새 Run 생성
전에 거부하지 못했다. `/workspace/llm-clean-p1/agent-usage-before.log`를 보존했다.

공통 사용량 보강 후 초기 집중 시험은 **74 pass, 15.755초**였다. Agent 보강의 첫 집중
시험은 **111개 중 failure 1 / error 1 / Skip 0, 37.466초**였다. 사용량 callback을
container에만 제공하던 경로 때문에 사용자 정의 Engine이 실행되지 않았다.
callback 제공과 사용량 저장을 Engine 재개 지원 여부에서 분리해 수정했다.
이 실패는 `resume-focused.log`에 보존하며 정상 결과로 합산하지 않는다.

수정 후 집중 시험은 **147 pass / failure 0 / error 0 / Skip 0, 34.367초**다.
원래 R3 assertion을 유지하고 새 테스트를 추가했다. 정상 기록에서 예산을 소진해도
저장 결과로 최종 답변을 만들 수 있고, 새로운 Tool 호출은 `tool_budget_exceeded`로
차단되어 실제 효과가 한 번만 남는다. 손상된 기록은 새 Run·추가 모델/Tool 호출 전에
거부하며 원본 파일 바이트를 보존한다.

```bash
python -m unittest -v tests.llm.test_current_contracts tests.llm.test_nested_graph \
  tests.llm.test_agent_delegation tests.llm.test_agent_workflow \
  tests.llm.test_engine_steering tests.llm.test_loop_steering tests.llm.test_configuration_objects
```

소유 선언으로 해결한 S3 후보 14개를 추가로 검토·제거했다. 현재 잔여 후보는 **725개**이며
새 위반·stale 항목은 0이다. 예외나 새 기준선 항목을 추가하지 않았다.
`resolved-resume-findings.json`에 개별 ID를 보존한다. R3의 한도 전달을 의도적으로 제거하는
mutation은 실제 두 번째 효과 assertion에서 실패해 정상적으로 검출됐다.

보강 후 첫 전체 회귀는 **2,223 발견·실행 / 2,222 pass / failure 0 / error 1 / Skip 0,
807.847초, exit 1**이었다. `test_instruction_reservations`의 직접 `steering_boundary` 호출이
새 필수 `tool_usage` 인자를 전달하지 않았다. 호출에 `tool_usage=None`을 명시하고 중복 scope
거부·원본 보존 assertion을 유지했다. open 경계는 사용량을 저장하지 않으며 select 경계의
실제 사용량 필수 검사는 완화하지 않았다.

추가 통합 시험은 한도 1의 승인 예약에서 정상 승인 후 효과가 한 번만 실행됨,
저장된 승인에도 현재 호스트 deny가 우선함, 승인 대기 중 사용량 손상이 `corrupt`로
표시되고 새 Run·모델·Tool 호출 전에 거부됨을 구별한다. 이 시험과 복원 경계 및 수정한
scope 시험은 **5 pass / failure 0 / error 0 / Skip 0, 1.422초**였다.

```bash
python -m unittest -v tests.llm.test_current_contracts.ResumeUsageContracts \
  tests.llm.test_instruction_reservations.ReservationTests.test_duplicate_execution_scope_is_rejected_before_persistence
python -m tests.llm.run_regression --suite all --output /workspace/llm-clean-p1/resume-full/regression.json
python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-p1/resume-full/llm-final.json
```

최종 검증 결과:

| 범위 | 실행 자료 | 실제 결과 |
|---|---|---|
| LLM 전체 | 테스트 호출 수정·통합 시험 추가 후 `llm-final.json` | **1,690 발견·실행·통과**, failure/error/Skip 0, 338.645초, exit 0 |
| Hub 전체 | 첫 전체 실행 `regression.json`의 `tests.hub.*` | **534 발견·실행·통과**, failure/error/Skip 0 |
| 합계 | `verified-partitions.json` | **2,224 통과**, 두 집합 중복·현재 발견 시험 누락 없음 |

첫 전체 실행 뒤 수정한 Python 파일은 LLM 테스트 두 개뿐이다. 제품 코드와 Hub 테스트가
그 실행과 같음을 hash로 확인했고, LLM 재실행 중 전체 Python source hash도 유지됐다.
Hub 통과를 재사용한 **분할 검증 집계**이며, 첫 전체 명령의 exit 1을 exit 0으로 바꾸거나
2,224개를 하나의 명령에서 통과했다고 보고하지 않는다.

실제 ish 호스트·Bubblewrap·MCP·X11·OCR 시험을 포함한다. 실제 인증 모델 호출과 커널
short-write 장애 주입은 여전히 미검증이다. 과거 사용량 필드가 없는 저장 기록은 자동 변환하거나
초기값으로 채우지 않고 거부한다. 예전 실패 자료도 `/workspace/llm-clean-p1/`에 보존한다.

## P1-a: 타입·현재 저장 형식·사용량 복원

| 소유자/파일 | 변경 근거와 계약 |
|---|---|
| `core/configuration.py`, `core/models.py:ProjectConfig` | 선택 계층의 None과 잘못된 falsey 객체를 구분한다. JSON/API 경계는 from_dict로 먼저 검사한다. Python 생성자의 pair iterator는 표준 dict 재구성 계약으로 명시하며 외부 JSON 입력에는 허용하지 않는다. leaf의 0/False/None/빈 목록은 보존한다. |
| EngineRegistry, Projects/ProjectHandle, ProjectManager, Hub Project 설정 소비자 | JSON 객체 검증을 먼저 수행한다. 저수준 생성의 falsey fallback을 제거하고 잘못된 입력에서 Project/Session 파일이 생성·변경되지 않는지 검증한다. |
| `services/runtime/tools.py:ToolExecutionScope` | 정책/retry 객체를 명시 검증한다. 사용량 필수 필드·정수 타입·예약 목록을 모두 검증한 뒤 원자적으로 복원한다. 현재 상한·승인 정책을 사용량에서 생성하거나 덮어쓰지 않는다. |
| `engines/agents.py:AgentDelegation` 위임 생산자 | 새 scope 초기 사용량은 ToolExecutionScope가 선언한다. 빠졌던 invocations를 포함해 현재 Writer를 통일한다. 저장 기록 누락을 기본값으로 채우지 않는다. |
| CheckpointRepository, OperationRepository | SCHEMA_VERSION을 각 저장소가 소유하고 Writer/Reader가 공유한다. bool/float/누락/다른 버전을 읽기에서 거부한다. |
| TestWorkspaces, VisionComponent | 각각 STORAGE_VERSION/FORMAT_VERSION을 소유한다. Vision의 표시용 asset_format도 같은 선언을 읽는다. |
| TransactionManager | journal의 FORMAT_VERSION을 소유하고 Transaction Writer가 참조한다. 기존 정확한 정수 검증을 유지한다. |
| Hub PreferencesStore, ViewStateStore | 각 VERSION을 소유한다. 현재 Writer와 Reader를 일치시키고 잘못된 버전을 거부한다. 설정값의 소유권을 Backend로 옮기지 않는다. |

Backup은 기존 정확한 타입 검사가 통과한다. upgrade 제거 및 나머지 선언 정리는 P1-b/c에
남겨 둔다. Hub의 오래된 필드 변환도 이 P1-a에서 완료한 것으로 보고하지 않는다.

사용량의 0·빈 예약은 **새 논리 scope의 초기 상태**다. 전달받은 usage 객체가 불완전하면
명시 거부한다. 빈 복원 목록은 기존 상태를 초기화하지 않으며, 전달받은 기록은 모두 검사한다.
초기 변경은 호출자의 전체 usage 누락 문제를 해결하지 못했다. 후속 보강은 위 절을 따른다.
승인 전 예약·완료 호출·invocation ID를 복원해도 현재 max_calls는 별도로 유지한다.

## 정적 검사

`tests/llm/check_architecture.py`의 S4는 제한된 AST 형태만 자동 인정한다.
현재 클래스에 정확한 정수로 선언한 필드 대응 상수와 `type(field) is not int`를
OR로 검사하고 즉시 raise하는 형식이다. 다른 필드/소유자, literal 재선언, AND로 변경,
타입 검사 삭제, bool 선언, raise 삭제 mutation은 계속 실패한다.
임의 helper·import·동적 재대입까지 증명하는 규칙은 아니다.

검토한 기존 후보 **28개만** 기준선에서 제거했다. 새 예외나 새 부채를 등록하지 않았다.

| 변경 경계 | 제거 후보 수 |
|---|---:|
| configuration presence | 3 |
| Tool scope policy/retry 객체 | 2 |
| Checkpoint/Operation 버전 | 6 |
| TestWorkspaces/Vision 버전 | 6 |
| Transaction 버전 | 4 |
| Hub Preferences/ViewState 버전 | 7 |

초기 변경 뒤 남은 후보는 **739개**였다. 후속 보강 뒤 725개이며,
`--strict` 또는 P1 전체 종료 규칙 통과를 뜻하지 않는다.
구체적인 제거 ID는 Git diff와 작업 증거 `resolved-type-findings.json`에 보존한다.

## 시험과 실패 기록

- P0 소스를 유지한 상태에서 새 계약 시험 15개를 먼저 실행했다:
  5개 pass / 10개 실패 시험, subtest assertion failure 72건 / error 5건 / Skip 0.
  `types-before-final.json`은 시험 ID와 subtest 집계를 구별한다.
- 첫 수정 후 15개 계약 시험은 통과했다. 함께 실행한 AST 시험 1개는 테스트 파일의
  `ast` import 누락으로 error였다. import를 추가하고 assertion은 유지했다.
- 타입/정적 guard/Agent 위임/중첩 Graph/명시 설정 집중 시험:
  78 pass / failure 0 / error 0 / Skip 0, 17.070초.
- 추가 재개 통합 시험의 첫 실행은 RunHandle 대신 Run 데이터의 경로를 읽어야 하는
  fixture 오류로 25개 중 1 error였다. `failed.data.paths`로 수정했다.
- 수정 후 새 계약·정적 검사 시험: 25 pass / failure 0 / error 0 / Skip 0, 2.002초.
  실제 첫 효과 이후 누락 counter·재시작·재개 거부 시험도 포함한다.
- 전체 실행 준비의 첫 환경 검사 명령은 필수 `--output` 인자 누락으로 exit 2였다.
  시험 실행 전 CLI 오류이며 `environment-arguments-before.log`로 보존했다.
  인자를 보완한 뒤 LLM·Hub 환경 검사, 문서, 정적 감사, R3 mutation은 exit 0이었다.
- 첫 전체 회귀: 2,214 발견·실행, 2,212 pass / failure 0 / error 2 / Skip 0,
  797.667초, exit 1. `test_backend.BackendTests.test_chain_reuses_runtime_and_exposes_run_step_history`,
  `test_run_api.OpenComponentTests.test_open_config_roundtrip_clone_session_overrides_and_field_names`가
  표준 `dataclasses.asdict()`의 dict 하위 타입 iterator 재구성에서 실패했다.
  이 실행의 Python source hash는 유지됐고 실패 자료는 `types-full/`에 보존했다.
- 원인은 ProjectConfig 생성자에 JSON 객체 전용 검증을 직접 적용한 변경이었다.
  Python 생성자 계약과 JSON 입력 경계를 구별해 위와 같이 수정했다. 기존 두 assertion은 유지한다.
  원인 확인용 임시 프로세스 patch에서의 2 pass는 제품 수정 검증으로 합산하지 않는다.
- 추가 입력 경계 시험 초안 4개는 수정 전 2 pass / 실패·오류 시험 2개,
  subtest failure 6건 / error 1건이었다. 저수준 Project 생성이 False/0/빈 문자열/목록/tuple에서
  실제 Project 5개를 만든 문제도 확인해 수정 대상에 포함했다.
- Python mapping 재구성과 JSON 경계를 분리한 수정 후 집중 시험은
  **71 pass / failure 0 / error 0 / Skip 0, 4.841초**였다.
  위 전체 회귀에서 오류가 난 두 기존 시험도 포함하며 assertion을 유지했다.
  명령은 아래와 같고 로그는 `mapping-focused.log`다.

  ```bash
  python -m unittest -v tests.llm.test_backend tests.llm.test_run_api \
    tests.llm.test_configuration_objects tests.llm.test_current_contracts \
    tests.llm.test_configuration_validation tests.llm.test_architecture_checks
  ```

- 이후 전체 usage 누락 시험에서 위의 안전성 실패를 발견했다. 71개 통과 집계에는
  이 새 시험이 포함되지 않는다. 수정 후 전체 회귀는 다시 실행하지 않았다.
  첫 전체 회귀의 2 error를 성공으로 바꾸어 보고하지 않으며 P1은 통과 상태가 아니다.

새 저장 시험은 실제 Writer의 파일을 bool/float/문자열/Null/다른 버전/누락으로 변조하고,
거부 뒤 파일 바이트 보존과 현재 형식 재조회 성공을 확인한다. 기존 assertion을 삭제하거나
Skip을 늘리지 않는다. 새 재개 시험은 실제 첫 파일 효과 이후 사용량 counter를 제거하고,
백엔드를 다시 연 뒤 모델/Tool 호출·새 Run 생성 전에 재개가 거부되는지 검사한다.

```bash
python -m unittest -v tests.llm.test_current_contracts tests.llm.test_architecture_checks
python -m tests.llm.check_architecture
python -m tests.llm.check_budget_mutation
python -m tests.llm.run_regression --suite all --output <artifact>/regression.json
```

Python은 3.12.14이며 P0의 hash lock과 동일 host SHA를 사용한다.
증거 경로는 `/workspace/llm-clean-p1/`다. 실제 인증 모델·커널 short-write는 미검증으로 유지한다.
안전성 차단 해결·검증 뒤 P1-b의 변경 대상은 Hub preference/theme Reader, backup upgrade, 직접 callback과
실제 구독 소비자, Loop의 누락 requires_retry 추론이다. 관찰 callback을 durable handler로
바꾸거나 uncertain Tool의 재실행 권한을 넓히지 않아야 한다.

## P1-b: 현재 API와 저장 Reader 하나로 통일

P1-a 커밋 `e37a3e0be09492c1c1d0f9d2ebde48f7a4e56895`를 원격
`refactor/llm-clean`에 Push한 뒤 진행했다. P1-b는 아래 실패와 수정 후 전체 검증 기록으로 완료했다.
P1-c와 A–F의 완료를 뜻하지 않는다.

| 변경 경계 | 변경과 유지한 계약 |
|---|---|
| `hub/config/preferences.py`, `theme.py` | 옛 필드 삭제·이름 변환 Reader 제거. section 소유 dataclass 필드 집합과 정확히 일치하는 현재 저장값만 수락. 알 수 없는 root section도 거부. 실패한 읽기/저장은 원본 bytes 보존. 생성·편집 입력에는 Hub owner의 초기값을 적용해 완전한 section 저장. False·빈 dict·빈 email 등 해당 필드에서 유효한 값 유지. |
| `services/infrastructure/backups.py`, `lifecycle/projects.py`, `services/api.py` | upgrade·upgrade_backup·aupgrade_backup 제거. manifest 버전은 DirectoryBackups.FORMAT_VERSION 단일 선언. 현재 backup/restore/clone, checksum·도메인/Component 검증·staging·원본 보존 유지. |
| `llm.py`, `services/runtime/runs.py` | 직접 on_event/on_run_event 인자·속성·RunEventPublisher 제거. CLI·테스트의 실제 관찰자는 EventSubscriptions로 이관. 저장 후 알림·snapshot·비동기 callback·실패 격리·구독 해제 유지. 실행에 영향을 주는 EventHandlers는 그대로 유지. |
| `engines/loop/engine.py` | started record의 container/requires_retry를 정확한 bool로 요구하고 서로의 일관성 검증. 누락 시 key가 tool:로 시작하는지로 추론하던 경로 제거. 명시 재시도 선택·현재 binding·선택 Tool 검증 유지. 잘못된 falsey decisions를 빈 객체로 바꾸지 않음. |
| `tests/llm/check_architecture.py`, workflow | 폐기 publisher 이름과 requires_retry fallback 재도입 검출 추가. CI에서 `--require S1` 강제. 해결한 S1 36건·S3 4건·S4 2건만 기존 부채에서 제거. new/stale 0, 남은 후보 683건; strict 전체 통과가 아님. |
| 현재 API 문서·예제 | 구독 예제와 현재 backup/restore·Hub Reader 계약 갱신. architecture-history/backend-api-history/handoff의 과거 기록은 현재 계약과 구분해 보존. |

호환 동작 자체를 요구하던 시험은 현재 형식 거부·원본 보존으로 바꿨다. 정상 UI·저장·승인·효과
assertion을 없애거나 Skip을 확대하지 않았다. 새로운 검증은
`tests.llm.test_current_api`와 `tests.llm.test_long_running`에 있다. 실제 효과 시험은 파일을
쓴 직후 handler를 실패시켜 started 기록을 남긴다. container 또는 requires_retry를 지운 뒤
재개를 요청해 **새 Run 0, 추가 모델 호출 0, 추가 파일 쓰기 0, 손상 원본 변경 0**을 확인한다.

### 집중 검증과 실패 기록

공통 실행기: `/workspace/.setup/ish-llm/verified-next/bin/python` (Python 3.12.14).
환경: `TERM=xterm-256color`, `LITELLM_LOCAL_MODEL_COST_MAP=True`, `LITELLM_MODE=PRODUCTION`.

| 명령/증거 | 실제 결과 |
|---|---|
| `python -m unittest tests.llm.test_current_api` — 구현 전 `compat/before.log` | 4 tests, subtest failures 51, error 1, Skip 0, exit 1. 옛 API/불완전한 Reader 수락을 검출. 별도 error 1은 새 시험이 유효하지 않은 빈 display_name을 사용한 fixture 오류로, 유효한 빈 email 시험으로 수정했다. 제품 성공으로 계산하지 않음. |
| `python -m unittest tests.llm.test_current_api tests.llm.test_operational_storage tests.llm.test_service_boundaries tests.llm.test_run_api tests.llm.test_run_transitions tests.llm.test_loop_steering tests.llm.test_graph_decisions tests.llm.test_transactions tests.llm.test_interactions tests.hub.test_global_preferences tests.hub.test_provider_stabilization tests.hub.test_auxiliary tests.hub.test_render_intervals` | 177 pass, failure/error/Skip 0, 23.608초, exit 0. |
| `python -m unittest tests.llm.test_current_api tests.llm.test_long_running tests.llm.test_loop tests.llm.test_graph_checkpoints tests.llm.test_nested_graph tests.llm.test_current_contracts tests.llm.test_engine_steering tests.llm.test_interaction_decisions` | 173 pass, failure/error/Skip 0, 32.987초, exit 0. |
| `python -m unittest tests.llm.test_architecture_checks tests.llm.test_current_api` | 14 pass, failure/error/Skip 0, 1.569초, exit 0. |
| `python -m tests.llm.check_budget_mutation` | max_tool_calls 전달 제거 mutation에서 실제 두 번째 효과 assertion 실패를 검출. mutation 검사기 exit 0. 의도한 변이 시험의 실패를 일반 회귀 실패/성공 수에 섞지 않음. |
| `python -m tests.llm.check_architecture --require S1` | 필수 S1 0건, new/stale 0, outstanding 683. |
| `python -m tests.llm.check_docs` | 128문서, Python fence 178, 상대 링크 892, anchor 30, error 0. |

실행 로그/JSON은 `/workspace/llm-clean-p1/compat/`에 보존한다. 저장소에 편입된 동일 검사기로
재현하며, Cloud 전용 임시 도구를 완료 조건으로 삼지 않는다. 외부 host SHA는
`d833790959a76ac457fdeea3f21a6bea1eecfe12`를 유지하고 부모 Git에 포함하지 않는다.

### P1-a 원격 CI 후속 확인

[39395147762](https://github.com/E1der0wL/ish-llm/actions/runs/39395147762)는
`e37a3e0be09492c1c1d0f9d2ebde48f7a4e56895`의 두 job 모두 success다.
Checks annotation으로 Python 3.12.14, LLM 1,690 pass / Hub 534 pass,
failure/error/Skip/missing/duplicate 0을 확인했다. 각각 748.242초 / 846.612초다.
AppArmor 전역 제한 1, bwrap smoke 성공, 일반 unshare 거부, Tool 테스트 비 root 실행도
annotation으로 확인했다. 원시 job log 다운로드는 저장소 blob endpoint의 Forbidden으로
실패했으며, 읽지 못한 원시 로그를 검증 증거로 주장하지 않는다.

### P1-b 전체 회귀 중 소비자 누락 발견

첫 전체 회귀에서 `examples/llm/`의 직접 콜백 인자 7곳이 남아 있는 것을 발견했다.
`code_workflow`, `graph_rag`, `ish_loop`, `recovery_probe` 관련 시험이 생성자 TypeError로
실패했다. 좁힌 재현 명령은 다음과 같으며 **2 tests / 1 failure / 1 error / Skip 0,
0.004초, exit 1**이다.

```bash
python -m unittest \
  tests.llm.test_graph_engine.CodingWorkflowTests.test_iteration_exhaustion_does_not_report_success \
  tests.llm.test_ishrc_loop.IshrcLoopTests.test_default_project_reuse_and_session_history_with_real_loop
```

LLM/Hub 패키지 밖의 실행 소비자를 첫 이관에서 놓친 문제다. `custom_engine`,
`developer_assistant`, `bleach_research`를 포함한 7개 예제를 구독 API로 직접 옮겼다.
S1 수집 범위에도 examples의 Python API 소비자를 추가하고 재도입 fixture로 검증한다.
예제의 명시 실행 숫자를 Backend 기본값으로 간주하지 않으므로 examples에서는 S1을 검사한다.
LLM/Hub 제품 코드에 적용하는 S2–S6 범위를 줄이거나 부채를 추가하지 않았다.

첫 전체 실행의 LLM 구간이 끝난 뒤 이 소비자 수정과 검사기/검사기 시험 변경만 적용했다.
Hub 제품·시험 및 공통 llm 제품 코드는 그대로 유지했다. 첫 전체 명령의 실패를 성공으로
바꾸지 않고 LLM 전체를 다시 실행해 1,697 pass, failure/error/Skip/missing/duplicate 0,
342.606초, exit 0을 확인했다. 예제/복구/CLI 집중 45개도 28.784초에 통과했고,
`python -m examples.llm.custom_engine`은 `Echo: hello`를 출력하고 exit 0이었다.

첫 전체 실행은 2,230 discovered/started, 2,216 pass, 실패한 parent test ID 14개,
failure 기록 11개·error 4개·Skip 0, 801.871초, exit 1이었다. 동일 parent의 실패 subtest가
포함되므로 failure/error 기록 개수의 합과 실패 test ID 수는 다르다.
Hub에서는 `test_page_layout`의 옛 팔레트 자동 변환을 요구하는 시험 1개가 error였다.
옛 Reader를 복구하지 않고 옛 형식 거부·원본 보존 및 별도 현재 workspace에서 명시 팔레트
생성·색상/필드/저장 roundtrip으로 이관했다. 기존 현재 색상·필드 집합·roundtrip assertion은
유지했다. 해당 집중 시험은 exit 0이며 Hub 전체를 다시 실행한다. 이 후속 변경은
`tests/hub/test_page_layout.py` 한 파일뿐이고 LLM/Hub 제품 및 LLM 시험은 변경하지 않았다.
두 범위의 최종 발견 ID, pass 집합과 소스 hash 대조 결과를 아래 완료 기록에 남긴다.

### 다음 P1-c의 작업 경계와 남은 위험

P1-b의 S1 0건은 inventory에 정의한 호환 API 제거를 뜻한다. 모든 동적 경로나 앞으로 C에서
전환할 Hub의 환경 없는 Run fallback까지 제거됐다는 뜻은 아니다. 잔여 후보는 S2 104,
S3 535, S4 24, S5 2, S6 18이다. S6 Provider 해석 경계는 A에서 완료할 대상이다.

1. `tests/llm/check_architecture.py`의 S2는 현재 조건식의 논리 OR도 후보로 수집한다.
   타입 검증용 OR와 값을 고르는 fallback을 AST 문맥으로 구분하고, 실제 fallback을 놓치지
   않는 양성/음성 fixture를 먼저 보강한다. 파일 단위 면제나 후보 일괄 삭제는 하지 않는다.
2. `components/tools/{registry,decorator}.py`, 내장 Tool·MCP·Hub 등록 소비자와
   `services/runtime/tools.py`에서 실행 선언의 소유자를 통일한다. 현재 `Tool.contract=None`
   허용과 반복 생성 기본값을 명시 trusted 등록 계약으로 정리해야 한다. 단순히 None을
   기본 계약으로 바꾸는 호환 처리를 만들지 않는다. 승인 binding·격리 runner·실제 효과
   재실행 방지 시험이 필수다. 정상 Run에서 권한 우회를 새로 재현한 결과는 아니다.
3. `core/policies.py`, Engine/Component별 schema·validator·Reader/Writer와 runtime의
   상태·오류·버전을 계약군별로 정리한다. `runtime_tool_usage`, `agent_usage`, 현재 한도와
   승인·효과 원장을 혼합하거나 사용량 손상을 기본값으로 복구하지 않는다.
4. `hub/backend/project/defaults.py`, `hub/config/presets.py`는 생성 프리셋 소유자다.
   허용되는 초기값을 제거하지 않고 선언을 집중하며, 사용자 값 우선·저장·조회·편집·재열기
   무주입·Backend 안전성 검증을 실제 프로젝트로 확인한다.

P1-c 전체 및 A–F는 미완료다. 실제 인증 모델 호출, 커널 short-write 장애 주입,
전원 손실/손상 디스크는 계속 별도 미검증이며, 기존 crash/fsync 시험으로 대체해 통과 처리하지 않는다.


### P1-b 최종 전체 검증

최종 두 명령은 각각 전체 suite를 discovery하고 실행했다. **첫 `--suite all` 명령은
실패 그대로 보존**하며, 수정 후 성공한 LLM/Hub 두 실행의 합집합을 현재 discovery와 대조했다.

```bash
export TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION
export HUB_TEST_XVFB=/workspace/.setup/x11/root/usr/bin/Xvfb
export HUB_TEST_XCLIP=/workspace/.setup/x11/root/usr/bin/xclip
/workspace/.setup/ish-llm/verified-next/bin/python -m tests.llm.run_regression \
  --suite llm --output /workspace/llm-clean-p1/compat/llm-final.json
/workspace/.setup/ish-llm/verified-next/bin/python -m tests.llm.run_regression \
  --suite hub --output /workspace/llm-clean-p1/compat/hub-final.json
```

| 범위 | 발견/실행/통과 | failure/error/Skip | 누락/중복 | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| LLM | 1,697 / 1,697 / 1,697 | 0 / 0 / 0 | 0 / 0 | 342.606초 | 0 |
| Hub | 534 / 534 / 534 | 0 / 0 / 0 | 0 / 0 | 470.362초 | 0 |
| 최종 합집합 | **2,231 / 2,231 / 2,231** | **0 / 0 / 0** | **0 / 0** | 별도 suite 실행 | 두 명령 모두 0 |

Hub의 Xvfb/xclip 9개는 모두 실행·통과했다. Python 3.12.14와 실제 ish SHA
`d833790959a76ac457fdeea3f21a6bea1eecfe12`를 사용했다. 격리 제품 경로를 바꾸지 않고
Cloud 명령 sandbox 밖에서 실제 Bubblewrap 시험을 실행했다.

`verified-partitions.json`은 최종 discovery가 두 pass 집합과 정확히 일치하고 중복이 없음을
기록한다. LLM 최종 실행 이후에는 `tests/hub/test_page_layout.py`만 변경됐으며, 공통 제품과
LLM 시험·예제 소스는 그대로다. Hub 최종 실행 전후의 전체 Python 소스 hash도 일치한다.
초기 전체 실행 JSON·로그 및 모든 실패 재현은 별도로 보존한다.

P1-b 완료 범위는 현재 Reader/API·구독 소비자 전환·Loop 재시도 증거 검증과 S1 필수 gate다.
P1-c의 선언·명시 설정 전수 정리와 A–F 기능 구현을 완료한 것으로 보고하지 않는다.

## P1-c 첫 계약군: 생성 프리셋과 현재 저장 형식

시작 커밋은 `ee0f07ccea1977755482c75ce472e465d34d3169`다.
[해당 원격 CI](https://github.com/E1der0wL/ish-llm/actions/runs/39399658101)의 실제 Checks
annotation에서 LLM 1,697개·Hub 534개 통과, failure/error/Skip/누락/중복 모두 0을 확인했다.
각 job 시간은 710.289초·750.702초이며 Python 3.12.14다.

### 발견·수정

| 파일·심볼 | 문제/판정 근거 | 변경과 검증 경계 |
|---|---|---|
| `tests/llm/check_architecture.py:predicate` | S2가 `type(max_calls) is not int or max_calls < 1` 같은 타입/범위 검증도 fallback으로 수집 | Compare/Not/bool/그 조합만 문법적으로 구분. 함수 반환값·Name·Attribute·Subscript 타입을 추정하지 않음. 하위 AST는 계속 순회하여 조건식 안의 `policy or {}`도 검출 |
| 같은 파일 `key_set_comparison` | 필수 객체 키 집합 검사와 실제 version/status 값 비교가 섞임 | 정확한 `set(record)`/`record.keys()` 대 문자열 Set의 Eq/NotEq만 구분. 실제 버전 비교·중첩 fallback mutation은 계속 검출 |
| `hub/backend/project/settings.py:create_project,save_all` | 명시한 `components=[]`를 전체 등록 목록으로 덮어씀 | `defaults.creation_values()`가 **누락된 생성 필드만** 채움. 새 Project 저장/조회/편집/일괄 생성에서 빈 목록 보존. 기존 전체 목록 시험은 필드를 생략하는 경우로 구분 |
| `hub/backend/project/defaults.py`, `bootstrap.py` | 생성 시 conversation 저장 선택이 여러 경로에서 반복 | 생성 owner의 `CONVERSATION_STORAGE`를 조립 경로가 참조. Backend Reader/실행에서는 이 프리셋을 참조하지 않음 |
| `hub/backend/tools/builtin.py:ensure_selected`, `runtime.py:activate_project` | 첫 수정 후 재열기 시험에서 기존 Project에 builtin_tools와 enabled 초기값이 다시 저장됨 | 자동 쓰기 메서드 삭제. activate는 저장된 Component 선택만 준비. 폐기 메서드 재도입은 S1이 검출. 미선택/선택됐지만 enabled 누락/빈 목록 모두 설정 revision 유지·모델 Tool 목록 비어 있음·파일 효과 0 검증 |
| `hub/config/presets.py:PresetStore`, `project/presets.py:export` | Reader/내보내기에 version literal 중복; 미선택 프리셋 본문 미검증; 빠진 전역 설정 필드를 dataclass 기본값으로 복구 | `PresetStore.VERSION` 공동 참조. 정확한 int/version·root 필드·선택 대상·모든 저장 프리셋 검증. ProjectConfig는 Backend owner 검증 사용 |
| `hub/config/preferences.py:validate_section` | Preference와 preset이 같은 전역 설정을 따로 해석 | SECTIONS와 완전한 현재 필드 검증을 Preference owner가 소유. preset은 그 검증 사용; 실패 시 원본 변경 없음 |
| `docs/hub/questions-tools.md`, `presets-and-resume.md` | 전자는 기존 Project를 열 때 Component 추가를 설명; 후자는 재주입 금지. 빈 전역 설정 객체 예제도 current-only Reader와 충돌 | 사용자 상위 원칙에 따라 생성 초기값/명시 설정 변경/읽기 전용 재열기 구별. 완전한 현재 형식 설명으로 수정 |

정적 후보는 **63건**만 개별 대조 후 제거했다. 타입/조건 논리 S2 50건, 키 집합의 형태 검사
5건(S3 4·S4 1), 실제 preset 버전 선언/검사 수정 8건(S3 4·S4 4)이다.
새 허용 항목이나 파일 면제를 추가하지 않았다. 남은 후보는 **620건**이며
S2 54, S3 527, S4 19, S5 2, S6 18이다. 이 숫자는 미분류/미해결 후보이며
정적 strict 전체 통과나 P1-c 전체 완료를 뜻하지 않는다.

### 집중 검증과 실패 보존

명령의 Python은 `/workspace/.setup/ish-llm/verified-next/bin/python`이며
`TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION`을 사용했다.
증거는 `/workspace/llm-clean-p1/declarations/`에 보존한다.

| 명령/파일 | 실제 결과 |
|---|---|
| `python -m unittest tests.hub.test_creation_contracts -v`, `creation-before.log` | 수정 전 5개, failure 9개(서브테스트 포함), error/Skip 0, 3.861초, exit 1. 명시 빈 목록 덮어쓰기·불완전/미선택 preset 수락 재현 |
| 첫 수정 후 생성/프리셋/Settings/Bootstrap 집중, `creation-after.log` | 36개, failure 1·error/Skip 0, 45.488초, exit 1. 기존 Project 재열기의 builtin_tools 자동 저장 발견 |
| `python -m unittest tests.hub.test_creation_contracts tests.hub.test_defaults tests.hub.test_presets_widgets tests.hub.test_question_tools tests.hub.test_settings tests.hub.test_bootstrap tests.llm.test_current_contracts -v` | 67개 통과, failure/error/Skip 0, 67.612초, exit 0. 실제 파일 효과·기존 승인 후 Resume·누적 사용량 손상 거부 포함 |
| `python -m unittest tests.llm.test_architecture_checks -v` | 13개 통과, failure/error/Skip 0, 1.664초, exit 0. 새 규칙의 양성·음성 및 중첩 fallback mutation 포함 |
| `python -m tests.llm.check_budget_mutation` | 의도한 R3 시험 1개가 두 번째 실제 효과 assertion으로 실패, checker는 탐지 성공 exit 0. 일반 회귀 실패와 구별 |
| `python -m tests.llm.check_architecture --require S1` | S1 0, new/stale 0, 잔여 후보 620, exit 0 |
| `python -m tests.llm.check_docs` | 문서 128·Python fence 178·링크 895·anchor 30, 오류 0, exit 0 |
| `python -m tests.llm.check_environment --suite hub --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12` | Python 3.12.14·깨끗한 실제 host·Xvfb/xclip·bwrap smoke 확인, exit 0 |

전체 LLM·Hub 회귀는 이 계약군의 동일한 Python 소스를 고정하여 각각 실행했다.
`sources-before.json`과 종료 시 제품/시험/예제의 전체 Python 소스 hash가 일치했다.
실제 인증 모델 호출·커널 short-write·전원 손실/손상 디스크는 계속 미검증이다.

### 첫 계약군 전체 검증 완료

```bash
export TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION
export HUB_TEST_XVFB=/workspace/.setup/x11/root/usr/bin/Xvfb
export HUB_TEST_XCLIP=/workspace/.setup/x11/root/usr/bin/xclip
/workspace/.setup/ish-llm/verified-next/bin/python -m tests.llm.run_regression \
  --suite llm --output /workspace/llm-clean-p1/declarations/llm-regression.json
/workspace/.setup/ish-llm/verified-next/bin/python -m tests.llm.run_regression \
  --suite hub --output /workspace/llm-clean-p1/declarations/hub-regression.json
```

| 범위 | 발견/실행/통과 | failure/error/Skip/누락/중복 | 시간 | exit |
|---|---:|---:|---:|---:|
| LLM | 1,700 / 1,700 / 1,700 | 모두 0 | 343.657초 | 0 |
| Hub | 540 / 540 / 540 | 모두 0 | 487.990초 | 0 |
| 합집합 | **2,240 / 2,240 / 2,240** | **모두 0** | 별도 suite 실행 | 둘 다 0 |

`verified-partitions.json`에서 최종 discovery와 두 pass 집합의 정확한 일치·중복 없음을
확인했다. 실제 host 대상 3개 모듈 44개와 X11 9개도 이 집합에서 전부 실행·통과했다.
최종 문서 검사는 128개 문서·178개 Python fence·897개 링크·30개 anchor, 오류 0이다.
이는 P1-c의 **첫 계약군 완료**이며 Tool 실행 선언 및 나머지 owner 정리, A–F는 남아 있다.

## P1-c 두 번째 계약군: 필수 Tool 실행 선언

시작 커밋은 `ac0ef8d4f24c3bea3f9017db11c27a545192f912`다.
[첫 계약군 원격 CI](https://github.com/E1der0wL/ish-llm/actions/runs/39404149845)의 Checks
annotation에서 LLM 1,700개·Hub 540개 통과와 failure/error/Skip/누락/중복 0을 확인했다.
각 job은 678.321초·692.743초다. 전역 AppArmor 제한 1, 일반 unshare 거부,
패키지 bwrap smoke와 비 root Tool 시험도 통과했다.

### 선언과 소비 경계

- `llm/components/tools/registry.py`의 `ToolContract`는 revision/effect/isolation/
  approval_required/operation_key_required/retry_fields를 모두 필수로 받는다.
  `ToolEffect`, `ToolIsolation`이 허용 값을 소유하고 runner가 이를 참조한다.
  `from_dict()`는 완전한 현재 필드·정확한 값 타입을 검증하며 누락을 보충하지 않는다.
- `Tool`·`ToolCall`·`tool()`은 명시 계약을 요구한다. decorator의 반복 기본값과
  registry/runtime/worker의 계약 없음 분기를 제거했다. immutable Python 선언과
  저장용 JSON snapshot의 변환은 동일한 owner를 사용한다.
- Builtin/Memory/RAG/Agent/Goals/Refinement/Skills/Vision 및 Hub 등록자는 의미에 맞는
  계약을 각 구현체에서 선언한다. Memory의 기존 write-policy·provenance, Tool의 승인·
  operation key·격리는 유지한다. MCPConnector는 원격 Tool의 unknown effect와 승인 필수를
  adapter가 명시한다. 원격 annotation은 실행 권한의 근거가 아니다.
- `OperationRepository.load()`와 worker/MCP descriptor Reader는 계약의 누락 필드를
  거부한다. 이전 None/불완전 계약으로 저장된 자료를 자동 변환하지 않으며 원본은 보존한다.
- 테스트/예제/내장 Python Tool 소스도 정규 선언으로 이관했다. 승인 지문·실제 handler
  효과·재개 receipt assertion은 유지한다. 테스트 fixture의 `TEST_CONTRACT`는 테스트만의
  명시 선택이며 제품의 fallback으로 사용하지 않는다.
- 정적 검사 S2는 owner의 ToolContract 필드에 literal/symbol/field factory 기본값이
  추가되는 경우를 검출한다. 해결한 effect/isolation S3 후보 24개만 근거와 함께 제거했다.
  새 예외는 없고 잔여 후보는 596개(S2 54, S3 503, S4 19, S5 2, S6 18)다.
  P1-c 전체 완료나 strict 전체 통과가 아니다.

### 집중 시험과 실패 기록

증거 디렉터리는 `/workspace/llm-clean-p1/tool-declarations/`다. Python 3.12.14와
이전 계약군의 환경 변수를 사용했다.

| 실행 | 실제 결과와 조치 |
|---|---|
| `python -m unittest tests.llm.test_tool_declarations -v`, `before.log` | 수정 전 5개, failed subtest 8개·error 2·Skip 0, 0.002초, exit 1. 누락 선언 수락 및 새 정규 API 부재 재현 |
| `focused.log` | 관련 12개 모듈 209개, failure 4·error 1·Skip 0, 101.050초, exit 1. 세 constraint 시험과 Hub fixture의 unspecced AsyncMock이 가짜 execution_binding을 만들었고, crash subprocess fixture가 새 필수 계약을 빠뜨림 |
| fixture 수정 | AsyncMock의 실제 함수 spec을 명시하고 subprocess ToolCall에 완전한 계약 전달. 제품 binding JSON 검증·원래 crash exit 및 효과 assertion은 그대로 유지 |
| `corrected.log` | Tool 선언/constraint/격리/workspace/operation/current-reader/static/Hub questions 120개 통과, failure/error/Skip 0, 38.689초, exit 0 |
| `python -m tests.llm.check_budget_mutation` | R3 필드 전달 누락 시 두 번째 실제 효과 assertion으로 1개 시험 실패, 탐지 checker exit 0, 1.072초. 일반 회귀의 성공으로 세지 않음 |

전체 회귀의 결과와 추가 실패는 아래에 별도 기록한다. 이 지점의 집중 시험 성공만으로
전체 승인·Resume 회귀 통과를 선언하지 않았다.

첫 LLM 전체 회귀 `llm-regression.json`은 1,707개 발견/실행, 1,655개 통과,
실패한 상위 시험 52개(failure 1·error 54, 서브테스트 포함), Skip/누락/중복 0,
338.419초, exit 1이었다. 공용 `LongRunningTests.setup_app()`의 `tool_contract=None`
52건과 수동 승인 snapshot의 `contract=None` 3건이 원인이었다. 집중 시험에서 이 소비자를
놓쳤으며 전체 성공으로 처리하지 않는다. 테스트 데이터만 완전한 명시 계약으로 바꾸고
기존 승인 거부·지문 변경 거부·실제 효과·재시작 assertion을 유지한 재검증이 필요하다.

수정 후 `python -m unittest tests.llm.test_long_running tests.llm.test_contracts
 tests.llm.test_domain_hardening tests.llm.test_execution_boundaries tests.llm.test_interactions
 tests.llm.test_observability tests.llm.test_project_authority -v`는 **113개 통과**, failure/error/
Skip 0, 20.944초, exit 0이었다(`fixtures-corrected.log`).

Hub 전체 `hub-regression.json`은 **540개 발견/실행/통과**, failure/error/Skip/누락/중복 0,
491.142초, exit 0이다. X11 9개도 모두 통과했다. 두 전체 실행이 끝날 때까지 전체 Python
소스 hash 646개가 시작 snapshot과 같았음을 확인했다. 이후 수정은 위 두 LLM fixture
파일뿐이며 공통 제품·예제·Hub 시험은 그대로다. 최종 LLM 전체는 다시 실행한다.

### 두 번째 계약군 전체 검증 완료

```bash
export TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION
export HUB_TEST_XVFB=/workspace/.setup/x11/root/usr/bin/Xvfb
export HUB_TEST_XCLIP=/workspace/.setup/x11/root/usr/bin/xclip
/workspace/.setup/ish-llm/verified-next/bin/python -m tests.llm.run_regression \
  --suite llm --output /workspace/llm-clean-p1/tool-declarations/llm-final.json
/workspace/.setup/ish-llm/verified-next/bin/python -m tests.llm.run_regression \
  --suite hub --output /workspace/llm-clean-p1/tool-declarations/hub-regression.json
```

| 범위 | 발견/실행/통과 | failure/error/Skip/누락/중복 | 시간 | exit |
|---|---:|---:|---:|---:|
| LLM 최종 | 1,707 / 1,707 / 1,707 | 모두 0 | 339.063초 | 0 |
| Hub | 540 / 540 / 540 | 모두 0 | 491.142초 | 0 |
| 합집합 | **2,247 / 2,247 / 2,247** | **모두 0** | 별도 suite 실행 | 둘 다 0 |

`verified-partitions.json`에서 최종 discovery와 통과 집합의 정확한 일치·중복 없음을
검증했다. 최종 LLM 시작/종료 소스 hash 646개가 일치하고, Hub 실행 이후에는 LLM fixture
두 파일만 변경됐다. 실제 ish 대상 44개와 X11 9개가 모두 통과 집합에 포함된다.
원래 실패 JSON/로그와 수정 후 결과는 각각 보존했다. `check_architecture --require S1`은
new/stale/S1 0, 잔여 596개다. `git diff --check`도 통과했다.

**완료 범위는 필수 Tool 계약군**이다. 다음 P1-c 계약군에서 Provider/Memory 설정과
Host 자원 주입의 잘못된 falsey 객체 수락, 재시도 한도 계산의 중복 선언을 해결한다.
그 뒤에도 상태·이벤트·오류·저장 형식 등의 owner 정리가 남아 있으므로 P1-c 전체 및
A–F는 미완료다. 실제 인증 모델·커널 short-write·전원 손실/손상 디스크는 계속 미검증이다.

## P1-c 세 번째 계약군: Provider 설정과 공유 자원 입력

시작 커밋은 `df6b50813eba11776282a3feed8a1abb26d49a7a`이며, 필수 Tool 계약군을
`refactor/llm-clean`에 Push한 뒤 시작했다. 증거는 `/workspace/llm-clean-p1/provider-choices/`다.

### 발견과 변경

| 소유자·소비자 | 관찰한 문제 | 변경·보존 계약 |
|---|---|---|
| `providers/requests.py:resolve_provider_options` | `dict(options)`가 빈 문자열/배열/tuple 및 key-value 배열을 객체로 변환 | 복사 전 객체 타입 검사. 정상 0·False·nullable leaf 및 원본 보존 |
| `providers/calls.py:ProviderCalls` | False/0/빈 객체를 `ProviderLimits()`로 바꾸어 제한 없는 설정으로 수락 | `ProviderLimits` 또는 None만 수락. 유효한 falsey subclass의 identity와 max_waiting=0/wait_seconds=0 보존. 실제 acquire 경쟁에서 두 번째 호출 거부 |
| `runtime/policies.py:ProjectPolicyResolver` | 잘못된 falsey registry를 빈 mapping으로 바꾸고 falsey mapping의 등록 counter도 소실 | mapping/None 구분 후 복사. callable/name 검증 유지, 등록 counter 소실 방지 |
| Memory/Loop Provider 해석 | `provider or {}`로 잘못된 falsey 값과 nullable 해제를 혼합 | None만 명시 해제로 처리하고 다른 값은 Provider owner가 검증. 원본 설정에 빈 정책을 저장하지 않음 |
| `policies/retry.py:RetryPolicy`, streaming/invoke/SDK retry/Hub 관찰·복구 | 최초 호출 횟수 1을 여러 소비자가 다시 선언 | `INITIAL_ATTEMPTS`와 읽기 전용 `attempts` 계산을 공유. 명시 SDK retry와 outer retry 중첩 금지·수신 후 재시도 금지·취소 유지 |
| `CONFIGURATION.md`, `project-policies.md` | nullable 해제 설명을 Session에서 상위 한도를 없애도 된다고 읽을 여지 | 기존 narrowing 거부를 명시. 한도 없는 상위 정책의 해제와 구분. 이 계약군에서는 parent 제한 검증을 완화하지 않음 |

정적 후보 9건(S2 8·S3 1)을 실제 변경과 대조해 제거했다. 새 예외/면제는 없으며
남은 후보는 587개다. 문자열/숫자 전수 제거 또는 P1-c 전체 완료를 뜻하지 않는다.

### 집중 시험과 실패 보존

| 명령/증거 | 실제 결과 |
|---|---|
| `python -m unittest tests.llm.test_provider_choices -v`, `before.log` | 6개, failure 26·error 2·Skip 0, 0.014초, exit 1. 잘못된 입력 수락·falsey counter 소실을 재현. error 중 하나는 새 시험이 상위 max_attempts 제거를 허용한다고 잘못 가정한 경우이며, 기존 narrowing 거부와 합법적 null 해제를 각각 검사하도록 바로잡음 |
| 첫 집중 명령 `focused.log` | 115개, failure 0·error 1·Skip 0, 35.731초, exit 1. `tests.hub.test_retry_status`라는 존재하지 않는 모듈명을 지정한 loader 오류. 114개 성공을 전체 성공으로 보고하지 않음 |
| `python -m unittest tests.llm.test_provider_choices tests.llm.test_explicit_configuration tests.llm.test_provider_runtime tests.llm.test_provider_diagnostics tests.llm.test_retry tests.hub.test_retry_observations tests.hub.test_retry_ui tests.hub.test_provider_recovery -v`, `corrected.log` | 130개 통과, failure/error/Skip 0, 40.709초, exit 0 |
| `python -m unittest tests.llm.test_provider_choices tests.llm.test_architecture_checks -v`, `choice-audit.log` | 예외 타입 assertion을 구체화한 최종 새 시험 포함 21개 통과, failure/error/Skip 0, 1.679초, exit 0 |
| `python -m tests.llm.check_budget_mutation` | R3 필드 전달 누락 시 두 번째 실제 파일 효과 assertion으로 1개 실패, 1.024초. 탐지 checker exit 0 |
| `python -m tests.llm.check_architecture --require S1` | new/stale/S1 0, 잔여 587개, exit 0 |
| `python -m tests.llm.check_docs` | 128문서·178 Python fence·898링크·30 anchor, 오류 0 |

전체 회귀는 위 Python 소스 647개를 고정한 상태에서 실행한다. 실제 인증 모델·커널
short-write·전원 손실/손상 디스크는 계속 별도 미검증이며 fake/offline SDK 시험으로 대체하지 않는다.

### 세 번째 계약군 회귀 결과와 안전성 중단

동일한 Python 소스 647개로 아래 두 명령을 실행하고 시작/종료 hash를 대조했다.

```bash
export TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION
export HUB_TEST_XVFB=/workspace/.setup/x11/root/usr/bin/Xvfb
export HUB_TEST_XCLIP=/workspace/.setup/x11/root/usr/bin/xclip
/workspace/.setup/ish-llm/verified-next/bin/python -m tests.llm.run_regression \
  --suite llm --output /workspace/llm-clean-p1/provider-choices/llm-regression.json
/workspace/.setup/ish-llm/verified-next/bin/python -m tests.llm.run_regression \
  --suite hub --output /workspace/llm-clean-p1/provider-choices/hub-regression.json
```

| 범위 | 발견/실행/통과 | failure/error/Skip/누락/중복 | 시간 | exit |
|---|---:|---:|---:|---:|
| 기존 LLM 발견 집합 | 1,714 / 1,714 / 1,714 | 모두 0 | 345.518초 | 0 |
| Hub | 540 / 540 / 540 | 모두 0 | 488.279초 | 0 |
| 위 두 집합 | **2,254 / 2,254 / 2,254** | **모두 0** | 별도 suite 실행 | 둘 다 0 |
| 이후 추가한 실제 만료 손상 시험 | 1 / 1 / 0 | **failure 1**, 나머지 0 | 0.237초 | **1** |

위 기존 집합의 실제 host 44개·X11 9개는 모두 통과했다. 그러나 새로 발견한 안전성 문제를
별도 시험으로 추가했으므로 **현재 작업 트리 전체를 통과한 것으로 보고하지 않는다.**

#### 새 중단 원인: 만료 필드 누락을 만료 없음으로 복원

[실제 재현 시험](../../tests/llm/test_interaction_storage.py)은 임시 디렉터리의 파일 효과와
scripted 모델을 사용하며 실제 인증 모델을 호출하지 않는다.

1. 이미 만료된 승인 요청으로 Run을 pause하고 파일 효과가 없음을 확인한다.
2. 정상 만료 기록에 `decisions={key: True}`로 Resume를 요청하면 거부된다.
3. 실제 checkpoint record의 `interaction.expires_at` **한 필드만 제거**한다.
4. 같은 Resume 요청을 보내 실제 종료까지 기다린다.
5. 기대는 접수 전 거부·Run 1개·모델 1회·효과 0이다. 실제는 **접수 허용·Run 2개·모델 2회·파일 쓰기 1회**였다. 손상 원본은 변경되지 않았다.

```bash
TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION \
  /workspace/.setup/ish-llm/verified-next/bin/python \
  -m unittest tests.llm.test_interaction_storage -v
```

결과는 **1개 / failure 1 / error·Skip 0 / 0.237초 / exit 1**이다.
로그는 `provider-choices/interaction-storage-failure.log`이며, 독립 사전 재현의 JSON은
`/workspace/llm-clean-p1/interaction-expiry-effect-before.json`이다. 정상 승인 assertion을
삭제하거나 expectedFailure/Skip으로 바꾸지 않았다.

원인은 `llm/core/interactions.py:InteractionRequest.from_dict()`가 `cls(**data)`에
저장값을 그대로 넘기고, 빠진 `expires_at`을 생성자의 None 기본값으로 보충하는 것이다.
이 Reader와 expires_at 선언의 AST는 `origin/main` SHA
`94229cb865374d99f20ec48c5e48269488041a4e`와 동일하다. **실제 효과 재현은 현재 작업 트리에서만
실행**했으며 main 체크아웃에서도 실행했다고 주장하지 않는다. 이번 Provider 변경이 이
Reader를 수정한 것은 아니다.

사용자의 “핵심 안전성 회귀가 발생하면 중단하고 보고” 원칙에 따라 이후 제품 구현을 중단했다.
P1-c 세 번째 계약군과 실패 시험은 **미커밋 작업 트리에 보존**한다. 마지막 검증·Push 커밋은
`df6b50813eba11776282a3feed8a1abb26d49a7a`다. P1-c 전체 및 A–F는 완료되지 않았다.

#### 재개 시 필요한 수정과 검증

- InteractionRequest/Option/Response의 저장 Reader가 완전한 현재 필드와 타입을 확인하도록
  한다. 명시적 expires_at=None은 보존하고 필드 누락을 None으로 복구하지 않는다.
- Checkpoint·InteractionRepository·Resume admission이 같은 owner 검증을 사용하여
  손상을 새 Run·모델·Tool 실행 전에 거부하는지 확인한다. 승인·권한·사용량·효과 원장의
  독립 판단을 합치거나, 사용량으로 승인/재개 가능성을 추론하지 않는다.
- 필드 누락/잘못된 상태·actor·타입, 원본 보존, 정상 승인/거부/만료/갱신, Loop/Graph의
  재개와 영수증을 검증한다. 구 Reader·자동 변환·허용 확대를 추가하지 않는다.
- 새 실제 효과 시험, R3 한도 누락 mutation, 전체 LLM/Hub와 정적 gate를 통과한 뒤에만
  단계 완료·커밋·Push를 판단한다. 인증 모델·커널 short-write는 계속 미검증이다.

### 중단 시 최종 확인

현재 discovery 2,255개와 실행한 세 집합을 대조했다. **2,254 pass / 1 failure /
0 error / 0 Skip / 0 누락 / 0 중복**이며 전체 성공은 아니다.
`provider-choices/verified-partitions-with-failure.json`에 실제 test ID 합집합과 소스 보존
검증을 남겼다. 이전 647개 Python 파일은 전체 실행 당시 그대로이고, 실패 시험 한 파일만
추가됐다. 문서 검사는 128문서·178 Python fence·900링크·30 anchor, 오류 0이며 정적 검사는
S1/new/stale 0·미해결 후보 587개다. 원격 main은 기준 SHA 그대로다.

마지막 Push `df6b508`의 [원격 CI](https://github.com/E1der0wL/ish-llm/actions/runs/39407797300)는
별도로 성공했다. Checks annotation은 Python 3.12.14, LLM 1,707개(966.638초)·Hub
540개(840.782초), failure/error/Skip/누락/중복 0을 기록한다. AppArmor 전역 제한 1,
일반 unshare 거부, 패키지 bwrap smoke·비 root Tool 시험도 확인했다. **이 CI는 미커밋
Provider 변경과 새 실패 시험을 포함하지 않으며 현재 작업 트리의 성공 증거가 아니다.**

미커밋 변경과 새 시험의 별도 patch는
`/workspace/llm-clean-p1/p1c-provider-and-safety.patch`에 보존했다.

## 영속 데이터 감사와 추가 원칙

2026-10-20 원감사는 제품·테스트·기존 문서를 변경하지 않고 진행했다. 그 뒤 사용자가 추가한
소유 구현체·공통 인프라·Application의 책임과 조건부 격리/삭제 원칙은
[수명주기 계약](data-lifecycle.md)에 문서화했다. **이번 문서 보강은 제품 수정이나 실패 해결이 아니다.**
기존 미커밋 Provider 변경·승인 실패 시험과 원감사 산출물을 보존한다.

원감사 범위는 llm Python 175개·28,994행, AST 호출 14,977개와 영속 자료 38개 그룹이다.
문서 129개 목록에는 제품 문서 128개와 테스트 안내 1개가 포함된다. 수명주기/검증 호출 후보
2,736개와 경로 문자열 후보 322개는 확정 결함 수가 아니다. 외부 Tool·SDK의 임의 저장까지
실행 검증한 것으로 보고하지 않는다.

원감사 산출물은 `/workspace/llm-clean-p1/lifecycle-audit/REPORT.md`와 같은 디렉터리의
AST·경로·소유자·지원 행렬·소스 hash·로그에 있다. 아래는 **원감사 때 실행한 결과**이며
추가 원칙 문서화 후 재실행한 결과가 아니다. Python 3.12.14, 외부 ish 소스 SHA
`d833790959a76ac457fdeea3f21a6bea1eecfe12`를 사용했다.

| 명령 | 실제 결과 |
|---|---|
| `python /workspace/llm-clean-p1/lifecycle-audit/run_focused.py` | 267 통과, failure/error/Skip/누락/중복 0, 42.214초, exit 0 |
| `python /workspace/llm-clean-p1/lifecycle-audit/run_edges.py` | 36 통과, failure/error/Skip/누락/중복 0, 4.978초, exit 0 |
| `python -m unittest tests.llm.test_nested_graph.NestedGraphTests.test_resume_preserves_one_call_agent_budget_and_blocks_new_effect -v` | 1 통과, 0.936초, exit 0 |
| `python -m unittest tests.llm.test_interaction_storage -v` | 1 failure, error/Skip 0, 0.221초, exit 1 |
| `python -m tests.llm.check_budget_mutation` | 의도된 max_calls 누락 변이를 실제 두 번째 효과로 탐지, detector exit 0; 일반 회귀 수에 합산하지 않음 |
| `python /workspace/llm-clean-p1/lifecycle-audit/probes.py` | 새 계약 위반 7개 관측, exit 1 |
| `python /workspace/llm-clean-p1/lifecycle-audit/approval_probes.py` | 새 계약 위반 4개 관측, exit 1 |

일반 unittest는 **305개: 304 통과·1 실패·오류/Skip 0**이다. 11개 probe 관측을 통과
시험으로 계산하지 않는다. 첫 probes 실행의 감사 스크립트 오류 1건은 초기 로그에 보존하고
스크립트만 수정해 재실행했다. 전체 LLM/Hub·호스트 API·X11은 원감사에서 재실행하지 않았다.
실제 인증 모델, 커널 short-write·전원 손실·네트워크 파일시스템은 계속 미검증이다.

승인 상태 소실, RAG 원본 삭제, Session 삭제의 quota 초기화는 A–F 진행을 차단한다.
수명주기 계약의 H1–H5를 각 owner에서 검증하고 P1을 재검증한다. 자동 삭제 정책·중앙 데이터
관리자를 추가하지 않으며 격리·삭제로 승인·예산·효과 증거를 초기화하지 않는다.

추가 원칙 문서화 단계에서는 `python -m tests.llm.check_docs --output
/workspace/llm-clean-p1/lifecycle-principles/docs-check.json`으로 129문서·178 Python fence·
916 상대 링크·34 anchor, 오류 0을 확인했다. `git diff --check`도 통과했다.
시작 파일 hash 804개와 대조해 변경이 문서 9개와 새 문서 1개에 한정됨을 확인했다.
제품·테스트·기존 감사 자료는 보존했고 기능 회귀를 이번에 재실행한 것으로 보고하지 않는다.
이 문서 변경은 미커밋이며 마지막 검증·Push 커밋 `df6b508`을 바꾸지 않았다.
