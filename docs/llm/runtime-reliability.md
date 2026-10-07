# 실행 안정성과 UI 연결

Loop 재개·영속 승인·조건부 재시도·누적 사용량·보관·편집 충돌·RAG 색인 작업은
[장시간 실행·복구 API](long-running.md)를 함께 참고한다.

후속 추가된 ProcessToolRunner, 작업 키 원장과 명시적 외부 결과 확인 API는
[프로세스 격리·중복 방지 안내](process-isolation.md)를 참고한다. 아래 기본 in-process 실행의
한계는 그대로이며 새 기능은 ToolRuntime를 통해 선택적으로 연결한다.

Project → Session → Run → Step 구조를 유지한다. 서비스가 저장과 실행 정책을
담당하고, Engine은 이벤트를 보고한다. 프로젝트 정책은 ProjectConfig로 저장하고
공유 자원·어댑터는 BackendServices로 구성한다. Backend 정의의 닫힌 schema와
명시적 metadata/선택 구현체의 설정 경계를 유지한다.

## 장시간 작업과 실행 한도

```python
import litellm
from llm.llm import LargeLanguageModel, LoopEngine, BackendServices, ToolRuntime

def count_request(request):
    return litellm.token_counter(
        model=request["model"], messages=request["messages"],
        tools=request.get("tools"), tool_choice=request.get("tool_choice"),
    )

services = BackendServices(
    tool_runtime=ToolRuntime(),
    token_counters={"custom": count_request},
    conversation_cache_size=32,
)
backend = LargeLanguageModel(
    "./workspace", services=services,
    engines={"loop": LoopEngine()},
)
project = await backend.projects.acreate(config={"policies": {
    "tools": {"max_calls": 80},
    "run": {"max_queued": 20, "timeout_seconds": 1800},
}, "parameters": {"engines": {"loop": {'policy': {'max_iterations': 40, 'completion': {'max_tokens': 32000, 'reserve_tokens': 4000, 'counter': 'custom'}}}}}})
```

숫자는 예시다. 선택한 모델의 문맥 한도, 출력 한도, 작업 성격에 맞춘다.
CompletionPolicy은 매 LiteLLM 요청 직전에 messages뿐 아니라 tools까지 포함한
요청을 counter에 전달한다. counter는 빠른 동기 함수여야 하며 모델별 정확성은
호출자가 책임진다. 출력 예약분은 입력 예산에서 제외할 뿐 모델의 max_tokens를
설정하지 않는다. completion 설정에도 출력 한도를 지정한다.
Loop는 토큰 계산을 이벤트 루프 밖에서 수행한다. 오래된 턴 선택은 일반적인 단조
토큰 계수에서 이진 탐색을 사용해 계산 호출 수를 줄이며 마지막 요청도 다시 검증한다.
counter는 동시 Session에서도 사용할 수 있게 스레드 안전하게 작성하고 실행 중 예산 설정은
변경하지 않는다.

예산을 넘으면 오래된 사용자/응답 턴부터 요청에서 제외한다. 시스템 지시와 현재
사용자 입력 이후 Tool 호출·결과는 함께 유지한다. 현재 작업만으로 초과하면
`context_budget_exceeded`로 실패하며 다음 모델 호출은 하지 않는다. 기록된 원문은
삭제하지 않는다. Memory 처리기를 명시적으로 설정하면 현재 작업의 오래된 Tool 교환도
요약할 수 있다. Loop는 저장된 회차/Tool 영수증으로 명시적 재개를 지원한다.
Graph Agent는 선택한 Engine의 policy.completion을 상속·재정의한다. 새 Engine은 필요할 때
`CompletionPolicy.from_config(명시한_설정, context.token_counters)`로 알고리즘을 구성한다.
서비스가 모든 Engine에 같은 CompletionPolicy를 주입하지 않는다.

RunPolicy는 Engine 종류와 무관하게 적용된다. max_queued는 Session별 QUEUED 수로,
실행 중인 요청은 제외한다. 한도 초과는 저장 전 `RunRequestError(code="queue_full")`로
거절한다. 재개 요청도 같은 한도를 사용하며 이미 저장된 복구 대기열은 버리지 않는다.
시간 한도는 문맥 준비부터 Engine 종료까지이며 큐 대기와 최종 저장은 제외한다.
초과 시 Run/Assistant/미완료 Step을 실패로 마무리하고 다음 대기 요청을 처리한다.

미설정 RunPolicy의 각 제한은 없고 CompletionPolicy도 생성하지 않는다. 기존 작업을
임의로 중단하지 않도록 운영 한도는 명시적으로 설정한다. `request.wait(timeout=...)`는
호출자의 대기 한도이며 실제 Run 실행 한도와 다르다.

## Tool 승인과 실행 어댑터

```python
async def authorize(call):
    # UI 구현이 결정하는 awaitable. 재시작 뒤 이 Future를 복구하지 않는다.
    return await ui.confirm_tool(
        run_id=call.run_id, step_id=call.step_id,
        name=call.name, arguments=call.arguments,
    )

services = BackendServices(tool_runtime=ToolRuntime(
    allowed_tools=("rag_search",),  # None이면 등록된 Tool을 허용
    max_calls=30,
    authorize=authorize,
))
```

`ui`는 호스트가 제공한다. 정책은 모델이 Tool을 고른 이후 실제 실행 전에 검사한다.
Tool 정의를 자동 활성화하거나 모델에 보이는 카탈로그를 수정하지 않는다.
허용 목록 밖의 호출은 `tool_denied`, 한도 초과는 `tool_budget_exceeded`다.
authorize는 정확히 True를 반환해야 한다. 오류나 False/None이면 실행하지 않는다.
승인 대기와 실행에 동일한 Tool 시간 예산을 사용하며 초과는 `tool_timeout`이다.

Tool Step은 `phase=authorizing`으로 먼저 저장된다. 승인 후 `STEP_UPDATED` 이벤트로
`authorization=allowed, phase=executing`을 저장한 뒤에만 핸들러를 호출한다.
승인 결과 저장 실패도 실행을 막는다. 결과/실패는 같은 Step에 남긴다.
취소된 승인 대기는 Tool을 호출하지 않으며 Run/Step 상태를 INTERRUPTED로 마무리한다.

LoopEngine, Graph의 ToolNode, Graph Agent가 동일한 **Run별** ToolExecutionScope를
공유한다. 병렬 분기도 예산을 중복 할당하지 않는다. 허용 목록 검사를 통과한 승인
시도부터 호출 예산을 소비하며 거절/실패 시 반환하지 않는다. 재개는 새 Run이므로
새 예산과 현재 승인 정책을 적용한다. 임의 개발자 핸들러의 직접 I/O까지 가로채지는 않는다.
새 Engine은 `ToolExecutor.execute(..., context=context)`를 통해 공통 정책에 참여한다.

격리 실행이 필요하면 async `runner(tool, call)`을 주입한다. 결과는 기존 Tool과 같이
JSON 직렬화 가능한 값이어야 한다. 기본값은 `await tool.handler(arguments)`다.
runner에 별도 프로세스/호스트 실행을 연결할 수 있지만 **현재 기본 실행은 OS sandbox가
아니다**. timeout도 취소에 협조하지 않는 코드나 이미 시작한 외부 부작용을 되돌리지 않는다.
ToolCall 인자는 복사본이므로 승인 함수가 바꾸어도 실행 인자가 바뀌지 않는다.

## UI 상태, 취소, 느린 구독자

```python
request = await session.run.submit("작업", engine="loop")
status = await session.run.astatus(queued_limit=20)
# session_id, status, active_run_id, engine, started_at,
# queued_count, queued_request_ids
cancelled = await request.cancel()  # QUEUED일 때만 True. 실행 중이면 False

subscription = backend.events.subscribe(
    render_event, channel="engine", delivery="queued",
    buffer_size=64, overflow="drop_oldest", callback_timeout=2,
)
print(subscription.stats)
# active, delivered, dropped, failures, timed_out, pending
subscription()  # 구독 해제
```

상태 조회는 Session의 현재 Run과 선택한 대화 저장소만 읽으며 과거 Run/Step 전체를
읽지 않는다. 실행을 시작하지 않는 영속 상태 스냅샷이므로 프로세스 재시작 직후에는
start/submit이 복구하기 전의 상태가 보일 수 있다. 취소와 실행 시작은 동일한 잠금
경로에서 판정한다. 취소된 대기 요청에는 Run을 생성하지 않는다.
취소된 요청의 wait는 `RunRequestError(code="request_cancelled")`를 낸다.
파일 모드의 취소는 재시작 후에도 유지된다. 실행 중 요청은 기존 interrupt를 사용한다.

queued/drop_oldest는 큐가 차면 오래된 **관찰 알림**만 버린다. 저장된 대화/Step/Run은
그대로이며 UI는 dropped 증가, 재접속, 화면 전환 때 저장소를 다시 조회해야 한다.
TEXT_DELTA를 유실 가능 모드에서 단순히 붙이기만 하면 화면 내용이 불완전해진다.
Run 최종 알림도 이 모드에서 누락될 수 있으므로 request.wait/상태 재조회로 확정한다.
중요한 검증/승인은 관찰 콜백이 아니라 ToolRuntime 또는 EventHandlers에 연결한다.

기본 overflow=block은 기존 backpressure 동작이다. callback_timeout은 멈춘 구독을
비활성화해 후속 호출과 무한 종료 대기를 막는다. callback은 취소에 협조해야 한다.
동기 queued 콜백의 이미 실행 중인 스레드는 종료할 수 없으며 같은 구독에서 새 스레드를
계속 만들지는 않는다. inline 동기 콜백은 이벤트 루프를 막지 않아야 한다.
UI에는 queued 전달을 권장한다. 레거시 on_event/on_run_event 직접 콜백에는 이 설정이
자동 적용되지 않는다.

## 파일 조회 성능과 저장 보장

파일 ConversationStore의 증분 읽기 상태를 SessionManager의 공유 팩토리가 최근 32개
Session까지 LRU로 재사용한다. 실행·조회가 같은 설정을 사용하고, 파일 변경·교체·절단은
기존 stat/증분 재생 검사로 반영한다. 크기는 `conversation_cache_size`로 조정하며
0이면 파일 캐시를 끈다. 사용자 주입 팩토리의 수명/저장 방식을 변경하지 않는다.
메모리 대화는 LRU로 퇴출하지 않는다. 활성 Run이 가진 저장소 참조는 캐시 퇴출 후에도
유효하다. 캐시 크기는 Session 개수 기준이며 전체 메모리 바이트 상한은 아니다.

상태별 개수는 JSONL 이벤트 적용 때 집계해 대기열 검증 시 과거 본문을 복사하거나
매번 훑지 않는다. 제한된 오름차순 조회는 전체 메시지 참조 목록도 만들지 않는다.
JSONL fsync 및 UI 통지 전 저장 순서는 유지한다. 첫 대용량 파일 재생, 과거 Run 목록
정렬, 토큰 계수, 토큰별 fsync 비용까지 없앤 것은 아니다.

## 검증과 남은 운영 과제

`tests/llm/test_runtime_reliability.py`는 실제 파일/메모리 서비스, Loop, LangGraph와
스크립트형 모델 응답으로 정책 전달·취소·재시작·저장 실패·정체 구독자·캐시를 검증한다.
기존 SDK/HTTP 모의 서버, 프로세스 crash/recovery, RAG/Workflow 통합 테스트도 함께 실행한다.
이번 검증에는 실제 유료 모델 호출이나 장시간 Linux 운영 부하 시험은 포함하지 않는다.

여전히 필요한 운영 작업은 배포 OS의 sandbox 검증, 취소에 협조하지 않는 provider
스레드 관리, 외부 제공자의 idempotency 연결, 실제 모델/호스트 장시간 부하 검증, 백업 복구
훈련이다. 자동 요약/압축, Loop 재개, 비용 상한, 저장소 인덱스는 별도 확장 과제다.
이번 변경만으로 상용 제품과 동등한 운영 보장을 주장하지 않는다.
