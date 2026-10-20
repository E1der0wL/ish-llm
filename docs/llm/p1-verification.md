# P1 구현·검증 기록

P0의 [고정 조합과 최신 허용 조합 검증](p0-sdk-transition.md)이 통과한 뒤
`refactor/llm-clean`에서 진행한다. P1-a에서 누적 Tool 사용량 소실을 재현해 중단한 뒤,
사용자의 보강·재개 요청에 따라 **책임 분리와 필수 상태 복원을 보강하고 P1-a 검증을 완료**했다.
P1-b도 호환 경로 제거와 전체 회귀 검증을 완료했다. P1-c는 계약군별 진행 중이고 A–F는 미착수이며
P1 전체를 완료한 것으로 보고하지 않는다.
main·이전 backend 브랜치·Draft PR #1을 변경하거나 이전 구현을 가져오지 않는다.

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
