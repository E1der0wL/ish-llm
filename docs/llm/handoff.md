# 최신 작업 인계 — 2026-10-26

## Loop 공개 프롬프트 설정 제거

기준 HEAD: `7cd4c6c6d13b5152680b682078310841a0dd1fe6`.
작업 시작 시 존재하던 변경을 보존했다. 이번 실행 코드 변경은
`llm/engines/loop/configuration.py`, `llm/engines/loop/engine.py`에 한정된다.
Hub 제품 코드는 수정하지 않았고 관련 Hub 테스트 두 파일의 이전 설정 예시만 갱신했다.

- Loop의 공개 `config`는 `buffer_size`만 제공한다. Project/Session 및 Agent
  `engine_options.config.system_prompt`는 검증 오류이며 자동 이관·alias는 없다.
- 시스템 지침은 참조 Prompt → Agent의 인라인 `system_prompt` → 선택 Skill 순서로
  구성한다. Loop는 Agent 지침을 실행 입력으로 사용하고 Engine effective configuration에
  복제하지 않는다. missing/null/빈 문자열은 인라인 지침을 만들지 않으며 선택한 자료는 유지한다.
- `_configure()`의 프롬프트 상태와 Project/Session 프롬프트 상속 경로를 제거했다.
  구성된 지침은 기존 실행 binding에 계속 포함한다. 승인 후 지침이 달라지면 재개를 거부하며
  정상 재개는 완료된 모델 응답·Tool 영수증을 재사용한다. 저장 형식 버전은 바꾸지 않았다.
- `bleach_research`, `developer_assistant` 예제를 Agent 지침 경로로 수정했다.
  사용자 지침이 없는 경우 예제 코드도 임의의 지침을 채우지 않는다.
- Graph의 `cleanup_timeout`은 변경하지 않았다. 취소한 노드의 정리를 명시된 시간만 기다리고
  미완료 작업은 PendingWork가 추적하는 Graph 설정이다. Loop에 같은 옵션을 추가하지 않았다.

### 이번 검증 결과

Linux Python **3.12.14**, Linux 파일시스템 사본에서 실행했다. 겹치는 suite는 합산하지 않는다.

| 검사 | Passed | Failures | Errors | Skipped |
|---|---:|---:|---:|---:|
| 초기 지침·설정·장시간 실행 focused | 58 | 0 | 0 | 0 |
| Agent·Graph·승인·설정 focused | 162 | 0 | 0 | 0 |
| 전체 LLM — 예제 보완 전 | 2,558 | 0 | 1 | 0 |
| 예제 보완 후 Skill·Agent·명시적 설정 재검증 | 46 | 0 | 0 | 0 |
| Hub 공개 API·설정 UI 연결 — 9개 모듈 | 51 | 0 | 0 | 0 |

전체 LLM은 2,559개를 누락 없이 실행했고 **2574.10초**가 걸렸다. 유일한 오류는
`test_skill_tools`의 예제 Project 생성이었다. JSON 예제에 남은
`parameters.engines.loop.config.system_prompt`를 새 schema가 거부한 것으로 확인했다.
예제 JSON을 `agent.system_prompt`로 옮기고 예제 실행 코드에 명시적 전달을 연결했다.
실제 예제 요청이 그 지침을 모델 입력에 전달하는 회귀도 추가했다. 이후 해당 모듈과 Agent·설정
검사 46개가 모두 통과했다. **예제 보완 후 전체 suite를 다시 실행한 결과로 표기하지 않는다.**
전체 실행 이후 Backend Python 코드는 변경하지 않았다. 최종 LLM discovery는 새 예제 테스트를
포함해 2,560개다. Hub 전체 suite는 이번 작업에서 실행하지 않았다.

문서, S1–S7 architecture 및 계약 감사도 통과했다. 감사 대상은 649개 제품 파일,
15,522개 후보이며 누락·위반은 0건이다. 실제 사내 모델/외부 endpoint나 원격 CI는 실행하지 않았다.
커밋·업로드는 이번 요청 범위에 없다.

사본: `/home/user/.cache/ish-agent-prompt-lpof8mya`.
Python: `/home/user/.cache/ish-scope-deps-69rd8deo/.venv-linux312/bin/python`.
참조 호스트: `d833790959a76ac457fdeea3f21a6bea1eecfe12`의 깨끗한 checkout.
보고서: `tests/llm/reports/agent-prompt-20261026/`의 `llm.json`, `example-fix.json`,
`hub-public.json`, `focused.json`, `verified.json`. `llm-initial-source.json`은 전체 실행 당시
사본 해시를 보존하고 `snapshot.json`은 최종 파일 해시를 담는다.

```sh
.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /tmp/agent-prompt-llm.json
.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --modules tests.llm.test_skill_tools tests.llm.test_agent_models tests.llm.test_explicit_configuration --output /tmp/agent-prompt-examples.json
```

## 스키마 식별자 충돌 보강

기준 HEAD: `7cd4c6c6d13b5152680b682078310841a0dd1fe6`.
작업 시작 시 존재하던 Hub 변경을 그대로 보존했다. 이번 제품 코드 변경은
`llm/core/schema.py`에 한정되며, 기존 Engine binding과 실행 검증을 완화하지 않았다.

- 익명 소유 스키마에 부여하는 식별자를 결정적인 계층형 URI로 변경했다.
  서로 다른 소유자가 `inner.json` 같은 상대 `$id`를 사용해도 각각 자기 범위에 속한다.
  생성 URI는 스키마 조합 정보이며 사용자 설정·저장값·네트워크 요청 주소가 아니다.
- 공통 `checked_schema()`에서 상대 ID를 해석한 뒤 동일 resource/anchor 주소의 충돌을
  검사한다. 같은 정규 JSON 선언은 재사용 가능하지만 다른 선언은 주소와 두 위치를 포함한
  오류로 거부한다. 명시한 `$id`를 임의로 바꾸지 않는다.
- `$ref` 문자열·공유 객체·재귀 및 dynamic reference 의미를 보존한다. 실제 스키마만 순회하고
  `const`·`enum`·예제 데이터는 해석하지 않는다. 특정 Engine/Adapter 예외는 추가하지 않았다.

원래 실패를 수정 전·후 비교로 재현했다. 두 익명 스키마의 상대 child ID가 충돌하면 수정 전에는
정상 입력을 거부하고 잘못된 입력을 허용했다. 수정 후에는 정상 입력만 허용한다.
회귀 8개를 추가했으며 기존 설정 소유권·승인·명시적 재개·Tool 효과 재사용·저장 형식은 유지한다.
상세 계약은 [Agent 스키마 설명](agents.md)을 따른다.

### 검증 결과와 남은 항목

Linux Python **3.12.14**, Linux 파일시스템 사본에서 실행했다. 서로 겹치는 suite는 합산하지 않는다.

| 검사 | Passed | Failures | Errors | Skipped |
|---|---:|---:|---:|---:|
| 스키마 focused | 36 | 0 | 0 | 0 |
| Agent·Graph·승인·설정 focused | 159 | 0 | 0 | 0 |
| 전체 LLM | 2,555 | 0 | 0 | 0 |
| 전체 Hub | 616 | 0 | 4 | 0 |
| Hub 실패 4건 개별 재검사 — 수정 후 | 2 | 0 | 2 | 0 |
| 같은 4건 비교 — 수정 전 스키마 코드 | 3 | 0 | 1 | 0 |

전체 LLM은 **2738.72초**, 전체 Hub는 **1954.52초**다. Hub 오류 4건은
`tests/hub/support.py::eventually()`의 3초 제한 초과다. `test_component_managers`의
초기 `view.connected` 대기 3건과 `test_retry_observations`의 Tool backoff 관측 대기 1건이다.
스키마 충돌/검증 예외는 없었다. 같은 제한을 유지한 개별 재검사에서는 두 건이 통과했으며,
수정 전 `schema.py`를 넣은 별도 실제 디렉터리 사본에서도 초기 연결 timeout이 재현됐다.
시간 민감성이 확인됐지만 환경 부하·테스트 순서 등 상세 원인을 확정하지 않았다.
**Hub 전체 통과라고 보고하지 않는다.** Hub 제품·테스트와 timeout 값은 수정하지 않았다.

최초 수정 전 비교 사본은 Hub assets를 symlink로 연결해 소스 링크 금지 계약에 걸렸다.
그 결과는 비교 근거에서 제외하고, 실제 디렉터리 사본으로 재실행한 결과만 위 표에 기록했다.
처음 X11 사전 점검은 `/usr/bin` 경로에 도구가 없어 실패했으며, 기존 테스트 전용 X11 경로와
라이브러리 경로를 명시한 환경에서 전체 Hub와 별도 비교를 실행했다. 시스템 설치는 변경하지 않았다.

문서 검사와 S1–S7 architecture 검사를 통과했다. 계약 감사는 649개 제품 파일,
15,524개 후보를 대상으로 누락·위반 0건이다. 테스트 코드의 기대값을 완화하거나
사용자 기본값·자동 migration·호환 alias를 추가하지 않았다.

검증 사본: `/home/user/.cache/ish-schema-collision-u2wt1mym`.
Python: `/home/user/.cache/ish-scope-deps-69rd8deo/.venv-linux312/bin/python`.
참조 호스트는 CI 고정 commit `d833790959a76ac457fdeea3f21a6bea1eecfe12`의 깨끗한 checkout이다.
보고서·수정 전 비교·해시는 `tests/llm/reports/schema-collision-20261026/`에 보존한다.

```sh
.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /tmp/schema-collision-llm.json
.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /tmp/schema-collision-hub.json
```

1,221개 검증 파일의 LF 정규화 해시가 작업 소스와 일치했다. 이번 인계 문서만 검사 후 추가하고
문서 검사를 다시 통과했다. Hub 코드·테스트·문서·예제는 작업 시작 시점과 동일하다.
외부 모델/사내 endpoint 및 원격 CI는 실행하지 않았다. 이번 요청에는 commit/push가 없어
작업 트리에 변경을 남긴다.

---

아래는 이전 단계 기록이다. 현재 검증 결과는 위 표를 따른다.

## Agent 스키마 참조 합성 보강

기준 HEAD: `7cd4c6c6d13b5152680b682078310841a0dd1fe6`.
앞선 Engine별 Agent 폼 구현을 유지하면서 다음 공통 합성 경계를 보강했다.

- `core/schema.py::nest_schema()`는 소유 스키마의 resource 범위를 보존한다.
  기존 `$id`와 참조를 그대로 사용하고, 참조가 있는 익명 스키마에는 결정적인 Schema resource
  식별자를 부여한다. 빈 `$id`(`""`, `"#"`)도 익명으로 취급한다. 참조 문자열을 수정하지 않아
  공유된 스키마 객체의 이중 변환이 사라진다. 외부 reference와 schema default는 계속 거부한다.
- `engines/registry.py::describe_agent()`는 등록 Engine 이름 조건을 루트 `allOf`에 추가한다.
  원본 `properties.engine`의 위치·참조·기존 const·boolean schema를 변경하지 않는다.
- 추가 회귀에서 공통 외형 합성의 공유 `allOf` 목록 변형도 재현했다.
  `EngineAgentBinding.describe()`의 목록 추가 한 줄을 복사 후 조합으로 변경했다.
  `EngineAgentBinding.bind()`의 검증과 `for_agent()`의 의미 검증은 그대로 유지한다.

Schema resource 식별자는 UI/검증용 구조 정보이며 사용자 설정·저장 Agent 값·외부 조회 주소가
아니다. 특정 Engine/Adapter 예외, 이전 검증 결과 재사용, 사용자 기본값, 호환 alias 또는
migration은 추가하지 않았다. 승인·재개·Tool 영수증과 저장 형식도 변경하지 않았다.
자세한 조회·조합 계약은 [Agent 문서](agents.md)를 따른다.

### 최종 검증

Linux Python **3.12.14**, Linux 파일시스템 사본에서 새로 실행했다. suite는 서로 겹치므로
합산하지 않는다. 아래 전체 LLM 결과는 최종 코드와 테스트를 모두 포함한다.

| 검사 | Passed | Failures | Errors | Skipped |
|---|---:|---:|---:|---:|
| 최종 Agent schema focused | 28 | 0 | 0 | 0 |
| 전체 LLM | 2,547 | 0 | 0 | 0 |
| 전체 Hub 실행 시도 | 61 | 1 | 105 | 0 |

전체 LLM에는 승인·명시적 재개·Tool 효과 재사용·중첩 Agent/Graph·required scope·Provider·
RAG·저장 복구 검사가 포함된다. 실행 시간은 **2143.22초**다. S1–S7 architecture 검사,
647개 제품 파일/15,449개 후보의 계약 감사, 문서 검사(177개 문서/165개 Python 예제)도 통과했다.

Hub는 기존 `hub/backend/project/title_values.py:14`의 `LoopParameters.COMPLETION` 참조 때문에
실패했다. 103개는 test module import 실패로 내부 테스트가 실행되지 않았고, 추가 오류 2개와
로더 실패 1개도 같은 import 오류다. Hub 제품·테스트 코드와 Backend 호환 API를 수정하지 않았다.
따라서 Hub 전체 연결이나 UI 렌더링까지 검증되었다고 판단하지 않는다.

추가 공통 목록 테스트의 최초 fixture에는 자체 `engine_options`를 처리하는 `for_agent()`가
없어 한 번 실패했다. fixture의 소유자 검증을 구현한 뒤 28개 focused와 전체 LLM을 통과했다.
이를 통과시키기 위해 제품 binding 검증을 완화하지 않았다.

최종 사본: `/home/user/.cache/ish-schema-references-nu05_xd7`.
Python: `/home/user/.cache/ish-scope-deps-69rd8deo/.venv-linux312/bin/python`.
참조 호스트는 CI 고정 commit `d833790959a76ac457fdeea3f21a6bea1eecfe12`의 깨끗한 checkout을
사용했다. 이전 검사와 달리 호스트 소스·Git revision 혼합이나 assertion 수정 없이 실행했다.
로그·JSON 결과·hash는 `tests/llm/reports/agent-schema-references-20261026/`에 보존한다.
suite 종료 후 LF 정규화 기준 **1,217개 파일**이 작업 소스와 일치했다. 이 인계 기록만 이후에
추가하고 문서를 다시 검사했다. 외부 모델/사내 endpoint 및 원격 CI는 실행하지 않았다.
이번 요청에 commit/push는 포함되지 않아 변경은 작업 트리에 남긴다.

---

아래는 이전 단계의 기록이다. 최종 결과는 위 표를 기준으로 한다.

## Engine별 Agent 폼 조회

기준 HEAD: `7cd4c6c6d13b5152680b682078310841a0dd1fe6`.
이번 변경은 Agent 편집 UI를 위한 조회 계약이다. Hub 제품 코드는 변경하지 않았다.

```python
catalog = await project.agents.adescribe()
form = await project.agents.adescribe(engine="loop")
snapshot = await project.agents.asnapshot("reviewer")
```

선택 Engine의 순수 동기 `describe_agent()`가 자기 Agent JSON Schema를 제공한다.
Loop는 선택 Completion adapter 스키마와 실행 옵션을, Graph는 Workflow Agent 필드와
필수 workflow 선택을 공개한다. 같은 스키마를 binding에서도 검사하고 `for_agent()`의
의미 검증을 유지한다. 사용자 정의 Engine의 schema hook은 선택 사항이며, 없으면 기존
실행은 유지하지만 선택 폼 조회는 명확하게 거부한다. 공통 최상위 계약은 확대하지 않는다.

조회는 값·기본값·Agent·Run을 만들지 않는다. 기존 저장 형식, Agent revision/CAS,
승인·재개와 완료 Tool 재사용 계약은 변경하지 않았다. 상세 계약은 [Agent 문서](agents.md)를 따른다.

### 이번 검증

Linux Python **3.12.14**, Linux 파일시스템 사본에서 새로 실행했다.
아래 suite는 중복되므로 합산하지 않는다.

| 검사 | Passed | Failures | Errors | Skipped |
|---|---:|---:|---:|---:|
| Agent schema·모델·위임·Graph focused | 77 | 0 | 0 | 0 |
| 전체 LLM | 2,533 | 1 | 0 | 0 |
| CI 고정 호스트로 로더·CLI 재검사 | 7 | 0 | 0 | 0 |
| 전체 Hub 실행 시도 | 61 | 1 | 105 | 0 |

전체 LLM의 실패 1건은 참조용 로컬 ish 소스에 `_is_library_available`가 없어서 발생했다.
제품·테스트 소스는 그대로 유지하고 CI가 고정한 host commit
`d833790959a76ac457fdeea3f21a6bea1eecfe12`를 별도 임시 경로에 추출해 외부 `HOST`
fixture로 지정했다. 로더·CLI 7개를 재실행해 모두 통과했다. 원래 전체 실행이
0 failures였다고 표현하지 않으며, 남은 미해결 LLM 테스트 실패는 없다.

Hub errors 중 103개는 모듈 import 실패이며 해당 모듈 내부 테스트는
실행되지 않았다. 이전 모델 소유권 변경으로 제거된 `LoopParameters.COMPLETION`을
`hub/backend/project/title_values.py`가 참조하는 기존 연결 문제다. 이번 작업에서
Hub를 수정하거나 Backend 호환 alias를 복원하지 않았다. Hub가 통과했다고 판단하지 않는다.

S1–S7 architecture, contract coverage, 문서 검사를 통과했다. 전체 LLM 실행 시간은
2140.31초다. 최초 사본에 Git inventory와 reference host 의존성 정보가 누락된
검사는 중단했으며, 이를 보완하고 관련 12개 검사를 통과한 뒤 전체를 다시 실행했다.
전체 회귀에서 새 스키마 검증이 라이브러리 예외를 노출하는 문제도 발견했다. 기존 Agent
입력 오류의 `ValueError` 계약을 유지하도록 수정하고 Graph 요청 검사까지 focused에 포함한
후 아래 전체 결과를 다시 얻었다.

최종 실행 사본: `/home/user/.cache/ish-agent-schema-3n3ejtzh`.
Python: `/home/user/.cache/ish-scope-deps-69rd8deo/.venv-linux312/bin/python`.
로그·JSON 결과·LF 정규화 hash는 `tests/llm/reports/agent-schema-20261026/`에 보존한다.
전체 suite 종료 시 1,247개 파일이 작업 소스와 일치했다.
이 인계 문서는 그 결과를 기록한 후 문서 검사를 별도로 실행했다.

```sh
.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /tmp/agent-schema-llm.json
.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /tmp/agent-schema-hub.json
```

실제 외부 모델/사내 endpoint 및 원격 CI는 실행하지 않았다. 이번 사용자 요청에
commit/push가 포함되지 않아 작업 트리에 변경을 남긴다.

---

아래는 이전 Agent 모델 설정 분리 당시의 인계다. 아래 검증 수를 이번 결과로 재사용하지 않는다.

## Agent 모델 설정과 Loop 실행 설정 분리

기준 HEAD: `c7d9bfafe3aab261684fe26ac3bbf010b38135d8`.
모델·인증·SDK 인자는 저장 Agent의 `completion`, 반복/Tool/문맥 예산/외부 재시도는 Loop의
실행 설정으로 분리했다. [소유권·수동 변경 계약](agent-models.md)을 따른다.

제품 Python 변경은 `engines/loop/configuration.py`, `engines/loop/engine.py`,
`services/command_line.py`다. Agent CRUD와 기존 definition/revision snapshot을 재사용하며,
새 저장 도메인·설정 계층·호환 alias·자동 migration을 추가하지 않았다.
Run/Step/ToolExecutor/승인 저장/Provider 구현과 저장 형식 버전은 변경하지 않았다.

모델 미설정 시 다른 Agent나 Project/Session의 모델을 빌리지 않는다. 예제·CLI와 회귀 fixture는
각 Agent에 모델 인자를 명시한다. 모델 변경으로 Agent revision이 달라지면 이전 승인·재개를
거부하며 완료 Tool 효과를 다시 실행하지 않는다. API 키를 직접 저장하면 Agent와 admission/Run
definition snapshot에도 포함되는 기존 저장 계약을 명시했다.

### 이번 검증 결과

Linux Python **3.12.14**, Linux 파일시스템의 소스 스냅샷에서 실행했다.
아래 검사는 서로 겹치므로 합산하지 않는다.

| 검사 | Passed | Failed/Error | Skipped | 근거 |
|---|---:|---:|---:|---|
| 설정·모델·승인·재개 focused | 209 | 0 | 0 | `corrected.json`, 15개 모듈 |
| CLI와 실제 ish loader | 7 | 0 | 0 | `plugin.json` |
| 최종 전체 LLM | 2,519 | 0 | 0 | `final.json`, 1,793.66초 |

새 회귀는 Agent 모델 격리, SDK 명시값과 생략/null 보존, Engine의 옛 모델 경로 거부,
CLI 소유권, 모델 변경 후 기존 효과/승인 재사용 금지를 검사한다.
초기 스냅샷에서 발견한 옛 모델 상속 fixture를 수정한 뒤 전체를 다시 실행했다.
초기 `full.txt`는 중단한 진단 실행이며 최종 결과가 아니다.

최종 실행 스냅샷: `/home/user/.cache/ish-agent-models-w_qzpivs`.
Python: `/home/user/.cache/ish-scope-deps-69rd8deo/.venv-linux312/bin/python`.
원본 로그와 소스 hash는 `tests/llm/reports/agent-models-20261026/`에 보존하며 Git/배포에서 제외한다.
실행한 Python 파일 993개를 작업 소스의 LF 정규화본과 바이트 비교해 불일치 0을 확인했다.
최종 문서 수정 후 `check_docs`, S1–S7 `check_architecture`,
`check_contract_coverage --require-complete`도 모두 통과했다. 이 정적 검사는 외부 모델 호출을
검증한 결과가 아니며 실행 회귀와 구분한다.

```sh
# 위 Linux 소스 스냅샷에서 실행한 전체 회귀 명령
.venv-linux312/bin/python -m tests.llm.run_regression --suite llm \
  --output /mnt/d/WorkSpace/ish/tests/llm/reports/agent-models-20261026/final.json
```

### Hub 영향 — 수정하지 않음

사용자의 범위 제한에 따라 `hub/`, `tests/hub/`는 변경하지 않았다.
공개 API 연결 검사는 발견 단계에서 실패했다. 103개 모듈이 같은
`AttributeError: LoopParameters.COMPLETION`으로 import되지 않아 실행 검증까지 진행하지 못했다.
이는 103개 테스트 실행 실패나 Hub 통과를 의미하지 않는다.

- `hub/backend/project/title_values.py`: 제거한 Loop 모델 상수를 import 시 참조하여 시작 차단.
- `hub/backend/project/bootstrap.py`: 모델을 Loop 설정에 쓰는 옛 `update_completion` 경로.
- `hub/backend/execution/engine_selection.py`: Loop effective view와 제거한 모델 조회 API 사용.
- `hub/backend/project/naming.py`: 제목 생성에 사용할 모델을 옛 Engine 설정에서 조회.

Hub는 생성/preset·Agent 모델 선택·모델 표시·제목 생성 경로를 새 계약에 연결해야 한다.
현재 Hub와 함께 그대로 실행 가능한 상태라고 보고하지 않는다. Backend에 Hub 전용 fallback은 없다.
실제 외부 모델/사내 endpoint 호출과 원격 CI는 이번 검증 범위에 포함하지 않는다.

---

## 이전 Tool scope 인계 기록

아래는 이전 변경 당시의 기록이다. 아래 LLM/Hub 통과 수를 위 Agent 모델 설정 분리의
결과로 재사용하지 않는다.

기준 main: `b4bd478f7f8ccc7326a25173e9bbae6398b69bd4` 및 이번 필수 Tool scope 보강.
main과 origin/main이 같은 기준 커밋임을 확인한 뒤 작업했습니다.
리팩터링 전 누적 인계 내용은 [고정 커밋 원문](https://github.com/E1der0wL/ish-llm/blob/b4bd478f7f8ccc7326a25173e9bbae6398b69bd4/docs/llm/handoff.md)에 보존합니다.

## 현재 구현과 이번 변경

- 독립 Project Agent, 수신자별 요청/Run, 병렬 Dispatcher, Session 전송, 최종 Completion 검토와
  Owner별 계약은 현재 main의 구현입니다. [아키텍처](architecture.md)와 [공개 API](backend-api.md)를 따릅니다.
- RunManager가 만든 ToolExecutionScope를 Tool/Loop/Graph/Agent가 공유합니다. 실행 문맥에 scope가
  없거나 타입이 잘못되면 `tool_invocation_invalid`로 거부합니다. 정책 없는 새 객체로 이어가지 않습니다.
- child scope는 부모 scope에서만 축소합니다. 전달받은 명시적 빈 정책 scope도 같은 객체를 유지합니다.
  context 없는 독립 ToolExecutor 호출의 초기화는 유지합니다.
- 승인 저장 후 명시적 resume, 변경된 classification/contract/constraints 검증, 완료 Tool 재사용,
  uncertain effect 보호와 기존 저장 형식은 유지합니다. Hub 제품 코드는 변경하지 않았습니다.

## 확인한 검증 결과

아래 결과는 이번 코드 변경을 포함한 동일 Linux 스냅샷의 실제 실행 결과입니다.
문서 링크/예제 문법 검사는 실행 회귀와 구분합니다. Hub 전체를 다시 실행한 결과로 표현하지 않습니다.

| 검사 | Passed | Failed/Error | Skipped | 범위 |
|---|---:|---:|---:|---|
| 전체 LLM | 2,510 | 0 | 0 | 엄격 발견/실행 검사, 약 2,035초 |
| Hub 공개 API 연결 | 29 | 0 | 0 | 아래 7개 모듈, 약 213초 |

Hub 모듈: `test_approval_continuation`, `test_backend_diagnostics`, `test_coding_skill`,
`test_execution`, `test_multi_agent`, `test_agent_graph`, `test_tool_retry`.
새 `tests.llm.test_tool_scope`는 scope 누락/잘못된 타입, child 권한 축소, 동일 객체 보존,
실제 Run 실패와 durable 승인 재개를 검증합니다.

Python **3.12.14**, LiteLLM **1.106.0**, MCP **2.3.0**, Pydantic **2.14.1**의 CI 고정 의존성을 사용했습니다.
reference ish commit은 `d833790959a76ac457fdeea3f21a6bea1eecfe12`입니다.
이전 환경 설정 시도의 중단 로그와 최종 성공 실행은 구분해 보존했습니다.
로컬 원본 로그·hash: `tests/llm/reports/tool-scope-20261025/` (Git/배포 제외).
문서 정리 후의 링크·정적 계약 재검증은 [이번 문서 검토](documentation-review.md)에 따릅니다.

## 재현 환경과 명령

소스와 Python 환경을 WSL Linux 파일시스템에 둡니다. `/mnt/d`에서 시간 민감 시험을 직접 돌리지 않습니다.
고정 의존성은 `tests/llm/dependencies-linux.lock`, reference host revision 및 필수 OS 패키지는
[CI 정의](../../.github/workflows/tests.yml)가 소유합니다. `ish.platform/`은 별도 참조 체크아웃이며 배포하지 않습니다.

```sh
# 준비된 Linux Python 3.12.14와 reference host로 새 스냅샷 실행
.venv-linux312/bin/python tests/llm/run_linux.py --full
# Hub 전체 검사가 필요할 때
.venv-linux312/bin/python tests/llm/run_linux.py --full --hub

# 이미 Linux 파일시스템에 준비된 소스에서 문서/계약 검사
.venv-linux312/bin/python -m tests.llm.check_docs
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --require S6 --require S7
.venv-linux312/bin/python -m tests.llm.check_contract_coverage --reviews docs/llm/contract-coverage-reviews.json --require-complete --output /tmp/ish-contract-coverage.json
```

이번 검증 스냅샷은 `/home/user/.cache/ish-tool-scope-tvbgpwz2`, Python은
`/home/user/.cache/ish-scope-deps-69rd8deo/.venv-linux312/bin/python`입니다.
이 경로는 이 컴퓨터의 검증 기록이며 제품이 의존하는 경로가 아닙니다.

## 남은 검증 경계

외부 인증 모델·사내 endpoint·장시간 부하·전원 차단/디스크 손상은 이번 자동 회귀의 검증 범위가 아닙니다.
이전 커밋의 CI 성공을 새 커밋의 CI 성공으로 승계하지 않습니다.
운영상 한계와 실제 외부 효과의 원자성 제한은 [운영 준비 상태](production-readiness.md)에 있습니다.
