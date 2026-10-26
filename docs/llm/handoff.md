# 최신 작업 인계 — 2026-10-26

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
