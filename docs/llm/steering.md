# 실행 중 추가 지시

## 명시적 방향 전환과 미반영 지시 철회

`steer()`는 현재 호출을 취소하지 않는다. Loop는 첫 Tool을 포함한 미시작 호출
직전에 pending 지시를 확인한다. `redirect()`는 동일한 영속 접수 경계를 사용하고,
선택한 소비자가 `redirectable`을 선언한 경우에만 현재 모델/leaf Tool 호출에 취소를 요청한다.
Graph 컨테이너·다른 병렬 Agent·별도로 시작한 background process를 자동 종료하지 않는다.
`agent_run`으로 위임한 자식 실행 역시 부모를 취소한다고 함께 중단하지 않는다. 자식의
수신 채널을 명시적으로 선택한다.
호출자는 대상과 방식을 명시하며 Backend는 새로운 timeout, retry, 우선순위 기본값을 만들지 않는다.

취소는 Run이나 저장 ACK Task가 아닌 현재 invocation에만 전달한다. cleanup 뒤 모델은 같은
Run의 다음 iteration에서 원본 요청과 이미 반영된 지침을 유지하여 재판단한다. 실제 시도와
사용량은 지워지지 않으며 iteration limit도 초기화하지 않는다. 중단된 모델 Step의 부분 출력은
보존하지만 완성 응답/Tool 실행 근거로 사용하지 않는다. 취소를 무시한 늦은 모델 결과는 폐기한다.

Tool의 실제 반환 결과는 취소 경쟁에서도 보존한다. 취소로 종료된 read-only Tool은 trusted
ToolContract의 무효과 선언을 근거로 다음 판단을 허용한다. 외부/unknown 효과 Tool은 실행 여부를
추측하지 않고 uncertain 실패와 STARTED checkpoint를 유지한다. 자동 재시도하지 않으며 기존
명시적 재개·retry_nodes·operation receipt 계약을 적용한다. 완료된 Tool을 다시 실행하지 않는다.
프로세스/SDK/remote service의 실제 종료 능력은 해당 구현이 소유한다. 취소 요청은 서버 추론 중단
확인이 아니고, 외부 효과 rollback도 아니다. 새로운 Tool 인자는 기존 검증·분류·승인을 다시 거친다.

`withdraw_instruction(run_id, instruction_id)`와 `replace_instruction(run_id, instruction_id,
content, request_key=...)`는 아직 어떤 대상에서도 checkpoint에 선택되지 않은 pending 지시만
처리한다. 원본은 삭제하지 않고 unapplied와 withdrawn/superseded 사유를 남긴다. 대체는 원본과
같은 대상/예약을 사용하며 새 메시지를 만든다. 이미 dispatch된 `redirect`는 취소 동작을 되돌릴 수
없으므로 철회·대체 대신 새 정정 지시를 보낸다. 선택 또는 반영과 경합하면 저장
직렬화 순서로 한쪽만 성공한다. 이미 선택된 지시에는 새 정정 지시를 보낸다. 선택은 재개 문맥의
근거이므로 단순히 applied 표시가 아직 없다는 이유로 철회하지 않는다.
예약이 이미 특정 실행에 결합됐지만 아직 선택되지 않았다면, 대체 지시도 그 결합을 그대로
유지한다. 다음 반복/다른 실행으로 예약을 새로 배정하지 않는다.

취소의 접수·선택·반영은 기존 Message/Checkpoint가 source of truth다. runtime control은 잠든
invocation을 깨우는 수단일 뿐 별도 승인/지시 원장이 아니다. `applied`는 계속 입력 준비 저장
확인이며 Provider 수신 또는 모델 이행을 의미하지 않는다. Engine/Runner가 협조하지 않는 호출의
강제 종료나 고정 응답시간은 보장하지 않는다. Application이 표시·버튼·실제 대기 제한을 결정한다.

Loop의 Tool 호출 사이 미시작 호출 생략과 선택적 호출자 `request_key` 접수 계약은
[상호작용 계약](interactive-contracts.md#loop의-tool-호출-사이-추가-지시)을 따른다.
완료된 Tool과 started/waiting 효과를 되감지 않고 미시작 호출만 닫아 다음 모델 입력에서 재판단한다.

추가 지시는 같은 Run의 실행 입력에 보태는 사용자 메시지다. 일반 `submit()`은 다음
Run으로 대기하고, `steer()`는 지정한 실행으로만 전달한다. RunStatus와
Project → Session → Run → Step 소유 관계는 바꾸지 않는다.

## UI에서 사용하기

단독 Loop는 기존 API를 사용한다. 시작 알림 직후에도 접수할 수 있으며, 실제 반영은
모델 입력을 구성하는 안전한 경계에서 수행한다.

```python
instruction = await session.run.steer(run_id, "결과에 근거도 포함해줘.")
```

빠른 방향 전환은 별도의 명시적 API다. 같은 요청 키의 재접수는 지시와 취소를 중복 생성하지 않는다.

```python
instruction = await session.run.redirect(run_id, "현재 분석을 멈추고 오류 원인부터 확인해줘.",
                                         request_key="ui-direction-1")
```

일반/예약 지시는 선택되기 전 철회하거나 대체할 수 있다. 이미 반영된 입력을 삭제하는 API가 아니다.

```python
replacement = await session.run.replace_instruction(run_id, pending_instruction_id,
                                                    "수정한 지시", request_key="ui-replace-1")
withdrawn = await session.run.withdraw_instruction(run_id, replacement.id)
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

상태 값은 `llm.core.steering.InstructionStatus`가 단일 선언한다. 현재 Reader 검증,
접수·예약·반영·종료 Writer와 Session clone 소비자는 이 선언을 참조한다.
대상별 상태는 pending/applied/unapplied이며 partially_applied는 지시 전체 집계에만 사용한다.

반영 시각은 대상마다 다르다. 여러 대상에 대한 동시 반영/모델 호출 트랜잭션은 제공하지
않는다. `applied`는 검증된 입력 준비의 저장 확인이며 provider 수신이나 모델 이행 증명이
아니다. 저장과 외부 모델 요청 사이에서 연결 실패나 취소가 발생할 수 있다.

`STEERING_CHANGED`는 원자 읽기 view 공개 후 발행한다. 초기 OPEN 관찰은 아직 fsync 전일 수
있고 실행 허용이나 장애 복구 보장이 아니다. 나머지 선택/반영·승인 경계는 내구성 확정을 유지한다.
`metadata.instructions` 또는
`metadata.targets` 또는 `metadata.routes`가 포함될 수 있으므로 키 존재를 확인한다. Run 종료 알림도 처리하고,
구독 누락/drop 발생 또는 API 대기 취소 뒤에는 instructions/targets/routes 조회 API로 다시 읽는다.
알림 순서보다 저장된 상태가 우선이다.

## 공통 Engine 계약

BaseEngine 상속은 자동 지원 선언이 아니다. 새 소비자는
`steering_mode = SteeringMode.CONSUME`과 `checkpoint_name`을 선언하고 다음을 구현한다.

1. 자신의 체크포인트를 초기화하거나 검증된 재개 스냅샷을 사용한다.
2. `open_instructions(context)`의 이벤트를 끝까지 전달한다. 새 checkpoint와 첫 선택을
   준비한 경우 `checkpoint_event=..., boundary=...`를 함께 전달하여 같은 준비 ACK를 받을 수 있다.
   이때 서비스가 pending 지시를 선택하며 Engine은 ACK 뒤의 inbox를 사용한다.
3. 안전한 경계에서 `select_instructions(context, checkpoint=..., boundary=...)`를 전달한다.
   OPEN에서 이미 확정한 첫 boundary는 다시 선택하지 않는다. Loop의 첫 선택 이후 processor
   준비 중 도착한 지시는 다음 선택 경계에 남는다.
4. 저장 ACK 이후 `context.steering.messages`를 자신의 입력으로 변환·검증한다.
5. `apply_instructions(context, checkpoint=..., boundary=..., details=...)`를 전달한다.
6. 실행을 허용하는 Step/승인/예약 ACK 이후 모델 등 실제 작업을 진행한다. Step 전에 외부
   확장 hook을 호출한다면 `yield self.execution_boundary()`의 ACK로 준비 기록을 먼저 fsync한다.
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

방향 전환은 별도 지원 선언이다. 소비 Engine이 `redirectable = True`를 선언하고
`open_instructions(context, redirectable=True)`로 연다. `context.steering.control.stream()`
또는 `call()`로 취소 가능한 invocation만 감싼다. 저장 ACK나 Run 전체, 자식 컨테이너를
감싸지 않는다. `InvocationRedirected`를 받으면 자신의 interrupted checkpoint를 기록하고
다음 입력 선택으로 이동한다. Tool 호출은 기존 ToolExecutor가 leaf invocation 제어를 소유한다.
`ToolRedirected`의 무효과/불확실 효과를 구분하며 uncertain을 새 시도로 숨기지 않는다.
선언하지 않은 Engine의 Tool 호출에 취소용 task 경계를 추가하지 않는다.
취소용 child task에서 설정한 ContextVar 값은 호출자의 오류/재시도 callback에 보존한다.
특정 Host 변수 이름을 해석하지 않으며 ContextVar token은 생성한 Context 안에서만 사용한다.
Agent factory가 반환한 실제 Engine의 지원 선언을 검증하며, 예약 경로의 기존 Engine 모드와
checkpoint identity 계약은 그대로 유지한다.

RunManager가 메시지·대상·선택·반영 기록을 저장한다. Engine은 영속 파일에 쓰지 않는다.
취소/GeneratorExit 중에는 새 이벤트를 yield하지 않는다. Run 종료/복구가 미반영 지시와
수신 대상을 닫는다. 새 timeout이나 재시도 기본값은 없다.

전달 Engine은 `declare_instruction_routes`로 검증된 경로를 Run당 한 번 선언하고,
`bind_instruction_route`로 실제 노드 시작 시 한 번 결합한다. 결합 ACK 전에는 노드 효과를
실행하지 않는다. 서비스는 접수와 결합을 같은 StorageIO 직렬화/트랜잭션 경계로 처리한다.
Graph의 AgentNode.instruction_engine은 등록 Engine의 `steering_mode=CONSUME` 선언을
사용하며 미선택 Agent의 factory를 평가하지 않는다. `for_agent()`의 반환 Engine은 이
선언을 유지해야 한다. 경로 열거·반복/분기 해석은 Graph가 소유한다.
같은 실행 scope를 여러 번 결합하거나 열면 거부한다.

Run 채널의 필수 필드와 검증은 `SteeringChannel`이 소유한다. 일반 채널의 accepting,
mode, input_message_id, targets, root_opened, routes, bindings, routes_declared는 모두
필수다. 세 flag는 정확한 boolean이어야 하며 mode·원본 입력 ID·route/target/binding의
무결성도 조회와 저장 전에 같은 Reader에서 확인한다. 필드 누락을 빈 채널로 복원하지 않는다.
기존 명시적 Resume의 정확한 `{accepting: false}` 일시 중지 표지만 별도 유효 상태이며,
숫자 0이나 일부 필드만 남은 손상 레코드는 이 표지로 해석하지 않는다. 거부 시 원본은 보존한다.

Message 수신 영수증의 상태 전이·application·집계는 `InstructionReceipt`와
`InstructionMessageRecord`, 이벤트와 부분 변경 통지는 `SteeringEventData`와
`SteeringNotification`이 소유한다. runtime은 이 Owner의 타입·Projection·전이를 호출한다.
선택 Checkpoint의 Tool 사용량 부착도 `ToolUsageCheckpoint`에 위임하며 저장 ACK 전후의
효과 순서를 유지한다. 부분 통지의 생략과 빈 목록은 구별한다.

## Graph 전달과 명시적 재개

Graph는 `FORWARD`다. AgentNode가 호출별 채널을 만들고 체크포인트 bridge를 통해
자식의 선택·반영 이벤트를 부모 저장 경계로 전달한다. 각 자식은 별도 전달함을 사용한다.
Graph Agent와 중첩 Workflow에서도 같은 경로를 쓴다. 미지원 합성 Engine에는 입력 채널을
주입하지 않으므로 내부 Loop에 자동 전달되지 않는다.

Loop는 `steering:<iteration>` 키를 유지한다. 일반 지시는 첫 Tool을 포함한 미시작 호출 경계와
completion 종료 경계에서 확인한다. 명시적 방향 전환은 현재 invocation에 취소를 요청한 뒤
다음 completion 입력에 반영한다. Graph는 LangGraph 스케줄링과
Workflow 상태 binding을 유지하며 Workflow 정의 자체를 변경하지 않는다.

선택 체크포인트는 `kind=instruction`, `status=input`으로 구분하고 메시지 ID와 대상 scope만 저장한다.
input 값은 `llm.core.steering.INSTRUCTION_INPUT_STATUS`가 단일 선언하며 Reader와 서비스·
Loop·Graph의 입력 기록이 이를 참조한다. 지시 반영 상태인 `InstructionStatus`와는 구분한다.
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
`validate_resume` 계약도 구현해야 한다. AgentNode는 선택된 factory의 반환 Engine과
등록 선언의 일치를 승인 대기·실행·재개 전에 검증한다. 이 검증이 실패하면 해당 Agent의
자원 연결과 효과를 시작하지 않는다. 앞서 완료된 다른 노드의 효과를 되돌리지는 않는다.
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

## 내부 계약 Owner와 복제 집계

추가 지시 이벤트의 wire 필드와 동작은 `SteeringEventData`/`SteeringOperation`, Run 채널과
예약 경로/대상은 `SteeringChannel`, Message에 저장된 대상별 소비 영수증과 집계는
`InstructionMessageRecord`/`InstructionReceipt`/`InstructionApplication`이 소유한다.
서비스는 저장 잠금과 트랜잭션·ACK 순서를 조율하며 상태·필드 구조를 따로 해석하지 않는다.
공개 `RunInstruction`과 변경 알림의 기존 JSON, 초기 생략 필드와 미결합 null은 유지한다.

대화를 복제할 때는 pending 대상만 cloned 사유로 닫고 이미 applied인 대상의 영수증은
보존한다. 전체 상태는 같은 대상별 집계 규칙으로 계산한다. 따라서 일부가 이미 반영됐다면
partially_applied이며 전부 미반영으로 덮어쓰지 않는다. 원본은 변경하지 않고, 노드 scope의
지시를 후속 실행의 일반 문맥으로 전달하지 않는다. 손상된 원본 Reader는 계속 거부한다.

변경 및 재현/안전성 근거는 [전수 작업 검증 기록](whole-ownership-verification.md)에 남긴다.
