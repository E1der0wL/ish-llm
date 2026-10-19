# 출시 전 호환 계층 제거 작업 기록

현재 작업은 **R3 안전성 회귀로 중단**했다. R0~R2는 검증·커밋·Push했고, R3~R7은
완료하지 않았다. R3 초안은 외부 증거 디렉터리에 보존하고 제품 소스를 R2로 복원했다.
아래의 로컬 결과를 GitHub Actions 성공으로 간주하지 않는다.

## 브랜치와 보존한 기준선

- 작업 브랜치: Codex 로컬 `work` → 원격 `refactor/llm-backend` (일반 fast-forward Push).
- 기존 Draft PR: <https://github.com/E1der0wL/ish-llm/pull/1>. 새 PR 생성·main 병합 없음.
- `main`: `94229cb865374d99f20ec48c5e48269488041a4e` (변경하지 않음).
- 변경 전 구현: `fafa8ce7f5333da279488c1ac4cac6b7d060a95c`.
- 실제 참조 호스트: `d833790959a76ac457fdeea3f21a6bea1eecfe12`.
  `ish.platform/src/ish`를 사용하며 호스트 수정·추적·커밋 없음.
- Python: 3.12.14. 기존 A~F 보고서와 실패·재검증 이력은 그대로 보존했다.
- R0: `e6feefb` (감사·실제 기준선), R1: `2bd94e53d72fc3b957d82033ad8ad4ce5e86adef`
  (단일 계약).
- R2: `b6b71c76385f15a4c705231764da79bebda42dd4` (typed Provider/Tool 계약),
  원격 `refactor/llm-backend` 동일 SHA를 확인했다. 변경 파일 177개; 정확한 목록은
  `git diff-tree --no-commit-id --name-status -r b6b71c76385f15a4c705231764da79bebda42dd4`로 조회한다.

설계와 전수 감사는 [PRE_RELEASE_CONTRACT.md](PRE_RELEASE_CONTRACT.md),
[COMPATIBILITY_REMOVAL_AUDIT.md](COMPATIBILITY_REMOVAL_AUDIT.md)를 따른다.

## R2 구현

실제 SDK 변환은 `llm/providers/wire_codec.py`에 한정했다. 과거 dict/kwargs와 현재 타입을
자동 판별하던 `model_codec.py`, LegacyMessageView/LegacyRequestSnapshot, marker 감지,
old response projection을 제거했다. 실제 LiteLLM completion/operation Adapter는 유지한다.

Engine·Memory는 `completion_adapter=`, RAG·Vision은 `adapter=`만 받는다.
처리기는 ModelMessage/ModelRequest/ModelResponse를 직접 사용하고, Counter도
ModelRequest를 받는다. SDK callable 주입은 실제 Provider Adapter 내부에만 남겼다.
Recovery의 수정 가능 범위, 사용자 원문·Tool 권한 검사, 부분 스트림 후 재호출 금지,
취소·시간·usage 한도는 유지했다. 진단 분류는 정규 `termination`을 사용하고 원시
finish_reason은 진단 표시 자료로만 보존한다.

Tool.definition/Registry.definitions는 ToolDefinition을 사용하고 prepare는 dict 인자만
받는다. Worker descriptor, Graph/Agent 실행 binding, Hub의 Tool fingerprint도 같은
현재 JSON을 사용한다. 승인·ToolContract·효과 영수증·idempotency 검증은 유지한다.
Hub의 스키마 보강은 typed 정의도 함께 갱신하며 불일치 검사를 우회하지 않는다.

주요 변경 영역은 `llm/providers`, `llm/core/model_protocol.py`, `llm/engines`,
`llm/components/{processing,tools,memory,rag,vision}`, `llm/policies/completion.py`,
Hub completion/recovery/Tool/진단 소비자, 관련 테스트와 실행 예제다.
R2에는 Agent/Run의 구형 실행 API 제거가 아직 포함되지 않았다.

Provider 구형 fixture 3개 테스트를 현재 형식 3개로 교체했다. 완료 응답 재개방,
승인 전 효과 0회·승인 후 1회, 실패 후 기존 효과 영수증 재사용, Graph Step 재사용,
worker descriptor hash를 검증한다. 원본 archive와 내부 파일 hash도 검사한다.
현재 fixture는 수정 소스에서 생성했음을 manifest에 명시하고 파일별 소스 hash를 기록한다.
R5에서 최종 저장 계약의 구현 SHA로 다시 생성한다.

## 실제 검증 기록

명령의 Python은 `.venv-linux312/bin/python`이다. 공통 환경은
`TERM=xterm-256color`, `LITELLM_LOCAL_MODEL_COST_MAP=True`, `LITELLM_MODE=PRODUCTION`,
`HUB_TEST_XVFB=/workspace/.setup/x11/root/usr/bin/Xvfb`,
`HUB_TEST_XCLIP=/workspace/.setup/x11/root/usr/bin/xclip`이다.
실제 Bubblewrap을 실행할 수 있는 Cloud 권한으로 격리 검증을 수행했다.

| 게이트 | 명령 | 실행 | 통과 | 실패 | 오류 | Skip | 시간 | exit |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| R0 LLM | `-m unittest discover -v -s tests/llm -t .` | 1747 | 1747 | 0 | 0 | 0 | 418.481s | 0 |
| R0 Hub | `-m unittest discover -v -s tests/hub -t .` | 534 | 534 | 0 | 0 | 0 | 552.000s | 0 |
| R0 host | `-m unittest -v tests.llm.test_plugin tests.llm.test_tool_packages tests.hub.test_ish_integration` | 44 | 44 | 0 | 0 | 0 | 18.568s | 0 |
| R2 첫 전체 LLM | 동일 LLM 명령 | 1751 | 1746 | 1 | 4 | 0 | 415.941s | 1 |
| R2 첫 전체 Hub | 동일 Hub 명령 | 534 | 530 | 3 | 1 | 0 | 556.818s | 1 |
| R2 첫 전체 host | 동일 host 명령 | 44 | 44 | 0 | 0 | 0 | 18.318s | 0 |
| R2 수정 집중 검증 | `r2-full1-fixes.command.json`의 7개 대상 | 41 | 41 | 0 | 0 | 0 | 2.678s | 0 |
| R2 최종 LLM | 동일 LLM 명령 | 1751 | 1751 | 0 | 0 | 0 | 416.812s | 0 |
| R2 최종 Hub | 동일 Hub 명령 | 534 | 534 | 0 | 0 | 0 | 556.561s | 0 |
| R2 최종 host | 동일 host 명령 | 44 | 44 | 0 | 0 | 0 | 18.517s | 0 |

R2 최종 전체 게이트는 종료 코드 0으로 완료됐고 Python 소스 hash가 전후 일치했다.
X11 9개는 첫 전체 및 최종 전체에서 모두 실제 실행·통과했다. host 44개는 전체 LLM/Hub에 포함되는 중복 재검증이다.
R0 고유 ID 2,281개 → R2 2,285개: 10개 이름/구형 계약을 현재 테스트로 교체하고
4개 부정 테스트를 추가했다. 교체별 보존 불변식은 감사표의 대응표에 기록했다.

첫 전체 LLM 실패는 대체 Adapter의 dict capture/decoder, raw Rerank fixture 3개,
SDK 요청의 명시적 빈 messages 기대값 누락이었다. Hub 실패는 원시 finish_reason으로
분류하던 진단 fixture 3개와 인자 스키마만 바꾼 Tool fixture 1개였다. 현재 계약으로
fixture를 수정했고 assertion·실패 주입·Skip 조건은 제거하거나 완화하지 않았다.
RAG·Vision의 raw completion 결과는 소비·repair 전에 TypeError로 거부하도록 보강했다.

원본 명령·로그·exit·소스 파일 hash·테스트 ID 목록은 Cloud의
`/workspace/compat-removal-evidence/`에 보존한다. `r0-*`, `r2-full-dev1-*`, `r2-final-*`,
`r2-test-id-diff.json`이 전체 게이트의 근거다. 최종 R2 게이트는 llm/hub/tests/examples의
Python 파일 집합과 SHA256을 전후 비교한다. 테스트 실행 당시 소스는 R1 HEAD 위 R2
수정 트리이며, 전체 완료 후 동일 구현을 R2 커밋 `b6b71c7`로 보존·Push했다.

개발 중 실패/중단도 성공으로 처리하지 않았다:

| 로그 prefix | 실제 결과 | 수정/진단 |
|---|---|---|
| r2-operations-dev1 | 12개, 오류 5 | 제거한 image_content를 VisionData가 참조; ContentPart로 이관 |
| r2-operations-dev2 | SIGINT, exit -2 | 같은 Adapter 내부 callable 교체로 캐시 identity가 그대로인 fixture; Adapter 자체 교체로 수정 |
| r2-operations-dev3 | 97개, 오류 1 | NaN 주입을 typed 경계 밖에서 수행한 fixture; 실제 SDK Adapter 안에서 주입하도록 수정 |
| r2-operations-dev4 | 171개, 오류 2 | 구 raw/multivector 기대값과 fixture를 현재 vector/usage 계약으로 이관 |
| r2-operations-dev5 | 31개 통과 | Provider runtime/common operations |
| r2-typed-smoke1 | 77개, 오류 21 | Hub의 typed Tool 정의 동시 갱신 및 typed recovery fixture |
| r2-typed-smoke2 | SIGINT, exit -2 | 제거된 engine.completion_fn에 주입해 취소 대기점 미도달; Adapter 주입으로 수정 |
| r2-typed-smoke3 | 112개, 오류 19 | Hub fingerprint에 ToolDefinition.to_dict 적용 |
| r2-hub-typed4 | 65개, 오류 1 | 제거된 original_messages 참조를 typed messages로 이관 |
| r2-contracts-dev5 | 258개, 실패 1 | diagnostic termination fixture 갱신, raw-only 분류 거부 검증 추가 |
| r2-current-fixtures | 27개, 오류 1 | 존재하지 않는 unittest 클래스 지정으로 loader 오류 |
| r2-current-fixtures-confirm | 27개 통과 | 올바른 클래스명으로 현재 fixture·모델 계약·Hub 지침 확인 |

## R3 안전성 회귀와 중단

사용자의 “핵심 안전성 회귀가 발생하면 중단하고 보고” 조건에 따라 R3 구현을 중단했다.
R3 초안에서 **Agent `policy.max_tool_calls=1`이 Run의 ToolExecutionScope에 적용되지
않는 회귀**가 실제 테스트로 확인됐다. 아직 검증된 R3 커밋은 없다.

원인: `RunAgentResolver.snapshot()`에서 binding의 `adapter` 필드를 제거했지만,
`RunManager._context()`의 정책 합성에는
`if selected.get("adapter") == "agent":` 조건이 남았다. 이 조건이 거짓이 되어 Agent의
`max_tool_calls` 적용과 Project 한도를 넓히지 않는 검사를 건너뛰었다. 해당 실행의
`agent_execution.tool_policy.max_calls`는 기대값 `1` 대신 `None`이었다.

실패한 테스트는 `tests.llm.test_agent_runs.AgentRunTests.test_loop_approval_uses_agent_tool_scope_and_preserves_budget_on_resume`이다.
승인 전 효과 0회, 승인 후 효과 1회, 완료 상태까지의 기존 assertion은 통과했고
그 뒤 Tool 한도 assertion이 실패했다. 이 결과만으로 추가 효과가 실제 발생했다고
주장하지 않는다. 실행 한도를 보장하지 못하므로 다음 단계에 사용할 수 없는 상태다.
assertion 삭제·완화, Skip 추가, 구 API 우회는 하지 않았다.

| 게이트 | 명령 | 실행 | 실패 | 오류 | Skip | 시간 | exit |
|---|---|---:|---:|---:|---:|---:|---:|
| R3 첫 집중 검증 | `-m unittest -v tests.llm.test_agent_domain tests.llm.test_agent_runs tests.llm.test_multi_agent tests.llm.test_multi_run_interleavings tests.llm.test_cross_session_requests tests.llm.test_completion_review` | 63 | 1 | 44 | 0 | 10.854s | 1 |
| R3 안전성 단독 재현 | `-m unittest -v tests.llm.test_agent_runs.AgentRunTests.test_loop_approval_uses_agent_tool_scope_and_preserves_budget_on_resume` | 1 | 1 | 0 | 0 | 0.294s | 1 |
| R2 소스 복원 후 같은 테스트 | 위 단독 명령 | 1 | 0 | 0 | 0 | 0.345s | 0 |

첫 집중 검증의 오류에는 subTest 오류가 포함되므로 63에서 단순 차감해 통과 수를
계산하지 않는다. 주요 오류는 아직 이관하지 않은 Fake Engine의 `for_agent` 누락,
제거한 submit/Request/Session API 사용, AgentComponent import였다. 이는 R3 미완료
항목이며 성공으로 처리하지 않는다. 별도 수동 Echo smoke도 테스트 코드의 잘못된
`output_event` 호출로 Run이 실패해 성공 증거에 포함하지 않았다.

R3에서 수정한 추적 파일 30개와 새 파일 2개는 다음에 보존했다:

- `/workspace/compat-removal-evidence/r3-blocked.patch`
- `/workspace/compat-removal-evidence/r3-blocked-new-files.tar.gz`
- `/workspace/compat-removal-evidence/r3-blocked-manifest.json`
- `r3-domain-dev1-*`, `r3-safety-repro-*`, `r2-restored-safety-*` 명령·원문 로그·exit·소스 hash

패치 SHA256: `bda98d7486b5c6082e2359c43b7b96963f8b587f9139ff551c59feb6b6359526`.
보존 후 이번 R3 수정 파일만 R2 HEAD로 복원했다. 새 파일도 archive/hash를 확인한 뒤
작업 트리에서 제거했다. 제품·테스트 소스는 R2 커밋과 일치하며, 중단 보고 문서만
추가 커밋한다. R3 초안은 원격 브랜치나 Draft PR에 포함하지 않았다.

## 구현 상태와 다음 작업

| 단계 | 상태 |
|---|---|
| R0 | 완료: 원격/기준선 확인, 감사표, 실제 전체 회귀 |
| R1 | 완료: 정규 Provider/Agent/Run/저장 계약 문서 |
| R2 | 완료: typed Provider·Processor·RAG·Vision·Tool 및 직접 소비자 이관, 전체 회귀·Push |
| R3 | **중단·미완료**: Tool 한도 안전성 회귀 재현, 초안 보존 후 제품 소스 R2 복원 |
| R4 | 미완료: Hub Agent 관리·명시 선택·현재 Run 관계 |
| R5 | 미완료: 나머지 현재 fixture/예제/문서 및 최종 잔존 감사 |
| R6 | 미완료: Actions 진단·게이트 분리와 실제 새 CI 실행 검증 |
| R7 | 미완료: 최종 정리된 소스의 전체 회귀 및 출시 전 검토 |

재개 시 첫 수정 대상은 `llm/services/runtime/{agents,runs}.py`의 정책 합성이다.
Agent 한도와 Project 한도의 관계를 discriminator 없이 검증하고, 최초 실행·Resume
모두에서 한도 초과 시 추가 Tool 효과가 0회임을 확인해야 한다. 그 다음
`services/lifecycle/agents.py`, `services/runtime/requests.py`, `services/api.py`,
`core/models.py`, history/Session 저장 계약과 관련 테스트를 이관한다.
Core API와 함께 Hub `backend/{runtime,bootstrap,history}.py`,
`backend/{execution/environment,project/record_management,project/naming,tools/resources}.py`,
`components.py`의 실제 Agent·요청 문맥·단수 Run 사용처도 수정해야 한다.

위험 요소는 Agent 한도 누락, 변경된 binding으로 인한 승인/Resume 무효화, request별
환경을 Agent 정의에 덮어써 발생하는 실행 조건 변경, pending 접수/실제 시작의 원자성,
구형 fixture를 제거하면서 crash/효과 중복 방지 증거를 잃는 것이다. 기존 F01~F14와
C01~C12, 실제 효과 후 재개방 검증을 유지해야 한다. 이 단계들은 아직 완료하지 않았다.

## CI 및 미검증

기존 CI 실패: <https://github.com/E1der0wL/ish-llm/actions/runs/39288390903>,
job `118182322629`, fafa8ce. `Install Linux sandbox runtime`에서 중단됐고 이후 테스트는
실행되지 않았다. RTM_NEWADDR 오류 원문은 사용자 제공 근거다. GitHub 웹 상태는 확인했지만
원문 log URL은 404, REST API는 환경 egress proxy CONNECT 403으로 접근하지 못했다.
새 CI 성공 증거는 아직 없다. 네트워크 격리 해제·process 강등·Skip 확대는 하지 않았다.

실제 인증 모델 호출과 커널 수준 short-write 장애 주입은 미검증이다.
다중 Agent 대화 UI, Session 전송 UI, Plan 시각화, 업무별 Reviewer 신규 구현은 이번 범위 밖이다.
