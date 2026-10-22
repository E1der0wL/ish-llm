# Agent Skills 전환 검증 기록

기준 commit: `f32b1f174445c80316d7b93b4bde44c328dd642c`.
작업 브랜치: `refactor/llm-clean`. [설계 계약](agent-skills.md)을 따른다.
이전 검증 결과를 이번 변경의 통과 증거로 승계하지 않는다.

| 단계 | 범위 | 상태 |
|---|---|---|
| AS1 | 표준 문서 Owner·native package·운영 기록·파일 효과/장애 검증 | AS3 통합 통과 |
| AS2 | Agent·Refinement·Hub·예제 및 관련 안전성 소비자 | AS3 통합 통과 |
| AS3 | 전체 Level 3·문서·정적 감사·R3·환경·정확한 Push SHA 원격 CI | 로컬 통합 통과; 원격 결과는 해당 commit Checks 기록 |

결과는 실제 명령·exit·discovered/started/pass/failure/error/skip/xfail/xpass/missing/
unexpected/duplicate·소스/환경 동일성과 함께 단계별 추가한다. 집중 검증과 전체·원격 완료를 구분한다. main/refactor/llm-backend/Draft PR #1은 변경 대상이 아니다.

## AS1 — native 저장 Level 1

`SkillDocument/SkillName`, `SkillRecord`, `SkillStorage`를 추가하고 SkillComponent/Data가
Owner를 사용하도록 전환했다. `_state/usage`만을 위해 기존 Component 사용량 경로 hook을
분리했으며 영수증 형식·검증·예산 의미는 변경하지 않았다.

실행: `TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip .venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm as1-native-01 tests.llm.test_agent_skills`

exit 0, discovered/started/pass 15/15/15. failure/error/skip/xfail/xpass/missing/unexpected/duplicate
모두 0. 실제 디렉터리 import/export의 CRLF·주석·파일 권한·빈 디렉터리 보존, legacy 거부,
링크/FIFO 거부, 자료/운영 기록 소실, stale CAS, 저장·import 장애 후 원본 보존, fork/clone/
backup 및 파일 변경 후 오래된 Tool 읽기 거부를 검증했다. 전체 회귀는 AS2 소비자 전환 후
AS3에서 실행한다. 이 결과는 AS1 집중 검증이며 소단계 전체 완료가 아니다.

실행 전후 source hash `2b07aaae87d5672e39e2c18469fdf17b5717c93964d5e8fb8ef66d3420f1af5e`,
input hash `b27cf89375f9a6923eddc4f7ffb338c4bad79ed40f340991ef3f20523518d6dc`가 각각 동일했다.
Python 3.12.14, host `d833790959a76ac457fdeea3f21a6bea1eecfe12`.
증거: `/workspace/llm-clean-resume/b/as1-native-01/{llm-execution.json,llm.json,llm.log,source-before.json,input-before.json}`.
기준 commit의 미커밋 AS1 소스이며 이후 소비자 변경에 이 통과를 전체 결과로 재사용하지 않는다.

## AS2 — 관련 소비자 Level 2

Agent는 SkillDocument 본문을 사용하고 기존 Tool 권한을 그대로 적용한다. Refinement는
원문 patch와 대상 Owner의 이름 검증을 사용하며 승인·평가·CAS·rollback·부모/Agent 삭제
보호를 유지한다. Hub 기본 지침은 표준 디렉터리로 배포하고 공개 import API로 설치한다.
Hub 관리 창은 Markdown을 직접 편집하고 생성 Tool의 출처는 Skill 운영 기록에 둔다.

집중 범위는 저장 Owner, 실제 호출 소비자, 참조·승인·예산·삭제·파일 영향·중단 복구와
플러그인/정적 감사 증거다. 기존 Assertion·실제 효과 횟수를 유지하고 표준 원문 fixture로
전환했다. native 저장 새 시험 18개와 Unicode Refinement 시험 1개를 추가했다.

| 실행 | discovered/started/pass | failure/error | exit | 원인·처리 |
|---|---|---|---|---|
| as2-contracts-01 | 81/81/79 | 0/2 | 1 | 구 문서 예제·fixture 이름 불일치; 표준 원문과 일치하는 ID로 수정 |
| as2-contracts-02 | 233/233/232 | 0/1 | 1 | 문서 편집 예제에 남은 최상위 description; Markdown CAS 편집으로 수정 |
| as2-hub-01 | 28/28/27 | 0/1 | 1 | 구 단일 .md/JSON 편집 fixture; 표준 디렉터리·원문 편집으로 수정 |
| as2-contracts-03 | 250/250/250 | 0/0 | 0 | 관련 LLM 16개 모듈 통과 |
| as2-hub-02 | 28/28/28 | 0/0 | 0 | 관련 Hub 6개 모듈 통과 |

모든 실행에서 skip/xfail/xpass/missing/unexpected/duplicate는 0이다. 실패 로그를 보존하며
실패한 실행을 성공 결과로 합산하지 않는다. 이후 수정은 원래의 원본 보존·권한·효과 Assertion을
유지했다. 통과한 두 실행의 source/input hash는 각각
`777baa1fb58ac0ee26bcbd440af2fa1564ba3aabb9615fc981bacc3f7174d715` /
`b6bde1765ea4b6e4450c00d9534c0d9178d3f9d730cf6fca466f570ff8e4a10d`이며
실행 전후 동일하다. Python·host·설치된 의존성은 AS1과 동일하다.

실제 프로세스 종료(71) 후 Markdown·자료·운영 기록 복구, 저장 직전 다른 원문 차단,
사용량 영수증 소실 후 호출 한도 초기화 차단 및 실제 효과 1회 유지도 통과했다.
전체 회귀는 소비자 전환과 연결된 AS1–AS2를 묶는 AS3에서 수행하며 이 결과로 대신하지 않는다.

실행 명령과 전체 결과는 다음 증거에 보존한다. 모든 명령은 앞 절과 같은 TERM/LiteLLM/X11
환경 및 `.venv-linux312/bin/python`을 사용한다.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/as2-contracts-01/llm.json --modules tests.llm.test_agent_skills tests.llm.test_skill_packages tests.llm.test_skill_references tests.llm.test_refinement tests.llm.test_schema_ownership tests.llm.test_definition_components`

증거: `/workspace/llm-clean-resume/b/as2-contracts-01/{llm-execution.json,llm.json,llm.log,source-before.json,input-before.json}`.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/as2-contracts-02/llm.json --modules tests.llm.test_agent_skills tests.llm.test_skill_packages tests.llm.test_skill_references tests.llm.test_refinement tests.llm.test_schema_ownership tests.llm.test_definition_components tests.llm.test_architecture_boundaries tests.llm.test_skill_tools tests.llm.test_agent_workflow tests.llm.test_nested_graph tests.llm.test_data_quarantine tests.llm.test_explicit_configuration tests.llm.test_component_storage_integrity tests.llm.test_usage_deletion`

증거: `/workspace/llm-clean-resume/b/as2-contracts-02/{llm-execution.json,llm.json,llm.log,source-before.json,input-before.json}`.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/as2-hub-01/hub.json --modules tests.hub.test_defaults tests.hub.test_resource_tools tests.hub.test_coding_skill tests.hub.test_component_managers tests.hub.test_component_catalog tests.hub.test_file_root_presence`

증거: `/workspace/llm-clean-resume/b/as2-hub-01/{hub-execution.json,hub.json,hub.log,source-before.json,input-before.json}`.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/as2-contracts-03/llm.json --modules tests.llm.test_agent_skills tests.llm.test_skill_packages tests.llm.test_skill_references tests.llm.test_refinement tests.llm.test_schema_ownership tests.llm.test_definition_components tests.llm.test_architecture_boundaries tests.llm.test_skill_tools tests.llm.test_agent_workflow tests.llm.test_nested_graph tests.llm.test_data_quarantine tests.llm.test_explicit_configuration tests.llm.test_component_storage_integrity tests.llm.test_usage_deletion tests.llm.test_plugin tests.llm.test_architecture_reviews`

증거: `/workspace/llm-clean-resume/b/as2-contracts-03/{llm-execution.json,llm.json,llm.log,source-before.json,input-before.json}`.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/as2-hub-02/hub.json --modules tests.hub.test_defaults tests.hub.test_resource_tools tests.hub.test_coding_skill tests.hub.test_component_managers tests.hub.test_component_catalog tests.hub.test_file_root_presence`

증거: `/workspace/llm-clean-resume/b/as2-hub-02/{hub-execution.json,hub.json,hub.log,source-before.json,input-before.json}`.

## AS3 준비·정적 재검토

S1–S7의 기존 137개 개별 근거를 유지했다. SkillData._target은 literal revision을
SkillRecord.REVISION으로 바꾼 한 occurrence만 새 ID로 대응시켰고 대상 snapshot의 정확한
고정 버전 비교·stale 거부를 재검토했다. 변경된 공유 사용량 경로 hook, native seeding,
Refinement 대상 이름 및 Hub 원문 편집의 관련 source/test/contract만 다시 검토했다.
검증 코드·규칙·debt baseline·필수 CI job은 변경하지 않았다.

PyYAML은 이미 설치·잠금된 6.0.3을 직접 의존성으로 선언하고 plugin import를 yaml로 지정했다.
기존 잠금 파일을 constraint로 재생성해 모든 배포판 pin과 아티팩트 hash가 동일함을 확인했다.
잠금 파일에서 바뀐 값은 입력 선언 fingerprint뿐이다. `tests.llm.dependencies --installed` exit 0.
정적 검사 required outstanding/new/stale/review errors 0, 문서 145개·Python fence 183개·
상대 링크 1075개·anchor 37개 오류 0(최종 통합 gate에서도 다시 검사).
마지막 집중 검증 후 공개 API docstring의 구 Skill 예제와 해당 감사 pin, 이 결과 문서만 갱신했다.

AS3는 전체 회귀·R3·문서·정적 감사·host/namespace/offline SDK 환경을 현재 통합 소스에서
수행한다. AS1–AS2는 함께 동작하는 저장/소비자 전환이므로 AS3 전체 성공 후 하나의
검증된 통합 변경으로 commit·Push한다. 정확한 Push SHA의 원격 결과를 확인하기 전에는
원격 CI 통과를 선언하지 않는다.

## AS3 — 전체 Level 3 결과

실행: 앞 절과 동일한 환경에서 `.venv-linux312/bin/python /workspace/llm-clean-resume/b/run-gate.py as3-final-l3` 및 `.venv-linux312/bin/python /workspace/llm-clean-resume/b/run-aux-snapshot.py as3-final-aux`. 두 실행의 source/input snapshot은 동일하고 실행 전후 변하지 않았다.

| 전체 suite | discovered/started/pass | failure/error/skip/xfail/xpass/missing/unexpected/duplicate | exit | 실행 초 |
|---|---|---|---|---|
| llm | 2299/2299/2299 | 모두 0 | 0 | 1134.740 |
| hub | 586/586/586 | 모두 0 | 0 | 1030.957 |

두 suite는 각 한 번의 전체 실행이며 집중 검증의 통과 수를 합산하지 않았다.
승인·예산·실제 Tool 효과·Checkpoint·Resume·RAG·삭제·트랜잭션 전체 안전성 시험을 포함한다.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/as3-final-l3/llm.json`

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/as3-final-l3/hub.json`

보조 게이트 6개 명령 모두 exit 0: R3 mutation, S1–S7, 문서, LLM/Hub 환경, 실제 SDK 오프라인 진단. R3는 의도적 한도 제거가 실제 추가 효과를 일으키는 원래 Assertion을 실패시킴을 확인했다. 이를 제품 회귀 실패나 통과 수와 섞지 않는다. 정적 감사 137개 근거의 current hash가 일치하며 new/stale/outstanding/review error는 0이다. 문서 오류 0, SDK 진단 2개 통과·skip/error/failure 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_budget_mutation` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --require S6 --require S7 --output /workspace/llm-clean-resume/b/as3-final-aux/static.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/b/as3-final-aux/docs.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_environment --suite llm --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/b/as3-final-aux/env-llm.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_environment --suite hub --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/b/as3-final-aux/env-hub.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_provider_environment --output /workspace/llm-clean-resume/b/as3-final-aux/provider-environment.json` — exit 0.

source hash: `405791473dcba9cc9bdb54f29ea11aab31081626ce6f7597b0256b28248de00f`.

input hash: `482aafc303b50c1106d4e800e6274d697bba31486ba0ce6c596e9eb6791e5a05`.

Python 3.12.14, host `d833790959a76ac457fdeea3f21a6bea1eecfe12`; 설치된 의존성·실행 바이너리·환경은 input snapshot에 고정했다. 증거는 `/workspace/llm-clean-resume/b/as3-final-l3/`의 suite별 `*-execution.json`, `*.json`, `*.log`와 `/workspace/llm-clean-resume/b/as3-final-aux/`의 `execution.json` 및 개별 결과다. 각 폴더에 source/input before/after를 함께 보존한다.

전체 실행 후에는 이 결과 문서만 갱신했다. 제품 Python·테스트·Fixture·패키지 자산·의존성·발견 목록을 바꾸지 않았으며 최종 문서 검사와 source 동일성을 다시 확인한다. 결과 기록을 위한 문서 수정으로 같은 로컬 전체 회귀를 반복하지 않는다.

원격 검증의 정규 증거는 Push된 정확한 SHA의 GitHub Actions `Linux backend contracts` Check Run과 업로드된 gate artifact다. 로컬 결과를 원격 통과로 승계하지 않는다. 해당 SHA의 LLM/Hub 전체 집계와 필수 job/step 실제 결과를 조회해 최종 보고에 SHA·CI 링크를 명시한다. main, refactor/llm-backend와 Draft PR #1은 변경하지 않는다. 유료 외부 모델의 판단 품질 평가는 이 저장 형식 전환의 완료 범위가 아니다.
