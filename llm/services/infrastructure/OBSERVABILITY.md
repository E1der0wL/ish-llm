# Host observability

`backend.observability.snapshot()` / `await backend.observability.asnapshot()`은
현재 backend 수명 동안의 읽기 전용 운영 projection이다. 모델/Tool 호출, dependency 준비,
파일 변경을 하지 않는다. 재시작 시 누적 통계는 초기화된다. 도메인 복구나 실행 결정에 사용하지 않는다.

```python
snapshot = backend.observability.snapshot()
print(snapshot["runtime"])    # active_runs, queued_requests, unfinished_work
print(snapshot["tools"])      # requests, executions, retries, reused, approval_required, failed
print(snapshot["providers"])  # active, waiting, calls, completed, failed, cancelled, retries
print(snapshot["workers"])    # spawned, completed, failed, protocol_failed, cancelled
print(snapshot["events"])     # delivered, dropped, failures, timed_out, pending
```

## Ownership

| 사실 | 계측 경계 |
|---|---|
| Run 시작/종료 | RunManager가 domain commit 성공 이후 기록 |
| 논리 Tool 요청/실행 시도/재사용 | ToolExecutor |
| non-streaming SDK 호출 | providers.observations / requests |
| streaming SDK 호출 및 outer retry | providers.litellm / BaseEngine의 공통 stream bridge 호출부 |
| 이벤트 전달 | EventSubscriptions |
| inspector/execution worker 상태 | Project Tool process adapter |

Component별 record 호출을 추가하지 않는다. 공통 ComponentData.model_scope는 Host observer를
provider boundary로 전달만 한다. Run의 중첩 Engine/Agent도 같은 문맥을 사용한다.

현재 provider active/waiting은 ProviderCalls.stats, 이벤트 통계는 EventSubscriptions.stats,
Run/queue/unfinished 값은 기존 manager runtime에서 snapshot 시 읽는다. 독립 gauge를 만들지 않는다.
snapshot은 순간별 projection이며 여러 owner를 하나의 트랜잭션으로 잠근 일관된 도메인 snapshot은 아니다.

EventSubscriptions는 활성/정리 중인 구독 통계와 종료된 구독의 숫자 합계를 합성한다.
종료 때 한 번만 합산하고 구독 객체·콜백·worker·큐를 보관하지 않는다. 전달 통계의
소유자는 그대로 EventSubscriptions이며 Observability에 중복 counter를 추가하지 않는다.
해제 후에도 개별 Subscription.stats는 마지막 통계를 제공한다. pending은 살아 있는
알림 큐에서 읽고, 명시적 해제로 버린 대기 알림은 overflow dropped나 delivered로 세지 않는다.

## Counter 의미

- tools.requests: 논리 호출. 승인 후 재개는 기존 checkpoint/approval 예약으로 식별해 중복 집계하지 않는다.
  Loop/Graph는 해당 invocation의 정확한 checkpoint key를 ToolExecutor에 전달한다.
  확장 Engine은 `BaseEngine.execute_tool(context, ..., checkpoint_key=...)` 또는
  `context.execute_tool(..., checkpoint_key=...)`을 사용한다. helper가 기존 key와
  waiting Tool approval의 Interaction 선택지에 연결된 decision만 ToolExecutor에 전달한다.
  직접 호출 시에도 ToolExecutor가 durable 승인 연결을 검증한다. 누락/불일치는
  `ToolInvocationError(code="tool_invocation_invalid")`이며 handler/worker/Tool Step 시작 전에
  실패한다. 이는 관측 장애가 아닌 Engine 호출 계약 오류로, observer 없이도 검증한다.
  key 없는 일반 호출은 허용한다. Graph pause_before 확인은 Tool 승인/중복 집계 근거가
  아니며, 해당 checkpoint의 확인 decision을 Tool 승인으로 전달하면 거부한다.
  확인 후 ToolPolicy.authorize가 ASK하면 새 논리 Tool 요청 1건으로 기록하고,
  그 Tool approval을 재개할 때는 같은 요청으로 센다. 같은 Tool/인자의 과거 승인 후보로
  키를 추측하지 않으며 모호한 durable 재개는 거부한다. key는 승인 권한 자체가 아니다.
- tools.executions: 실제 handler/runner 시도 시작 수. retries는 그중 첫 시도 이후의 추가 실행 수다.
- tools.reused: operation receipt 결과 재사용. handler/worker는 실행하지 않는다.
- tools.approval_required: Tool invocation이 ASK로 일시정지한 수다.
- tools.failed: 최종 논리 호출 실패. retry 중간 실패는 포함하지 않는다.
- workers.spawned: inspector와 execution worker 시작 수의 합. recent.name으로 inspect/execute를 구분한다.
- 정상 error envelope를 반환한 child는 healthy completed이며, Tool 실패는 ToolExecutor가 기록한다.
- providers.calls: 공통 호출 경계로 들어간 outer attempt 수. admission 실패도 포함할 수 있다.
- providers.retries: ish outer retry 결정 수. SDK 내부 HTTP retry는 관찰할 수 없어 추측하지 않는다.
- failures_by_code: 각 경계의 실패 사실 수. 하나의 worker 장애가 Tool/Run도 실패시킬 수 있으므로
  이 합은 독립 장애 건수가 아니다. 각 runs/tools/workers counter는 자기 경계에서만 증가한다.

완료 checkpoint를 재사용하여 ToolExecutor에 진입하지 않는 Engine 결과 복원은 새 Tool 요청이 아니다.
backend 재시작 전의 요청/승인은 누적 통계로 복원하지 않는다. 과거 실행은 도메인 API로 조회한다.

## 안전한 관찰

record(fact) 하나에서 counter, latency(count/total_seconds/max_seconds), stable failure code,
최근 이벤트를 파생한다. threading lock으로 집계를 보호한다. 원시 latency sample은 보관하지 않는다.
recent는 최대 256개, failure-code key는 최대 128종 및 other다. ID는 recent의 값일 뿐 aggregate key가 아니다.
prompt, arguments/results, source/requirements, 원문 evidence, 환경, traceback 필드를 받지 않는다.

`ServiceConfig(observability_sink=callback)`은 정규화된 안전한 fact 사본을 받을 수 있다.
callback은 thread-safe이고 빠른 동기 함수여야 한다. core는 디스크/네트워크에 기록하지 않으며
외부 exporter는 별도 adapter 책임이다. callback 예외는 Run/Step 결과에 영향을 주지 않는다.

UI는 drop 통계를 보고 저장된 Run/Step/output cursor를 다시 조회해야 한다.
metrics/recent는 승인 기록, Tool 영수증, 저장된 실행 결과를 대체하지 않는다.
