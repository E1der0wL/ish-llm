# P1 구현·검증 기록

P0의 [고정 조합과 최신 허용 조합 검증](p0-sdk-transition.md)이 통과한 뒤
`refactor/llm-clean`에서 진행한다. P1-a에서 누적 Tool 사용량 소실을 재현해 중단한 뒤,
사용자의 보강·재개 요청에 따라 **책임 분리와 필수 상태 복원을 보강하고 P1-a 검증을 완료**했다.
P1-b/c와 A–F는 미착수이며 P1 전체를 완료한 것으로 보고하지 않는다.
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

이 추출 코드는 이번 P1에서 변경하지 않았고 P0 커밋 `2284464`에도 동일하게 존재한다.
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
