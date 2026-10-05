# 컴포넌트의 공통 모델 처리기

`llm/components/processing.py`는 모델 호출 전후에 컴포넌트가 참여하는 실행 계약이다.
Memory 저장 방식이나 Skill/MCP 정책을 알지 않는다. 현재 실제 제공자는 Memory이며,
다른 컴포넌트는 `completion_processors` capability를 export해서 참여한다.
Project → Session → Run → Step 계층은 그대로 유지한다.

## 실행 수명과 순서

```text
CompletionPipeline 진입
  → (priority, name) 오름차순으로 session(context) 생성
  → 반복마다 prepare(CompletionRequest)
  → 최종 CompletionPolicy 적용
  → 모델 호출과 기존 LLM Step 기록
  → after_completion(CompletionObservation)
  → Tool 요청이면 기존 ToolExecutor 실행 후 다음 반복
  → 최종 응답이면 finish(CompletionObservation)
  → 출력 이벤트
CompletionPipeline 종료
  → 성공·예외·취소·소비자 조기 종료 모두 세션 역순 aclose(error)
```

`session()`은 한 Engine 호출의 임시 상태를 만든다. 공유 Processor 객체나 Engine 인스턴스에
Run별 가변 상태를 저장하지 않는다. 생성자는 외부 자원을 획득하지 않고, 비동기 자원 획득은
`prepare`에서 한다. 훅은 선택적 구현이며 `CompletionSession`의 기본 메서드는 아무 작업도
하지 않는다. 이름 중복·잘못된 priority/close_timeout은 등록 해석 단계에서 거부한다.
프로젝트 컴포넌트 선택 순서를 바꿔도 실행 순서는 바뀌지 않는다.

`prepare`는 매 반복 호출된다. 실행당 한 번 준비할 일은 세션에 준비 여부를 저장한다.
`after_completion`은 정상 스트리밍 및 Loop의 Tool 호출 형식 검증을 마친 응답마다 호출한다.
이 시점에는 Tool 효과가 아직 발생하지 않았다. 예외를 발생시키면 Tool 실행을 진행하지 않는다.
`finish`는 최종 응답에만 한 번 호출하며 실패/중단에는 호출하지 않는다. 따라서 이는 **Run이
영속적으로 완료되었다는 알림이 아니다**. 실제 완료 알림에는 기존 Run lifecycle 구독을 쓴다.

세 이벤트 훅의 async iterator는 항상 닫는다. `aclose(error)`는 이벤트 없이 자원만 정리하며
정상 경로에서는 None, 비정상 경로에서는 원래 예외/취소/GeneratorExit를 받는다. 생성 중 오류가
나도 이미 생성한 세션을 정리한다. 하나의 정리 실패가 나머지 정리를 건너뛰게 하지 않는다.
원래 실패/취소를 정리 오류로 덮어쓰지 않으며, 정상 경로의 정리 실패는 Run 실패로 전파한다.
따라서 부분/최종 텍스트가 이미 관찰되었어도 Run 상태를 함께 확인해야 한다.

정리는 같은 asyncio.Task에서 실행하여 ContextVar 토큰 등 소유 문맥을 보존한다.
각 Processor의 `close_timeout`은 명시적인 협력적 비동기 기한(양수) 또는 None(추가 기한 없음)이다.
builtin Memory/Goal의 aclose는 자원을 닫지 않는 no-op으로 None을 사용한다. 취소를 무시하거나 동기 함수를
무한 실행하는 플러그인을 강제 종료하는 OS 격리 기능은 아니다. 전체 실행 기한은 기존
RunPolicy를 공유하고, 개별 훅의 작업 기한/Step은 구현체가 BaseEngine.step 등으로 정한다.

## 입력 출처와 수정 범위

`CompletionRequest`의 필드:

| 필드 | 용도 |
| --- | --- |
| `parameters` | 공급자에 전달할 열린 설정 dict. messages는 별도 필드 사용 |
| `messages` | `CompletionMessage(value, source_id, continuation)` 목록 |
| `iteration` | 현재 모델 호출 순번, 1부터 시작 |

`CompletionMessage.value`는 공급자 메시지 dict이며 `source_id`는 원본 Conversation 메시지 ID다.
추가 Skill/RAG 참고자료는 `source_id=None`으로 만든다. 출처 ID는 공급자 요청에 섞이지 않는다.
`continuation=True`는 Loop의 실행 중 추가 지시처럼 같은 작업에 속한 user 메시지다.
SDK에는 이 표시를 보내지 않는다. 과거 턴 선택·요약 시 원래 요청과 함께 유지하거나
제거하며, 활성 턴에 이미 추가된 지시의 본문·순서·ID는 processor가 변경하지 않는다.
입력 선택은 prepare 전에, 적용 기록은 prepare와 CompletionPolicy 검증 뒤에 저장한다.
Tool 없는 응답 뒤에도 새 지시가 있으면 다음 반복으로 이어지므로 after_completion은
각 응답을 관찰하고 finish는 실제로 Loop를 마칠 마지막 응답에만 실행된다.
변환마다 원본과 다른 사본을 사용하고, 다음 Loop 반복에서는 원본 transcript로 다시 시작한다.
따라서 매 반복 참고자료를 추가해도 이전 반복의 변환 내용이 자동 누적되지 않는다.

현재 사용자 메시지와 원본 메시지의 상대 순서·role은 보존해야 한다. 과거 대화를 제거할 때는
완전한 턴 단위로 제거한다. 원본 ID 중복/위조는 거부한다. 현재 사용자 입력 이후 Tool
호출/결과의 순서·인자·ID는 보존하며 Tool 결과 본문 압축만 허용한다. 참고자료는 현재 입력
앞에 삽입하거나 현재 입력의 사본에 추가한다. 현재 입력 뒤에 독립 메시지를 추가하지 않는다.

처리기가 tools/tool_choice/functions/function_call/stream/n/num_retries를 바꾸는 것도 거부한다.
Tool 구성·권한은 capability/Agent/ToolExecutor 경로가 소유한다. 일반 모델 인자는 열린 dict로
유지하므로 모델 선택 등은 가능하지만, 예산 계수기가 그 모델을 지원하는지도 구성자가 확인해야 한다.
이는 신뢰하는 개발자의 확장 계약이며 악의적인 Python 코드의 보안 경계가 아니다.

Memory는 요약에 포함된 **원본 ID와 원본 내용이 그대로 남아 있는지** 확인한 후에만 그
메시지들을 제거한다. 앞선 처리기가 참고자료를 삽입해도 해당 자료는 제거하지 않는다.
앞선 처리기가 원문을 수정/제거했다면 해당 요청에서는 요약 교체를 생략한다. system/developer
지시는 유지한다. priority만으로 출처 충돌을 해결하지 않는다.

## 요청·응답 관찰

`CompletionObservation`은 iteration, request, response, original_messages를 제공한다.
request는 처리기와 CompletionPolicy을 거친 뒤 `BaseEngine.stream_completion`에 전달한 요청이다.
공급자 어댑터가 내부에서 채우는 stream_options 기본값 등까지 포함한 HTTP wire 요청은 아니다.
original_messages는 처리 전 대화와 현재 실행의 원본 Tool transcript이고 최종 응답은 response에
별도로 있다. 공급자 설정에 살아 있는 SDK 클라이언트/콜백이 있으면 기존 copy_params 계약처럼
객체 참조는 유지한다. 이 런타임 핸들을 수정하면 안 된다.

각 관찰자는 독립적인 dict/list 사본을 받는다. 사본의 응답을 바꿔도 최종 답변이나 다음
관찰자, 실행할 Tool에는 영향을 주지 않는다. 이 훅은 결과 변환/자동 재시도 API가 아니다.
검증 실패 후 재수정은 명시적인 Engine/Workflow 제어 흐름에서 처리한다.
처리기 동작의 Step/Completion 관찰은 기존 EngineEvent로 전달하며 영속 파일은 직접 쓰지 않는다.

## 새 컴포넌트 예제

```python
from llm.components.base import Component
from llm.components.processing import CompletionMessage, CompletionSession


class InstructionsSession(CompletionSession):
    async def prepare(self, request):
        request.messages.insert(0, CompletionMessage({
            "role": "system", "content": "설명을 작성할 때 프로젝트 용어를 사용하세요.",
        }))
        if False:
            yield  # 이 예제는 관찰 이벤트를 만들지 않는다.


class InstructionsProcessor:
    name = "instructions"
    priority = 0
    close_timeout = 5.0

    def session(self, context):
        return InstructionsSession()


class InstructionsComponent(Component):
    name = directory = "instructions"
    capabilities = ("completion_processors",)

    def resolve(self, project, capability):
        if capability != "completion_processors":
            raise ValueError("Unsupported capability")
        return InstructionsProcessor()
```

이 system 지시는 개발자가 작성한 신뢰하는 지침의 예다. 검색 결과·MCP 리소스 같은 외부
내용을 무조건 system 지시로 승격하지 않는다. 실제 데이터 접근은 ComponentData를 통해
연결하는 resolve_runtime을 사용하면 잠금/수명 검사를 유지할 수 있다.

Memory의 순서는 현재 설정을 읽어 `config.processing.priority`를 수정한 뒤
`memory.aconfigure(settings)`로 저장한다. policy.processing의 명시적 활성화 설정도 보존한다.
configure는 설정 전체 교체이므로 유지할 다른 설정도 전달한다. 낮은 priority가 먼저 실행된다.
기존 사용자 정의 처리기는 새 CompletionRequest/CompletionObservation 계약으로 갱신해야 한다.
과거 dict 기반 prepare 및 finish(messages, response)의 호환 별칭은 제공하지 않는다.

## 적용 범위와 추가 고려 사항

- 직접 소비자는 LoopEngine이다. Graph의 Loop Agent와 Pipeline 내부 Loop에도 적용된다.
  순수 Graph 노드, BaseEngine의 모든 모델 호출, Memory의 보조 모델 호출에 재귀 적용하지 않는다.
- 컴포넌트 순서를 정하는 데 priority/name이면 현재 충분하다. before/after 의존성 그래프는
  도입하지 않았다. 같은 필드를 수정하는 여러 처리기의 의도까지 자동 병합하지 않는다.
- 여러 처리기의 외부 쓰기는 하나의 트랜잭션이 아니다. 앞선 처리기가 기록한 뒤 다음 처리기가
  실패할 수 있다. 각 컴포넌트가 revision·출처·원자 쓰기를 관리하고 자동 효과 재시도는 하지 않는다.
- 스트리밍 텍스트는 검증 전에 UI에 도착할 수 있다. 검증 전 비공개 출력이 필요하면 별도 버퍼링
  정책이 필요하다. after_completion은 이미 전달한 델타를 철회하지 않는다.
- 훅은 순차 실행된다. 보조 모델/검색을 많이 연결하면 지연·비용이 늘어난다. 해당 컴포넌트의
  설정·캐시·기한을 사용한다. 실행 결과의 의미적 정확성은 실제 Skill/MCP 구현과 모델로 따로 검증한다.

회귀 검사: `tests/llm/test_completion_processing.py`, `tests/llm/test_memory_processing.py`.

GoalProcessor도 같은 capability를 사용한다. `goals.policy.inject=true`와
`goals.config.priority`를 명시해야 현재 user 입력 사본에 Goal reference를 추가한다.
중첩 Agent에는 `goals.config.nested_agent_ids`로 지정한 Agent만 전달한다.
Memory의 구조화 요약도 이 입력 처리 계약 안에서 실행되며 current input, steering,
완료되지 않은 Tool pair를 제거하지 않는다. 최종 입력 예산은 여전히 CompletionPolicy가 검사한다.
