# Project Tool worker / observability 검증

Linux Python 3.12.14의 native Linux filesystem snapshot에서 실행한다.
`tests/llm/run_linux.py --full`은 현재 소스를 복사하고 SHA-256 manifest와 focused/suite 로그를 남긴다.

```text
wsl -e /home/user/.cache/ish-provider-sdk-fbmj8dmh/.venv-linux312/bin/python \
  /mnt/d/WorkSpace/ish/tests/llm/run_linux.py --full
```

경로는 이 workstation의 검증 환경이다. 다른 Linux 환경에서는 준비된 Python 3.12.14로
`python tests/llm/run_linux.py --full`을 실행한다. 실제 모델 인증이나 외부 네트워크는 필요 없다.

## 최종 실행 결과 — 2026-10-02

| 검사 | 통과 | 실패 | 생략 | 시간 |
| --- | ---: | ---: | ---: | ---: |
| Focused worker/Tool/provider/observability | 145 | 0 | 0 | 78.572초 |
| 기존 전체 repository suite | 856 | 0 | 0 | 228.015초 |

Focused suite와 기존 suite는 일부 검사가 겹치므로 합산한 고유 테스트 수가 아니다.
실행 환경은 Linux Python 3.12.14이며, native snapshot은
`/home/user/.cache/ish-provider-r34w5ztp`이다. manifest의 Python 파일 269개를
현재 작업 소스 및 실제 Linux snapshot과 각각 SHA-256으로 대조하여 모두 일치했다.
`tests/llm/reports/focused.txt`, `suite.txt`, `snapshot.json`에 실행 근거를 남겼다.
이 결과 문구는 검사 후 문서에 추가했으며 Python 소스는 변경하지 않았다.

## 단계별 검사

### Durable invocation helper 보강 — 2026-10-02

- BaseEngine/EngineContext.execute_tool이 기존 checkpoint key와 Interaction decision을 연결한다.
- ToolExecutor는 직접 호출에도 연결 누락·decision/Tool/인자/binding 불일치를 검사한다.
  잘못된 연결은 handler/worker/Tool Step 시작 전에 tool_invocation_invalid로 거부한다.
- custom Engine ASK → 새 scope 재개 → retry에서 requests=1, executions=2, retries=1을 확인했다.
  일반 decision/pause_before, 거절, 반복·중첩 invocation 경로와 기존 resume 동작을 유지한다.
- 최종 focused **150 passed, 0 failed, 0 skipped (82.960초)**,
  기존 전체 **856 passed, 0 failed, 0 skipped (328.703초)**.
  native snapshot: `/home/user/.cache/ish-provider-nok4fhny`.
  위와 동일한 Linux Python 3.12.14 명령으로 실행했다. 로그/manifest는 reports에 갱신했다.

- Inspector: 기존 signature/default/schema, CRUD, clone/backup, dependency 준비와 backend 오염 방지.
- Execution: 다른 PID, 호출별 global 초기화, 최소 환경, fd print capture, 출력 상한, source/requirements race 거부.
- Cancellation: inspector prepare 취소, 실제 Run interrupt/Tool timeout/backend shutdown, child process group 회수.
- Approval: ASK/DENY 동안 execution 호출 0회, approve/resume 이후 1회. source 변경 시 과거 승인 재사용 거부.
- Error: ToolExecutionError의 effect/retryable, trusted ProviderError의 Step/Run code, unknown/arbitrary code의 tool_failed 수렴.
- Dependencies: prepare만 installer 사용, Run/resolve 설치 금지, 실제 plugin/lib import, backend sys.modules 비오염.
- Observability: retry 요청 1/실행 2/retry 1/최종 실패 0, 승인 재개 중복 방지,
  서로 다른 checkpoint에 같은 Tool/인자가 있어도 별개 요청, operation reuse 실행 0회,
  worker crash의 worker/Tool failure 각 1회, 기존 gauge 합성, thread safety, bounded recent, sink 실패 격리.
- Privacy: prompt/arguments/results/environment 고유 문자열이 snapshot/recent에 포함되지 않음.
- Boundary audit: Project source exec는 child만 소유, Engine은 ToolExecutor를 사용,
  framework SDK 호출은 공통 provider 경계 사용, Component별 telemetry 분산 금지.

Worker는 runtime isolation이다. 테스트는 같은 Linux process group의 descendants를 검사하며,
악의적으로 setsid로 벗어나는 process나 filesystem/network 격리의 보증은 아니다.
Host-owned builtin과 RAG/Memory/MCP handler는 강제로 worker로 옮기지 않는다.

## 유지하는 실행 계약

ToolExecutor가 approval, operation receipt, retry, timeout, result validation, Step 이벤트를 소유한다.
RunManager는 commit 이후 Run 통계를 기록한다. worker는 Run/Step 파일을 쓰지 않는다.
관찰은 실행 결정을 내리지 않으며 SDK 내부 retry 횟수나 child Tool 내부의 임의 네트워크 호출을 추측하지 않는다.
전체 기존 suite에는 queued request, Session 병렬 실행, stale recovery, 승인·중첩 Graph,
저장 트랜잭션과 process interruption 회귀가 포함된다.
