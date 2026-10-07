# 대화 저장 방식 선택

대화 메시지 저장소를 **프로젝트별로** 선택한다. 기본값은 기존 append-only JSONL 파일이다.
메모리 모드는 같은 API와 상태 전이를 제공하지만 백엔드 종료/프로세스 종료 후 복구하지 않는다.

```python
file_project = await backend.projects.acreate("보관할 대화", conversation_storage="file")
memory_project = await backend.projects.acreate("임시 대화", conversation_storage="memory")

# 동기 API에도 같은 인자를 지원한다.
project = backend.projects.create("임시 작업", conversation_storage="memory")
print(project.data.conversation_storage)  # "memory"
```

두 프로젝트를 하나의 백엔드에서 동시에 사용할 수 있다. 선택은 `project.json`의
`conversation_storage`에 저장되며 ProjectConfig 내부 설정이 아니다. 다시 불러올 때도
그 선택을 따른다. Project 복제는 선택을 유지하고, SessionManager로 다른 프로젝트에 Session을
복제하면 대상 프로젝트의 저장소에 메시지의 독립 사본을 만든다.

생략하면 `LargeLanguageModel(conversation_storage=...)` 또는
`BackendServices(conversations=...)`의 기본값을 새 프로젝트에 기록한다. 아무것도 지정하지
않으면 `file`이다. 백엔드 기본값을 바꿔도 이미 선택을 기록한 프로젝트에는 영향이 없다.
저장된 프로젝트에서 이 필드가 누락되면 오류다. 읽는 것만으로 필드를 추가하거나
대화를 다른 저장소로 옮기지 않는다. 사용자 팩토리는 명시적인 null 선택을 사용한다.

```python
# Session이 없는 프로젝트에서만 변경할 수 있다.
await project.asave(conversation_storage="file")
```

Session이 있으면 소프트 삭제된 Session도 포함하여 변경을 거부한다. 실행기가 보유한 저장소와
조회 저장소가 달라지는 것을 방지하기 위한 규칙이며 자동 마이그레이션은 제공하지 않는다.

```python
from llm.llm import LargeLanguageModel, LoopEngine

async def main():
    async with LargeLanguageModel(
        "./workspace",
        engines={"loop": LoopEngine()},
    ) as backend:
        project = await backend.projects.acreate("메모리 대화", conversation_storage="memory",
            config={"parameters": {"engines": {"loop": {"config": {
                "completion": {"model": model_name}}}}}})
        session = await project.sessions.acreate("대화 세션")
        request = await session.run.submit("내 이름은 민수야.", engine="loop")
        run = await request.wait()
        print((await run.aresponse()).content)

        # 같은 Session의 다음 Run은 앞선 메시지를 문맥 정책에 따라 받는다.
        request = await session.run.submit("내 이름이 뭐라고 했지?", engine="loop")
        run = await request.wait()
        print((await run.aresponse()).content)
        history = await session.aconversation()
```

모델 인증은 기존 SDK 환경변수 설정을 사용한다. `conversation_storage`는 저장 위치만
바꾸며 모델이나 Engine을 선택하지 않는다. 모든 요청에 `engine=`을 지정해야 한다.
`project.sessions.load()`로 다시 얻은 핸들과 Run/응답 조회도 같은 저장소를 사용한다.

## 수명과 저장 범위

| 동작 | 파일 | 메모리 |
| --- | --- | --- |
| 대화 저장 | Session의 conversation.jsonl | 백엔드가 소유하는 Session별 저장소 |
| 스트리밍·대기 요청·중단 | 지원 | 지원 |
| session.run.shutdown() 후 같은 백엔드에서 재시작 | 유지 | 유지 |
| backend.shutdown() | 파일에 유지 | 대화와 대기 요청 해제 |
| 프로세스 재시작 | 대기 요청 복구, 실행 중이던 Run은 재실행 금지 | 메시지와 대기 요청 복구 불가 |
| Session/Project 복제 | 메시지 복사 | 메모리 메시지의 독립 사본 생성 |
| 소프트 삭제/복원 | 유지 | 같은 백엔드 수명 안에서 유지 |
| 영구 삭제 | 소유 파일 삭제 | 소유 파일 삭제 및 해당 메모리 대화 해제 |

**이 설정은 Conversation만 대상으로 한다.** Project/Session/Run/Step 메타데이터,
도메인 로그와 Tool이 생성한 파일은 기존 파일 저장을 유지한다. Engine/Tool이 Step
metadata 등에 텍스트를 넣으면 해당 텍스트도 그 도메인 파일에 기록된다. 전체 백엔드를
파일 없이 실행하거나 모든 텍스트의 디스크 기록을 차단하는 옵션은 아니다.

메모리 모드에서는 새 conversation.jsonl을 생성하지 않는다. 기존 파일 대화를 자동으로
가져오거나 수정/삭제/이전하지도 않는다. 백엔드 기본값 변경은 저장된 선택을 바꾸지 않는다.
이전 Run의 `result`/Step 기록은 조회할 수 있지만, 선택한
저장소에 응답 Message가 없으면 `run.response`/`aresponse()`는 KeyError를 발생시킨다.
메모리 대화가 없더라도 남아 있는 stale Run/Step은 기존 규칙대로 interrupted로 복구하며
자동 재실행하지 않는다.

메시지는 명시적으로 정리하기 전까지 메모리에 보관한다. ContextPolicy는 모델에 전달할
문맥만 고르며 저장소의 오래된 메시지를 자동으로 지우지 않는다.

## BackendServices 및 직접 주입

다음 설정은 새 프로젝트의 기본값을 지정한다.

```python
from llm.llm import LargeLanguageModel, BackendServices

backend = LargeLanguageModel(
    "./workspace",
    services=BackendServices(conversations="memory"),
)
```

같은 BackendServices를 재사용해도 각 백엔드는 별도 메모리 팩토리를 만든다. 이미 별도
conversations 설정을 지정했다면 Facade의 conversation_storage를 동시에 지정하지 않는다.
이 규칙은 사용자 팩토리를 조용히 기본 구현으로 덮어쓰는 것을 방지한다.

호스트가 여러 백엔드의 순차 재생성 동안 대화를 유지하고 싶다면 팩토리를 직접 소유한다.

```python
from llm.llm import LargeLanguageModel, BackendServices, MemoryConversations

conversations = MemoryConversations()
services = BackendServices(conversations=conversations)
backend = LargeLanguageModel("./workspace", services=services)
# backend 종료 후 같은 services로 새 백엔드를 만들면 같은 Session 대화를 다시 사용한다.
# 모든 사용자가 종료했을 때 호스트가 conversations.clear()를 호출한다.
```

사용자 팩토리를 직접 주입할 때는 프로젝트별 `conversation_storage`를 지정하지 않는다.
이 경우 새 프로젝트의 선택은 `null`이며 주입된 팩토리를 따른다. 이미 `file`/`memory`를
선택한 프로젝트에 사용자 팩토리를 적용하려 하면 대화 접근 시 명시적인 충돌 오류가 난다.
프로젝트 선택으로 사용자 팩토리를 조용히 교체하지 않는다.

직접 주입한 팩토리는 backend.shutdown()이 자동 정리하지 않는다. 프로세스 간에는
공유되지 않으며 workspace 소유권과 Session의 단일 실행 규칙도 그대로 적용된다.

저수준 API는 `SessionManager(conversations="memory")`로 동일한 선택이 가능하다.
직접 SessionManager를 소유한다면 모든 RunManager 종료 후 `close_conversations()`로
자동 생성된 메모리 저장소를 정리한다. 사용자 팩토리는 `factory(session) -> Conversation`
계약을 구현하며, 선택적인 `discard(session)`는 Session/Project 영구 삭제 후 호출된다.

## ish 명령에서 선택

```text
llm --engine loop --model gemini/gemini-3.8-flash --prompt "안녕" --conversation-storage memory
```

이 단일 요청 명령은 매번 새 백엔드를 만들고 종료하므로 메모리 대화는 명령 종료 시
사라진다. 여러 차례 대화를 유지하는 UI는 LargeLanguageModel 객체를 계속 소유한다.

## 검증

```powershell
.\.venv312\Scripts\python.exe -m unittest tests.llm.test_memory_conversation -v
.\.venv312\Scripts\python.exe -m unittest tests.llm.test_project_conversation_storage -v
.\.venv312\Scripts\python.exe -m unittest discover -s tests/llm -t . -v
```

전용 테스트는 실제 파일 저장소와의 분리, LoopEngine의 다음 요청 문맥, Facade 공유,
스트리밍·대기·중단·동시 Session, 복제·삭제, 종료와 stale Run 복구를 검증한다.
모델 응답은 결정적인 테스트 스트림을 사용하며 외부 모델 API를 호출하지 않는다.
