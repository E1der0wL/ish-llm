# 공통 Engine 출력

`llm.core.results`의 `EngineDelta`, `EngineOutput`은 실행 결과를 표현하는 데이터
클래스다. 새로운 도메인이나 Manager는 추가하지 않는다. 기존
Project → Session → Run → Step 경계를 유지한다.

| 자료형 | 역할 |
| --- | --- |
| `EngineDelta` | 한 출력의 텍스트 변경: append / replace |
| `EngineOutput` | 확정된 텍스트·JSON 결과, 또는 재조회한 부분 출력 |
| `CompletionResult` | 모델 호출별 사용량, 종료 이유, 시간 |
| `ExecutionResult` | Run 상태, 오류, 사용량 합계, 최종 `output` 조회 |

두 출력 클래스는 `output_id`, `step_id`, `visibility`, `sequence`를 공유한다.
`EngineOutput`에는 `text`, 임의 JSON 값인 `data`, 열린 `metadata`, `final`이 있다.
`to_dict()` / `from_dict()`로 직렬화 가능한 독립 스냅샷을 주고받는다.

- UI 출력 식별자는 **(Run ID, output_id)**다. 서로 다른 출력을 한 버퍼에 섞지 않는다.
- `step_id=None`은 Run 결과다. 자식 Agent/Graph/LLM/Tool 결과는 Step ID를 가진다.
- `visibility="user"`는 사용자 표시용, `"internal"`은 내부 작업 관찰용이다.
  이는 UI 분류이며 권한이나 비밀값 필터가 아니다.
- `sequence`는 RunManager가 저장할 때 부여하는 Run 전체 출력 순번이다.
  Engine을 직접 호출한 이벤트의 순번은 0이며, 순번 0을 재조회 커서로 사용하지 않는다.
- `final=True`는 해당 출력이 확정되었다는 뜻이다. Run 성공은 별도 Run 상태로 판단한다.

## Engine 개발

문자열 또는 EngineDelta를 yield하는 BaseEngine은 Step별 델타와 결과, Run 최종 결과를 자동 생성한다.
구조화 결과를 직접 제공하려면 마지막에 EngineOutput을 yield하거나 async 함수에서 반환한다.

```python
from llm.engines.base import BaseEngine
from llm.core.results import EngineDelta, EngineOutput

class ReviewEngine(BaseEngine):
    async def run(self, context):
        yield EngineDelta(text="초안 검사 중…")
        yield EngineDelta(text="수정본 검사 중…", operation="replace")
        yield EngineOutput(text="검사 완료", data={"passed": True, "issues": []})
```

BaseEngine이 ID와 소유 Step을 부여하므로 위 예제는 경로나 저장 서비스를 다루지 않는다.
문자열은 append 델타다. 빈 문자열 replace도 유효하며 현재 텍스트를 지운다. 최종 EngineOutput을
생략하면 append/replace를 모두 반영한 텍스트로 결과를 만든다. EngineDelta 생성 시 output_id를
생략할 수 있고, BaseEngine은 전달된 객체를 복사하여 현재 Step ID로 연결한다.

복잡한 execute()를 직접 구현할 때는 공통 메서드를 사용한다. 메서드는 파일을 쓰거나 작업을
실행하지 않고 EngineEvent를 반환하므로 async generator에서는 yield, Graph 콜백에서는 emit한다.

| 메서드 | 역할 |
| --- | --- |
| `delta_event(context, text_or_delta, step_id=...)` | 현재 Run/중첩 소유 Step 또는 명시한 Step의 델타 생성 |
| `output_event(context, EngineOutput(...))` | 현재 엔진의 최종 결과 생성 |
| `step_completed_event(context, step_id, EngineOutput(...), metadata=...)` | 명시한 Step의 결과와 완료 생성 |

상속 클래스에서는 self로 호출한다. 상속을 사용하지 않는 Graph/Pipeline/Agent 실행기도
BaseEngine의 정적 메서드로 재사용할 수 있다. 복잡한 실행기에 단일 Step 실행 흐름을 강제하지 않는다.
Step 완료 메서드는 이미 시작한 Step에 사용한다. run()에서는 이벤트를 직접 조립하는 대신
EngineDelta/EngineOutput을 전달하고, 여러 Step이 필요하면 기존 step()을 사용한다.

이벤트와 결과의 Step ID를 일치시키고, 중첩 엔진은 context.output_step_id에 결과를 연결한다.
서비스가 순번을 부여하므로 공통 메서드는 sequence를 0으로 초기화한다. 입력 객체는 수정하지 않는다.
context.output_visibility 또는 전달한 값이 internal이면 내부 출력으로 유지한다. 한 Step의
스트리밍 도중 visibility를 바꾸면 실패한다. 다른 표시 범위는 별도 Step으로 분리한다.
결과 검증 실패도 STEP_FAILED로 전달하며, 실패·취소 시 generator 정리와 큐 처리는 유지한다.
새 객체 생성 시의 자동 ID와 저장된 값의 복원은 구분한다. from_dict()에서 output_id가
누락되면 오류이며 임의 ID로 손상된 기록을 복원하지 않는다. 저장 순번이 있는 출력에는
순번 0을 포함한 과거/중복 델타를 적용할 수 없다.

Loop는 호출별 스트림을 서로 다른 LLM Step에 남기고 마지막 모델 응답만 Run 결과로 확정한다.
Graph는 Workflow outputs에 매핑된 JSON을 Run output.data로 반환한다. 중첩 Graph와 Agent는
부모 Run을 덮어쓰지 않는다. Agent의 입력/출력 매핑 계약 `{text, data?}`는 그대로 유지한다.
Tool 결과도 `step.output.data`로 조회한다. Pipeline은 각 단계를 kind="engine" Step으로 묶고
마지막 단계의 결과를 자신의 결과로 사용한다. Pipeline 단계들의 임시 context.state는 공유한다.

## UI 조회 및 스트리밍

```python
request = await session.run.submit("요청", engine="loop")
run = await request.wait()
result = await run.aresult()
if result.output is not None:
    print(result.output.text, result.output.data)

for step in await run.steps.alist():
    if step.output is not None:
        print(step.kind, step.output.text, step.output.data)

snapshots = await run.aoutputs()              # 실행 중인 부분 출력도 포함
changes = await run.aoutput_events(after=0, limit=100)
```

실시간 구독에서는 `event.delta` 또는 `event.output`을 읽는다. TEXT_DELTA는 append/replace를
구분하고, OUTPUT 및 STEP_COMPLETED의 output은 해당 카드의 최종 스냅샷으로 적용한다.

```python
from llm.core.results import EngineOutput, EngineDelta

cards = {}

def apply(run_id, value):
    key = (run_id, value.output_id)
    previous = cards.get(key)
    if previous is not None and value.sequence <= previous.sequence:
        return  # 재조회와 실시간 이벤트의 중복
    if isinstance(value, EngineDelta):
        previous = previous or EngineOutput(value.output_id, step_id=value.step_id,
            visibility=value.visibility, final=False)
        cards[key] = previous.apply(value)
    else:
        cards[key] = value
    # UI는 cards[key].visibility에 따라 대화/내부 작업 패널에 표시한다.

def on_event(run_data, event):
    value = event.delta or event.output
    if value is not None:
        apply(run_data.id, value)

subscription = backend.events.subscribe(on_event, delivery="queued", buffer_size=128)
```

기본 overflow="block"은 순서를 유지한다. drop_oldest를 선택한 UI는 순번이 건너뛰거나
subscription.stats["dropped"]가 증가하면 실시간 적용을 잠시 버퍼링하고, 마지막으로 연속 적용한
순번부터 aoutput_events(after=cursor)를 읽어 복구한다. 그 후 버퍼의 중복 순번을 제외하고 적용한다.
누락된 이벤트를 건너뛴 채 최신 순번을 커서로 확정하면 안 된다. 마지막 이벤트가 유실될 수도 있으므로
Run 완료 알림 또는 UI 재접속 시에도 재조회한다. 위 간단한 예제는 block 구독을 사용한다.

## 저장 및 변경점

- Run/state/outputs.jsonl: 모든 출력 변경의 append-only 저널. 저장이 끝난 이벤트만 구독자에게 전달한다.
- Run/run.json의 metadata.output: 최상위 결과 스냅샷.
- Step/step.json의 metadata.output: 해당 Step의 결과 스냅샷. 공개 조회에서는 step.output을 사용한다.
- Session 대화: 사용자 표시용 델타를 기록하고, 텍스트 엔진 종료 시 최종 답변으로 본문을 확정한다.
  교체도 JSONL 이벤트를 추가하며 대화 파일 전체를 다시 쓰지 않는다. 중간 응답은 출력 저널에 남는다.

출력 저널과 최종 스냅샷은 **Run/Step 영속 기록**이다. conversation_storage="memory"여도 유지된다.
메모리 대화의 재시작 복구나 Workflow 재개 조건은 바뀌지 않는다. 실패·중단·pause 시 아직 확정되지
않은 출력은 final=False로 조회하며 실행을 자동 재시도하지 않는다. 불완전한 마지막 저널 줄은
무시하고, 완전한 줄의 손상/잘못된 순번은 오류로 처리한다.

재조회는 after/limit 커서와 재생성 가능한 희소 위치 인덱스를 사용한다.
기존 주입 RunRepository를 실행·조회가 공유하며, 다른 저장소는 record_output/output_events/
outputs 계약도 구현해야 한다. 예전 event.text와 Step metadata의 raw output/result에 대한 호환
별칭·자동 변환은 추가하지 않았다.

## 출력 저장과 조회의 운영 설정

출력 저널의 커서 조회에 재생성 가능한 희소 위치 인덱스를 사용한다.
BackendServices.output_index_stride로 간격을 지정한다(0은 비활성화).
OutputBuffer는 기본 즉시 저장 또는 제한된 델타 묶음 저장을 선택한다.
외부 알림은 해당 저장이 끝난 뒤 전송한다. 자세한 계약과 설정은
[실행 자원과 저장 정책](operational-storage.md)을 참고한다.
