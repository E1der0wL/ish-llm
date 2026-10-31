# Codex 작업 지침서 — 현재 llm 연결 유지와 Hub 요청 지연 개선

작성일: 2026-10-31

작업 위치: `D:\WorkSpace\ish`

목적: 현재 최적화된 llm을 그대로 사용하면서 Hub의 남은 요청·화면 조회 비용을 줄이고, 사용자가 ish에서 체감할 수 있는 상태로 검증한다.

이 문서는 앞으로 수행할 Hub 작업 지침이다. 아래의 개선 후보를 이미 발견된 결함이나 구현 완료 항목으로 해석하지 않는다.

## 1. 작업 범위와 시작 조건

- 주 수정 범위: `hub/`, `tests/hub/`, `docs/hub/`. 필요할 때만 `examples/hub/`.
- `llm/`은 읽기 전용이다. 백엔드의 저장, 검증, 승인, 재개, SDK 호출 구현을 수정하지 않는다.
- `ish.platform/`은 참조·연결 검증용이다. 호스트를 리팩터링하지 않는다.
- 기존 naming/ownership/explicit configuration 규칙을 유지한다.
- 테스트 기록·측정 스크립트는 새로운 `tests/hub/reports/<작업명>/`에 둔다.
- 이번 지침은 커밋·push나 사용자 실제 workspace 변경을 요청하지 않는다.

시작할 때 Git HEAD, branch, status와 실행 소스 경로·해시를 기록한다. 지침 작성 시 HEAD는
`cade23d161c088890e920214a9638b6d8004c1d0`이며, 백엔드 최적화에는 **아직 커밋하지 않은 변경**이 포함되어 있다.
HEAD만 checkout하면 현재 검증한 코드와 다를 수 있다. 최신 상태를 읽고 이미 해결된 기능을 되돌리지 않는다.

기존 수정·미추적 파일을 보존한다. 작업 트리를 깨끗하게 만들기 위한 reset/checkout/clean,
이전 테스트 수정 되돌리기, 전체 파일 덮어쓰기를 하지 않는다. 다른 checkout에서 진행한다면
현재 llm 변경이 실제 포함되었는지 먼저 확인한다. 백엔드 변경과 Hub 변경을 한 성능 비교에서 섞지 않는다.

공통 정적 검사의 검토 자료 갱신이 필요할 경우에만 아래 파일의 **이번에 수정한 Hub 파일에 해당하는 항목**을 갱신할 수 있다.

- `docs/llm/contract-coverage-index.json`
- `docs/llm/contract-coverage-reviews.json`
- `tests/llm/fixtures/architecture_reviews.json`

이것은 검토 증거 갱신에 한정한다. 검사 통과를 위한 예외 추가, 검토하지 않은 항목 일괄 승인,
다른 작업자의 분류·변경 삭제는 금지한다. 그 밖의 llm 코드·테스트 변경이 필요하면 근거를 보고하고 범위를 먼저 확인한다.

## 2. 현재 완료 상태를 먼저 확인한다

다음은 현재 소스에서 확인한 내용이다. 같은 기능을 다시 만들지 않는다.

### Backend

- SDK 사전 준비 경로와 Tool binding 준비 비용 개선이 있다.
- 동일 JSON schema의 문법 검사 결과 재사용이 있다. 실제 설정값·의미·권한·binding 검증은 계속 수행한다.
- 요청 이력 중복 검증과 결과 대기 중 중복 조회를 줄였다.
- record journal은 이미 여러 파일의 쓰기를 하나의 저장 확정으로 묶는다.
- 승인·체크포인트·Tool 효과의 저장 순서와 명시적 재개 계약은 유지된다.

반면 **“실행 배정 + Run 시작”, “초기 체크포인트 + 추가 지시 수신 준비”를 각각 묶어 12회를 10회로 줄이는 설계는 아직 구현하지 않았다.**
10회는 제안 단계의 예상치이며 실측 결과가 아니다.
이번 Hub 작업에서 이 기능을 구현하거나 구현되었다고 가정하지 않는다. 새로운 이벤트 묶음 API도 존재한다고 가정하지 않는다.

### Hub

다음 최적화가 이미 있다. 코드와 동작을 확인하고, 결함이 재현되거나 추가 비용이 측정되는 부분만 보강한다.

- `backend/bootstrap.py::prepare_backend()`: Hub native provider의 명시적 시작 준비.
  `HubConfig.prepare_provider`와 custom Engine factory 조건을 존중한다.
- `backend/runtime.py::submit_input()`: 접수 후 전체 snapshot을 다시 읽지 않고 저장된 요청 ID로 결과를 반환한다.
- `submission_state()`: 유휴 Session은 신선한 공개 Session 자료로 판단하며, 실행 중일 때 공개 runtime 조회를 사용한다.
- `backend/worker.py`: 전경 요청에서 오래된 화면 조회를 취소하고, 수명 이벤트에 맞춰 화면을 갱신하는 경로가 있다.
- `backend/observability/reads.py`, `snapshot.py`, `projection.py`: 관찰 캐시·무효화·변경된 출력의 재사용이 있다.
- `backend/execution/binding_cache.py`: Run 범위의 조건부 binding 재사용이 있다.
- `ui/submitted.py`: 저장된 Run 시작 관찰을 이용한 제출 메시지 표시가 있다.

관련 문서: [관찰과 조회](backend-observation.md), [시작 진행 표시](backend-progress.md),
[렌더링](rendering-performance.md), [요청 이력](request-history.md).
날짜가 붙은 과거 측정값은 당시 결과다. 최신 구현에서 동일 비용이 남았다는 증거로 사용하지 않는다.

## 3. tool_context_chars 테스트 상태

`tool_context_chars`는 Hub가 소유하는 양의 정수 설정이다.
현재 선언된 기본값은 24,000자이며, 모델 전체의 token capacity가 아니다.

현재 Hub 계약:

- general에서 해당 필드가 생략되면 선언된 Hub 기본값을 사용한다.
- 조회가 저장 파일을 다시 쓰지는 않는다.
- 명시적으로 저장한 양의 정수는 보존한다.
- 0, 음수, bool, 실수, 문자열은 거부한다.
- 다른 필수 필드 누락과 unknown field의 엄격한 검증은 유지한다.
- 저장 후 재시작을 통해 처리기에 전달하는 기존 적용 시점을 유지한다.

`tests/llm/test_current_api.py::CurrentPreferencesTests`의 이 계약에 대한 수정은
**현재 작업 트리에 이미 반영되어 있으며 직전 전체 LLM 회귀에서도 통과했다.**
이를 아직 수정하지 않은 실패라고 가정하거나, 모든 누락 필드를 허용하도록 테스트를 느슨하게 만들지 않는다.

먼저 확인할 Hub 테스트:

- `tests.hub.test_global_preferences`
- `tests.hub.test_general_options`
- `tests.hub.test_defaults`

값을 키워 요약을 피하거나, 읽기 과정에서 기존 설정을 자동 저장하거나,
이 기본값을 llm의 기본값으로 옮기는 작업은 하지 않는다.
불일치가 새로 재현되면 현재 Hub 선언과 실제 소비 경로를 근거로 수정한다.

## 4. 먼저 전체 요청 경로를 측정한다

측정 경계는 실제 Hub를 통과해야 한다.

```text
ish 입력 / Enter
  → Hub UI 요청 전달
  → BackendWorker 대기·lock
  → Hub 사전 조회·선택 처리
  → 공개 session.run.submit()
  → llm 접수·실행 준비
  → 실제 LiteLLM HTTP 요청 수신
  → 첫 응답
  → Hub 화면 반영
```

가능하면 같은 monotonic clock 기준으로 다음 관찰 지점을 연결한다.

| 지점 | 기록할 내용 |
|---|---|
| Enter | UI가 원래 입력과 선택한 Project/Session/Agent를 캡처한 시각 |
| Worker | 명령 도착, startup 대기, lock 대기와 실행 시작 |
| Backend API | 공개 submit 진입과 정상 반환 |
| 접수 표시 | 저장된 요청 ID를 UI가 받은 시각 |
| Run 시작 | 영속 Run 시작 이벤트를 관찰한 시각 |
| Provider | 로컬 HTTP fixture가 실제 요청 본문을 받은 시각 |
| 응답 | 첫 모델 delta와 첫 화면 표시 |

이벤트 전달은 비동기이므로 Run 시작 관찰과 UI 접수 callback의 도착 순서를 고정하지 않는다.
식별자로 연결하고, 도착 순서가 바뀌었다고 새 요청이나 중복 실행으로 해석하지 않는다.

추가로 공개 조회 종류·횟수, snapshot 요청·취소·완료 횟수, 실제 모델 요청 횟수,
Tool 노출 수, 입력 크기, UI loop 지연을 기록한다.
저장 호출의 전체 시간을 순수 디스크 읽기나 lock 시간이라고 부르지 않는다.

측정은 다음 두 층으로 분리한다.

1. 실제 BackendWorker + 현재 llm + 로컬 OpenAI-compatible SSE fixture:
   외부 모델 추론 편차 없이 요청 도달과 UI 비용을 비교한다.
2. 실제 ish 화면:
   Enter 직후 반응, 입력 유지, 첫 출력, 중단·재개를 확인한다.
   사내 endpoint에 접근할 수 없다면 실제 사내 모델 검증은 미실행으로 보고한다.

시작 준비 시간, 준비 완료 후 첫 요청, 같은 Session의 반복 요청을 따로 기록한다.
SDK 준비 비용을 앞당겼다면 그 비용을 숨기지 말고 시작 시간과 함께 보고한다.
빠른 “준비 중” 표시를 실제 모델 요청 도달 개선으로 표현하지 않는다.

## 5. 성능 비교 조건

- before/after에서 **동일한 llm 소스**를 사용한다. Hub 변경만 비교한다.
- Python 3.12.14, 동일 SDK 버전, 동일 파일 저장·실제 fsync 조건을 유지한다.
- 모델 설정, 수신 Agent, Tool 목록, 입력·문맥 길이, Memory/RAG·제목 생성·요약 설정을 고정하고 보고한다.
- 별도 테스트 workspace를 사용한다. 사용자의 실제 대화·파일·설정을 바꾸지 않는다.
- before/after를 번갈아 여러 번 측정하고 표본 수·중앙값·범위를 기록한다.
- 프로파일링 실행과 최종 지연 측정은 분리한다.
- 식별자·단계·길이·횟수·입력 포함 여부만 기본 보고한다.
  API key, 전체 환경변수, authorization, 비공개 문서·prompt·provider 원문을 추가 로그에 남기지 않는다.
- 인사, 읽기 Tool을 사용하는 요청, 긴 이력, 활성 Run 중 다음 요청, 취소·재개를 구분한다.
- 주 호출과 제목 생성·요약 같은 보조 모델 호출을 분리해 센다. 기능을 몰래 끄고 빨라졌다고 보고하지 않는다.
- Tool 결과를 전달하는 정상적인 후속 completion과 동일 사용자 요청의 중복 submit을 구분한다.

참고로 직전 **Hub 미포함** native backend 측정은 버전별 반복 요청 10개의 중앙값 1.37 → 1.16초,
fsync 12회·약 0.76초였다. 첫 요청은 버전별 2개로 중앙값 3.10 → 3.31초였으며 개선이 확인되지 않았다.
19개 BuiltinTools를 등록한 별도 fixture이므로 이 수치를 Hub 지연에서 빼서 Hub 비용을 계산하면 안 된다.
또한 현재 환경에서 1초 미만을 보장하는 근거도 아니다.

참고 자료 (저장소 루트 기준 로컬 경로):

- 백엔드 변경·측정 보고서: `tests/llm/reports/backend-boundaries-20261031/summary.md`
- 측정 수치: `tests/llm/reports/backend-boundaries-20261031/latency.json`
- 백엔드 측정 harness: `tests/llm/reports/storage-latency-20261031/backend_probe.py`

이 자료는 로컬 보고서여서 다른 checkout에 없을 수 있다. 없으면 수치를 재현했다고 쓰지 말고
자료 부재를 명시한다. Hub 전체 요청 경로 측정을 backend-only harness로 대체하지 않는다.

## 6. Hub에서 우선 검토할 개선 후보

### A. 요청 전달 전에 화면 준비가 실행을 막는지 확인

전체 이력, 전체 Step/완료 자료, 설정 form/schema, RAG catalog 등 표시용 작업 때문에
submit이 기다리는지 측정한다. 필요하지 않은 표시 준비만 뒤로 옮긴다.
실제 요청 대상·권한·설정 검증을 관찰 캐시로 대신하지 않는다.

### B. 전경 요청과 snapshot 조회의 경합 축소

전송·중단·승인 같은 전경 동작이 불필요하게 이전 화면 조회 뒤에서 기다리는지 확인한다.
기존 preemption을 재사용하고, 취소 대상을 UI 관찰 Task로 한정한다.
받아들인 저장 작업은 backend의 cancellation drain을 따르며 강제로 끊지 않는다.
일반 delta마다 읽기를 취소해 영원히 화면이 갱신되지 않는 구조도 만들지 않는다.

### C. 실제로 중복되는 공개 조회만 합치기

동일 자료를 여러 화면 요소가 동시에 반복 조회한다면 기존 ObservationReads와
무효화 구조 안에서 재사용하거나 진행 중 조회를 공유하는 방안을 검토한다.
한 대기자의 취소가 다른 대기자나 backend 실행을 취소하지 않도록 한다.
진행 중 읽기보다 최신 이벤트가 도착하면 오래된 결과가 다시 덮어쓰지 않아야 한다.

관찰 캐시는 화면용이다. 접수, 승인, resume, retry, Tool 실행 허용의 근거로 사용하지 않는다.
Run/Session/Project 변경, terminal 상태, 이벤트 누락, 정의·설정 변경을 올바르게 반영한다.
공개 API로 변경을 확인할 수 없는 자료를 영구 캐시하지 않는다.

### D. 기존 SDK 준비와 binding 재사용 확인

새 prewarm worker나 두 번째 cache를 만들지 않는다.
사용자가 끈 preparation을 켜거나 custom provider/Engine에 native SDK 준비를 강제하지 않는다.
기존 binding cache의 Run 범위·정의/handler/env/cwd/설정 식별과 무효화를 유지한다.
최적화를 이유로 backend 내부 registry나 준비 객체를 직접 수정하지 않는다.

### E. 실제 표시 반응 확인

로컬 “준비 중”과 영속 “접수됨/실행 중”을 구분한다.
전체 snapshot을 기다리지 않아도 전달할 수 있는 기존 접수·Run 시작 관찰을 재사용한다.
오래된 callback은 다른 Session에 표시하지 않으며, 접수 실패 시 입력 초안을 보존한다.
성능 개선을 위해 출력·reasoning·승인 요청을 누락하거나 잘라내지 않는다.

모든 후보를 반드시 수정할 필요는 없다. 이미 해결되었거나 비용이 작으면 근거와 함께 유지한다.
확인되지 않은 지연 원인을 가정해 broad refactor를 하지 않는다.

## 7. 반드시 유지할 경계

- Project → Session → Run → Step와 실제 scheduling·persistence는 llm 소유다.
- Hub의 “준비 중”은 UI 안내다. durable QUEUED 이전에 접수 성공이라고 표시하지 않는다.
- 정책 승인 응답 저장과 실제 실행은 별개다. 공개 API의 명시적 resume을 유지한다.
- 실패·중단·paused Run을 속도 개선 명목으로 자동 submit/restart하지 않는다.
- 완료 Tool 결과, operation receipt, 불확실 효과, changed binding/constraints 보호를 우회하지 않는다.
- 재개 실패 시 새 요청으로 몰래 바꾸지 않는다.
- Provider retry와 분류는 backend 소유다. Hub에 별도 전역 retry loop를 만들지 않는다.
- 캐시는 관찰/유도 자료만 소유한다. 새로운 Run 로그·대화·승인 source of truth를 만들지 않는다.
- Agent의 모델 설정과 Engine 실행 설정을 섞지 않는다.
- 앱 preset은 Hub가 명시적으로 선택·저장한다. 기존 Project에 새 값을 자동 주입하지 않는다.
- fsync를 mock/no-op 처리하거나 memory storage로 바꾸어 성능 개선으로 보고하지 않는다.
- Step/승인/체크포인트 기록을 생략하지 않는다.
- 라이브러리 native 호출이나 새로운 llm private API로 우회하지 않는다.
- 타이밍만 맞추기 위한 고정 sleep, 임의 재시도 증가, 새로 숨긴 cache/갱신 한도를 추가하지 않는다.
- 새 튜닝값이 정말 필요하면 기존 Hub 설정 Owner·schema·UI·저장 계약에서 명시적으로 다룬다.

## 8. 예상 확인·수정 위치

필요한 파일만 수정한다. 다음 목록은 감사 지도이며 전부 수정하라는 뜻이 아니다.

| 위치 | 확인할 책임 |
|---|---|
| `hub/ui/input/submission.py`, `hub/ui/requests.py`, `hub/ui/submitted.py` | Enter, 요청 전달, callback, 로컬 안내와 영속 상태 |
| `hub/backend/worker.py` | 전경 명령·snapshot 경합, 취소·종료, UI loop 전달 |
| `hub/backend/runtime.py` | 공개 submit 연결, 사전 조회, 현재 Session/Agent 선택 |
| `hub/backend/observability/reads.py`, `snapshot.py`, `projection.py` | 조회 공유·무효화·재조정·화면 자료 구성 |
| `hub/backend/bootstrap.py`, `execution/provider_adapter.py` | 기존 SDK 준비 경로 |
| `hub/backend/execution/binding_cache.py`, `environment.py`, `engine_selection.py` | 실행 준비·표시용 선택의 중복 비용 |
| `hub/config/general.py`, `preference_records.py`, `widget/forms/general_settings.py` | tool_context_chars의 현재 계약과 UI 적용 |
| `tests/hub/`, `docs/hub/` | 해당 회귀와 실제 결과·사용법 |

## 9. 단계별 검증

1. 현재 Git/소스 상태와 tool_context_chars 테스트 수정 여부를 확인한다.
2. 현행 Hub baseline을 측정한다. 아직 수정할 필요가 없는 후보를 구분한다.
3. 확인된 Hub 병목을 작은 단위로 수정하고 관련 집중 검증을 실행한다.
4. 동일한 llm·설정·요청으로 before/after를 다시 측정한다.
5. 실제 ish 연결을 확인하고, 최종 Hub 코드의 전체 Hub 회귀를 한 번 실행한다.
6. 결과·한계·수동 테스트 방법을 보고한다.

Linux Python 3.12.14와 Linux 파일시스템의 소스 스냅샷·workspace·환경을 사용한다.
타이밍 검증을 /mnt/c 또는 /mnt/d의 실행 workspace에서 하지 않는다.
기존 해시 기반 snapshot 도구와 [검증 계층 정책](../llm/verification-policy.md)을 재사용한다.
원본 snapshot/report를 사후 편집하지 않는다.
이번 범위의 사용 준비 검증을 최종 병합·배포의 Level 3 전체 통합 완료로 보고하지 않는다.
승인·저장·공통 API의 의미를 바꿔야 하거나 영향 범위가 불명확하다면 작업 범위와 검증 계층을 다시 확인한다.

현재 존재하는 관련 집중 모듈:

```text
tests.hub.test_global_preferences
tests.hub.test_general_options
tests.hub.test_defaults
tests.hub.test_provider_preparation
tests.hub.test_submission_latency
tests.hub.test_submission_observation
tests.hub.test_observation_reads
tests.hub.test_low_resource_observation
tests.hub.test_run_path_speed
tests.hub.test_http_backend
```

모듈 존재 여부를 다시 확인하고 변경 범위에 맞는 추가 승인·재개·출력·ish 연결 테스트를 선택한다.
필수 케이스:

- 실제 HTTP 요청에 현재 사용자 입력과 선택한 Agent가 정확히 포함됨.
- 중복 Enter/callback/재조정으로 같은 요청이 두 번 전송되지 않음.
- 접수 전 오류, 접수 성공 후 화면 조회 오류, 접수 중 취소가 구분됨.
- 승인 전 Tool 효과 0회, 명시적 재개 후 효과 1회.
- 중단·재개에서 완료된 Tool 효과 재실행 없음.
- 오래된 snapshot이 최신/terminal 상태를 되돌리지 않음.
- 이벤트 누락 후 공개 서비스로 화면 재조정.
- Session/Project 전환 중 늦은 callback이 다른 화면에 섞이지 않음.
- 조회 대기 취소가 Run/Tool을 취소하지 않으며 종료 시 관찰 Task를 회수함.
- 설정 저장·재열기·재시작, 잘못된 tool_context_chars 거부.

예를 들어 **검증된 Linux source snapshot을 cwd로 하여** 기존 통합 test runner를 사용할 수 있다.
보고서 경로는 새 작업 디렉터리의 절대 경로로 전달한다.

```sh
python -m tests.llm.run_regression --suite hub --modules \
  tests.hub.test_global_preferences tests.hub.test_general_options \
  tests.hub.test_defaults tests.hub.test_provider_preparation \
  tests.hub.test_submission_latency tests.hub.test_submission_observation \
  tests.hub.test_observation_reads tests.hub.test_low_resource_observation \
  tests.hub.test_run_path_speed tests.hub.test_http_backend \
  --output /ABSOLUTE/NEW-HUB-REPORT/focused.json

# 최종 Hub 변경 후 1회. --suite hub이므로 전체 llm suite는 실행하지 않는다.
python -m tests.llm.run_regression --suite hub \
  --output /ABSOLUTE/NEW-HUB-REPORT/hub.json
```

위 경로는 placeholder이므로 실제 새 보고서 경로로 바꾼다. 테스트 이름·옵션은 현재 runner에서 검증한다.
관찰/연결을 수정한 경우 필요한 llm 공개 API 관련 집중 테스트도 선택할 수 있다.
llm 코드를 변경하지 않았는데 전체 LLM 2,679개를 기계적으로 다시 실행하지 않는다.
추가 code change·실패·새로운 우려가 없다면 같은 전체 suite를 재실행하지 않는다.
새 안전성 우려로 범위를 넓힐 필요가 있으면 구체적 이유를 먼저 알린다.

## 10. 실제 ish에서 사용자가 확인할 절차 제공

사용자가 기존 ish 실행 설정으로 새 Hub 코드를 적용할 수 있도록 정확한 실행 소스 경로와
재시작 필요 여부를 안내한다. 실행 중인 worker를 종료하지 않은 채 코드만 바꿔 놓고
새 코드로 테스트했다고 보고하지 않는다.

별도 테스트 workspace에서 다음을 확인할 수 있게 준비한다.

1. 시작 진행 표시가 끝난 뒤 첫 인사 요청.
2. 같은 Session에서 반복 인사·읽기 요청.
3. 허용된 테스트 파일 읽기 Tool 요청.
4. 긴 이력에서 입력·스크롤·첫 출력 반응.
5. 승인 필요한 안전한 임시 파일 작업: 승인 전 변경 없음, 재개 후 한 번만 변경.
6. 출력 중 중단과 명시적 재개, Session 전환.
7. tool_context_chars 변경·재시작·재열기.

사내 인증 정보나 원문을 지침서·보고서에 넣지 않는다.
실제 ish/terminal 또는 사내 endpoint를 사용할 수 없으면 무엇을 확인하지 못했는지 적고,
모의 PTK/HTTP fixture 통과를 실제 사내 검증 성공이라고 쓰지 않는다.

## 11. 최종 보고

다음을 실제 결과로 보고한다.

1. 작업 전후 HEAD와 uncommitted diff, 실제 테스트/실행 source snapshot.
2. 현재 llm 변경이 보존되었는지와 llm 실행 코드의 변경 여부.
3. 이미 해결되어 유지한 항목, 재현한 Hub 병목, 실제 수정한 파일.
4. tool_context_chars의 선언·테스트·전달 경로와 기존 수정 유지 여부.
5. 각 측정 경계의 before/after, 표본 수·중앙값·범위, cold/warm 구분.
6. 공개 조회·snapshot·실제 모델 호출 횟수와 UI 지연 변화.
7. 승인·재개·중복 실행 방지 검증 결과.
8. 집중/전체 Hub 테스트의 passed/failed/errors/skipped와 미검증 환경.
9. 실제 ish 실행 여부와 사용자가 그대로 따라 할 확인 절차.
10. 남은 지연이 Hub, backend storage, 준비, endpoint, 화면 중 어디인지와 미확정 부분.

현재 백엔드의 최근 근거는 `tests/llm/reports/ish-provider-771onlyj/`에 있다:
전체 LLM 2,679 passed, 0 failures/errors/skipped; 별도 집중 189 passed 중 Hub 공개 API 14개.
이는 **당시 백엔드 검증 결과**이며 이번에 변경한 Hub 전체가 검증되었다는 뜻이 아니다.

## 12. 완료 조건

- 현재 llm을 변경하거나 검증을 우회하지 않고 Hub가 연결된다.
- 이미 존재하는 준비·캐시·진행 표시를 중복 구현하지 않는다.
- 실제 확인된 Hub 비용만 줄이고, 코드 변경 없이 측정만으로 충분한 항목은 유지한다.
- 요청 원문과 수신 대상이 보존되고 중복 전송·Tool 중복 실행이 없다.
- 접수·승인·재개·취소·저장 오류의 의미가 유지된다.
- 기존 사용자 설정과 파일을 자동 변환하거나 덮어쓰지 않는다.
- 현재 Hub 변경의 검증과 정확한 실행/측정 근거가 남는다.
- 유의미한 개선이 없거나 남은 병목이 backend/endpoint라면 그대로 보고한다.

이번 목표는 숫자를 맞추기 위한 기능 생략이 아니다.
**현재 backend 보장을 유지하면서 Hub의 불필요한 준비·조회·화면 대기를 줄이고,
사용자가 실제 ish에서 결과를 확인할 수 있게 하는 것**이다.
