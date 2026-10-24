# Runtime — Run 실행과 이벤트 조율

대기 요청을 실제 Run으로 실행하고, Engine 이벤트를 영속 기록·관찰 알림으로 연결합니다. 승인, Tool 실행, 체크포인트, 사용량 제한의 실행 경계도 이곳에 있습니다.

`submit(..., recipients=..., request_context={...})`는 선택한 Agent binding과 선택적 요청
문맥을 검증·복사하여 QUEUED 메시지에 저장합니다. 생략한 문맥은 생략 상태로 유지합니다.
Run 시작과 재개는 저장된 Agent 정의와 문맥으로 실행별 Engine을 연결하고 capability를
검사합니다. RunManager는 Workflow ID의 의미를 해석하지 않습니다. Agent 정의의
engine_options와 요청별 request_context는 서로 다른 계약입니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | 런타임 서비스 패키지 설명입니다. |
| [runs.py](runs.py) | RunRepository/RunManager와 Run 수명 알림. 큐, 시작·종료, 중단과 명시적 재개를 조율합니다. |
| [events.py](events.py) | 이벤트 처리기 등록, EventContext, 동기·queued 구독과 구독 자원 회수입니다. |
| [checkpoints.py](checkpoints.py) | Engine 체크포인트 저장·조회·명시적 정리와 삭제 표식 검증입니다. 승인 증거는 보존합니다. |
| [interaction_records.py](interaction_records.py) | 단일 control Record Reader, 조회 projection, retry 요청 Writer와 저장 경로를 소유합니다. |
| [interactions.py](interactions.py) | 승인·확인 요청과 사용자 응답의 영속 검증·저장입니다. |
| [steering.py](steering.py) | 추가 지시의 대상 등록·마감, 메시지·중첩 체크포인트 참조, 대상별 반영 기록을 연결합니다. |
| [tool_errors.py](tool_errors.py) · [tool_identity.py](tool_identity.py) | 기존 Tool 오류/승인 신호와 논리 호출 identity의 단일 정의입니다. |
| [tool_retry_records.py](tool_retry_records.py) · [tool_approval_records.py](tool_approval_records.py) | 재시도 상태 Reader/Writer와 승인 action/source projection입니다. 추가 재시도나 실행 허가를 만들지 않습니다. |
| [tools.py](tools.py) | ToolExecutor와 Tool 실행 범위. 승인, 실행, 재시도, 결과·Step 이벤트를 담당합니다. |
| [operations.py](operations.py) | Session 범위 Tool operation 원장과 완료 결과 재사용·불확실 효과 조정입니다. |
| [processes.py](processes.py) | 명시적으로 선택한 ProcessToolRunner의 입장·실행·출력·취소 수명을 관리하며 격리 인자는 LinuxSandbox에 위임합니다. |
| [_worker.py](_worker.py) | ProcessToolRunner의 내부 child 진입점입니다. 공개 Engine이나 UI 실행 파일이 아닙니다. |
| [policies.py](policies.py) | 공통 정책을 ContextPolicy/RunPolicy로 해석하고 usage 계산기 등록을 확인합니다. 모델별 입력 정책·재시도는 생성하지 않습니다. |
| [output.py](output.py) | 출력 델타의 저장 batching, 최종 출력과 조회 journal을 관리합니다. |
| [pending.py](pending.py) | 취소 후에도 남을 수 있는 비동기·동기 작업의 수명 추적입니다. |
| [usage.py](usage.py) | Project 모델 사용량 예약과 명시적 한도 적용입니다. |

## 요청과 종료

요청은 먼저 ConversationStore에 QUEUED로 저장합니다. `_begin`에서 COMMITTED와 Run 시작을 처리한 뒤 Engine을 소비하고 `_finish`에서 종료합니다. Step·Assistant·Run·Session 기록과 트랜잭션 경계를 거친 **저장 후** Run 수명 알림을 보냅니다.

Run 시작 시 저장한 정책이 해당 Run의 정책 원본입니다. `EventContext.update_metadata()`는 정책·완료 영수증·재개/Checkpoint·추가 지시·Agent binding/요청 문맥·실행 증거 등 서비스 관리 필드를 덮어쓰지 못하게 합니다. 실제 보호 집합은 EventContext의 선언과 각 필드 Owner를 따릅니다. 사용자 정의 메타데이터는 별도 키로 저장합니다.

RunHandle의 `checkpoint_plan → abandon_resume → checkpoint_plan → delete_checkpoints`는 대화를
삭제하지 않는 명시적 정리 경로입니다. 완료 Run은 포기를 생략합니다. 실제 차단 조건과 보존 범위는
[체크포인트 공통 계약](../../../docs/llm/checkpoints.md)을 따릅니다.

`steer(run_id, text, targets=...)`로 접수한 지시는 일반 요청 대기열과 구분합니다. 메시지 저장·대상별 선택·반영·접수 마감을 기존 StorageIO 트랜잭션에서 처리하며 저장 후 STEERING_CHANGED를 알립니다. 서비스는 Loop 반복 번호나 Graph 실행 순서를 결정하지 않습니다. [공통 전달·재개 계약](../../../docs/llm/steering.md)을 따릅니다.

`reserve_instruction(run_id, text, targets=routes)`는 아직 시작하지 않은 Agent의 다음 실행에 한 번 예약합니다. 예약 접수와 실행 scope 결합은 같은 저장 직렬화 경계를 사용합니다. 미사용 예약은 종료 사유를 기록하고 재개에 이전하지 않으며, 적용한 예약은 원본 영수증 변경 없이 기존 문맥으로 복원합니다.

## UI 구독

열린 `backend`에 구독을 연결하는 예입니다. `event`의 구조는 선택한 channel에 따릅니다.

```python
async def update_ui(run, event):
    # 화면이 요구하는 변경만 전달한다. 도메인 파일을 직접 수정하지 않는다.
    pass

subscription = backend.events.subscribe(
    update_ui, channel="engine", delivery="queued",
    buffer_size=100, overflow="drop_oldest",
)
# 화면을 닫을 때:
await subscription.aclose()
```

`channel="engine"` 콜백은 `(run, event)`, `channel="run"` 콜백은 Run 수명 이벤트 하나 `(event)`를 받습니다. `drop_oldest`는 관찰 전용이므로 누락 시 저장된 Run/Step/출력을 다시 조회해야 합니다. `unsubscribe()`는 즉시 해제하고 대기 알림을 버립니다. `aclose()`는 진행 중 콜백과 worker 정리까지 기다립니다. 진행 중 콜백을 강제 취소하지 않으며 자기 콜백 안에서 aclose를 기다리는 것은 거부합니다.

백엔드 종료는 활성 구독을 배출합니다. 명시적 callback timeout이 없다면 끝나지 않는 콜백이 종료를 지연시킬 수 있고, 동기 콜백의 스레드를 강제 종료하지는 못합니다. [서비스 확장 계약](../../../docs/llm/service-extensions.md)을 참고하세요.

## Tool 실행 경계

Tool source worker는 함수를 실행하지만 승인·retry·Step·영수증은 ToolExecutor가 소유합니다. 승인 재개에서는 Engine이 stable checkpoint key를 공통 helper에 전달해야 합니다. 완료 결과는 재사용하고, 불확실한 부작용을 자동 반복하지 않습니다. [Tool 패키지](../../components/tools/README.md)와 [Engine 계약](../../engines/README.md)을 함께 참고하세요.

[상위 안내](../README.md)


Interaction 저장 조회는 기존 envelope 경계를 통해 현재 Record 전체를 검증한 후 typed
control projection을 사용합니다. 응답 저장은 실행·큐 등록을 하지 않습니다. 갱신은
취소/만료 증거와 요청 identity를 유지하며, 원본·갱신 연결은 동일 저장 트랜잭션에서
확정합니다. [Interaction 계약](../../../docs/llm/interactions.md)을 참고하세요.
