# ish-llm

Linux의 **ish에서 사용하는 AI 실행 백엔드 플러그인**입니다. 대화와 작업 기록을 저장하고, 모델의 응답을 스트리밍하며, Tool이나 Workflow를 실행합니다. 셸과 화면은 ish 또는 UI 플러그인이 담당합니다.

처음 사용하는 경우 **[ish에서 실행하기](#ish에서-실행하기)** → **[LargeLanguageModel 사용하기](#largelanguagemodel-사용하기)** 순서로 읽으세요. 확장하려는 개발자는 [새 Engine](#새-engine-만들기), [새 Component](#새-component-만들기)부터 시작할 수 있습니다.

## 주요 개념과 실행 흐름

영속 데이터의 소유 관계는 **Project → Session → Run → Step**입니다.

| 개념 | 역할 | 예 |
| --- | --- | --- |
| Project | 설정, 선택한 Component와 여러 Session을 보관하는 작업 공간 | 개인 업무 프로젝트 |
| Session | 다시 열 수 있는 대화·작업 세션. Conversation도 여기에 속함 | 문서 작성 대화 |
| Run | 한 사용자 요청을 실제로 실행한 기록 | “이 문서를 요약해줘” |
| Step | Run 안에서 관찰할 수 있는 실행 단위 | 모델 호출, Tool 호출, Graph 노드 |
| Engine | Run을 어떻게 실행할지 정의하는 전략 | LoopEngine, GraphEngine, PipelineEngine |
| Component | Project에 연결할 기능과 해당 기능의 데이터를 관리 | Tools, RAG, Memory, Agents, Workflows |

```text
UI / ish 명령
  → LargeLanguageModel
  → Project 선택 → Session 선택
  → submit(입력, engine="등록 이름")
  → 요청 저장(queued) → Run 생성 → Engine 실행
  → EngineEvent → 서비스가 응답·Step·체크포인트 저장
  → UI 이벤트 / 저장된 결과 조회
```

한 Session의 Run은 순서대로 실행됩니다. 다른 Session은 동시에 실행할 수 있습니다. 일반 입력은 현재 작업을 중단하지 않고 대기열에 들어갑니다. 중단은 별도의 `interrupt()` 호출입니다.

Engine과 Component는 소유 계층의 중간 도메인이 아닙니다. Engine은 실행하고, Component는 기능별 자료를 관리하며, 서비스가 Run/Step의 저장과 수명을 담당합니다.

## ish에서 실행하기

### 설치

검증 환경은 **Linux Python 3.12.14**입니다. 저장소의 `llm/` 폴더를 ish 홈의 `plugin/script/` 아래에 복사합니다.

```text
<ish-home>/plugin/script/llm/
  llm.py
  core/
  engines/
  components/
  providers/
  services/
  ...
```

플러그인 자체를 pip로 설치할 필요는 없습니다. ish가 [llm.py](llm.py)의 `PLUGIN_META`를 읽어 외부 라이브러리 의존성을 처리합니다. `tests/`, `examples/`, `docs/`는 개발 저장소의 별도 폴더이며 플러그인 배포에 필요하지 않습니다.

### .ishrc.py에 명령 연결

```python
llm_plugin = plugin.get("llm")
if llm_plugin is not None:
    prompt.set_tool("llm", function=llm_plugin.main)
```

이후 ish에서 실행합니다. 모델명과 경로는 사용하는 서버에 맞게 바꾸세요.

```sh
llm --engine loop --model openai/YOUR_MODEL --prompt "안녕하세요." --workspace /home/user/.ish/llm-workspace
```

OpenAI-compatible 서버는 `--api-base https://your-server/v1`도 전달할 수 있습니다. 이 간단한 CLI는 LiteLLM이 인식하는 인증 환경변수를 사용하며 `--api-key` 옵션은 없습니다. API 키를 코드의 `ProjectConfig`로 전달하는 방법은 아래 예제에 있습니다.

`main(*argv)`는 ish worker에서 호출할 수 있는 동기 진입점입니다. **호출마다 새 Project와 Session을 만드는 확인용 명령**입니다. 저장된 대화를 이어가려면 아래 API로 Project/Session ID를 다시 열거나 개발 저장소의 [ish Loop 예제](../docs/llm/ishrc-loop-test.md)를 사용하세요.

다른 플러그인에서 클래스만 가져올 수도 있습니다.

```python
llm_plugin = plugin.get("llm")
if llm_plugin is not None:
    LargeLanguageModel = llm_plugin.LargeLanguageModel
    ProjectConfig = llm_plugin.ProjectConfig
    LoopEngine = llm_plugin.LoopEngine
```

ish 명령용 함수를 직접 만든다면 import 가능한 모듈의 동기 진입점 안에서 `asyncio.run(...)`으로 백엔드를 생성·정리하세요. 실행 중인 백엔드나 이벤트 루프를 worker에 전달하지 않습니다.

## LargeLanguageModel 사용하기

`LargeLanguageModel`은 저장소, 서비스, 등록된 Engine/Component를 한 번 구성하고 공개 핸들로 연결하는 Facade입니다. 모델 호출이나 저장 책임을 별도로 복제하지 않습니다.

### 요청 한 건 실행하고 결과 받기

아래는 Python 모듈로 실행할 수 있는 예제입니다. ish에서 가져온 클래스도 같은 방식으로 사용합니다. 모델명과 인증값은 실제 값으로 교체하세요.

```python
import asyncio

from llm.llm import LargeLanguageModel, ProjectConfig, LoopEngine, RunStatus
from llm.engines.base import EngineEventType


def on_event(run, event):
    if event.type == EngineEventType.TEXT_DELTA and event.delta is not None:
        if event.delta.visibility == "user":
            print(event.delta.text or "", end="", flush=True)


async def main():
    async with LargeLanguageModel(
        "/home/user/.ish/llm-workspace",
        engines={"loop": LoopEngine()},
        on_event=on_event,
    ) as backend:
        project = await backend.projects.acreate(
            "첫 프로젝트",
            config=ProjectConfig(
                completion={
                    "model": "openai/YOUR_MODEL",
                    "api_key": "YOUR_API_KEY",
                    # "api_base": "https://your-server/v1",
                },
                engines={"loop": {"max_iterations": 4}},
            ),
            components=[],
            conversation_storage="file",
        )
        session = await project.sessions.acreate("첫 대화")
        request = await session.run.submit("안녕하세요.", engine="loop")
        run = await request.wait()
        result = await run.aresult()

        print()
        print("Project ID:", project.id, "Session ID:", session.id)
        print("상태:", result.status)
        if result.status == RunStatus.COMPLETED:
            print("최종 응답:", (await run.aresponse()).content)
            print("사용 토큰:", result.total_tokens)
        else:
            print("오류:", result.error_code, result.error)


if __name__ == "__main__":
    asyncio.run(main())
```

`submit()`의 결과는 **RequestHandle**입니다. `await request.wait()`가 요청의 종료를 기다려 **RunHandle**을 반환합니다. 완료 여부는 `ExecutionResult.status`로 확인합니다. Graph의 구조화된 최종 결과는 `result.output`의 `data`로 조회할 수 있습니다.

`ProjectConfig`는 열린 JSON 설정 객체입니다. 여기에 넣은 API 키도 Project 설정 JSON에 저장됩니다. 별도 비밀값 저장소는 없으며, 원한다면 키를 저장하지 않고 SDK의 환경변수 인증을 사용할 수 있습니다.

### 기존 대화와 기본 Project 사용

다음은 열린 `backend` 안에서 사용하는 코드입니다.

```python
project = await backend.projects.aload(project_id)
session = await project.sessions.aload(session_id)
request = await session.run.submit("앞의 내용을 이어서 설명해줘.", engine="loop")
run = await request.wait()
```

`await backend.projects.aget_default(config=ProjectConfig(...))`는 기본 Project를 생성하거나 다시 엽니다. 처음 생성할 때 등록된 모든 Component를 선택하고 파일 대화 저장을 사용합니다. **기존 Project의 설정은 인자로 덮어쓰지 않습니다.** 모델과 RAG 등의 필수 설정이 자동으로 채워지는 것도 아닙니다. 수정은 `project.asave(config=...)` 또는 해당 Component의 설정 API로 수행합니다.

기본 Project를 사용하더라도 `submit(..., engine="loop")`처럼 실행할 Engine 이름은 매번 명시해야 합니다.

### 자주 쓰는 API

표의 호출은 열린 백엔드에서 사용합니다. ID, 설정, 데이터는 호출자가 준비합니다.

| 작업 | 비동기 API |
| --- | --- |
| Project 생성·조회·목록 | `backend.projects.acreate(...)`, `aload(id)`, `alist()` |
| Project 수정·복제·삭제 | `project.asave(...)`, `aclone(title=...)`, `adelete()` |
| Session 생성·조회·목록 | `project.sessions.acreate(...)`, `aload(id)`, `alist()` |
| Session 수정·복제·삭제 | `session.asave(...)`, `aclone(...)`, `adelete()` |
| 요청·대기·중단 | `session.run.submit(..., engine=...)`, `wait_idle()`, `interrupt()` |
| 실행 중인 Engine에 추가 지시 | `session.run.steer(run_id, text, targets=...)`, `run.ainstructions()`, `run.ainstruction_targets()` |
| 미시작 Agent 실행에 한 번 예약 | `run.ainstruction_routes()`, `session.run.reserve_instruction(run_id, text, targets=selected_routes)` |
| Run 조회·결과·Step | `session.run.aload(id)`, `run.aresult()`, `run.steps.alist()` |
| 대화 조회 | `session.aconversation()` |
| Component 핸들 획득 | `await project.components.aget("prompts")` |
| JSON 정의 CRUD | `component.acreate(...)`, `aload(id)`, `asave(id, data)`, `adelete(id)` |
| Component 설정 조회·교체 | `component.aconfiguration()`, `aconfigure(settings)` |
| 설정 화면 구성 | `backend.project_schema()`, `project.aconfiguration()` |
| Project/Session 결과 모음 | `project.results.alist()`, `session.results.alist()` |

Project/Session의 `adelete()`는 기본적으로 소프트 삭제이며 `arestore()`로 복원합니다. 영구 삭제는 `permanent=True`를 명시합니다. Component 레코드 삭제는 해당 레코드를 제거합니다.

동기 API도 있지만 비동기 UI에서는 `a` 접두사의 I/O API를 사용하세요. `submit`, `steer`, `wait`, `interrupt`, `shutdown`은 원래 비동기입니다. `project.data` 같은 동기 조회보다 `await project.aget_data()`가 적합합니다.

### 실행을 유지하면서 지시 추가하기

UI가 Run 시작 알림에서 받은 `active_run_id`로 호출합니다. 단독 LoopEngine은 다음처럼 사용합니다.

```python
instruction = await session.run.steer(active_run_id, "결과를 한국어 표로 정리해줘.")
run = await session.run.aload(active_run_id)
instructions = await run.ainstructions()
```

같은 Run의 다음 completion 입력에 추가하며 `RUNNING` 상태를 유지합니다. 진행 중인 completion이나 Tool은 중단하지 않고, 현재 Tool 묶음이 끝난 뒤 반영합니다. 일반 `submit()`은 계속 다음 Run의 대기 요청입니다.

Graph는 `await run.ainstruction_targets()`의 실행 목록을 UI에 표시하고, 사용자가 선택한 소비자 ID를 `targets=[...]`로 전달합니다. 병렬·중첩·반복 노드의 실행은 각각 구분합니다. 시작 전 노드는 별도 예약 API로 지정하며 종료/미지원 대상에 대신 보내거나 자동 방송하지 않습니다.

미시작 Agent에는 `await run.ainstruction_routes()`에서 조회한 `SteeringRoute`를 선택해 `await session.run.reserve_instruction(run.id, text, targets=selected_routes)`로 예약합니다. 접수 이후 경로별 다음 실행 한 번에만 적용하고, 실행되지 않으면 `unapplied`와 사유를 남깁니다. 미사용 예약은 재개에 자동 이전하지 않으며, 적용한 예약은 기존 문맥만 복원합니다. 지속 변경은 중단 후 Workflow를 수정하고 새 요청으로 처음부터 실행합니다.

추가 지시는 `pending`, `applied`, `unapplied`, `partially_applied`로 조회합니다. 대상별 결과는 `instruction.targets`에 있습니다. `applied`는 입력 준비에 포함했다는 뜻으로 모델의 이행이나 요청 성공을 보장하지 않습니다. UI는 `STEERING_CHANGED`를 받고, 알림을 놓쳤으면 `ainstructions()`, `ainstruction_targets()`, `ainstruction_routes()`로 다시 읽습니다. 접수가 마감된 대상은 `RunRequestError(code="steering_unavailable")`로 거절하며 다른 실행으로 자동 전송하지 않습니다.

기존 반복·토큰·시간 제한은 유지됩니다. 이미 시작한 Tool을 즉시 멈춰야 한다면 `interrupt()`를 사용하세요. Pipeline 단계 라우팅은 지원하지 않습니다. [공통 계약과 Graph 대상 선택](../docs/llm/steering.md), [Loop 반영 시점](engines/loop/README.md#실행-중-추가-지시)을 참고하세요.

### 설정과 UI 연결

- `completion`: LiteLLM에 전달할 모델·인증·추론 옵션.
- `engines.<등록 이름>`: 해당 Engine의 실행 설정.
- `policies`: 문맥, 완료 토큰, Run, 조건부 Tool 재시도, 사용량·보관 등의 정책. 호스트의 Tool 실행 권한은 ServiceConfig/ToolPolicy가 별도로 소유합니다.
- `session_defaults`: 새 Session에 전달할 설정.
- `component_configurations.<이름>`: 선택한 Component의 설정.

설정이 없으면 임의의 사용자 정책을 만들지 않습니다. SDK 옵션은 생략하여 SDK 동작에 맡깁니다. 설정 누락은 상위 명시값을 상속하고, 지원되는 필드의 명시적 `null`은 상위 값을 덮어씁니다. 상세 우선순위와 강제 불변식은 [설정 계약](CONFIGURATION.md)에 있습니다.

상시 UI에서는 백엔드를 유지하고 종료 시 `await backend.shutdown()`을 호출합니다. `on_event`는 Engine 진행, `on_run_event`는 저장 후 Run 수명 알림입니다. 느린 UI에는 queued 구독을 사용하고, 화면 해제 시 `await subscription.aclose()`로 자원을 정리합니다. 이벤트 누락 가능성이 있는 관찰 구독은 `run.aview()`, `run.aoutput_events()`, `run.steps.alist()`로 저장 상태를 다시 읽어야 합니다. [서비스](services/README.md)와 [이벤트·구독](services/runtime/README.md)을 참고하세요.

승인과 재개는 `run.ainteraction_views()`, `run.arespond(...)`, `session.run.resume(..., engine=...)`로 연결합니다. 승인이 재개 실행 자체를 뜻하지는 않습니다. 명시적 재개는 새 Run을 만들며, 부작용이 불확실한 Tool을 자동 재실행하지 않습니다.

## 새 Engine 만들기

가장 작은 Engine은 `BaseEngine.run()`에서 문자열을 내보내면 됩니다. 아래 코드는 모델 없이도 실행할 수 있습니다.

```python
from llm.engines import BaseEngine, EngineContext


class EchoEngine(BaseEngine):
    async def run(self, context: EngineContext):
        yield "입력: "
        yield context.messages[-1].content
```

`LargeLanguageModel(workspace, engines={"echo": EchoEngine()})`로 등록한 뒤 `submit(..., engine="echo")`로 선택합니다. `BaseEngine`이 Step·출력 이벤트를 구성하고 서비스가 저장합니다.

확장 계약은 다음과 같습니다.

1. 일반 Engine은 `execute(context)`에서 비동기 `EngineEvent` 스트림을 제공합니다. 단순 작업은 `BaseEngine.run()`을 구현합니다.
2. 필요한 기능만 `required_capabilities = ("tools",)`처럼 선언합니다. 객체는 Run별 `context.capabilities`로 전달됩니다.
3. Run/Step/Session/Conversation 파일을 직접 쓰지 않습니다. 체크포인트도 이벤트로 전달합니다.
4. 실행 상태를 공유 Engine 인스턴스에 누적하지 않습니다. 여러 Session에서 같은 등록 객체를 사용할 수 있습니다.
5. Tool은 공통 `context.execute_tool(..., checkpoint_key=...)`로 연결합니다. 승인·재시도·영수증은 ToolExecutor가 소유합니다.
6. 취소를 삼키지 않고 자원을 정리합니다. 명시하지 않은 timeout/retry/출력 제한을 추가하지 않습니다.
7. 공개 설정이 있다면 `configuration_schema()`와 `configuration(...)` 계약을 구현해 검증·UI·실행 해석을 일치시킵니다.

Graph Agent용으로도 사용하려면 `for_agent(definition)` 계약과 AgentNode 등록을 추가합니다. 체크포인트 기반 재개는 별도의 생성·검증 구현이 필요합니다. 단순 Engine에 이러한 기능이 자동 부여되지는 않습니다.

출력 데이터 클래스, Tool helper, 오류 전달과 재개 계약은 [Engine 개발 안내](engines/README.md)에 있습니다.

## 새 Component 만들기

데이터를 관리하는 최소 Component는 이름과 소유 디렉터리를 선언합니다.

```python
from llm.components import Component


class NotesComponent(Component):
    name = "notes"
    directory = "notes"

    def validate_record(self, identifier, data):
        super().validate_record(identifier, data)
        if not isinstance(data.get("text"), str):
            raise ValueError("notes.text must be a string")
```

`LargeLanguageModel(workspace, components=[NotesComponent()])`로 **종류를 등록**한 뒤, `projects.acreate(..., components=["notes"])`에서 **Project에 선택**합니다.

```python
notes = await project.components.aget("notes")
identifier = await notes.acreate({"text": "회의 메모"}, identifier="meeting")
record = await notes.aload(identifier)
```

확장 계약은 다음과 같습니다.

1. `directory`는 Project 루트의 안전한 직접 하위 디렉터리여야 합니다. 핵심 도메인 경로나 다른 Component의 경로를 소유하지 않습니다.
2. Base의 JSON CRUD·직렬화·복제 계약을 재사용합니다. 데이터 형식 제약은 `validate_record()`에 둡니다.
3. 설정은 `ProjectConfig.component_configurations`로 관리합니다. 별도 `component.json`을 만들지 않습니다.
4. UI/앱은 잠금과 수명 검사를 제공하는 `ComponentData` 핸들을 사용합니다. 전용 API가 필요하면 `data_class`에 하위 클래스를 지정합니다.
5. 실행 기능이 필요하면 `capabilities`와 `resolve/resolve_runtime`을 구현합니다. Component가 Run/Step 수명을 직접 관리하지 않습니다.
6. `configuration_schema()`는 허용 형식을 설명합니다. 사용자 미설정 값을 schema default로 생성하지 않습니다.

Tool은 Python 패키지, RAG는 색인 세대 등 별도 저장 구조를 가질 수 있습니다. [Component 개발 안내](components/README.md)에서 공통 CRUD와 전문 Component의 차이를 확인하세요.

## 코드와 문서 찾기

| 경로 | 읽을 때 |
| --- | --- |
| [core/](core/README.md) | 도메인, 설정, 출력·승인 등 공통 데이터 계약 |
| [engines/](engines/README.md) | Loop/Graph/Pipeline 실행과 새 전략 구현 |
| [components/](components/README.md) | 기능별 데이터·검색·Tool·장기 기억 |
| [providers/](providers/README.md) | LiteLLM 호출, 오류·retry·로그·임베딩 검증 |
| [services/](services/README.md) | 공개 API, 도메인 수명, 저장·복구·이벤트 |
| [CONFIGURATION.md](CONFIGURATION.md) | 명시적 설정 원칙과 우선순위 |
| [llm.py](llm.py) | PLUGIN_META, LargeLanguageModel, ish worker용 main |
| [errors.py](errors.py) | 신뢰할 수 있는 오류 코드의 CodedError 계약 |
| [_platform.py](_platform.py) | Linux 실행 조건 검사 |
| [__init__.py](__init__.py) | 플러그인 패키지 초기화 |

개발 저장소에는 [아키텍처](../docs/llm/architecture.md), [인수인계](../docs/llm/handoff.md), [예제 폴더](../examples/llm/), [테스트 실행기](../tests/llm/run_linux.py)가 있습니다. 플러그인만 복사한 배포 환경에서는 이 저장소 문서를 별도로 확인하세요.

전체 검사는 저장소 루트에서 Linux Python 3.12.14로 실행합니다.

```sh
python tests/llm/run_linux.py --full
```

이 명령은 Linux 파일시스템에 소스 스냅샷을 만들고 검증합니다. 실제 모델/사내 서버 검사는 별도로 [Graph·RAG 예제](../examples/llm/graph_rag.md)를 사용합니다. 파일 대화 저장은 재시작 후 유지되지만 `conversation_storage="memory"`의 대화·대기 요청은 프로세스 종료 시 사라집니다.
