# LoopEngine — 모델과 Tool을 반복 실행

LiteLLM completion 스트림을 읽고, 모델이 요청한 Tool을 실행한 뒤 결과를 다음 모델 호출에 전달합니다. 모델 응답, Tool 실행, 반복 체크포인트를 같은 Run 안에서 관찰할 수 있습니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | LoopEngine 공개 import를 제공합니다. |
| [engine.py](engine.py) | 설정 해석, 스트리밍, Tool 호출, 반복 제한, 체크포인트·명시적 재개를 구현합니다. |

## 등록과 실행

```python
from llm.engines.loop import LoopEngine

engines = {"assistant": LoopEngine()}
# LargeLanguageModel(..., engines=engines)에 전달한 뒤:
# request = await session.run.submit("문서를 설명해줘", engine="assistant")
```

등록 이름에 맞춰 `ProjectConfig.parameters["engines"]["assistant"]`에 설정합니다. `settings_name`을 명시하면 별도 설정 키를 사용할 수 있습니다. 요청마다 Engine 이름을 지정합니다.

## 한 반복의 흐름

1. 반복 경계에서 추가 지시를 선택하고 Session 문맥과 completion processor로 입력을 구성합니다.
2. LiteLLM을 `stream=True`로 호출하고 델타·Completion 정보를 전달합니다.
3. Tool 요청이 있으면 공통 ToolExecutor를 통해 실행합니다.
4. 완료 응답·Tool 영수증을 체크포인트 이벤트로 전달하고 다음 반복으로 갑니다.
5. 최종 응답 또는 명시적 반복 제한·중단·실패로 실행을 끝냅니다.

`policy.request_timeout`은 Loop 호출 제한이고 `config.completion.timeout`은 SDK 인자입니다. 서로 자동 복사하지 않습니다. 미설정 request/tool timeout이나 max_iterations를 임의로 만들지 않습니다.

같은 등록 이름의 `policy.completion`는 CompletionPolicy의 max_tokens/reserve_tokens/counter를,
`policy.provider`는 스트리밍 외부 호출의 max_attempts/wall_timeout/delay_seconds/max_delay_seconds를
설정합니다. Project → Session → Agent → 명시적 host 순으로 해석하며 각 section 전체의
null은 상속된 정책을 해제합니다. 미설정이면 입력 예산이나 외부 재시도·기한을 추가하지 않습니다.
SDK 인자는 `config.completion`에 그대로 두며 provider 설정과 서로 복사하지 않습니다.
공통 Run/usage 정책은 입력 정책을 해제해도 계속 적용됩니다. [설정 예](../../../docs/llm/project-policies.md)

## 실행 중 추가 지시

`await session.run.steer(run_id, text)`는 실행 중인 단독 Loop에 지시를 접수하고 `RunInstruction`을 반환합니다. `submit`과 달리 새 Run이나 일반 요청 대기열을 만들지 않습니다. RunStatus를 추가하지 않으며 기존 `RUNNING` 상태를 유지합니다. 종료 중/종료한 Run과 다른 런타임이 소유한 Run은 거절합니다. Graph 안의 Loop는 `targets=[실행 대상 ID]`로 지정하며, Pipeline 라우팅은 지원하지 않습니다. 재개 Run은 원본 체크포인트 검증이 끝난 뒤에만 접수합니다. 실행별 수신 상태는 `run.ainstruction_targets()`에서 확인합니다. [공통 전달 계약](../../../docs/llm/steering.md)을 참고하세요.

```text
steer → ConversationStore에 pending 저장
      → 현재 completion / Tool 묶음 완료
      → 다음 반복 입력 선택을 checkpoint에 저장
      → processor·토큰 예산 검증
      → 적용 기록 저장 → 다음 completion
```

- 여러 지시는 저장 순서대로 user 메시지에 추가합니다. 현재 Tool 호출/결과 쌍 사이에는 넣지 않습니다.
- Tool 없는 응답이 나왔어도 접수된 지시가 있으면 다음 반복으로 이어갑니다. 지시가 없으면 접수를 마감한 뒤 최종 출력을 확정합니다. 마감과 접수는 같은 저장 직렬화 경계를 사용합니다.
- `max_iterations`, Run 시간 제한, 사용량·Tool 제한을 늘리거나 초기화하지 않습니다. 반복 여유가 없으면 현재 응답으로 완료하고 남은 지시에 `unapplied / iteration_limit`을 기록합니다. 실패·중단·승인 대기에서도 아직 사용하지 않은 지시는 사유와 함께 남습니다.
- 새 지시는 기존 system prompt, 설정, Tool 권한이나 승인 결정을 바꾸지 않습니다. 이미 실행한 부작용을 되돌리지 않으며 긴 Tool 도중에는 기다려야 합니다.

### 저장과 UI 상태

본문은 선택한 Session ConversationStore에만 저장합니다. Message의 `metadata.steering`이 대상 Run, 원본 입력 ID, 처리 상태와 적용 이력을 가지며, Run의 `metadata.steering`은 접수 가능 여부를 저장합니다. 둘 다 서비스 소유입니다. Engine은 내부 `STEERING` 이벤트를 내고 저장 ACK 후 `EngineContext.steering`의 런타임 전달함을 읽습니다. Engine이 파일을 직접 쓰지 않습니다.

`RunHandle.instructions()` / `ainstructions()`는 접수한 지시와 명시적 재개에서 참조한 지시를 반환합니다. `RunInstruction`과 `InstructionStatus`는 `llm.llm`에서 가져올 수 있습니다.

| 상태 | 뜻 |
| --- | --- |
| `pending` | 저장됐지만 아직 모델 입력에 사용하지 않음 |
| `applied` | processor·문맥 예산 검증 후 모델 입력에 포함할 준비를 확정함 |
| `unapplied` | Run이 끝나거나 제한에 도달해 사용하지 못함. `reason`으로 확인 |
| `partially_applied` | Graph 다중 대상 중 일부만 반영되고 모든 대상의 처리가 끝남 |

`applications`에는 사용한 Run ID, 반복 번호, LLM Step ID와 시각이 기록됩니다. `applied`는 provider 수신 확인이나 모델의 지시 이행을 보장하지 않습니다. 이후 사용량 제한, 연결 오류 또는 취소로 실제 요청이 실패할 수도 있습니다.

저장 후 `STEERING_CHANGED` 이벤트의 `metadata.instructions` 또는 `metadata.targets`로 알립니다. 키 존재를 확인하고, 구독 누락이나 API 호출자 취소 뒤에는 저장된 지시와 대상을 다시 조회하세요. 일시적인 이벤트 도착 순서보다 저장소 조회가 우선입니다. 지시 ID는 일반 `RequestHandle` ID로 사용할 수 없습니다.

추가 지시 전에 이미 출력된 답변은 출력 저널에 남습니다. 최종 `EngineOutput`과 Assistant 응답은 마지막 답변을 반영합니다. UI는 델타 문자열을 무조건 누적하는 대신 기존 OUTPUT/replace 계약을 처리해야 합니다.

### 재개와 문맥 보호

`steering:<iteration>` 체크포인트는 해당 반복에 선택한 **메시지 ID 목록**만 저장합니다. 저장된 completion과 Tool 영수증을 재사용하고, 참조된 지시를 원래 반복 위치에 한 번만 복원합니다. 선택 전에 끝난 `unapplied` 지시는 명시적 재개에서도 자동 삽입하지 않습니다. 사용자가 새로 지시하거나 일반 요청으로 제출해야 합니다.

프로세스 재시작 시 stale Run은 기존 규칙대로 interrupted가 되고 pending 지시는 `process_restart`로 미반영 처리합니다. 일반 요청처럼 자동 실행하지 않습니다. 이미 선택된 지시는 명시적 재개 시 다시 입력에 포함될 수 있으며 적용 이력에 새 Run을 추가합니다. memory 저장을 선택했으면 재시작 뒤 원본 메시지가 소실되어 재개할 수 없습니다.

추가 지시는 원래 요청과 같은 대화 턴입니다. ContextPolicy, CompletionPolicy와 Memory 요약은 과거 턴을 통째로 선택하고, 현재 원래 입력·Tool 교환·추가 지시를 보존합니다. Memory는 지시를 가로지르는 활성 Tool 이력 압축을 건너뜁니다. 사용자 주입 completion policy가 이를 지원하려면 `prepare_turn(request, current_index, turn_starts)` 계약을 구현해야 하며 미지원 시 provider 호출 전에 거절합니다. completion processor도 추가 지시를 삭제하거나 변경할 수 없습니다.

단독 Loop의 명시적 재개 뒤 일반 대화에서는 원본 지시를 가장 최근 재개 턴에 연결합니다. 저장된 Message의 소유 Run은 바꾸지 않으며 형제 재개 분기 지시는 섞지 않습니다. Graph 노드 대상 지시는 해당 scope의 체크포인트로만 복원하며 후속 일반 대화에 자동 삽입하지 않습니다.

## 재개와 확장

완료된 Tool 결과는 재사용합니다. 실행 효과가 불확실한 Tool은 `retry_nodes`가 필요하며 자동 재실행하지 않습니다. 승인 대기는 저장된 Interaction과 checkpoint key로 연결합니다.

Loop가 요청하는 capability는 `tools`, `completion_processors`입니다. RAG 검색이나 Memory 처리를 추가할 때 Loop 내부에 전용 저장 코드를 넣지 않고 Component의 capability를 연결하세요. 출력·취소·오류 경계는 [상위 Engine 계약](../README.md)을 따릅니다.

[상위 안내](../README.md)
