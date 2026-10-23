# 선언 위치·전수 완료 기준과 실행 Owner 보강

기준 SHA `e8769c174401ba6530439ffc0527f93b0e6c41ab`, `refactor/llm-clean`에서 이어 진행한다.
[현행 선언·고정 구조 계약](contract-ownership.md), [전체 작업 목록](ownership-refactor.md),
[Level 1–3 정책](verification-policy.md)을 적용한다. 과거 감사·통과·실패 기록은 변경하지 않는다.

## 전체 작업의 상태와 DC1

전체 리팩터링은 **미완료**다. `tests/llm/check_contract_coverage.py`는 Git이 추적하는 제품
파일과 새 제품 파일을 `llm/`·`hub/`에서 수집한다. Python뿐 아니라 같은 경로의 assets·문서도
보수적으로 목록에 넣는다. 파일 삭제·신규 파일·내용 변경·symlink·파싱 불가는 미검토/무효화로
드러난다. AST는 한 번 사용된 값, dict 키·값, 조회·갱신, 비교, 기본 인자, 동적 접근, match,
표시 template까지 후보로 수집한다. 문자열 외의 숫자·bool·None도 포함한다.

명령: `.venv-linux312/bin/python -m tests.llm.check_contract_coverage --output /workspace/llm-clean-resume/dc-inventory-final.json --require-complete`.

`--require-complete`는 전체 소유권 감사의 완료 판정용이며 미검토가 있으면 exit 1이다.
옵션 없이 보고서를 생성하는 exit 0은 감사 통과가 아니다. 이 도구는 기존 S1–S7·R3·전체 회귀를
대체하지 않으며 CI의 필수 검증을 부분 실행으로 바꾸지 않는다. 의미 해석·실제 호출 관계를
AST만으로 증명하지 않는다. 이전의 139개 S1–S7 발생 검토와 38개 데이터군 감사는 그대로
재사용하되 새 선언 위치·고정 구조 소비의 전수 승인으로 자동 복사하지 않는다.

초기 목록: 파일 441개, Python 리터럴 후보 32,283개. 새 기준의 파일 검토와 후보 분류는 아직
승계하지 않아 미검토 파일/동적 경로 각각 441개, 미분류 후보 32,283개다. 이 숫자는 **위반 수가
아니다**. 새 도구에서 분류한 위반이 0이어도 미분류가 남으면 완료를 차단한다.
현재 소스의 파일/hash/후보 수 목록은 [고정 목록](contract-coverage-index.json)에 보존한다.

검토는 `--reviews`에 파일별 JSON 배열로 공급한다. 각 항목은 `path`, 현재 `sha256`, `reason`,
`dynamic_paths_reviewed`, `contracts`, `decisions`를 요구한다. 계약에는 `owner`, `declaration`,
`consumers`, `tests`, `complete`를 기록한다. 후보에는 정확한 `id`, `classification`
(`declaration`/`non_contract`/`violation`), `owner`, `reason`을 기록한다. 선언 위치 밖의 리터럴을
`declaration`으로 승인할 수 없고 누락·중복·삭제된 후보의 검토도 통과시키지 않는다.
전체 의미 판정과 동적 경로 검토는 사람이 확인해야 하며 자동 승인·baseline 갱신 기능은 없다.

## DC2 구현과 영향

- `AgentRecord`·`AgentBinding`: canonical Field 선언과 단일 Reader, 검증된 snapshot의 속성,
  사본 반환과 `to_dict` 경계. 기존 필드·현재 정수 버전·Project/Agent/Engine·revision 일치를
  검증한다. Resolver → 접수 → Run 생성 → AgentInvocation → Resume/Restart → Run Reader가
  타입과 Owner API를 사용한다. 공개/저장 JSON에서는 원래 모양을 보존한다. 저장 fixture의
  생성은 명시 직렬화하고 내부 접수 fixture는 typed binding을 전달한다. 독립 wire 기대값과
  손상·원본 보존·실제 효과 Assertion은 제거하지 않는다.
- `AgentCatalog`: dataclass 필드가 형태 선언이다. 정확한 필드·버전·Project·ID/digest를 검증한
  뒤 불변 records map을 제공한다. Repository는 속성과 검증된 추가/삭제 API를 사용하며 clone의
  누적 catalog를 명시 전달한다. 파일 확장자는 Repository 선언부로 모은다. 원본 파일/목록
  대조, 잠금·CAS·트랜잭션·실제 쓰기 순서는 유지한다.
- `EngineContextBinding`: Engine 확장 경계가 `for_context` 동기성·execute·capability·실행 모드·
  steering·checkpoint 계약 보존을 검증한다. Resolver는 이를 호출한다. 새 기본값은 없다.
- `SessionRuntimeView`: 명시 constructor로 생성한다. 미완료 작업 수는 Application의 조회
  메서드를 사용하며 서비스에서 `_managers`와 pending work 구조를 직접 해석하지 않는다.
- `CheckpointConversation`: 공통 대화 참조 필드를 소유하며 Loop/Graph Writer와 Resume/삭제
  Reader가 같은 타입을 사용한다. Engine 고유 header는 계속 Engine이 검증한다. 공통 참조
  Projection이 Engine의 전체 binding·재개 검증을 대신하지 않는다.
- `InstructionCheckpoint`: kind/status/scope/message ID의 선언·판별·검증·사본·명시 변경을
  소유한다. Runtime·Loop·Graph·Agent·recovery·retention이 같은 Reader를 사용한다. 재개용
  사본에서 ID를 변경할 때 envelope와 Tool 사용량·다른 필수 영수증을 보존한다. 원본을 변경하거나
  필수 ID 소실을 빈 기록으로 복구하지 않는다.

이는 위 계약의 소비 경로 보강이며 이 파일들 전체의 모든 리터럴을 정리했다는 뜻은 아니다.
AgentDefinition의 전체 설정 구조, AgentExecution의 다른 중첩 binding, RequestAdmission의
나머지 envelope, Component/Service/Hub 응답과 전수 미검토는 원래 전체 작업에 남아 있다.
새 목록을 이유로 이들을 허용 예외·완료·작업 범위 밖으로 바꾸지 않는다.

## 실행 환경·실패 증거

실행 위치 `/workspace/ish-llm`, Python `.venv-linux312/bin/python` 3.12.14,
host `d833790959a76ac457fdeea3f21a6bea1eecfe12`.
공통 환경은 `TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip`.
실제 argv·exit·집계·전체 실패 로그·source/input before/after는 각 실행 디렉터리에 보존한다.
필수 skip/xfail/xpass/누락/중복은 성공으로 처리하지 않는다.

| 실행 디렉터리 (`/workspace/llm-clean-resume/b/` 아래) | 결과 | 원인·처리 |
|---|---|---|
| dc2-agent-initial | exit 2, 발견 전 거부 | 모듈명에서 `tests.llm.`을 누락. 미완전 실행이며 성공 수에 포함하지 않음 |
| dc2-owners-01 | exit 1, 발견/실행 100, pass 95, failure 0/error 5 | 내부 접수 fixture가 이전 dict를 전달. typed 경계로 수정, 기존 안전성 Assertion 유지 |
| dc2-owners-02 | exit 1, 발견/실행 227, pass 225, failure 1/error 1 | 추가 fixture의 import 누락과 새 후보 시험의 기대 개수 오류(실제 10). 둘 다 수정 후 재검증 |

위 실행들의 skip/xfail/xpass/missing/unexpected/duplicate는 모두 0이다. 각 실행의 소스/환경은
before/after hash가 동일하며 서로 다른 버전의 결과를 합산하지 않는다. 정적 감사 초기 실행은
변경된 계약/소스 hash의 재검토를 요구했다. 기존 발생의 해당 규칙 판단을 재확인하고 변경 근거만
갱신한다. 전수 선언 위치 적합성까지 승인한 것으로 확대하지 않는다.

## 검증 게이트

현재 변경은 Agent 접수·공통 Checkpoint·복구/삭제·Hub 조회에 걸치므로 집중 검증 뒤
**Level 3 전체 LLM/Hub**를 수행한다. 승인·예산·효과·Checkpoint·RAG·수명주기 손상,
실제 파일 효과·프로세스 중단·재개/삭제·R3 mutation을 포함하고 문서·정적·실환경도 확인한다.
제품 소스와 시험 입력을 고정한 뒤 실행하며, 결과 문서만 추가할 때에는 제품·시험·환경의
동일성을 별도로 확인한다. DC2 통합 통과는 DC3의 전수 잔여 0을 의미하지 않는다.

원격 CI는 최종 Push의 정확한 SHA에서 두 필수 job과 전체 시험 집계를 실제 확인해야 통과다.
이전 SHA의 취소된 CI는 통과로 승계하지 않는다. main·refactor/llm-backend·Draft PR #1은
변경하지 않는다. 최종 결과는 다음 절에 덧붙인다.

## DC2 집중·안전성 검증 결과

- `dc2-safety-03`: 발견/실행/pass 320/320/320, exit 0, 108.353초. failure/error/skip/xfail/xpass/missing/unexpected/duplicate 모두 0.
  실제 명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/dc2-safety-03/llm.json --modules tests.llm.test_execution_contract_values tests.llm.test_contract_coverage tests.llm.test_architecture_checks tests.llm.test_architecture_reviews tests.llm.test_agent_storage tests.llm.test_request_admission tests.llm.test_runtime tests.llm.test_run_api tests.llm.test_loop_steering tests.llm.test_checkpoint_integrity tests.llm.test_graph_checkpoints tests.llm.test_agent_execution tests.llm.test_run_transitions tests.llm.test_runtime_reliability tests.llm.test_transactions tests.llm.test_engine_steering tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_multi_agent_crash`.
  source hash `f58dccc6b85f9fd5f2bccb0092861f32517ce5bfa068b39ffa5963204d0d77aa`, 실행 전후 동일.
  input hash `f7e5ee4970c76aa1d86f704fa4246f6770f0d2c6baa5a71b7eaf109c1778fb0a`, 실행 전후 동일.
- `dc2-final-boundaries`: 발견/실행/pass 59/59/59, exit 0, 7.093초. failure/error/skip/xfail/xpass/missing/unexpected/duplicate 모두 0.
  실제 명령: `/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/dc2-final-boundaries/llm.json --modules tests.llm.test_execution_contract_values tests.llm.test_contract_coverage tests.llm.test_agent_storage tests.llm.test_architecture_reviews tests.llm.test_architecture_checks`.
  source hash `ff8a559be7e4ff97ae67e95f82ea9c4aeb86b2cfcc11218ff696d8265f006481`, 실행 전후 동일.
  input hash `8c0966f7739d4d1bbe814919422204e4137bab930ce891de2cfe78019cef185f`, 실행 전후 동일.

첫 실행은 Owner/직접 소비자·트랜잭션·실제 중단·승인·예산·효과·삭제 안전성 23개 모듈이다.
최종 경계 실행은 catalog가 비 JSON Mapping을 저장 입력으로 받아들이지 않도록 보강한 뒤
Owner·저장·감사 5개 모듈을 다시 실행했다. 고정 목록의 모든 제품 파일/hash를 비교하는 시험도
추가했다. 이 두 실행을 하나의 전체 회귀로 합산하지 않는다.

`dc2-aux-01`의 mutation/static/docs/env-llm/env-hub/provider-environment 6개 명령은 모두
exit 0이다. 각각 실제 argv는 `/workspace/llm-clean-resume/b/dc2-aux-01/execution.json`에 있다.
정적 검토는 기존 139개 발생을 유지하고 Agent Reader의 정확한 정수/current version guard 4개를
추가했다. 총 143개, new/stale/outstanding/review error 0이다. 범위가 제한된 기존 S1–S7 판정은
새 전수 완료 게이트의 미검토를 면제하지 않는다. 문서 147개·fence 183개·상대 링크 1089개·
anchor 37개 오류 0. 집중 실행과 환경 변수(X11 경로)가 달라 input hash는 구분하며 제품
source는 동일했다. 최종 통합 소스에서 보조 게이트를 다시 실행한다.


## 첫 전체 실행과 반환 타입 시험 수정

`.venv-linux312/bin/python /workspace/llm-clean-resume/b/run-gate.py dc2-final-l3`는 exit 1이다.

| suite | discovered/started/pass | failure/error | skip/xfail/xpass/missing/unexpected/duplicate | exit | 초 |
|---|---|---|---|---|---|
| LLM | 2318/2318/2317 | 1/0 | 모두 0 | 1 | 1164.449 |
| Hub | 586/586/586 | 0/0 | 모두 0 | 0 | 1039.321 |

유일한 실패는 `tests.llm.test_common_boundaries.ConfigurationBoundaryTests.test_nested_record_envelope_has_no_graph_dependency`다.
기존 시험은 `instruction_records()`가 raw dict를 반환한다고 기대했다. 이제 검증된
`InstructionCheckpoint`를 반환하므로 `to_dict()`를 통해 **같은 독립 payload 기대값**과 비교하도록
수정했다. 그래프 독립성, scope, 잘못된 envelope 거부 Assertion을 제거하거나 기대 JSON을
제품 선언에서 생성하지 않는다. 제품 코드 변경 없이 이 시험과 이를 참조하는 감사 근거 1개만
갱신했다. 첫 전체 실행을 통과로 기록하거나 실패 1개를 집계에서 제외하지 않는다.

첫 전체 실행의 source `ff8a559be7e4ff97ae67e95f82ea9c4aeb86b2cfcc11218ff696d8265f006481`,
input `311b47d1821feaa9c41d6b8e30847b2a40c5ac5af86328b4f7729256ce61468e`는 실행 전후 동일하다.
`dc2-final-aux`의 R3·정적·문서·LLM/Hub 환경·SDK 진단도 이 두 hash에서 모두 exit 0이다.
각 실제 argv와 전체 실패 증거는 해당 디렉터리의 `*-execution.json`/`execution.json`, suite JSON,
로그와 source/input manifest에 보존한다.

수정 후 집중 검증과 **LLM 전체**를 다시 실행한다. Hub는 위 독립 전체 실행을 재사용하되,
제품·Hub 시험/fixture·공통 실행 도구·의존성·환경·발견 목록 동일성을 manifest로 확인한다.
수정된 LLM 시험은 Hub가 import하거나 실행하지 않는다. 새 소스에서 정적·문서·R3도 다시 확인한다.
서로 다른 두 실행을 하나의 동시 전체 실행으로 표현하지 않는다. Push SHA의 원격 CI는 두 suite를
모두 실행하고 실제 완료 결과를 확인한다.


수정 후 `dc2-boundary-correction` 집중 검증은 발견/실행/pass 52/52/52, exit 0,
5.936초이며 failure/error/skip/xfail/xpass/missing/unexpected/duplicate 모두 0이다.
실제 명령: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm dc2-boundary-correction tests.llm.test_common_boundaries tests.llm.test_execution_contract_values tests.llm.test_architecture_reviews tests.llm.test_architecture_checks tests.llm.test_contract_coverage`.
source `2a814b43f5277aa70fad8e8c401fbe2f98e367268600b0cde0eef823d352d1c2`,
input `3b27f885b51ec0f086b1843963a40dd0d607332a593f6eb8ea885ccc1bd0af5a`는 전후 동일하다.

Hub 재사용 근거: 전체 source manifest 차이는 `tests/llm/test_common_boundaries.py` 하나다.
전체 input 차이는 그 시험·해당 감사 근거 JSON·이 결과 문서뿐이며 제품/Hub 시험·fixture·
실행 도구·의존성·host·환경은 동일하다. Hub 소스에서 수정된 시험을 참조하지 않는다.
증거 `/workspace/llm-clean-resume/declaration-coverage/hub-reuse-proof.json`에 차이를 보존한다.
재실행은 새로운 디렉터리에 기록하며 이전 실패 결과를 덮어쓰지 않는다.


### 재실행의 실행 세션 종료

`dc2-corrected-l3`는 실행 도구가 `Unknown process id 93310`을 반환했고 부모/시험 프로세스도
남아 있지 않아 **미완료**로 기록한다. 마지막 로그는 refinement 시험 진행 중이며 실패/오류
라인은 없었다. 최종 suite JSON·exit code·after manifest가 없으므로 통과 집계로 사용하지 않는다.
관측한 cgroup OOM/oom_kill은 모두 0이며 종료 원인은 확정하지 못했다. 로그와 before manifest,
`interruption.json`을 보존한다. 제품·시험 소스는 변경하지 않는다. 직전 실행 구간을 집중 확인한
뒤 실행 도구 세션과 분리한 프로세스에서 전체 LLM을 다시 실행하고 종료 코드와 결과를 파일에
보존한다. 시험 범위·시간 제한·안전성 Assertion을 줄이지 않는다.


`dc2-interruption-check`에서 `test_recovery_probe`·`test_refinement` 17개를 다시 실행했다.
발견/실행/pass 17/17/17, exit 0, 27.765초이며 모든 실패·오류·skip·누락·중복 수는 0이다.
실제 명령: `.venv-linux312/bin/python /workspace/llm-clean-resume/b/focus.py llm dc2-interruption-check tests.llm.test_recovery_probe tests.llm.test_refinement`.
source `2a814b43f5277aa70fad8e8c401fbe2f98e367268600b0cde0eef823d352d1c2`,
input `76037d744a67462729ccde3c8219f9ae88a72f0c8de9c7ecec21629d57da0186`는 전후 동일하다.
전체 재실행은 `dc2-recovered-l3`에, 동일 소스의 보조 검증은 `dc2-recovered-aux`에 기록한다.


## 최종 로컬 통합 검증 결과

제품 코드는 첫 전체 실행 이후 변경되지 않았다. 수정한 LLM 시험을 포함한 전체 재실행은 통과했다.
Hub는 동일성·발견 목록을 검증한 앞의 독립 전체 통과를 재사용한다. 각 실행을 구분해 기록한다.

| suite | 실행 | 발견/실행/pass | failure/error/skip/xfail/xpass/missing/unexpected/duplicate | exit | 초 |
|---|---|---|---|---|---|
| llm | dc2-recovered-l3 | 2318/2318/2318 | 모두 0 | 0 | 1153.356 |
| hub | dc2-final-l3 | 586/586/586 | 모두 0 | 0 | 1039.321 |

실제 전체 명령:

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/b/dc2-recovered-l3/llm.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/b/dc2-final-l3/hub.json` — exit 0.

보조 검증은 `dc2-recovered-aux`에서 모두 exit 0이다. 실제 명령:

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_budget_mutation` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --require S6 --require S7 --output /workspace/llm-clean-resume/b/dc2-recovered-aux/static.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/b/dc2-recovered-aux/docs.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_environment --suite llm --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/b/dc2-recovered-aux/env-llm.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_environment --suite hub --host-revision d833790959a76ac457fdeea3f21a6bea1eecfe12 --output /workspace/llm-clean-resume/b/dc2-recovered-aux/env-hub.json` — exit 0.

`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_provider_environment --output /workspace/llm-clean-resume/b/dc2-recovered-aux/provider-environment.json` — exit 0.

최종 LLM/보조 게이트 source hash `2a814b43f5277aa70fad8e8c401fbe2f98e367268600b0cde0eef823d352d1c2`, 각각 실행 전후 동일.

최종 LLM/보조 게이트 input hash `86cc3f20e2cf4d57ce36ff8b1d496826bca5b0712c24d73cd6f57e41f513beca`, 각각 실행 전후 동일.

기존 S1–S7의 개별 검토 143개, new/stale/outstanding/review error 0. R3의 실제 호출 한도 제거 검출과 SDK 오프라인 진단·host/namespace/X11 확인을 유지했다.

이 결과는 DC2의 통합 검증이며 전체 소유권 전환 완료가 아니다. 전수 기준의 파일 441개와
후보 32,283개는 아직 미검토/미분류이므로 `check_contract_coverage --require-complete`는
exit 1이어야 한다. 완료 판정을 통과시키기 위한 자동 승인이나 제외는 추가하지 않는다.

이 결과를 기록한 뒤 제품·시험·fixture·의존성·발견 목록의 동일성을 확인하고 문서 검사를
다시 실행한다. 같은 제품 소스의 LLM/Hub 전체 회귀를 문서 결과 추가만으로 반복하지 않는다.
Push 뒤 정확한 SHA의 두 필수 CI job과 전체 집계를 확인하며 최종 보고에 SHA·CI 링크를
남긴다. 원격의 정규 결과는 해당 commit의 GitHub Actions Check Run과 gate artifact다.
