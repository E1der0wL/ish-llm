# 최신 작업 인계 — 2026-10-26

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
