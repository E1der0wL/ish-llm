# A Provider 전환 검증 기록

H1–H5 및 [P1 최종 로컬 게이트](p1-verification.md)는 완료했다. 현재 A를 진행 중이며,
과거 backend 브랜치의 구현/시험은 읽기 전용 참고다. 이 문서는 A의 변경·실제 실행 증거만
추가한다. [Level 1–3](verification-policy.md)과 [공통 호출 값](model-protocol.md)을 따른다.

## A-c 구현과 A 로컬 Level 3 완료

사용자가 확정한 6항의 재시도 계약을 구현했다. 기존 RetryPolicy를 그대로 사용하고 실제 SDK
전송 사본에서만 생략된 재시도를 0으로 보장한다. 명시 SDK/주입 client 정책·원본 및 저장 설정은
보존하며, Backend 시도 상한과 명시 SDK 정책의 하위 HTTP 총수를 구별한다.

A-a/A-b 공통 값과 codec에 이어 A-c에서 실제 Engine·Processor·Memory·Tool/worker·RAG/Vision·Hub
경로를 연결했다. 독립 wire Adapter의 실제 Tool 효과·승인 후 재시작·opaque/ID pair·완료/usage·
취소·잘못된 Checkpoint 재개 거부를 검증했고 S6 18건을 해결했다. 별도 구형 reader/호환 진입점은
복원하지 않았다. 승인·예산·Tool 효과·Checkpoint·Resume 계약은 완화하지 않았다.

### 전체 게이트 a3-full-2

| suite | 발견/실행/통과 | Failure/Error/Skip | exit | 초 |
|---|---|---|---|---|
| LLM 전체 | 2137/2137/2137 | 0/0/0 | 0 | 753.900492 |
| Hub 전체 | 577/577/577 | 0/0/0 | 0 | 768.991969 |

expected failure/unexpected success/누락/중복/예상 밖 실행은 모두 0이다. R3 Mutation,
S1–S6 정적 감사, 문서, 실제 host 환경 LLM/Hub, offline SDK 2사례의 6개 보조 명령은 모두
실제 exit 0이다. 실제 HTTP/165줄 Graph/RAG probe의 별도 통과와 제한적 증거 재사용 근거는
아래 Level 2 기록을 따른다. 서로 다른 실행을 하나의 전체 실행으로 합산하지 않는다.

실제 명령은 `.venv-linux312/bin/python -m tests.llm.run_regression --suite llm|hub
--output /workspace/llm-clean-resume/a/a3-full-2/<suite>.json`의 suite별 독립 실행이다.
각 전체 명령·실제 exit·요약은 `a3-full-2/<suite>-execution.json`, 보조 명령은
`a3-full-2/aux/execution.json`, 원본 출력은 같은 디렉터리의 log/JSON에 보존했다.

source 713파일 `9ab5a52fa70d7d23f76fb08d242a3e17b911fa8f35d59b1aa9b13b8a61e7b60e`,
input 877파일 `1ed40e7077447f73e045dc23615b026947fa7f2147fad4de9e7216157f48d6bf`는
실행 전후 정확히 같다. 기준 HEAD는 `9716ed4eb19023cca2db182fe7d4b2d0f4c25aa3`에 당시
미커밋 A-c 변경을 더한 상태다. Python 3.12.14/136개 lock 의존성/고정 ish host
`d833790959a76ac457fdeea3f21a6bea1eecfe12`/환경 5개/bwrap/X11 동일성도 확인했다.

전체 실행 뒤 변경은 이 결과 기록·clean-refactor 상태 문장·관련 감사 source pin과 새
`core/embeddings.py`의 파일 끝 빈 줄 제거다. 마지막 정리는 staged diff 검사에서 검출했고,
변경 전후 AST와 같은 파일명으로 compile한 code object의 marshal bytes가 정확히 같음을
`a3-eof-identity.json`에 확인했다. 실행 로직·시험·Fixture·환경은 바뀌지 않았다.

### 원격 확인과 남은 CI 안정화

GitHub API는 Forbidden이지만 공개 GitHub 웹 페이지의 정확한 SHA와 원본 annotation으로
실제 결과를 조회했다. [P1 원격 run 39689439897](https://github.com/E1der0wL/ish-llm/actions/runs/39689439897)은
`81ce84f86246ba8b9470ae4e4e103876c53f255d`에서 LLM 2101 통과, Hub 577개 중 576 통과/1실패였다.
실패는 `SettingsUsabilityTests.test_navigation_and_project_creation_require_a_popup_name`의
프로젝트 제목 `'Named project'` 대비 `'Hub'`다. 과거 로컬 성공 기록은 변경하지 않으며 원격
미확인 상태를 이 확인된 실패로 보완한다. 원본 웹/annotation은 외부 `p1-remote-run.html/.txt`와
`ci-web-preflight.json`에 보존했다.

현재 A-c 전체 Hub 실행에서는 같은 시험도 통과했으나 과거 CI 실패를 무시하지 않는다.
프로젝트 활성화 callback과 UI snapshot은 별도 전달이며 busy 해제만으로 화면 반영 완료를
증명하지 못한다. 동일한 기존 timeout 내에서 새 프로젝트 ID의 snapshot까지 기다리도록 시험을
보강하고 제목/목록/팝업 assertion을 유지했다. 새 Push의 CI에서 Hub 전체를 다시 검증한다. A의 원격
CI 통과는 새 Push의 정확한 SHA와 실제 필수 Job 결과를 확인한 뒤에만 선언한다.

A-c 구현은 `5a5bf257dde9f675c100232fc797eb5e29f67046`으로 로컬 커밋했다. 뒤이은 CI 시험
보강은 `tests/hub/test_settings_usability.py`에 새 프로젝트 ID snapshot 대기만 추가했다.
기존 15초 timeout, 생성 개수·빈 이름 거부·팝업 종료·정확한 제목 assertion은 모두 유지한다.

`a3-ci-focus`에서 settings_usability/creation_contracts/settings 3모듈의 19개를 실행해
발견/실행/통과 19/19/19, Failure/Error/Skip/expected failure/unexpected success/누락/중복/
예상 밖 실행 모두 0, 실제 exit 0, 46.649801초를 확인했다. 명령은
`python -m tests.llm.run_regression --suite hub --modules tests.hub.test_settings_usability
 tests.hub.test_creation_contracts tests.hub.test_settings --output <실행 디렉터리>/hub.json`이다.
정확한 실행 명령/exit는 `a3-ci-focus/hub-execution.json`에 보존했다.

source `683918b4640ce15c5e889baf5370ecb870bf6a1b6dd0b6c50de90509ef357d95`,
input `5d2293f76f607f0a6280fe3bae1a7fe80c6c860ae8f97be646f111287ec90f3e`는
실행 전후 정확히 같다. 기준 HEAD는 A-c 커밋에 UI 시험 보강을 더한 상태다.

제품 runtime·모든 다른 시험·Fixture·의존성·host·환경이 전체 게이트와 같음을 확인했다.
제품 파일 차이는 위에서 compile bytes 동일성을 입증한 EOF 정리뿐이다. LLM discovery 2137과
Hub 577도 유지된다. 로컬 전체 결과는 영향 없는 범위에 한해 재사용하고 변경된 UI 시험은 위
집중 결과로 별도 보완한다. 이 분할 검증을 새 단일 전체 실행으로 표현하지 않는다. 단순 시험
동기화 수정 때문에 같은 제품의 로컬 전체를 반복하지 않으며, Push의 정확한 SHA에서 CI의
전체 LLM/Hub 및 필수 Job을 실행·확인한다. 새 원격 결과 전에는 CI 통과로 기록하지 않는다.

아래는 앞선 검증과 계약 확정 전 실패·중단의 역사 기록이며 새 통과로 소급 변경하지 않는다.

### 확정 계약 Level 2 통과, 전체 게이트 준비

`a3-retry-level2`는 수정한 실제 SDK 경계와 Base/Loop·RAG·Hub 소비자, 신규 실제 HTTP 시험,
독립 Adapter 실제 Tool 효과, 공통 핵심 안전성 및 아키텍처 검사 자체의 시험을 묶었다.

| suite | 모듈 | 발견/실행/통과 | Failure/Error/Skip | exit | 초 |
|---|---|---|---|---|---|
| llm | 22 | 285/285/285 | 0/0/0 | 0 | 83.500679 |
| hub | 6 | 47/47/47 | 0/0/0 | 0 | 67.631149 |

expected failure/unexpected success/누락/중복/예상 밖 실행은 모두 0이다. R3 Mutation,
S1–S6 정적 감사(152개 개별 검토, outstanding/new/stale 0), 문서 검사, offline SDK 2사례는
모두 실제 exit 0이다. 이 선택 실행은 전체 LLM/Hub 회귀 통과로 합산하지 않는다.

source 713파일 `9ab5a52fa70d7d23f76fb08d242a3e17b911fa8f35d59b1aa9b13b8a61e7b60e`,
input 877파일 `5ac3e1b05638271336b573a2cf36bb7d403061e5e619c5f0deae1bd4fd44a60a`는
실행 전후 정확히 같다. HEAD는 `9716ed4eb19023cca2db182fe7d4b2d0f4c25aa3`에 미커밋 변경을
더한 상태다. Python 3.12.14/lock 136개/고정 ish host/환경 5개와 bwrap/X11은 앞선 실행과 같다.
정확한 선택 모듈·명령·exit와 로그는 `a3-retry-level2/*-execution.json`, `aux/execution.json`에 있다.

실제 HTTP/165줄 Graph/RAG probe는 `a3-retry-contract-2`의 별도 통과 증거를 재사용한다.
그 이후 변경은 이 검증 기록, 기존 감사 source pin, `test_rag_models`의 SDK 전송 기대 kwargs
뿐이다. 제품·probe/Fixture·의존성·환경은 hash로 동일함을 확인했고, 전체 discovery는
LLM 2137/Hub 577로 유지된다. 서로 다른 실행을 하나의 전체 실행으로 표현하지 않는다.

### 확정 계약 구현과 수정 중 검증

`providers/retry.sdk_transport_request`가 원본 사본에서만 SDK 전송 옵션을 결정하고,
`providers/litellm.completion/resolve_call`의 실제 동기/비동기 SDK 호출에 적용한다.
기존 RetryPolicy·예산/관찰·취소·부분 응답 경계는 변경하지 않았다. 주입 callable의 시험
경계에는 가상의 SDK 기본값을 넣지 않는다. 실제 SDK mock의 전송 인자만 새 계약으로 수정했고
원본 params/ModelRequest/저장 설정의 생략·명시 null/0/양수와 client identity 검사는 유지/추가했다.

| 실행 | 발견/실행/통과 | Failure/Error/Skip | 실제 exit | 초 |
|---|---|---|---|---|
| a3-retry-contract-1 집중 9모듈 | 115/115/113 | 4/0/0 | 1 | 20.517021 |
| a3-retry-contract-1 실제 HTTP probe | 부분 실행, 캐시 재사용 실패 | 집계 없음 | 1 | — |
| a3-retry-contract-2 집중 12모듈 | 153/153/152 | 1/0/0 | 1 | 36.449316 |
| a3-retry-contract-2 실제 HTTP probe | 전체 probe 통과 | assertion 실패 없음 | 0 | — |

모든 집중 실행의 expected failure/unexpected success/누락/중복/예상 밖 실행은 0이다.
첫 집중 실행의 실패 ID는 2개/subTest 실패는 4개다. num_retries=0을 명시 max_retries/client
옆에 덧붙이면 LiteLLM이 후자의 값을 덮는 문제를 실제 HTTP로 검출해 수정했다. 새 시험에서
num_retries=2의 HTTP를 2회로 예상했던 부분도 설치 SDK 직접 호출로 확인했다. 실제 native
completion은 첫 HTTP 3회 후 wrapper 2회로 총 5회다. 명시된 SDK 값은 보존하되 Backend
SDK 진입은 1회임을 검증한다. 생략 1회/provider 상한 2회의 기존 안전성 기대값은 바꾸지 않았다.
직접 SDK 관찰은 `/workspace/llm-clean-resume/a/sdk-retry-precedence.log`에 보존했다.

첫 probe는 Project 설정 바인딩마다 생긴 기본 Adapter의 객체 identity가 VectorCache key를
바꿔 재사용을 막는 A-c 회귀를 검출했다. `LiteLLMOperations.cache_identity`는 상태 없는 기본
SDK Adapter만 공통 타입 identity를 사용하고, 주입 callable/custom Adapter는 기존 객체와 callable
identity를 유지한다. 호출 생략 assertion을 유지한 두 번째 probe에서 13개 chunk가 모두 warm
cache로 재사용됐다. query cache의 모델/Project/주입 구현 구분 시험도 실행했다.

두 번째 집중 실행은 새 실제 HTTP 시험 5개(생략/명시 상한의 completion·embedding·stream,
명시 SDK no-overlap, 실제 client 보존, 실제 partial HTTP 중단, null/host/client 전송 보존)를
모두 통과했다. 유일한 실패는 기존 RAG SDK dispatch 시험의 exact kwargs에 새 전송 0이 누락된
것이며 기대 kwargs만 수정했다. 실제 probe는 503 2회, strict invalid response 2회, auto의
명시 2시도 후 형식 전환 1회, Graph/RAG CRUD·검색·cache·재시작·증거 재열기를 모두 통과했다.
새 소스의 Level 2 핵심 안전성과 전체 Level 3는 아래 후속 실행으로 별도 확인해야 한다.

- `a3-retry-contract-1`: source `00f4367e08c7bd48e48cb29f0eed3e54da5824d38a9c7a3a984239314f94a709`, input `dad143c14c2fe654204039c9d807c85708ab49d88232a9c8382e972594f30320`.
- `a3-retry-contract-2`: source `3d825ddbaadd875fa2efe0a7dfbe61ebf062049070e5e70b065b4e0bf3b23739`, input `1637e29f095c292f5da721712dffb3ca9b7e4a67836eeac64cd7e4047cb5d89e`.

각 실행의 정확한 명령·실제 exit·원본 로그는 같은 디렉터리의 `*-execution.json`과 log에 보존했다.
Python/host/lock/환경 5개는 위 계약 확정 전 기록과 같고 실행 전후 소스·입력이 정확히 일치한다.
정적 재감사 입력 실행은 기존 source pin 변경으로 exit 1이며, 재검토 후 같은 후보를 다시 검사한다.

## A-c 차단 기록: SDK 내부 기본 재시도와 전송 계약 충돌

2026-10-22 현재 A는 완료되지 않았다. A-a/A-b만 로컬 커밋이며 A-c는 미커밋이다.
작업 브랜치 원격은 `81ce84f86246ba8b9470ae4e4e103876c53f255d`, 로컬 HEAD는
`9716ed4eb19023cca2db182fe7d4b2d0f4c25aa3`다. 실패를 제외하거나 B–F로 진행하지 않는다.

실제 LiteLLM 1.106.0의 비스트리밍 OpenAI 503 경로에서 `provider.max_attempts=2`가
**HTTP 요청 6회**를 발생시켰다. Provider 관찰은 외부 시도 1·2를 정확히 기록했지만 각 SDK
호출 내부가 3회 요청했다. `sdk.num_retries=None`, `sdk.DEFAULT_MAX_RETRIES=0` 상태였다.
설치된 `litellm/llms/openai/openai.py`의 completion 경로는
`inference_params.pop("max_retries", 2)`를 사용하므로 기존 전역 설정만으로 차단되지 않는다.

충돌 근거:

- [Provider retry 계약](../../llm/providers/README.md): 생략된 SDK retry의 기본은 0이며
  명시한 SDK retry에 외부 retry를 겹치지 않는다.
- `tests.llm.test_provider_runtime.RuntimeTests.test_sdk_defaults_and_plain_model_client_are_preserved`:
  실제 SDK 호출을 mock한 kwargs에 생략된 num_retries/max_retries를 추가하지 않는다고 검증한다.
- 사용자 검증 정책 8절: 기존 계약 간 충돌 또는 새 설계 결정이 필요하면 후속 단계로 진행하지 않는다.

권고안은 ModelRequest/저장 설정의 Missing·Null·Value를 유지하면서 실제 SDK 전송에서만
기본 retry=0을 보장하는 현재 Adapter 계약을 명시하는 것이다. 명시한 SDK retry·client는
보존하고 외부 retry 중첩 금지를 유지해야 한다. 입력 보존과 실제 SDK 전송 검사를 분리하되
기존 1/2회 호출 상한·실제 효과 Assertion을 유지하는 방식의 계약 결정이 필요하다.
대안은 실제 retry 경계를 증명할 수 없는 요청을 호출 전에 거부하는 방식이다. SDK 내부
재시도를 허용하도록 기대 호출 횟수를 늘리거나 의존성을 낮추는 변경은 하지 않았다.
아직 이 retry 수정은 제품에 적용하지 않았다.

### 전체 실패와 수정 후 집중 검증

| 범위 | 발견/실행/통과 | Failure/Error/Skip | 실제 exit | 초 |
|---|---|---|---|---|
| `a3-full-1` LLM 전체 | 2132/2132/2115 | 42/4/0 | 1 | 721.640222 |
| `a3-full-1` Hub 전체 | 577/577/576 | 0/1/0 | 1 | 766.458601 |
| `a3-repair-focus-1` LLM 13모듈 | 181/181/181 | 0/0/0 | 0 | 100.626110 |
| `a3-repair-focus-1` Hub shell_environment | 11/11/11 | 0/0/0 | 0 | 15.254142 |

LLM 전체의 실패 ID는 17개이며 subTest의 Failure 집계는 42개다. 모든 실행의 expected
failure/unexpected success/누락/중복은 0이다. 두 전체 suite와 각 집중 실행의 소스/환경
before·after는 정확히 일치한다. 전체 실행 보조 검사 6개(R3, S1–S6, 문서, 양쪽 호스트 환경,
실제 offline SDK 2사례)는 모두 exit 0이었다. 이것을 전체 회귀 통과로 합산하지 않는다.

실제 제품 회귀 2개를 수정했다. BaseEngine은 스트림을 완전히 수신한 후 얻은 실제 usage를
종료 검증 실패에도 확정하되, 중단된 스트림의 unknown usage를 확정하지 않는다. final에서만
제공하는 usage도 계속 반영한다. Memory의 저장 Tool 요약 검증은 제거된 SDK schema 함수 대신
선택 Adapter의 입력 소유권/설정 검증을 사용한다. 6개 자식 프로세스 프로그램의 구 생성자도
실제 Adapter로 이관하여 원래 강제 종료 77·경계 도달·원본/완전한 새 상태·효과 횟수 검사를
그대로 다시 실행했다. 나머지는 공통 요청/Tool 값·정규 종료/원래 종료 증거의 소비자 수정이다.
이 범위는 위 181/11 집중 실행을 통과했지만, 수정 후 전체 게이트는 아직 실행하지 않았다.

### 실제 HTTP probe의 실패 증거

`python -m tests.llm.provider_probe --output <실행 디렉터리>/provider-probe.json`의 실제 exit는
`a3-repair-focus-1`, `a3-provider-probe-2` 모두 1이다. 첫 probe는 과거 synthetic 500 기대값이
현재 오류 분류와 충돌했다. 현재 `requests.error_code`와 `ExtractionModeTests`는 특정 synthetic
500을 invalid_response로 분류하며 명시 auto 모드에서만 JSON 형식을 전환한다. 해당 분류 코드는
A에서 변경하지 않았다. 기존 실패/호출 횟수 Assertion을 실제 HTTP 503에 적용하고 strict/auto
사례를 추가하자 두 번째 probe가 기대 2회 대비 실제 6회를 검출했다.

`/workspace/llm-clean-resume/a/diagnose-http-retries.py`로 독립 재현하여 HTTP 6회, Provider
시도 2회와 SDK 전역값을 기록했다. 재현 스크립트 exit 0은 관찰 완료이며 안전성 통과가 아니다.
원본 `provider-probe.log`, `provider-probe-execution.json`, `diagnose-http-retries.log`를 보존했다.
프로덕션 자격증명 없이 loopback HTTP 서버와 실제 설치 SDK를 사용했다.

- `a3-full-1`: source `72f6eb2d432e0842a078c07d1c1a2f280cca866382b00b4c9d07755cd858570b`, input `20985daf1582500f26d1f4bd07884733e2d8cf36cedb073aefea37f5da7cfeba`.
- `a3-repair-focus-1`: source `11384d0c74ec4ba27d4fe7674cdca14eed189e88fc530b1d714eb4a8e675329c`, input `c9c4b7b6e43963661af7039fedfa64adae88f021a626c1286ce67226bc0d7622`.
- `a3-provider-probe-2`: source `9153dbbbeea1e22e4a3ff86d76c8fa49c1a886391f3f3cd18ceee1cfe46db6a5`, input `2cd2c5e99f55451d8d8f6c0fd6562cdf6ba9f4e314db547363d1af28c224624d`.

집중/전체 실행의 정확한 명령과 실제 exit는 각 디렉터리의 `*-execution.json`에 보존했다.

### 차단 상태에서 마친 안전성 검증

`a3-blocker-safety`는 전체 discovery 2132개 중 영향 범위 13모듈의 191개를 선택했다.
발견/실행/통과는 **191/191/191**, Failure/Error/Skip/expected failure/unexpected success/
누락/예상 밖 실행/중복은 모두 0, 실제 exit 0, 55.688432초다. 승인 저장·누적 사용량과 삭제·
효과 원장·Checkpoint·RAG·수명주기와 삭제·중첩 실행·트랜잭션의 실제 효과/장애 주입 검증에
BaseEngine·Provider runtime 소비자와 아키텍처 검사 자체의 시험을 더했다. 선택 명령은 다음과 같다.

```sh
.venv-linux312/bin/python -m tests.llm.run_regression --suite llm \
  --output /workspace/llm-clean-resume/a/a3-blocker-safety/llm.json --modules \
  tests.llm.test_interaction_storage tests.llm.test_usage_deletion \
  tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity \
  tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity \
  tests.llm.test_lifecycle_deletion_integrity tests.llm.test_nested_graph \
  tests.llm.test_transactions tests.llm.test_architecture_checks \
  tests.llm.test_architecture_reviews tests.llm.test_provider_runtime tests.llm.test_base_engine
```

같은 소스에서 `check_budget_mutation`, `check_architecture --require S1 --require S2
--require S3 --require S4 --require S5 --require S6`, `check_docs`, `check_provider_environment`를
각각 `python -m tests.llm.<검사명>`으로 실행해 모두 실제 exit 0을 확인했다. SDK 환경 검사는
실제 SDK/로컬 HTTP 2사례 통과이며 위 503 재시도 probe를 포함하지 않는다. 정확한 명령/출력은
`a3-blocker-safety/aux/execution.json`과 개별 JSON/log에 보존했다.

작업 경로는 `/workspace/ish-llm`, Python 3.12.14, lock 136개 의존성, ish host
`d833790959a76ac457fdeea3f21a6bea1eecfe12`다. 실행 환경은 `TERM=xterm-256color`,
`LITELLM_LOCAL_MODEL_COST_MAP=True`, `LITELLM_MODE=PRODUCTION`,
`HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb`,
`HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip`이다. source 712파일 hash
`9153dbbbeea1e22e4a3ff86d76c8fa49c1a886391f3f3cd18ceee1cfe46db6a5`, input 876파일 hash
`a873a2ff24a238d47a23725839d3a104b85ebdc193f4dfcb0a3575b6a311b9ec`는 실행 전후 정확히 같다.

이후 이 보고서와 아직 도달하지 못한 probe의 strict/auto 기대값만 정리했다. 기존
`ExtractionModeTests.test_modes_and_bounded_auto_fallback`과 `requests.TRANSIENT`에 따라
strict는 명시한 외부 2시도, auto는 같은 2시도 후 형식 전환 1시도여야 한다. probe의 최초
추가 초안 1/2회를 2/3회로 바로잡았으며, 실제 503의 기존 2회 상한은 그대로다. **이 뒤쪽
실제 HTTP 사례는 앞선 503 실패 때문에 아직 실행되지 않았고 통과 증거가 없다.**
안전성 191개를 SDK 재시도 문제 해결이나 A 완료로 간주하지 않는다.

계약 결정과 실제 HTTP 상한 재검증 전에는 A-c를 커밋/Push하거나 B–F로 진행하지 않는다.
수정 후 집중·핵심 안전성·R3 및 현재 통합 소스의 전체 LLM/Hub 게이트가 필요하다. A의 원격 CI
통과도 선언하지 않는다. main·refactor/llm-backend·Draft PR #1은 변경하지 않았다.

## A-c: 실제 호출 경로 전환 — 통합 검증 진행 중

Base/Loop·Processor·Memory·Tool/worker·RAG·Vision·Hub가 공통 호출 값을 사용한다.
SDK callable/decoder의 Engine·Component 진입점은 실제 Adapter로 이관했다. ToolDefinition의
직렬화 identity와 전체 호출 묶음 사전 검증, after_completion → RESPONDED → Tool 효과 순서는
유지한다. Loop Checkpoint 현재 형식은 v2이며 구형/필수 응답 증거 소실은 자동 복원하지 않는다.
CompletionResult에는 nullable 정규 termination을 필수 필드로 기록한다. 상세 현재 계약은
[공통 모델](model-protocol.md), [처리기](completion-processing.md), [확장 계약](shared-contracts.md)에 있다.

영향 범위는 공통 공개 호출/저장 계약과 모든 실제 소비자이므로 전체 회귀로 승격한다.
아래 집중 실행을 하나의 전체 실행으로 합산하지 않는다. 아직 A-c Level 2 및 A 상위 전체 게이트,
원격 Push/CI 완료를 뜻하지 않는다. main/backend/Draft PR #1은 변경하지 않았다.

독립 wire의 가짜 Adapter를 새로 구현하여 실제 파일 효과·signed opaque/Tool ID pair·Processor
사본·원본 Step 보존·승인 후 재시작·usage 미상 예약·부분 chunk 재시도 금지·취소 슬롯 해제·
명시 decoder 제한·구 Checkpoint/필수 필드 소실의 재개 거부를 검증한다. 과거 backend의 알고리즘과
실패 사례만 선택적으로 참고했고 구 Schema/호환 Reader·과거 통과 증거는 승계하지 않았다.

### 집중 실행 및 수정 증거

모든 실행은 HEAD `9716ed4eb19023cca2db182fe7d4b2d0f4c25aa3`에 당시 미커밋 변경을 더한 소스다.
Python 3.12.14, lock 136개 의존성, 외부 ish `d833790959a76ac457fdeea3f21a6bea1eecfe12`,
동일 환경 변수 5개와 bwrap/X11 바이너리를 고정했다. 각 디렉터리의 source/input before·after는
정확히 일치한다. 아래 hash는 해당 JSON 전체의 SHA256이며 서로 다른 행의 통과를 재사용하지 않는다.
외부 원본 로그/실행 명령/실제 exit/report는 `/workspace/llm-clean-resume/a/` 아래 보존했다.

| 실행 디렉터리 | suite | 발견/실행/통과 | Failure/Error/Skip | exit | 초 |
|---|---|---|---|---|---|
| a3-focus-loop-1 | llm | 발견 실패, 미실행 | 집계 없음 | 2 | — |
| a3-focus-loop-2 | llm | 52/52/52 | 0/0/0 | 0 | 7.998590 |
| a3-focus-consumers-1 | llm | 112/112/103 | 8/1/0 | 1 | 75.866501 |
| a3-focus-consumers-2 | llm | 71/71/71 | 0/0/0 | 0 | 62.091736 |
| a3-focus-operations-1 | llm | 135/135/130 | 2/3/0 | 1 | 24.389179 |
| a3-focus-operations-1 | hub | 22/22/16 | 0/6/0 | 1 | 8.479859 |
| a3-focus-operations-2 | llm | 85/85/85 | 0/0/0 | 0 | 26.983091 |
| a3-focus-operations-2 | hub | 22/22/22 | 0/0/0 | 0 | 13.882317 |
| a3-focus-boundaries-1 | llm | 219/219/214 | 2/3/0 | 1 | 66.790526 |
| a3-focus-boundaries-1 | hub | 43/43/43 | 0/0/0 | 0 | 92.411437 |
| a3-focus-independent-1 | llm | 발견 실패, 미실행 | 집계 없음 | 2 | — |
| a3-focus-independent-2 | llm | 87/87/87 | 0/0/0 | 0 | 31.251731 |
| a3-safety-1 | llm | 192/192/191 | 0/1/0 | 1 | 70.725067 |

실행된 시험의 expected failure/unexpected success/누락/중복은 모두 0이다. 발견 실패는 성공으로
처리하지 않는다. loop-1은 VisionData의 구 import, independent-1은 테스트 편집의 들여쓰기 오류로
실행 전 거부됐다. consumer-1은 구 callable 대입/Processor fixture, operations-1은 구 RAG
주입·가벼운 import 기대·Vision 설정 진단과 Hub Enum key 직렬화 문제를 검출했다. Hub key는
문자열 값으로 직렬화하며 JSON 검증을 완화하지 않았다. boundaries-1과 safety-1은 구 ToolDefinition/
ModelRequest 소비자 및 계수기 fixture를 검출했다. 기존 Assertion 수는 감소하지 않았으며 실제 효과,
입력 보존·예산 차단 검사를 유지했다. 수정 후 재검증은 별도 행으로 기록하고 원래 실패 증거를 보존한다.

새 독립 Adapter 9개와 필수 core9 안전성 116개는 safety-1에서 통과했다. 그 실행 전체는 계수기
fixture 1건 때문에 실패이며 완료로 선언하지 않는다. 현재 수정한 계수기와 SDK retry 필드 단일 선언은
후속 집중/전체 게이트에서 다시 검증한다.

### 소스/입력 동일성

- `a3-focus-loop-1`: source `cafe3f6cb44e8079e416fee30191eee28f7d8e0d75c53458b34037bba148af37`, input `6450535b87b9176eb8f73abeb2936f8226a35490eeb20db5c228132233bc5234`.
- `a3-focus-loop-2`: source `37fbb8e769661fc2e157c74419b4a32ad3a8464c2509e982f47e7cad7f5bfd79`, input `5caeea3a13fc9df3111a8983b94f619eca10d6bb02569122692ba20ae632dc25`.
- `a3-focus-consumers-1`: source `3b63851bff928bcf625e73225f9cbd1297539e5a07fe346ecef1cc26cb7cc5c2`, input `434708f64ae2925b5743e9f0b5f3030ec920a7aa623b35bd01b1873534278698`.
- `a3-focus-consumers-2`: source `45721e230fc782ae084b8d25de9a8fd873aa0d1ac0d2b717cbbe288d9215fe02`, input `30daeeb63be0697cc4b27ac62a880318dc243843c17c04b4e6647f559e374748`.
- `a3-focus-operations-1`: source `cde28340a989a520409fb48dc3433c536eba5f7f5dff868c424283ed43f1668a`, input `5be75bb09b2c1218fee5049a954c71d51e78e52b0e662df6ac324c126d2e82fd`.
- `a3-focus-operations-2`: source `847eaa2aaa1ad243274de90eba391b37d906007dca9118a5cccef66ff7820af8`, input `3888f7bda9bb2b5cd7fe143f28f07a53343d3ebdec3e1888d8770efdb0ff8562`.
- `a3-focus-boundaries-1`: source `874b6cc92b010f14b3ee2e56ada998a7636f04982ee1caa888215f9477997c35`, input `f4e422183b04c1928b0fe0cfd5dfa36b2d4f69ad5d16d4aa019b0899a2ca6a04`.
- `a3-focus-independent-1`: source `7139b34851d84f49f9780d4f9138fa1a32ef5064feac14b3ea8e4e238102409f`, input `080ca2f14fbaa19cf1ef17ddfe58f9c3d3693023f41135fca5ff76b0f10f8f71`.
- `a3-focus-independent-2`: source `8755abb5fd1836ba2e42e6eb414ffff9d995349a45a14ad44ce660e0e61d396f`, input `bbceac537686767f796b2973738c1bae27abcf466e4b3ee2063c423bcb8068da`.
- `a3-safety-1`: source `bae6148bd8b2dd582ba571c4d92b8a464af2d54ac612bded4320a4a5736a657b`, input `a2da25011b0348d9b03545791e6f47b604ead3022dbb3fa3fe3dfcdcc927fe49`.

### 실제 집중 명령

```bash
# a3-focus-loop-1/llm: actual exit 2
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a3-focus-loop-1/llm.json --modules tests.llm.test_model_protocol tests.llm.test_provider_codec tests.llm.test_loop
# a3-focus-loop-2/llm: actual exit 0
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a3-focus-loop-2/llm.json --modules tests.llm.test_model_protocol tests.llm.test_provider_codec tests.llm.test_loop
# a3-focus-consumers-1/llm: actual exit 1
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a3-focus-consumers-1/llm.json --modules tests.llm.test_base_engine tests.llm.test_adapter_boundaries tests.llm.test_extension_contracts tests.llm.test_reasoning_observations tests.llm.test_completion_processing tests.llm.test_memory_processing tests.llm.test_work_state
# a3-focus-consumers-2/llm: actual exit 0
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a3-focus-consumers-2/llm.json --modules tests.llm.test_memory_processing tests.llm.test_work_state tests.llm.test_loop
# a3-focus-operations-1/llm: actual exit 1
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a3-focus-operations-1/llm.json --modules tests.llm.test_rag_models tests.llm.test_provider_runtime tests.llm.test_vision tests.llm.test_rag_components tests.llm.test_rag_query_evidence tests.llm.test_rag_extraction_repair tests.llm.test_schema_ownership tests.llm.test_model_protocol tests.llm.test_provider_codec
# a3-focus-operations-1/hub: actual exit 1
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/a/a3-focus-operations-1/hub.json --modules tests.hub.test_provider_stabilization tests.hub.test_followup tests.hub.test_rag_extensions
# a3-focus-operations-2/llm: actual exit 0
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a3-focus-operations-2/llm.json --modules tests.llm.test_vision tests.llm.test_rag_components tests.llm.test_rag_optimization tests.llm.test_rag_query_evidence tests.llm.test_memory_runtime_choices
# a3-focus-operations-2/hub: actual exit 0
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/a/a3-focus-operations-2/hub.json --modules tests.hub.test_provider_stabilization tests.hub.test_followup tests.hub.test_rag_extensions
# a3-focus-boundaries-1/llm: actual exit 1
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a3-focus-boundaries-1/llm.json --modules tests.llm.test_builtin_tools tests.llm.test_tool_constraints tests.llm.test_tool_packages tests.llm.test_tool_declarations tests.llm.test_components tests.llm.test_test_workspaces tests.llm.test_memory_component tests.llm.test_skill_tools tests.llm.test_architecture_boundaries tests.llm.test_settings_contract tests.llm.test_runtime_reliability tests.llm.test_completion_integrity tests.llm.test_provider_choices
# a3-focus-boundaries-1/hub: actual exit 0
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /workspace/llm-clean-resume/a/a3-focus-boundaries-1/hub.json --modules tests.hub.test_question_tools tests.hub.test_tool_advice tests.hub.test_provider_recovery tests.hub.test_naming tests.hub.test_backend_diagnostics tests.hub.test_failure_diagnosis
# a3-focus-independent-1/llm: actual exit 2
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a3-focus-independent-1/llm.json --modules tests.llm.test_common_provider_acceptance tests.llm.test_tool_packages tests.llm.test_components tests.llm.test_runtime_reliability
# a3-focus-independent-2/llm: actual exit 0
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a3-focus-independent-2/llm.json --modules tests.llm.test_common_provider_acceptance tests.llm.test_tool_packages tests.llm.test_components tests.llm.test_runtime_reliability
# a3-safety-1/llm: actual exit 1
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a3-safety-1/llm.json --modules tests.llm.test_common_provider_acceptance tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_nested_graph tests.llm.test_transactions tests.llm.test_loop_steering tests.llm.test_project_policies tests.llm.test_instruction_reservations
```

## A-b: LiteLLM wire 변환 — 로컬 Level 2

`providers/wire_codec.py`가 공통 message/Tool 정의/request를 SDK wire로 변환하고 SDK의
message/Tool 정의를 공통 값으로 읽는다. signed/opaque 확장, 원래 인자 문자열, multimodal
part와 누락/null/빈 값의 차이를 보존한다. 공통 인자와 원래 문자열의 JSON 타입·값이 같을 때만
원문을 재사용하며 False/0·True/1·int/float를 Python 동등성으로 합치지 않는다. 부분/비유한/
비객체 인자와 잘못된 falsey 호출 배열을 거부하고 opaque가 role·호출 ID·인자를 주입하지 못한다.
알려진 Adapter 상태의 필수 필드 소실과 손상된 content는 기본값으로 복원하지 않는다.

이전 backend wire_codec의 해당 변환 알고리즘을 읽기 전용으로 참고했다. 현재 공통 값과
정확한 상태 검증에 맞춰 구현했으며 파일 통째 복사나 구형 record Reader는 없다. 아직 필요한
소비자가 없는 raw request decoder/호환 진입점은 추가하지 않았다. 기존707개 제품/시험 소스는
A-a와 같고 codec/시험2개만 추가했다. 현재 제품 경로에는 아직 연결되지 않아 Hub 소비자
실행은 배정하지 않았다. 실제 Adapter·Engine·Processor·Tool 연결과 SDK/비 OpenAI 실제
파일 효과·승인·재개 검증은 후속 A에 남아 있다.

| 실행 | 발견/시작/통과 | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 초기 집중 LLM | 22/22/22 | 0/0/0 | 0.019초 | 0 |
| 현재 Level 2 LLM | 161/161/161 | 0/0/0 | 49.440초 | 0 |

expected failure·unexpected success·missing·unexpected·duplicates도 각 실행0이다.
초기22개 통과 후 코드 검토로 Tool 결과의 명시 null 이름과 빈 호출 필드 보존을 보강했다.
초기 소스 hash `3a00aed3e9060734ff1f01bb086d0016434ea4e4381f4604ca795b28154747e3`의 결과는 현재 결과로 재사용하지
않았다. 현재22개 계약 시험과 안전성9개 모듈116개·감사23개를 함께 새로 실행했다. 별도 실행을
합산한 전체 회귀로 표현하지 않는다. 기존 실제 효과·원본 보존·중단/재개 Assertion은 유지했다.

공통 모델 문서의 원문 전체를 보존한 채 별도 Provider 절만 추가했다. 이 문서를 참조하는
기존 S4 리뷰2건은 나머지 pin·사유·시험 ID가 같음을 확인하고 문서 hash만 재검토했다.
새 예외, checker/미해결 목록 변경은 없다. R3 True/exit0, 정적 raw171/reviewed153/
미해결18(S6), S1–S5·new/stale/review오류0/exit0, 문서 오류0/exit0이다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a2-focused/llm.json --modules tests.llm.test_model_protocol tests.llm.test_provider_codec
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a2-level2/llm.json --modules tests.llm.test_model_protocol tests.llm.test_provider_codec tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_nested_graph tests.llm.test_transactions tests.llm.test_architecture_checks tests.llm.test_architecture_reviews
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_budget_mutation
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --output /workspace/llm-clean-resume/a/a2-level2/aux/static.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/a/a2-level2/aux/docs.json
```

현재 실행 HEAD `c9c76d92b491191724494c71302b3301955b1c96`에 A-b 변경을 더했다. source709개 hash
`2853f9b81a23b091b62faca82fd7112e697368309cbef4d8cdcccad7c83f7272`, 입력/환경873개 hash
`c6cf339e09cee4ba6caa9964197cf9ecd901f679e295d8923e5c019d4248b617`가 실행 전후 같다. Python3.12.14·136 의존성·env5개와
host `d833790959a76ac457fdeea3f21a6bea1eecfe12`를 유지했다. 원시 명령/exit/ID/log/manifest·초기/현재 소스와
감사 근거는 `/workspace/llm-clean-resume/a/a2-focused/`, `/workspace/llm-clean-resume/a/a2-level2/` 및 a2 증거에 보존한다.

문서를 먼저 갱신하고 동일 소스·staged diff 확인 후 로컬 커밋한다. **이번 소단계는 전체 회귀와
원격 CI를 실행/확인하지 않았다.** 영향은 아직 연결되지 않은 변환 모듈로 한정되며 다음
연결 소단계의 묶음 Level3 후 Push한다. A 전체·B–F는 미완료이고 S6 18건도 남아 있다.

## A-a: 순수 공통 호출 값 — 로컬 Level 2

`core/model_protocol.py`가 메시지/content/Tool 제안·결과·정의/요청/응답과 role·종료·선택
선언을 소유한다. SDK/영속 도메인 의존성 없이 finite JSON·opaque 복사·nullable 사용량·
완성된 호출 인자·교환 짝과 현재 형식 Reader/Writer를 검증한다. 런타임 client는 호출 사본에서
identity를 유지하고 영속화는 거부한다. nested optional 필드도 Reader에는 필수이며 bool/
float 버전, 구형 wire dict·누락·추가 필드를 기본값으로 복원하지 않는다.

기존 705개 제품/시험 소스는 P1 게이트와 동일하며 새 모델/시험 2개만 추가했다. 실제 import
검색에서 새 모델의 소비자는 새 시험뿐이다. 기존 Adapter·Engine·Processor·Hub는 아직
전환하지 않았다. 따라서 이번 소단계에 Hub 소비자 실행은 배정하지 않았고, 전체 회귀는
후속 연결 소단계의 묶음/A 상위 게이트로 유예했다. 새 값 자체는 실행 권한·원장·외부 효과를
소유하지 않는다. 승인 snapshot과 영속 CompletionResult의 예약/사용량 계약은 그대로다.

이전 backend의 model_protocol 일부 알고리즘과 test_model_protocol/test_common_provider의
실패 시나리오를 참고했다. 파일 통째 복사·호환 투영·과거 통과 증거 승계는 하지 않았다.
현재 owner enum, 정확한 nested 필드, 유한/순환 JSON 거부와 mutable 증거 분리 검증을 구현했다.
비 OpenAI Adapter의 실제 파일 효과·재개 검증은 후속 A에 남아 있다.

| 실행 | 발견/시작/통과 | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 집중 LLM | 11/11/11 | 0/0/0 | 0.009초 | 0 |
| Level 2 LLM | 150/150/150 | 0/0/0 | 49.296초 | 0 |

expected failure·unexpected success·missing·unexpected·duplicates도 각 실행0이다.
Level2는 새 계약11개와 공통 안전성9개 모듈116개, 감사 검사기23개를 실행했다. 새 계약11개는
Level2에서도 실행했으며 두 선택 실행을 합쳐 전체 회귀나 고유 시험 수로 표현하지 않는다.
승인·예산·효과·재개·RAG·삭제·필수 기록·트랜잭션 실제 원본 보존/장애 주입 Assertion을 유지한다.

초기 정적 보고는 `_record`의 정확한 정수/owner 버전 비교2건을 S4 개별 검토 대상으로
탐지했다. 당시 개별 checker exit는 따로 포착하지 못했고 요약을 출력한 외부 shell exit는0이다.
new2/required2였으므로 성공으로 처리하지 않았다. 실제 클래스 선언/호출 관계와 exact-field
검증 및 4개 정확한 시험 ID를 근거로 해당2건만 분류했다. 기존151개 리뷰·checker·미해결
목록은 변경하지 않았다. 새 Level2 runner가 실제 exit를 보존했다. R3 True/exit0,
정적 raw171/reviewed153/미해결18(S6), S1–S5·new/stale/review오류0/exit0, 문서 오류0/exit0이다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a1-focused/llm.json --modules tests.llm.test_model_protocol
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a1-level2/llm.json --modules tests.llm.test_model_protocol tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_nested_graph tests.llm.test_transactions tests.llm.test_architecture_checks tests.llm.test_architecture_reviews
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_budget_mutation
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --output /workspace/llm-clean-resume/a/a1-level2/aux/static.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/a/a1-level2/aux/docs.json
```

HEAD `81ce84f86246ba8b9470ae4e4e103876c53f255d`에 A-a 변경을 더해 실행했다. source707개 hash
`d2e08b5eb1be791d41ba5b4f6116ec768ebb5a4ccf70c17517b546c379a72fa5`, 입력/환경870개 hash
`bf3b8ce69c0118c91d75467a2272dd50a35708916321cbfe72f4952847fabd0b`가 실행 전후 같다. Python3.12.14·136 의존성·env5개,
host `d833790959a76ac457fdeea3f21a6bea1eecfe12`를 유지했다. 명령/exit/ID/log/manifest·초기 정적 실패 보고·
기존 소스 동일성/감사 근거는 `/workspace/llm-clean-resume/a/a1-focused/`, `/workspace/llm-clean-resume/a/a1-level2/`와 a1 증거에 보존한다.

문서를 먼저 갱신하고 동일 소스·staged diff를 확인해 로컬 커밋한다. **전체 LLM/Hub 회귀와
원격 CI는 이번 소단계에서 실행/확인하지 않았다.** 다음은 실제 Adapter와 공통 소비자 연결이다.
A 전체와 B–F는 미완료이며 S6 18건도 남아 있다. 다음 묶음 Level3 성공 후 작업 브랜치에 Push한다.
