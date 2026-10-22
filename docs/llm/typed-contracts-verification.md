# 고정 구조 데이터 Owner 보강 검증

기준: `38e18566c8ea60143601bff161f27c37775c4d6b`, 작업 브랜치 `refactor/llm-clean`.
[현행 계약](contract-ownership.md)의 고정 구조 데이터 절과
[TC1–TC3 범위·후속 대상](ownership-refactor.md)을 따른다. 과거 검증 결과를 변경하지 않는다.

## 변경과 영향

- TC1: `ModelUsageManifest`의 dataclass 필드가 유일한 형태 선언이다. 정확한 필드·버전·소유자·
  revision 검증 후 읽고 불변 ID map과 `with_record`를 사용한다. 공통 Component와 Skills가
  같은 초기화·Reader·Writer를 사용한다. 필수 파일 목록·내용 revision 대조는 저장 경계에 유지한다.
- TC2: `FileEvidence` 필드 metadata가 Schema를 파생하며 Reader/측정/내용 비교/Writer가 이를
  사용한다. `SkillFileEvidence`만 파일 모드를 추가한다. `SkillSnapshot`은 검증된 원문과 운영
  정보를 메모리에 분리 보관하고 조회 시 사본을 반환한다. `views`의 요청 필드 선언에서 Tool
  Schema와 Reader를 파생하고 handler는 속성·Projection을 사용한다. resource 응답도 같은
  Owner를 사용한다. 명시적 Null 거부와 index/path 상호 배타 선택은 기존 Schema를 유지한다.
- 저장 경로·JSON·공개 응답 모양·원본 Markdown·명시 설정·트랜잭션 순서·예약/영수증 규칙을
  유지한다. 런타임 조회 객체를 추가 정의 파일로 저장하지 않는다. 의존성·CI 필수 Job도 변경하지 않는다.
- S7은 전환한 Skill handler의 고정 dict 조립과 키 조회(상수 치환 포함), 공통 usage Reader/Writer와
  Skills 파일 증거의 부분 Reader 재도입을 검출한다. 동적 alias/다른 모듈의 모든 dict를 증명하는
  검사가 아니며 전수 완료의 근거로 사용하지 않는다.

## 검증 실행 정책과 환경

TC1–TC2는 하나의 관련 변경으로 추적한다. 공통 Component 사용량과 파일 증거가 여러 도메인에
영향을 주므로 집중 검증 뒤 TC3 전체 게이트를 필수로 실행한다. 필수 안전성 모듈과 실제
파일 효과·프로세스 중단·영수증 소실·CAS·승인/예산/삭제 Assertion을 그대로 유지한다.

실행 위치 `/workspace/ish-llm`, Python `.venv-linux312/bin/python` 3.12.14,
ish host `d833790959a76ac457fdeea3f21a6bea1eecfe12`.
공통 환경: `TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip`.
각 실행은 source/input before/after manifest와 실제 command·exit·집계를 보존한다.

## TC1–TC2 Level 1 초기 실행

명령: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm tc1-owners-01 tests.llm.test_typed_contracts tests.llm.test_agent_skills tests.llm.test_component_storage_integrity tests.llm.test_data_component tests.llm.test_skill_tools tests.llm.test_skill_references tests.llm.test_architecture_checks`.

exit 1, discovered/started/pass 92/92/91, failure 0/error 1. skip/xfail/xpass/missing/unexpected/duplicate
모두 0. 오류는 변경된 `contract-ownership.md` 감사 hash의 재검토 요구다. 변경된 계약·소스의
개별 근거를 대조한 후 갱신하며 검사나 원래 Assertion을 제거하지 않는다.
source `6765d91cdc6cf37e77aace6f752d453ed7d14a3914d68633bc8eba2dc2bcc83a`,
input `743fc38402e6317dabad3339f60437ff8be746092c4efce871717e86898704c8`, 각각 실행 전후 동일.
증거 `/workspace/llm-clean-resume/b/tc1-owners-01/`의 `llm-execution.json`, `llm.json`, `llm.log`,
`source-before.json`, `input-before.json` 및 대응 after. 실패 실행을 통과 실행으로 합산하지 않는다.

## TC1–TC2 Level 2 결과

| 실행 | 범위 | discovered/started/pass | failure/error/skip/xfail/xpass/missing/unexpected/duplicate | exit |
|---|---|---|---|---|
| tc2-contracts-01 | LLM 관련 22개 모듈 | 297/297/297 | 모두 0 | 0 |
| tc2-hub-01 | Hub 직접 소비자 6개 모듈 | 28/28/28 | 모두 0 | 0 |

실제 실행 명령은 다음과 같다. 공통 환경은 위 절과 동일하다.

`.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm tc2-contracts-01 tests.llm.test_typed_contracts tests.llm.test_agent_skills tests.llm.test_component_storage_integrity tests.llm.test_data_component tests.llm.test_skill_tools tests.llm.test_skill_references tests.llm.test_architecture_checks tests.llm.test_architecture_reviews tests.llm.test_skill_packages tests.llm.test_refinement tests.llm.test_schema_ownership tests.llm.test_definition_components tests.llm.test_architecture_boundaries tests.llm.test_operations_schema tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_nested_graph tests.llm.test_transactions`

`.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py hub tc2-hub-01 tests.hub.test_defaults tests.hub.test_resource_tools tests.hub.test_coding_skill tests.hub.test_component_managers tests.hub.test_component_catalog tests.hub.test_file_root_presence`

각 wrapper가 실행한 원래 `python -m tests.llm.run_regression --suite ... --output ... --modules ...`
argv와 exit는 각 디렉터리의 `llm-execution.json` 또는 `hub-execution.json`에 보존한다.
증거는 `/workspace/llm-clean-resume/b/tc2-contracts-01/`, `/workspace/llm-clean-resume/b/tc2-hub-01/`.
두 실행의 source hash `e07e8402fdc44f8d662bbb69afb779ededc659cd9b45a2d3104e2eaa990e3d97`,
input hash `baf9bac9ea6c145fc989b0f228e2c42848e695db56d6045cb03e4bf837ba57e4`가 실행 전후 동일하다.
실제 시간은 LLM 123.714초, Hub 183.698초다. 이후 제품 코드는 변경하지 않았고, S7에 bare 상수
키 접근 음성 사례를 보강한 다음 감사 시험을 재실행한다. 전체 실행은 TC3에서 별도로 수행한다.

정적 재검토는 기존 137개 발생의 원래 의미·시험을 유지하고, 변경된 사용량·파일·Skills 소비
근거만 다시 대조했다. 새 `ModelUsageManifest`의 정확한 정수/current version 검사 2개를
개별 근거와 실패 재현 시험으로 추가했다. 부채 baseline과 필수 CI Job은 변경하지 않았다.
S1–S7 new/stale/outstanding/review error 0, 검토 발생 139. 문서 146개·Python fence 183개·
상대 링크 1081개·anchor 37개 오류 0. 전체 게이트에서도 다시 확인한다.

TC3와 정확한 SHA 원격 검증은 아래 후속 결과로 기록한다. 이 Level 2 결과만으로 전체 완료를 선언하지 않는다.

S7 보강 후 `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm tc2-audit-01 tests.llm.test_architecture_checks tests.llm.test_architecture_reviews tests.llm.test_typed_contracts` 실행: exit 0, discovered/started/pass 37/37/37, failure/error/skip/xfail/xpass/missing/unexpected/duplicate 모두 0.
source `c9f8fe82aa7d7c022e6390b2c51ef09e9ca7ad45a0418b3d9ce52d6c47e0b32e`, input `ed2b09125d199d427f07dba8a1a76a5aa2e2ec3e94a771d081249a434cc67cf8`, 실행 전후 동일.
증거 `/workspace/llm-clean-resume/b/tc2-audit-01/`. 이 기록만 추가한 뒤 통합 소스를 고정한다.

## TC3 — 전체 Level 3 결과

동일한 고정 소스에서 LLM/Hub 전체 suite를 각각 한 번 실행했다. 집중 검증 결과를 합산하지 않는다.

| suite | discovered/started/pass | failure/error/skip/xfail/xpass/missing/unexpected/duplicate | exit | 초 |
|---|---|---|---|---|
| llm | 2306/2306/2306 | 모두 0 | 0 | 1126.238 |
| hub | 586/586/586 | 모두 0 | 0 | 1027.841 |

공통 환경에서 실행한 명령:

`.venv-linux312/bin/python /workspace/llm-clean-resume/b/run-gate.py tc3-final-l3` — exit 0.

`.venv-linux312/bin/python /workspace/llm-clean-resume/b/run-aux-snapshot.py tc3-final-aux` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/tc3-final-l3/llm.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/tc3-final-l3/hub.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_budget_mutation` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --require S6 --require S7 --output /workspace/llm-clean-resume/b/tc3-final-aux/static.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/b/tc3-final-aux/docs.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_environment --suite llm --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/b/tc3-final-aux/env-llm.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_environment --suite hub --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/b/tc3-final-aux/env-hub.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_provider_environment --output /workspace/llm-clean-resume/b/tc3-final-aux/provider-environment.json` — exit 0.

S1–S7 검토 발생 139개, new/stale/outstanding/review error 0. R3는 의도적
호출 한도 제거에 기존 실제 추가 효과 Assertion이 실패함을 검출했다. SDK 오프라인 진단 2개
통과·skip/error/failure 0. 문서·host·namespace·X11 검증도 통과했다.

source hash `c9f8fe82aa7d7c022e6390b2c51ef09e9ca7ad45a0418b3d9ce52d6c47e0b32e`.

input hash `72fdfc16afe594538af8f1186edf5fc547d5b6663d0e5340f1cf2e265691977f`.

두 실행의 before/after source/input manifest가 각각 동일하고 서로 일치한다. Python·host·설치된
의존성·실행 바이너리·환경은 input manifest로 고정했다. 증거는
`/workspace/llm-clean-resume/b/tc3-final-l3/`의 `*-execution.json`, `*.json`, `*.log`와
`/workspace/llm-clean-resume/b/tc3-final-aux/`의 `execution.json` 및 개별 결과, 각 폴더의 source/input before/after다.

전체 검증 후 제품·테스트·Fixture·발견 목록·자산·의존성은 변경하지 않고 이 결과 문서만 갱신한다.
최종 문서 검사와 소스/검증 입력 차이를 다시 확인하며 같은 소스의 전체 회귀를 중복 실행하지 않는다.

원격 검증의 정규 증거는 이 변경을 Push한 정확한 SHA의 GitHub Actions `Linux backend contracts`
Check Run과 gate artifact다. 실제 head SHA·필수 Job/step·전체 집계를 확인한 경우에만 원격
통과를 선언한다. 로컬 결과는 원격 결과를 대신하지 않는다. 최종 보고에 SHA·CI 링크를 남긴다.
보호된 main·refactor/llm-backend·Draft PR #1은 변경하지 않는다. TC3는 이번 TC 범위의 완료이며
[남은 고정 구조 데이터 전환](ownership-refactor.md)의 전체 완료는 아니다.
