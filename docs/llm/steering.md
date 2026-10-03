# 실행 중 추가 지시

추가 지시는 같은 Run의 실행 입력에 보태는 사용자 메시지다. 일반 `submit()`은 다음
Run으로 대기하고, `steer()`는 지정한 실행으로만 전달한다. RunStatus와
Project → Session → Run → Step 소유 관계는 바꾸지 않는다.

## UI에서 사용하기

단독 Loop는 기존 API를 사용한다. 시작 알림 직후에도 접수할 수 있으며, 실제 반영은
모델 입력을 구성하는 안전한 경계에서 수행한다.

```python
instruction = await session.run.steer(run_id, "결과에 근거도 포함해줘.")
```

Graph에서는 실행 대상 목록을 표시하고 사용자가 선택한 ID를 전달한다.

```python
run = await session.run.aload(run_id)
targets = await run.ainstruction_targets()
# UI에서 mode, node_id, node_path, accepting을 표시한다.
# selected_target_ids는 사용자가 선택한 targets 항목들의 id 목록이다.
instruction = await session.run.steer(
    run_id, "근거 문서도 함께 표시해줘.", targets=selected_target_ids,
)
instructions = await run.ainstructions()
```

`SteeringTarget`, `SteeringRoute`, `SteeringMode`, `RunInstruction`, `InstructionStatus`는 `llm.llm`에서
가져온다. 동기 조회는 `run.instruction_targets()`와 `run.instructions()`다.

- `id`는 이번 Run의 Engine 실행 ID다. Workflow 노드 ID와 다르며 새 실행에서 재사용하지 않는다.
  단독 Loop의 대상 ID는 `root`이고 API의 `run_id`와 함께 식별한다.
- `scope`는 중첩·병렬·반복 경로를 포함한 안정적인 체크포인트 호출 위치다.
  명시적 재개는 새 실행 ID를 만들되 같은 scope의 선택된 입력을 복원한다.
- `node_id`/`node_path`는 UI 표시용이다. 이 문자열만으로 전달 대상을 추측하지 않는다.
- `consume`은 직접 입력에 반영하는 Engine, `forward`는 자식을 연결하는 Engine,
  `unsupported`는 추가 지시를 지원하지 않는 Engine이다.
- Graph에서는 현재 `accepting=True`인 소비자 실행을 선택한다. Graph 컨테이너는
  후손 소비자의 위치를 제공하며, 컨테이너를 선택했다고 모든 후손에게 방송하지 않는다.
- Tool/검증 처리기, 아직 시작하지 않은 노드, 승인 대기 중인 Run은 접수 대상이 아니다.
  이 제한은 실행 ID를 받는 `steer`의 계약이다. 미래 Agent 실행은 아래 예약 API로 구분한다.
- 조회와 접수 사이에 실행이 종료될 수 있다. 접수 시 다시 검사하며 하나라도 사용할 수
  없는 대상이면 메시지 전체를 거부한다. 다른 노드로 대신 보내지 않는다.
  종료/미지원/다른 Run의 대상은 `steering_unavailable`, 빈 목록·중복·잘못된 타입은 `ValueError`다.

## 아직 시작하지 않은 Agent 실행 예약

Graph 사전 검증이 끝나면 `STEERING_CHANGED.metadata.routes`로 예약 경로를 알린다.
`run.instruction_routes()` / `await run.ainstruction_routes()`로 같은 저장 스냅샷을 읽는다.
Run 시작 알림 직후에는 사전 검증이 끝나지 않아 목록이 비어 있을 수 있다.

```python
routes = await run.ainstruction_routes()
# UI에서 workflow_path, node_id, engine을 표시하고 사용자가 routes 중에서 선택한다.
instruction = await session.run.reserve_instruction(
    run.id, "이번 검토에서는 오류 처리도 확인해줘.", targets=selected_routes,
)
```

`selected_routes`는 조회한 `SteeringRoute` 객체 목록이다. UI JSON을 다시 받을 때는
`SteeringRoute.from_dict(value)`로 역직렬화한다. 빈 목록/중복/미지원/스냅샷과 다른 경로는
`ValueError`로 접수 전체를 거부한다. 종료되었거나 일시 정지한 Run에는 예약할 수 없다.
미설정 엔진이나 다른 대상에 자동 대체하지 않는다.

- `workflow_path`는 루트 Workflow와 중첩 호출 위치를 포함하는 문자열 배열이고,
  `node_id`는 해당 위치의 노드 ID다. 표시용 `node_path`를 `/`로 분할해 만들지 않는다.
  예를 들어 flow의 child 호출로 nested Workflow를 실행하면 경로는
  `("flow", "child", "workflow", "nested")`다. 병렬 분기 위치도 구분한다.
- 반복 번호는 경로에 포함하지 않는다. 접수 이후 그 경로에서 시작되는 **다음 실행 하나**에
  결합한다. 이미 노드 시작 경계를 지난 실행에 소급 전달하지 않는다. 다음 실행이 없으면
  `unapplied`가 된다. 실행 중인 Agent에는 기존 `steer(..., targets=[실행 ID])`를 사용한다.
- 실제 소비 계약과 checkpoint_name/validate_resume을 갖춘 Agent만 예약할 수 있다.
  Graph Agent 컨테이너 자체가 아니라 그 안의 소비자 경로를 선택한다. Tool/일반 처리기는
  대상이 아니다. 카탈로그는 현재 Run의 정의 스냅샷이며 실행 도중 저장한 Workflow 편집으로
  바뀌지 않는다.
- 본문은 한 번 저장하고 대상별 `reservation`(경로), 안정적인 영수증 `id`, `scope`,
  `execution_id`, `status`, `applications`를 기록한다. 미결합 scope/execution_id는 null이다.
  scope는 노드 시작 시, execution_id는 자식 Engine 채널이 열릴 때 확정한다.
- 여러 예약은 대상별로 독립적으로 한 번 소비한다. 같은 실행의 후속 completion 문맥에
  지시가 남는 것은 중복 소비가 아니다. 이후 반복의 별도 노드 실행에는 전달하지 않는다.
- 정상 종료까지 시작되지 않은 대상은 `node_not_reached`, 실패/중단/일시 정지/복구는
  해당 종료 사유와 함께 `unapplied`로 남는다. 일부 대상만 반영되면 `partially_applied`다.

명시적 재개는 새 Run이다. **미사용 예약은 자동 이전하지 않는다.** 선택 체크포인트만
저장되고 적용 전 중단된 예약도 새 Run의 체크포인트 사본에서 제외한다. 이미 적용된 예약은
기존 문맥으로 한 번 복원하며 소비 영수증을 추가하지 않는다. 원본 Run의 메시지/체크포인트는
재개 때문에 수정하지 않는다. 일반 활성 실행 지시의 기존 재개 이력 계약은 유지한다.

지속적인 동작 변경은 `interrupt` 후 종료를 기다리고 Workflow를 수정한 다음 `submit`으로
처음부터 실행한다. 수정한 Workflow를 옛 체크포인트에 연결하지 않는다. 이전 Tool의 외부
효과가 취소되는 것은 아니며 기존 승인·실행 원장 정책은 계속 적용된다.

## 대상별 상태와 알림

지시 본문은 Session ConversationStore에 한 번 저장한다. `RunInstruction.targets`에는
대상별 `id`, `scope`, `status`, `applications`, 필요한 경우 `reason`이 있다.

| 지시 전체 상태 | 의미 |
| --- | --- |
| pending | 아직 처리되지 않은 대상이 하나 이상 있음 |
| applied | 모든 대상의 입력 준비가 확정됨 |
| unapplied | 모든 대상에서 사용되지 않고 종료됨 |
| partially_applied | 모든 대상의 처리가 끝났고 반영·미반영이 섞여 있음 |

반영 시각은 대상마다 다르다. 여러 대상에 대한 동시 반영/모델 호출 트랜잭션은 제공하지
않는다. `applied`는 검증된 입력 준비의 저장 확인이며 provider 수신이나 모델 이행 증명이
아니다. 저장과 외부 모델 요청 사이에서 연결 실패나 취소가 발생할 수 있다.

`STEERING_CHANGED`는 저장 완료 후 발행한다. `metadata.instructions` 또는
`metadata.targets` 또는 `metadata.routes`가 포함될 수 있으므로 키 존재를 확인한다. Run 종료 알림도 처리하고,
구독 누락/drop 발생 또는 API 대기 취소 뒤에는 instructions/targets/routes 조회 API로 다시 읽는다.
알림 순서보다 저장된 상태가 우선이다.

## 공통 Engine 계약

BaseEngine 상속은 자동 지원 선언이 아니다. 새 소비자는
`steering_mode = SteeringMode.CONSUME`과 `checkpoint_name`을 선언하고 다음을 구현한다.

1. 자신의 체크포인트를 초기화하거나 검증된 재개 스냅샷을 사용한다.
2. `open_instructions(context)`의 이벤트를 끝까지 전달한다.
3. 안전한 경계에서 `select_instructions(context, checkpoint=..., boundary=...)`를 전달한다.
4. 저장 ACK 이후 `context.steering.messages`를 자신의 입력으로 변환·검증한다.
5. `apply_instructions(context, checkpoint=..., boundary=..., details=...)`를 전달한다.
6. 저장 ACK 이후 모델 등 실제 작업을 진행한다.
7. 정상 종료 전에 `close_instructions(context)`를 전달한다.

helper는 async iterator다. `async with aclosing(...)` / `async for ...: yield event`
형태로 기존 이벤트 스트림에 연결한다. 서비스 ACK 없이 진행하면 fail-fast한다.
`BaseEngine.step()` 안에서도 전달할 수 있으며 적용 이력에 Step을 연결한다.
서비스는 반복 번호를 해석하지 않는다. `boundary`는 재개 후에도 동일하게 재구성할 수
있는 엔진 소유 문자열이다. `details`는 엔진 관찰 정보이며 Loop는 반복 번호를 넣는다.

`select_instructions(..., final=True)`는 입력이 없으면 해당 대상의 접수를 함께 마감한다.
실행 제한 때문에 계속할 수 없으면 `allow_continue=False, reason=...`를 전달한다.
최종 확인과 접수는 같은 저장 직렬화 경계에서 판정한다. 열린 소비자를 남기고 정상 종료하는
Engine은 실패 처리한다. 엔진별 입력 변환과 실행 제한은 공통 계층에서 결정하지 않는다.

RunManager가 메시지·대상·선택·반영 기록을 저장한다. Engine은 영속 파일에 쓰지 않는다.
취소/GeneratorExit 중에는 새 이벤트를 yield하지 않는다. Run 종료/복구가 미반영 지시와
수신 대상을 닫는다. 새 timeout이나 재시도 기본값은 없다.

전달 Engine은 `declare_instruction_routes`로 검증된 경로를 Run당 한 번 선언하고,
`bind_instruction_route`로 실제 노드 시작 시 한 번 결합한다. 결합 ACK 전에는 노드 효과를
실행하지 않는다. 서비스는 접수와 결합을 같은 StorageIO 직렬화/트랜잭션 경계로 처리한다.
Graph의 AgentNode.instruction_engine은 실제 소비 엔진 이름만 선언하며 경로 열거·반복/분기
해석은 Graph가 소유한다. 같은 실행 scope를 여러 번 결합하거나 열면 거부한다.

## Graph 전달과 명시적 재개

Graph는 `FORWARD`다. AgentNode가 호출별 채널을 만들고 체크포인트 bridge를 통해
자식의 선택·반영 이벤트를 부모 저장 경계로 전달한다. 각 자식은 별도 전달함을 사용한다.
Graph Agent와 중첩 Workflow에서도 같은 경로를 쓴다. 미지원 합성 Engine에는 입력 채널을
주입하지 않으므로 내부 Loop에 자동 전달되지 않는다. Pipeline 라우팅은 제공하지 않는다.

Loop는 `steering:<iteration>` 키와 기존 반영 시점을 유지한다. 현재 completion과
Tool 묶음을 마친 뒤 다음 completion에 반영한다. Graph는 LangGraph 스케줄링과
Workflow 상태 binding을 유지하며 Workflow 정의 자체를 변경하지 않는다.

선택 체크포인트는 `kind=instruction`, `status=input`으로 구분하고 메시지 ID와 대상 scope만 저장한다.
다른 custom Engine의 일반 input 기록은 해석하지 않는다. 자식 입력 기록은 기존
engine_record envelope로 Graph 체크포인트에 연결한다. 명시적 재개에서 Engine은
선택된 ID를 `context.steering.history`에서 읽어 원래 입력 위치에 복원하고 완료 작업은
자신의 체크포인트 정책대로 재사용한다. 예약은 서비스가 미적용 ID를 먼저 제외한 새 Run
사본을 전달하므로 Engine이 영수증을 해석하지 않는다. 선택되지 않은 지시는 자동 재생하지 않는다.
잘못된 scope/메시지 소실/동일 대상의 중복 선택은 거부한다.

노드 대상 지시는 다른 노드나 다음 일반 대화 입력에 자동 삽입하지 않는다. 대상 Engine의
문맥·completion 정책은 계속 적용하며 승인/Tool 권한을 지시로 바꾸지 않는다.
보관·복구는 중첩 입력 기록의 메시지 참조도 보호한다. file/memory 저장 선택은 그대로이며
memory 메시지는 백엔드 종료 후 복원되지 않는다.

추가 지시를 소비하는 custom Agent Engine은 `for_agent`, 유효한 `checkpoint_name` 및
`validate_resume` 계약도 구현해야 한다. AgentNode는 factory가 반환한 실제 Engine을
사전 탐색에서 검증하므로 계약 오류가 있으면 앞선 Tool 노드와 MCP 연결도 실행하지 않는다.
일반 미지원 Engine이나 최상위 소비자에 Graph 전용 재개 계약을 추가로 강제하지 않는다.
GraphEngine/ToolExecutor의 승인·retry·operation receipt·Step 수명은 이 기능으로 이동하지 않는다.

저장 버전은 변경하지 않고 개발 중인 추가 지시 payload에 대상 정보를 추가했다. 이전 개발용
지시 기록을 새 대상 정보로 자동 변환하거나 추측하지 않는다. 읽기 경계는 대상 ID/scope,
상태와 반영 이력을 검사하며 위반 시 `InstructionDataError(code="invalid_instruction_data")`를 낸다.
오류 타입은 `llm.core.steering`에서 가져온다. 일반 설정 오류나 모델 오류를 이 코드로 바꾸지 않는다.
Loop는 자신이 소유하는 입력 체크포인트의 현재 형식도 재개 전에 검사한다.
복구는 Session 전체 지시 검증을 끝낸 뒤 Run/Step/메시지 상태를 변경한다. 대상 정보가 없는
옛 pending 지시는 해당 Session 시작을 거부하며, 복구 계획에서도 자동 복구 대상으로 넣지 않는다.
오류가 난 원본 기록은 보존하고 새 Session을 사용한다. 기존 Session을 자동으로 변환하거나
대기 요청을 우회 실행하지 않는다. 일반 지시 없는 Run/Workflow 데이터는 그대로다.
memory 저장의 정상적인 재시작 소실은 형식 손상과 구분한다. 소실된 입력으로 명시적 재개는
할 수 없지만 같은 Session의 새 요청은 계속 실행할 수 있다.
