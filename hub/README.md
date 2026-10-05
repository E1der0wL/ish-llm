# hub

Prompt-Toolkit 기반 ish AI 대화 플러그인입니다. `LargeLanguageModel`의 공개 API로
Project/Session을 열고, 요청을 저장한 뒤 LoopEngine으로 스트리밍 응답을 받습니다.
사용자 메시지는 오른쪽 정렬하고 Assistant 응답은 Rich Markdown으로 표시합니다.
ish에서는 현재 PTY의 너비와 높이를 채우며, 터미널 크기를 변경하면 자동으로 다시
배치합니다. 좁은 화면에서는 세션 목록과 실행 상세를 접어 대화 공간을 확보합니다.

도움말은 언어팩의 Markdown 본문을 Rich로 렌더링합니다. 도움말·프로젝트 로그·컴포넌트 조회 결과는
공통 읽기 전용 팝업을 사용합니다. **Alt + 이동키**로 스크롤하고 **ESC / Enter / Ctrl+L**로 닫습니다.

## 실제 대화 실행

Linux Python 3.12.14 환경에서 저장소 루트로 이동합니다. `llm/`의 의존성과
`prompt-toolkit==3.0.53`, `rich>=14,<15`, `ascii-magic`, `pyfiglet`이 필요합니다. ish로 배포하면 플러그인
로더가 `llm` 요구사항과 Python 의존성을 처리합니다.

```sh
# ish 또는 Hub를 시작하기 전에 사용하는 제공자의 인증 환경변수를 설정합니다.
export OPENAI_API_KEY="YOUR_API_KEY"

python -m hub \
  --workspace "$HOME/.ish/hub-workspace" \
  --engine loop \
  --model openai/YOUR_MODEL
```

OpenAI 호환 서버라면 `--api-base https://YOUR_SERVER/v1`을 추가합니다.
`--model`에는 LiteLLM의 provider/model 형식을 사용합니다. 환경변수 인증을 사용할 수 있습니다.
설정 화면에서 `config.parameters.engines.loop.config.completion.api_key`를 직접 입력하면 다른 Project 설정과 함께 저장됩니다.

설정 화면에는 저장할 초안만 표시하며, 저장값·현재 적용값의 별도 표시 행은 없습니다.
호스트가 고정한 필드는 읽기 전용입니다. JSON 객체 일부만 고정된 경우 나머지 키는 수정할
수 있지만 고정 경로의 저장값을 바꾸면 저장을 거부합니다. 적용값이나 주입 client의 설정을
Project 설정으로 자동 복사하지 않습니다.

첫 실행은 파일 대화 저장을 사용하는 기본 Project를 만듭니다. 세션은 자동 생성하지 않습니다.
세션이 없으면 pyfiglet 안내 화면이 나오며, F4 또는 세션 목록에서 c를 눌러 직접 생성합니다.
마지막 세션을 삭제하면 같은 안내 화면으로 돌아갑니다.
이후 같은 workspace로 실행하면 기존 Project와 대화를 다시 엽니다.
**model/api-base 인자는 기본 Project를 처음 만들 때만 적용**합니다. 기존 설정을
조용히 덮어쓰지 않습니다. 다른 모델을 처음 시험하려면 새 workspace를 지정하거나
Ctrl+S의 Project 설정 또는 llm의 Project 설정 API로 기존 Project를 수정하세요.

특정 Project와 Session을 열 수도 있습니다. ID는 헤더와 F6 상세 화면에 표시됩니다.

```sh
python -m hub --workspace "$HOME/.ish/hub-workspace" --engine loop \
  --project PROJECT_ID --session SESSION_ID
```

ish에서는 `.ishrc.py`에 다음만 추가하면 실제 Hub가 설치됩니다.

```python
hub_plugin = plugin.get("hub")
if hub_plugin is not None:
    hub = hub_plugin.install(prompt)
```

기본 저장소는 `~/.ish/hub-workspace`입니다. 모델이 없는 첫 실행에서도 Hub와 설정을 열 수 있습니다.
Ctrl+S → 프로젝트를 선택하여 `config.parameters.engines.loop.config.completion.model`을 지정하고 저장한 뒤 대화하세요.
제공자 인증은 환경변수 또는 Project의 `config.parameters.engines.loop.config.completion.api_key`로 설정합니다.
기존 workspace와 모델을 지정하는 `install(prompt, config=HubConfig(...))`도 지원합니다.

기본 Engine은 `loop`, `graph`이고 Graph에는 AgentNode와 ToolNode 처리기가 연결됩니다.
새 Project에는 `tools`, `skills`, `mcp`, `rag`, `agents`, `workflows`, `memory`, `prompts`가 모두 등록됩니다.
Graph 실행은 Ctrl+E에서 요청별 Workflow를 선택합니다. 컴포넌트 등록과 별개로 실제 도구·Workflow 데이터,
RAG 모델·MCP 연결 등 사용하는 기능의 설정은 필요합니다. 기존 Project의 컴포넌트 선택은 보존됩니다.

`engine_factories`는 기본 엔진에 병합하고 `component_factories`는 내장 목록에 추가하거나 같은 이름을 교체합니다.
팩토리는 클래스나 인자 없는 생성 함수입니다. 설정 화면에는 별도 추가 기능 체크 영역 없이
엔진·등록된 컴포넌트 설정이 표시됩니다. 이전 `config.data.hub.engines` 목록은 엔진 노출을 제한하지 않습니다.
승인·질문 응답 폼과 PAUSED 실행 재개 UI는 아직 연결하지 않았으며 llm API에서 처리합니다.

| 키 | 실제 대화 모드 |
| --- | --- |
| Enter | 대기 상태에서는 요청 전송, 실행 중에는 전송 방식 선택 |
| Ctrl+Space | 입력 커서 위치에 줄바꿈. 미확정 자동완성은 취소 |
| Ctrl+Q (셸) | Hub 열기. Hub 안에서는 표시/숨김 단축키로 사용하지 않음 |
| Ctrl+X | 선택 Session의 활성 Run만 중단. 예약 요청은 유지·계속 실행 |
| F2 / 목록 ↑↓ | Session 전환. 다른 Session 실행은 계속됨 |
| 실행 중 Enter | 실행 중 추가 지시 / 후속 지시 선택. 여러 추가 지시 대상이면 대상 선택 |
| ↑ / ↓ | 자동완성이 열려 있으면 후보 선택 |
| F1 | 현재 언어의 도움말 |
| Ctrl+E | 다음 요청에 사용할 엔진 선택. Graph 엔진은 Workflow도 선택 |
| F4 | Session 생성/복제와 이름 입력 |
| F5 | 입력 Markdown 미리보기 표시/숨김 |
| F6 | Project/Session/Run ID, 상태와 Step 표시 |
| ESC | 메인에서 패널로 이동, 패널에서 최소화하여 셸로 복귀 |
| ← / → | 세션 목록에 포커스가 있을 때 공통 좌측 패널 너비 조절. 모양 설정에서 저장 |
| c / d / e / r | 세션 목록에서 생성 / 소프트 삭제 / 이름 변경 / 복제 |
| Ctrl+F | 현재 세션 출력 검색. Enter 다음 결과, Alt+P 이전 결과, Esc 닫기 |
| Ctrl+G | 사용자 요청·어시스턴트 작업 쌍 목록. ↑↓ 선택, d 삭제, r 선택 시점에서 복제 |
| Ctrl+L | 현재 프로젝트 실행 이력 최근 300건. 다시 열면 갱신 |
| Tab | 선택한 자동완성 확정. 미선택 시 첫 후보 확정, 목록이 닫혔으면 목록 열기 |
| Alt + 이동키 | ↑↓←→ / Home / End / PgUp / PgDn 전체 지원. 출력·도움말·로그·조회 결과에서 동일 |
| Ctrl+C | Hub가 열려 있으면 숨김. 독립 실행에서 숨긴 상태이면 종료 |

전송이 저장된 뒤에만 입력창을 비웁니다. 접수에 실패하면 오류와 입력을 유지합니다.
정상 종료는 활성 Run을 중단하고 예약 요청은 저장해 둡니다. 다시 연 Session의
예약 요청은 백엔드가 복구하며, 이전에 실행 중이었던 Run은 자동 재실행하지 않습니다.
다른 Session의 예약 요청은 해당 Session을 열 때 복구합니다.
기존 Project가 memory 저장을 선택했다면 화면에 표시되며, 그 메시지는 종료 시 사라집니다.

상단에는 선택된 프로젝트 이름만, 입력창 제목에는 메인 모델 이름을 표시합니다. 실행 중에는 경과 시간,
엔진, 예약 요청 수와 현재 Step을 표시하고 F6 상세에 모델 호출 수와 토큰 사용량을
표시합니다. 제공자가 보낸 `reasoning_content`는 답변과 분리된 Markdown 블록으로
표시합니다. 제공자가 생각 과정 텍스트를 보내지 않으면 임의로 만들지 않습니다.
이 데이터는 llm의 Completion 관찰값으로 저장되어 재접속해도 조회할 수 있습니다.

Ctrl+F는 현재 세션에 표시되는 사용자 메시지·응답·생각 과정에서 텍스트를 찾습니다.
대소문자를 구분하지 않는 일반 문자열 검색이며, 일치 부분과 현재 결과를 다른 색으로 강조합니다.
입력할 때 첫 결과로 이동하고 Enter/다음 버튼으로 다음 결과, Alt+P/이전 버튼으로 이전 결과를
순환합니다. Esc로 닫아도 읽던 위치와 강조는 유지하고, 검색어를 비우거나 세션을 바꾸면 해제합니다.
검색 대상은 Markdown을 렌더링한 출력이므로 원문의 서식 기호를 검색하는 기능은 아닙니다.

Hub에 연결된 다른 세션의 실행 시작·완료·실패·중단·일시 정지는 우측 하단에 알립니다.
알림은 입력 포커스를 바꾸지 않고 기본 8초 뒤 사라집니다. 일반 설정에서 알림 종류와 표시 시간을 변경할 수 있습니다. 최대 3개 세션의 최신 상태를 표시하며,
같은 세션의 알림은 갱신합니다. 초기 이력 조회는 알리지 않습니다. Hub 안에서 표시하는
일시 알림이며 OS 알림이나 영구 알림 이력은 아닙니다. 팝업을 조작하는 동안 알림은 가려집니다.

실행 중 **Enter**로 추가 지시 또는 후속 지시를 선택합니다. 기본 선택은 후속 지시입니다. ↑↓로 선택하고 Enter로 확정하며 ESC는 선택을 취소하고 초안을 보존합니다. 추가 지시 대상 Run이 바뀌면 전송하지 않고 오류를 표시합니다.
**ESC**는 좌측 패널로 이동하고, 패널의 **Tab·Space·Enter**는 입력창으로 이동합니다. Ctrl+D의 기존 Hub 동작은 제거했습니다.
대화의 Ctrl+X는 활성 Run을 중단하고 예약 요청을 보존합니다. 팝업의 ESC는 팝업만 닫으며 Alt+스크롤은 중단을 일으키지 않습니다.
자동완성은 입력 중 드롭다운으로 표시합니다. 지시는 `session.run.steer()`로
같은 실행에 접수하고 현재 completion/Tool이 끝난 다음 지원되는 처리 시점에 반영합니다.
지원하지 않는 엔진이나 종료된 실행에는 보내지 않고 입력을 보존합니다.

현재 키보드 포커스는 영역 제목 앞의 `▶`와 강조색으로 표시합니다.
세션 목록 제목은 ` 세션 목록`이고 스크롤바 양 끝은 `△`·`▽`입니다.
실행·응답 수신·생각 과정에는 `⚙`·`✍`·`💭` 등 상태 아이콘을 표시합니다.
``(U+F03A)는 Nerd Font 또는 Font Awesome 글리프를 지원하는 터미널 폰트가 필요합니다.
출력·미리보기·상세 영역은 포커스를 받지 않습니다. 출력창은 Alt + 이동키로 스크롤하고,
마우스로 클릭하거나 휠을 굴려도 입력 포커스를 빼앗지 않습니다. 좁은 화면에서도 ESC로
세션 목록을 열 수 있고 입력창으로 돌아가면 다시 접습니다. 설정 화면과 선택 대화상자는
Tab/Shift+Tab으로 항목을 이동합니다.
플러그인을 복사해 설치했다면 수정된 `hub/`를 설치 경로에도 반영한 뒤 ish를 재시작하세요.

F4에서 생성 또는 복제를 선택하고 이름을 입력합니다. 이름을 비우면 첫 응답 이후
메인 모델로 제목을 생성합니다. 이름 없는 복제는 복제된 대화로 제목을 생성합니다.
자동 이름 생성은 별도 모델 호출 한 번을 사용하며 `auto_title=False`로 끌 수 있습니다.
제목 생성용 Session은 UI 목록에서 제외하고 작업 후 소프트 삭제합니다. 사용자 대화에는
제목 요청이 들어가지 않습니다. 제목 모델·접속 인자는 완료된 대화의 Engine/Session 설정에서 가져오며,
`config.parameters.engines._hub_title.config.completion`의 명시적 값이 우선합니다.
대화의 JSON 출력 형식·종료 문자열·출력 토큰 제한은 제목에 자동으로 복사하지 않습니다.
실패하면 알리고 같은 설정·같은 응답으로 반복 호출하지 않습니다. 모델·접속 설정을 수정하거나
새 응답이 완료되면 다시 시도합니다. Graph처럼 직접 모델 설정이 없는 엔진은 loop 설정을 사용하며,
그것도 없거나 호스트 런타임 클라이언트만 사용하는 경우 제목 전용 completion을 설정해야 합니다.
복제는 원본의 실행과 대기열이 비었을 때 가능하며, 대화/설정은 복사하고 Run은 복사하지 않습니다.

## ish에서 실제 대화 사용

`llm/`과 `hub/`를 각각 `<ish-home>/plugin/script/llm/`,
`<ish-home>/plugin/script/hub/`에 복사합니다. 인증 환경변수는 **ish를 시작하기 전에**
설정합니다. ish 내부 자식 셸의 `export`는 이미 실행 중인 플러그인 프로세스 환경을
변경하지 않습니다.

`.ishrc.py`에 추가합니다.

```python
from pathlib import Path

hub_plugin = plugin.get("hub")
if hub_plugin is not None:
    hub = hub_plugin.install(
        prompt,
        config=hub_plugin.HubConfig(
            workspace=Path.home() / ".ish/hub-workspace",
            engine="loop",
            model="openai/YOUR_MODEL",
            language="ko",  # "en"도 지원
            file_root=Path.cwd(),  # @파일 경로 자동완성 기준 디렉터리
            # api_base="https://YOUR_SERVER/v1",
            # project_id="PROJECT_ID",
            # session_id="SESSION_ID",
        ),
        # theme=hub_plugin.HubTheme.dark(),
    )
```

Ctrl+Q로 처음 열 때 백엔드를 연결합니다. 셸로 돌아가거나 셸 명령을 실행해도
진행 중인 대화는 계속됩니다. `.ishrc.py` 재로드는 기존 설치·백엔드를 정리한 뒤
다시 설치합니다. `hub.close()`로 명시적으로 해제할 수 있습니다.

참조 ish CLI가 호출하는 `prompt.run()`을 감싸 `finally`에서 백엔드를 정리합니다.
호스트를 직접 임베딩하여 `prompt.run()`을 사용하지 않는 호출자는 자신의 종료 경계에서
`hub.close()`를 호출해야 합니다. 강제 프로세스 종료 후의 상태 복구는 llm이 담당합니다.

## 백엔드 없는 화면 미리보기

개발 저장소 루트에서 Linux Python 3.12.14, `prompt-toolkit==3.0.53`, `rich>=14,<15`로 실행합니다.

```sh
python -m examples.hub.preview
```

Hub가 열린 상태로 시작합니다. 뒤에 보이는 셸도 입력 보존을 확인하기 위한
모형이며 명령을 실행하지 않습니다. 100열 × 32행 이상을 권장합니다.
88열 미만에서는 왼쪽 목록을 숨깁니다. 116열 이상에서는 실행 상세도 열 수 있습니다.

| 키 | UI 동작 |
| --- | --- |
| Ctrl+Q (셸) | Hub 열기 |
| ESC | 메인에서 패널로 이동, 패널에서는 최소화하여 셸 복귀 |
| Tab | 선택한 자동완성 확정 (미선택 시 첫 후보). 목록이 없으면 열기 |
| ↑ / ↓ | 목록에서 샘플 Session 선택, 입력창에서 커서·자동완성 이동 |
| Alt + 이동키 | 출력창 스크롤 (방향키/Home/End/PgUp/PgDn 전체) |
| F2 | 다음 샘플 Session |
| F6 | 샘플 Run 상세 표시/숨김 |
| Enter | 입력창에서 전송 미리보기 안내만 표시; 입력 유지 |
| Ctrl+Space | 입력창 줄바꿈 |
| Ctrl+C | Hub가 열려 있으면 닫기; 독립 미리보기의 모형 셸에서는 종료 |

샘플은 완료 대화, 예약 요청 예시, 빈 대화 세 가지입니다. Session별 입력 초안은
실행 중 메모리에만 유지됩니다. 실제 실행/중단/복구 동작은 연결하지 않았습니다.

## ish에서 목업만 사용

`hub/` 폴더를 `<ish-home>/plugin/script/hub/`에 복사한 뒤 `.ishrc.py`에 추가합니다.
`PLUGIN_META`는 llm 플러그인, PTK와 Rich 의존성을 선언합니다. `config`를 생략하면 백엔드를 만들지 않는 목업 모드입니다.

```python
hub_plugin = plugin.get("hub")
if hub_plugin is not None:
    hub_preview = hub_plugin.install(prompt, preview=True)
```

ish에서는 처음에 숨겨져 있으며 Ctrl+Q로 엽니다. 셸 명령 입력이 활성화된 동안
사용하는 UI입니다. 외부 터미널 프로그램 실행 중에는 그 프로그램이 입력을 소유합니다.

`install()`은 `prompt.set_float()`와 `prompt.set_key()`를 사용합니다. Float를
다시 만들지 않고 표시 조건만 바꾸며 셸 입력을 유지합니다. Hub가 열린 동안
Application의 셸 키 바인딩을 비활성화하여 Enter/Tab 간섭을 막습니다.
Hub는 터미널의 별도 화면 버퍼(alternate screen)에 표시합니다. 패널에서 ESC로 최소화하면
기존 셸 출력과 입력 중인 명령을 복원합니다. 설정 화면에서 복귀할 때도 동일하며,
Hub가 열린 동안 들어온 셸 출력은 원래 셸 화면에 남습니다.
기존 Ctrl+Q 바인딩은 설치 동안 대체되고, `hub_preview.close()` 시 복원합니다.
같은 Prompt에 다시 설치하면 이전 설치를 먼저 해제합니다. 다른 Float는 유지합니다.

## 엔진 등록과 언어팩

`.ishrc.py`에서 엔진 인스턴스를 만드는 함수를 등록합니다. 생성은 백엔드 스레드에서
수행되며 Ctrl+E 또는 `/engine 이름`으로 요청마다 선택합니다. 이미 예약된 요청의 엔진은
바뀌지 않습니다. `engine`은 처음 선택할 엔진이며, `loop`와 `graph`는 기본 등록합니다.

```python
from llm.engines.loop import LoopEngine

config = hub_plugin.HubConfig(
    workspace=Path.home() / ".ish/hub-gemini",
    model="gemini/gemini-2.5-flash",
    engine="loop",
    language="ko",
    file_root=Path.cwd(),
    engine_factories={
        "loop": lambda: LoopEngine(completion_kwargs={"reasoning_effort": "low"}),
        "review": lambda: LoopEngine(
            system_prompt="코드를 검토하고 오류와 개선점을 설명하세요.",
            completion_kwargs={"reasoning_effort": "low"},
        ),
    },
)
hub = hub_plugin.install(prompt, config=config)
```

Gemini의 thinking/reasoning 설정은 [LiteLLM 공식 문서](https://docs.litellm.ai/docs/providers/gemini)에
따릅니다. 위 `completion_kwargs`는 엔진의 호출 옵션이므로 기존 Project에도 적용됩니다.

설정 창에서도 **Ctrl+S → 프로젝트 선택 → loop 설정 → `config.parameters.engines.loop.config.completion.reasoning_effort`**로
지정할 수 있습니다. `e`로 외부 에디터를 열어 `low` 등의 모델이 지원하는 값을 따옴표 없이 입력한 뒤
에디터에서 저장·종료하고 설정 화면에서 **저장**하세요. 저장한 프로젝트 옵션은 이후 새 Run에 적용되며 재시작 후에도 유지됩니다.
빈칸으로 저장하면 이 프로젝트의 옵션을 제거합니다. 이는 thinking 비활성화가 아니라 기본 동작으로 돌아가는 것입니다.
Session·Agent의 별도 completion 설정이나 `.ishrc.py`의 `completion_kwargs`가 있으면 해당 설정이 우선합니다.
설정 창에서 관리하려면 `.ishrc.py`에 고정한 `reasoning_effort`는 제거하세요.
모델의 thinking 동작과 텍스트 반환은 별개이며, Hub는 응답에 포함된 `reasoning_content`만 표시합니다.

모델 자체는 기존 Project의 저장된 모델을 유지합니다. 다른 Engine 구현도 같은 방식으로
등록할 수 있으며, Graph/Pipeline의 Workflow 설정과 필요한 Component는 함께 준비해야 합니다.
`component_factories`는 기본 목록에 추가하거나 같은 이름을 교체합니다. 내장 컴포넌트는 별도로 나열하지 않아도 됩니다.
`project_config`는 새 기본 Project의 설정이며 기존 Project를 덮어쓰지 않습니다.

UI 문구는 `locales/ko.py`, `locales/en.py`에 있습니다. 새 언어팩은 같은 키를 제공하고
`locales/__init__.py`에 등록합니다. 모델 답변, 프로젝트/세션 이름과 백엔드 원문 오류는
번역하지 않습니다. 독립 실행은 `python -m hub ... --language en --file-root /path/to/code`입니다.

아이콘은 `asset/icon.py`에서 관리합니다. 선택 표시, 스크롤 화살표, 상태 아이콘,
세션 목록 아이콘과 Markdown 구분선을 여기서 변경할 수 있습니다.
**Ctrl+S → 모양 → 아이콘 스타일**에서 `Nerd Font` 또는 `일반 (Unicode/이모지)`를 고릅니다.
Enter → 방향키 → Enter로 선택을 마친 뒤 저장하면 즉시 적용되고 다음 실행에도 유지됩니다.
기본값은 기존 외형을 유지하는 `nerd`이며, 일반 스타일의 저장값은 `unicode`입니다.
상단 바는 두 페이지에서 같은 위젯과 스타일을 사용하며 ` •  제목`으로 표시합니다.
세션 목록·상태·생각 과정·알림·편집 표시도 같은 스타일을 따릅니다.

하단 안내는 현재 포커스(입력창, 좌측 목록, 설정 항목의 선택/편집, 버튼, 팝업)에 맞춰 바뀝니다.
실제 키 등록과 같은 정의를 사용하며 ESC → Ctrl 조합 → Alt 조합 → 단독 키 순서, 각 그룹 안에서는 키 이름의 알파벳 순서로 표시합니다.
좁은 화면에서 전부 표시할 수 없으면 `…`로 생략합니다. 입력창 내부의 작성 안내는 유지하고,
좌측 패널 하단의 별도 조작 안내는 표시하지 않습니다.

### 컴포넌트 데이터와 설정

`/`를 입력하면 기본 명령과 **현재 프로젝트에서 활성화한 컴포넌트**만 표시합니다.
백엔드에 등록하지 않았거나 프로젝트 설정에서 해제한 컴포넌트는 표시하지 않습니다.
방향키로 후보를 선택하고 Tab으로 확정한 뒤 Enter로 실행합니다.

```text
/tools list
/tools get my_tool
/tools settings
/workflows list
/workflows get review_flow
/workflows create review_flow {"schema_version": 1, ...}
/workflows update review_flow {"description": "수정한 설명"}
/workflows delete review_flow
```

`create ID JSON`, `update ID JSON`의 데이터는 컴포넌트가 요구하는 JSON 객체입니다.
위 `...`는 실제 Workflow 정의로 채워야 합니다. 일반 데이터 수정은 최상위 키를
병합하므로 중첩 객체를 바꾸려면 그 객체의 전체 값을 입력합니다. Tool 데이터는
ToolComponent의 Python 패키지 형식을 따릅니다. `/컴포넌트 help`로 사용법을 볼 수 있습니다.
기본 명령과 이름이 겹치는 컴포넌트는 `/component:이름`으로 접근합니다.

`settings`는 해당 프로젝트의 컴포넌트 설정으로 이동합니다. 변경 후 저장을 눌러 적용합니다.

RAG의 현재 명령은 문서 JSON을 사용하는 CRUD입니다. 임베딩·추출 모델과 RAG 설정을 준비한 뒤 사용합니다.

```text
/rag create memo {"title":"메모","content":"등록할 원문","metadata":{"source":"manual"}}
/rag list
/rag get memo
/rag update memo {"content":"수정한 원문","expected_revision":1}
/rag delete memo
/rag settings
```

등록·수정·삭제는 RAGData의 문서 API를 사용하여 원문과 색인을 함께 관리합니다.
`/rag add <파일 경로 또는 run_id>`와 검색·작업 큐 관리 명령은 아직 구현하지 않았습니다.
현재 등록·수정 명령은 색인 완료까지 기다리며, 같은 BackendWorker의 다른 명령·상태 조회도 대기합니다.
별도의 색인 진행률·취소 UI는 없습니다. 삭제는 확인창에서 확정해야 실행됩니다.
조회 결과는 스크롤 가능한 팝업으로 표시하며, 오류가 나면 입력한 명령을 유지합니다.
데이터 삭제에는 확인 창이 뜹니다. 세션 삭제는 기록을 남기는 소프트 삭제인 반면,
컴포넌트의 데이터 삭제는 해당 컴포넌트 API의 삭제 동작을 수행합니다.

RAG의 명령은 문서 API에 연결됩니다. `create`는 `title`, `content`, 선택적으로
`metadata`를 받고, `update`는 변경할 필드를 받습니다. 문서 생성·수정은 색인 준비를 위해
설정된 모델을 호출할 수 있습니다. 검색·Tool 실행·Workflow 실행은 이 명령 범위에 포함하지 않습니다.

세션 목록에서 `c`/`r`로 이름을 지정하여 생성·복제하고, `e`로 이름을 변경합니다.
복제 창에서 마지막으로 포함할 요청·응답을 선택할 수 있습니다. 기본값은 최신 대화까지입니다.
`Ctrl+G`에서는 요청 요약, 엔진, 상태, 시간, 소요 시간, 응답 요약과 오류를 확인합니다.
삭제는 확인 후 해당 요청·응답·추가 지시를 대화와 이후 모델 문맥에서 제외하는 소프트 삭제입니다.
원본 JSONL 이벤트와 Run·Step 기록은 보존합니다. 실행·대기 요청 중에는 삭제할 수 없고,
미완료 재개가 참조하는 턴도 보호합니다. 재개가 완료되면 과거 paused 상태만으로 삭제를 막지 않습니다.
`Ctrl+L`은 `project.aactivity()`로 최근 실행 이력 300건을 저장된 순서로 표시합니다.
출력은 포커스를 받지 않으며 **Alt + 이동키**(↑↓←→ / Home / End / PgUp / PgDn)로 스크롤합니다.
`Enter`, `Esc`, `Ctrl+L`로 닫습니다. 조회 중에도 닫을 수 있으며, 다시 열면 최신 이력을 조회합니다.
본문은 최대 30행으로 표시하고 터미널의 여유 높이가 부족할 때만 줄어듭니다. 열린 상태의 크기 변경도 반영합니다.
각 항목에는 시각·상태·이벤트와 전체 세션/Run ID를 표시하고, Step ID와 오류 코드가 있으면 함께 표시합니다.
Run 시작·완료·실패·중단·일시 정지와 Step 실패를 표시하며, 운영 로그 파일이나 전체 도메인을 순회하지 않습니다.
이 목록은 파생 인덱스이므로 실행·복구의 기준 데이터는 기존 Run·Step입니다.

우측 하단 알림의 제목은 세션 이름입니다. `` 오류(적색), `` 정보(청색),
`` 경고(황색), `` 성공(녹색)을 사용하고 모양 설정의 `alert_*`로 색을 변경합니다.
패널에서 ESC로 셸 프롬프트에 돌아가 있어도 알림이 표시되며 입력 포커스는 유지합니다.
외부 명령 실행으로 PTK가 화면을 그리지 않는 동안의 변경은 다음 프롬프트에서 확인합니다.
명시적으로 바꾼 이름은 자동 이름 생성으로 덮어쓰지 않습니다. `d`는 확인 후 소프트 삭제하며,
실행·예약 요청·자동 이름 생성이 진행 중이면 삭제를 막습니다. 마지막 세션을 삭제하면 새 세션을 준비합니다.

### 요청별 Graph Workflow 선택

등록된 GraphEngine은 Ctrl+E 실행 방식 선택창에서 Workflow 선택 항목을 표시합니다.
`/engine graph`도 같은 창을 엽니다. 목록은 현재 프로젝트에서 활성화한 `workflows`
컴포넌트의 저장된 정의를 읽습니다. 방향키로 엔진을 고르고 Tab으로 Workflow 목록으로
이동한 뒤, 원하는 항목을 고르고 Tab → Enter로 확인합니다. Esc는 변경을 취소합니다.
선택된 엔진과 Workflow는 Ctrl+E로 다시 열어 확인할 수 있습니다. 입력창 제목에는 모델 이름을 표시합니다.

```python
from llm.components.agents import AgentComponent
from llm.components.tools import ToolComponent
from llm.components.workflows import WorkflowComponent
from llm.engines.graph import GraphEngine
from llm.engines.graph.agent import AgentNode
from llm.engines.graph.tool import ToolNode
from llm.engines.loop import LoopEngine

def make_graph():
    return GraphEngine(handlers={
        "agent": AgentNode(engines={"loop": LoopEngine()}),
        "tool": ToolNode(),
    })

# 기존 HubConfig에 아래 항목을 추가합니다.
# engine_factories={"graph": make_graph},
# component_factories=(ToolComponent, WorkflowComponent, AgentComponent),
```

새 프로젝트에는 모든 내장 컴포넌트가 등록됩니다. 기존 프로젝트의 선택은 보존되며,
추가 활성화가 필요하면 llm의 공개 `Project.components.aselect()` API로 선택을 변경합니다.
Workflow와 참조 Agent/Tool 정의는 llm 공개 API로 미리 등록해야 합니다.
[Workflow 등록](../llm/components/workflows/README.md)과
[Agent/Graph 예제](../docs/llm/graph-engine.md)를 참고하세요. 정의 편집기와 전체 그래프
미리보기는 이 선택창에 포함하지 않습니다. 현재는 설명·시작 노드·최상위 노드 수를 표시합니다.

Hub는 전송 시 `session.run.submit(text, engine=name, engine_options={"workflow": id})`를
호출합니다. 선택값은 프로젝트·세션·엔진별로 Hub 실행 중 기억하고, 재시작 후에는 다시
선택합니다. 이미 접수된 요청의 옵션은 llm 저장소에 남으므로 이후 UI 선택 변경에 영향을
받지 않습니다. 예약 요청은 해당 ID의 정의를 실행 시작 시 읽으므로 정의 내용까지 접수 시점에
고정되는 것은 아닙니다. Workflow가 없으면 Graph 선택을 확정할 수 없고, 접수 오류 시 입력은 유지됩니다.

입력창 명령은 `/help`, `/engine`, `/new`, `/clone`, `/preview`, `/stop`, `/details`입니다.
`/new 이름`, `/clone 이름`은 이름이 채워진 확인 창을 엽니다. `/`는 명령,
`/engine `은 엔진, `@src/`는 지정한 루트 아래 경로 후보를 자동으로 표시합니다.
방향키로 선택하고 Enter로 확정하며, 다시 Enter를 누르면 전송합니다.
방향키 ↑↓로 완성 후보를 선택하고 Tab으로 확정합니다. 후보를 고르기 전 Tab을 누르면
첫 후보를 확정합니다. Ctrl+Space는 미확정 후보를 취소하고 원래 입력에 줄바꿈을 넣습니다.
ESC로 좌측 패널에 이동하면
선택 중인 후보를 취소하고 입력한 원문을 유지합니다. 경로는 텍스트로 삽입하며
파일 내용 자체를 첨부하거나 읽어서 모델에 전송하지 않습니다.

## 메시지와 테마

사용자 메시지는 오른쪽 정렬이며, 긴 문장은 대화 영역 폭의 약 78% 안에서 줄바꿈합니다.
한글을 포함한 터미널 셀 너비로 계산하고, 창 크기나 상세 패널 폭이 바뀌면 다시 배치합니다.
전송한 사용자 메시지와 Assistant 응답 모두 기본적으로
[Rich Markdown](https://rich.readthedocs.io/en/stable/markdown.html)을 적용합니다.
제목, 강조, 목록, 코드 구문 강조, 표, 인용을 PTK의 스타일 fragment로 변환합니다.
Rich가 터미널에 직접 출력하지 않으며 PTK가 화면과 스크롤을 소유합니다.
동일한 내용·폭·테마는 다시 렌더링하지 않도록 뷰별로 캐시합니다.
입력창은 Markdown 원문을 편집합니다. 내용과 자동 줄바꿈에 맞춰 1~8줄로 늘어나며,
8줄을 넘으면 입력창 내부를 스크롤합니다. 작은 터미널에서는 가용 높이에 맞춰 줄어듭니다.
Rich 미리보기는 기본적으로 접혀 있고 F5로 엽니다.
입력창과 미리보기는 각각 테두리 박스로 표시합니다. 미리보기는 고정 높이를 유지해 출력 영역과
겹치지 않으며, 높이 24줄 미만에서는 접힙니다. Markdown 구분선(`---`)은 `──────`로 표시하고,
코드나 일반 텍스트의 하이픈은 변경하지 않습니다.
대화/미리보기에서 방향키와 마우스 휠은 커서가 경계에 도달하기 전에도 즉시 스크롤합니다.

사용자 본문은 Rich의 채움 공백을 제외한 내용 폭으로 배경 블록을 만들고 오른쪽에 붙입니다.
헤더는 `계정명 · 접수됨 · 2026-10-03 12:34:56` 형식이며, 상태와 시간은 따로 표시합니다.
시각은 저장된 메시지의 생성 시각을 실행 환경의 로컬 시간대로 변환한 값입니다.
어시스턴트 헤더는 `어시스턴트 · 완료 · 소요 1분 2.5초`처럼 날짜 대신 실행 시간을 표시합니다.
연결된 Run의 시작부터 종료까지의 시간으로, 예약 요청이 실행 순서를 기다린 시간은 제외합니다.
응답 중에는 현재까지의 시간을 갱신하고 완료·실패·중단 후에는 종료 시점의 값으로 고정합니다.
재실행해도 저장된 Run으로 복원하며, 복제된 대화나 실행 기록이 삭제된 메시지는
`소요 시간 알 수 없음`으로 표시합니다.

`예약`은 QUEUED 요청, 즉 현재 실행이 끝나면 순서대로 실행되는 요청입니다.
지정 시각에 실행하는 일정 예약은 아닙니다. 실행할 요청이 없는 IDLE은 `유휴`,
아직 시작하지 않은 PENDING 실행 단위는 `실행 전`으로 구분합니다.

이름은 기본적으로 현재 OS 계정명이며, 이후 프로필 설정에서도 같은 `UserProfile`을 사용할 수 있습니다.
Ctrl+S → 프로필에서 변경하거나 `HubConfig(..., user_profile=hub_plugin.UserProfile(display_name="표시할 이름"))`으로 초기값을 지정합니다.

읽던 위치와 마지막으로 선택한 Session은 `<workspace>/.hub/view-state.json`에 저장합니다.
스크롤 후 짧은 지연을 두고 저장하며, Session 전환·Hub 숨김·종료 시에도 저장합니다.
메시지 ID와 해당 메시지 안의 줄 위치를 이용해 전환·재실행 때 복원합니다.
명시한 `HubConfig.session_id`는 마지막 선택보다 우선합니다. 저장된 메시지가 삭제되었으면
남은 대화 범위 안으로 위치를 보정합니다. UI 위치 파일은 LLM 대화 기록과 별개입니다.

대화 본문과 입력창 내부는 기본적으로 **터미널 기본 전경·배경색**을 사용합니다.
색상은 배경(`background`), 전경(`foreground`), 강조 1~3(`accent1`, `accent2`, `accent3`),
주석(`comment`)의 6개 팔레트로 관리합니다. 강조 1은 선택·진행·성공, 강조 2는 사용자·정보,
강조 3은 안내·경고·오류에 사용하고 주석 색은 설명·구분선에 사용합니다.
사용자 본문·바·코드 블록·버튼·자동완성 등의 배경은 팔레트에서 자동으로 파생합니다.
기존 저장 설정은 주요 색을 새 팔레트로 읽고 위젯별 세부 색상은 더 이상 적용하지 않습니다.
Ctrl+S → 전역 설정 → 모양에서 색을 변경할 수 있습니다. 답변 뒤 다음 사용자 요청 앞에는
기존보다 빈 줄 두 개를 추가해 요청 단위를 구분합니다.
배경색을 `default`로 지정하면 터미널 기본 배경을 사용합니다. 투명도 자체는 터미널 프로그램 설정을 따르며, 뒤쪽 셸 글자가 비치는
Float 투명 모드와는 다릅니다. 배경의 셸 글자는 Hub 아래에서 가려집니다.

Ctrl+S → 모양에서 색을 변경하고 저장하면 즉시 적용됩니다. `.ishrc.py`에서 초기 테마를
지정할 수도 있으며, 화면에서 저장한 설정이 다음 실행에서도 우선합니다.

```python
hub_plugin = plugin.get("hub")
if hub_plugin is not None:
    hub_preview = hub_plugin.install(
        prompt,
        preview=True,
        theme=hub_plugin.HubTheme(
            background="default",  # 터미널 배경 유지
            foreground="default",  # 터미널 글자색 유지
            accent1="#46b59e",
            accent2="#67a9dd",
            accent3="#bc9850",
            comment="#8595a5",
        ),
    )
    # 표시 중에도 입력이나 선택을 잃지 않고 적용할 수 있습니다.
    # hub_preview.set_theme(hub_plugin.HubTheme.dark())
```

색상값은 `default` 또는 `#RRGGBB`입니다. 어두운 배경을 직접 지정한 예제는
`python -m examples.hub.preview --theme dark`로 확인합니다.

## 설정 화면

셸에서는 Ctrl+Q로 Hub를 연 뒤 Ctrl+S로 설정을 엽니다. 셸의 Ctrl+S 바인딩은 변경하지 않습니다. 설정 메인 영역의 Ctrl+S는 현재 페이지를 저장한 뒤
대화로 돌아갑니다. 저장 실패 시 화면과 초안을 유지합니다. 좌측 패널의 Ctrl+S 또는 Ctrl+C로 저장 없이 돌아갈 수 있습니다.
ESC는 메인에서 패널로 이동하며, 이미 패널이면 최소화하여 셸로 복귀합니다.
좌측 패널의 Tab·Space·Enter는 메인 영역으로 이동합니다. 메인은 설정 항목과 제목 없는 하단 버튼 영역의
두 부분이며 Tab / Shift+Tab으로 순환합니다. 별도 추가 기능 체크 영역은 없습니다.
중앙과 하단은 좌측 패널을 제외한 전체 너비를 사용하고, 하단 버튼은 가운데 정렬하며 좁은 화면에서는 줄을 나눕니다.
버튼은 양옆 기호 없이 전체 배경색으로 표시하고, 선택·포커스 시 글자만 강조 색과 볼드체로 표시합니다.
버튼에 포커스가 있을 때만 버튼의 커서를 숨기며 입력창과 셸의 커서에는 영향을 주지 않습니다.
버튼·체크 목록·스크롤바 색상도 6개 팔레트에서 파생합니다.
체크 목록은 위젯 전체에 배경을 적용하고, 선택·포커스에 따른 배경색 변경은 하지 않습니다.
스크롤 트랙과 손잡이는 공백으로 그리며 손잡이만 강조 색 배경을 사용합니다. 트랙과 △·▽의 배경은 투명합니다.
두 화면은 `ui/layout/TwoPanelPage`를 공유합니다. 메인 하단은 좌우에만 1칸 여백을 두고 항상 1행이며 진행 중에는
로딩 바, 그 외에는 알림을 표시합니다. 알림이 없으면 같은 행을 빈칸으로 유지합니다.
대화의 세션 목록과 설정의 전역·프로젝트 목록은 공통 `SidebarList`를 사용합니다.
설정 목록에서 ↑↓로 이동하거나 클릭하면 Enter 없이 해당 페이지가 열리며, 작성 중인 다른 페이지 초안은 유지됩니다.
조회 중 목록을 빠르게 이동하면 마지막 선택을 적용합니다. 좌측 하단 안내는 생성·삭제·이름·복제만 표시합니다.
각 영역 안에서는 방향키로 항목을 선택합니다.
중앙 입력칸에서 Enter로 직접 편집을 시작하고, 다시 Enter로 편집을 마칩니다.
직접 편집 중에는 방향키로 커서를 이동하고 Ctrl+Space 또는 Alt+Enter로 줄바꿈합니다.
항목 선택 상태에서 `e` 또는 `E`를 누르면 일반 설정에 저장된 외부 에디터가 열립니다.
직접 편집 중 `e`와 `E`는 일반 문자로 입력됩니다.
에디터에서 저장·종료하면 초안으로 돌아오며, **저장** 또는 Ctrl+S로 실제 설정을 저장합니다.
에디터가 실패하면 이전 초안을 유지합니다. 입력칸은 내용에 따라 1~3줄을 표시합니다.
ESC는 좌측 패널로 이동합니다. 설정에서는 Alt + 이동키 조합을 무시하며,
대화의 출력이나 포커스에 영향을 주지 않습니다.
ESC와 Alt/VT100 시퀀스를 구분하기 위해 Hub가 열려 있는 동안 PTK의 ttimeoutlen과 timeoutlen을
각각 0.02초로 설정합니다(단독 ESC 판별 대기 약 40ms). 셸로 돌아가면 원래 값을 복원합니다.
ESC를 무조건 즉시 확정하는 eager 바인딩은 Alt 조합을 깨뜨리므로 사용하지 않습니다.

왼쪽 위에는 전역 **프로필·모양·일반**, 아래에는 **프로젝트 목록**이 있습니다.
전역 설정은 이 workspace의 모든 Project에 적용되고 `<workspace>/.hub/preferences.json`에
원자적으로 저장됩니다. 다른 workspace에는 영향을 주지 않습니다.
설정 창에서도 패널에서 ESC를 누르면 셸로 복귀하며 저장하지 않은 초안은 유지합니다.

**일반**은 언어 팩·알림·시작 동작·대화 출력·외부 편집기 카테고리로 나뉩니다.

* 언어 팩: `language`에 `ko`, `en` 또는 사용자 팩 코드를 지정합니다. **팩 추가/갱신**에서 코드와
  메시지 키 → 번역 문자열 JSON(예: `{"settings_title": "My Settings"}`)을 입력합니다. 기존 코드면 교체하며,
  **팩 삭제**에서 사용자 팩을 제거합니다. 내장 팩은 유지하고 빠진 번역은 영어로 표시합니다.
  팩 변경도 **저장**으로 확정하며 **취소**로 초안을 버립니다. 언어 변경은 다음 Hub 시작부터 적용됩니다.
  이전 프로필 언어 설정은 일반 언어가 비었을 때 호환 기본값으로 사용합니다.
* 알림: `notification_kinds`는 `error`, `info`, `warning`, `success`의 JSON 배열이며 `[]`로 모두 끕니다.
  `notification_seconds`는 1~120초입니다. 종류 변경은 즉시 반영하고 시간은 이후 생성되는 알림에 적용합니다.
* 시작 동작: `restore_last_project`, `restore_last_session`을 각각 켜거나 끕니다(기본 `true`).
  `.ishrc.py`의 명시적인 프로젝트·세션 선택이 우선하며, 없어진 저장 대상은 기본 프로젝트·활성 세션으로 대체합니다.
* 대화 출력: `auto_scroll`은 기본 `true`입니다. 맨 아래를 보고 있을 때만 새 응답을 따라갑니다.
  `false`로 저장하면 읽던 위치를 유지하며 Alt + 이동키 수동 이동은 계속 가능합니다.

외부 편집기의 `editor`에는 외부 편집 작업에 사용할 명령(예: `vim`, `nano`, `code --wait`)을 미리 저장합니다.
비우면 `VISUAL`, `EDITOR`, `vi` 순서로 선택합니다. `GeneralSettings.editor_argv(path)`는 실행 인자를 반환하며
셸 코드를 해석하지 않습니다. 설정 입력칸의 `e`는 현재 적용된 에디터 명령을 사용합니다.
문자열은 텍스트 파일, 나머지 값은 JSON 파일로 편집합니다. 에디터가 자동 추가하는 마지막 개행은
원래 값에 없었으면 한 개 제거하며, 내용 중간의 개행은 유지합니다. `code`처럼 별도 창을 여는 에디터는
`--wait`처럼 종료를 기다리는 옵션을 지정해야 편집 결과를 받을 수 있습니다.
**프로필**에는 표시 이름, 이메일, 전화번호를 설정할 수 있습니다. 언어 선택은 일반 설정으로 옮겼습니다.
아래 **사용 통계**는 `Project.amodel_usage()`로 읽은 활성 프로젝트별
호출 횟수, 확인된 토큰, 예약 토큰, 사용량 미확인 호출 수와 각 프로젝트의 집계 기간을 표시합니다.
통계 영역으로 이동한 뒤 ↑↓로 스크롤하며 **불러오기**로 갱신합니다. 조회 오류는 해당 프로젝트에 표시합니다.

좌측 프로젝트 목록에서 c로 생성, d로 삭제, e로 이름 변경, r로 복제합니다.
하단에는 같은 너비의 **저장·불러오기·취소**가 있으며 기존 프로젝트는 **열기** 버튼으로 대화를 전환합니다.
좌측 프로젝트 영역에는 생성 버튼 없이 목록만 표시하며, 목록에서 `c`로 생성합니다.
프로젝트가 없어도 `프로젝트 없음` 영역에 포커스를 옮긴 뒤 `c`로 생성할 수 있습니다.
키·설명·`- 타입 : ...` 안내는 llm의 공개 JSON Schema에서 생성하며 타입은 입력창 위에 표시합니다.
기본 설정 아래에는 등록된 엔진과 해당 프로젝트 컴포넌트의 설정이 나타납니다.

문자열은 그대로, 숫자·불리언·배열·객체는 JSON으로 입력합니다. 빈칸은 미설정,
`null`은 명시적 null, 빈 문자열은 `""`입니다. 복합 스키마는 JSON 입력창으로 표시합니다.
입력하지 않은 설정과 알 수 없는 기존 키를 보존하고, 저장 시 타입·제약·설정 버전을 검증합니다.
입력값은 **저장** 버튼으로 적용합니다. 변경 사항이 있으면 `* 저장`으로 표시합니다.
**취소**는 해당 페이지의 초안을 버리고, **불러오기**는 저장된 최신 값을 불러옵니다.
페이지를 전환해도 초안은 유지됩니다. 저장해도 이미 접수된 요청은 변경하지 않습니다.

좌측 전역 설정 또는 프로젝트 영역의 ←/→는 공통 패널 너비를 한 칸씩 조절합니다. `모양 → sidebar_width`에서
18~60칸 범위로 지정할 수도 있습니다. 변경한 너비는 미리보기로 즉시 적용되며 **모양 페이지에서
저장하거나 취소**합니다. 대화의 세션 패널에도 같은 너비가 적용되며 작은 터미널에서는 화면 폭에 맞춰 제한됩니다.

새 프로젝트의 이름·모델·선택 설정은 빈칸으로 시작하며 현재 프로젝트 값을 복사하지 않습니다.
이름은 직접 지정해야 하며, 대화 저장 방식을 비워 두면 생성 시 file을 사용합니다.
모델은 나중에 지정할 수 있지만 설정 전에는 대화 요청을 전송할 수 없습니다.
기존 프로젝트를 열거나 복제할 때는 저장된 설정을 유지하며, 복제 버튼에서도 새 이름을 지정합니다.
삭제는 확인창을 거치는 소프트 삭제입니다. 실행 또는 예약 요청이 있으면 복제·삭제를
거부합니다. 현재 대화에 열린 프로젝트를 삭제하려면 다른 프로젝트의 **열기**로
먼저 전환하세요. 대화 저장 방식은 Session이 없는 프로젝트에서만 변경할 수 있습니다.

## 파일 안내

### 확장 출력 블록

Assistant 응답의 독립된 줄에 XML 요소를 넣으면 `OutputParser`가 Markdown과
`OutputBlock`으로 분리하고 `RendererRegistry`가 해당 렌더러로 전달합니다.

```xml
<hub-image src="images/result.png" alt="생성 결과" />
```

`src`는 현재 프로젝트의 파일 루트(또는 HubConfig.file_root)를 기준으로 해석합니다.
코드 블록·인라인 코드·들여쓴 코드의 태그는 해석하지 않습니다. 알 수 없는 태그와
잘못된 XML은 원문으로 표시합니다. 태그는 스트리밍 중 완성된 후 처리합니다.
본문에 `<`나 `&`가 필요하면 XML 이스케이프를 사용합니다. 중첩 요소와 DTD/CDATA는 지원하지 않습니다.
기존 Markdown 이미지를 자동 변환하는 기능이나 diff 전용 렌더러는 아직 포함하지 않습니다.

이미지는 ascii_magic으로 UI 스레드 밖에서 변환하고 PTK 문자·색상으로 그립니다.
외부 URL은 자동 다운로드하지 않으며, 이미지 변환 자체가 모델 호출을 발생시키지 않습니다.
파일은 16 MiB·800만 픽셀 이내, 출력은 최대 100열·40행으로 제한합니다.
한 세션 화면에서 최대 32개를 캐시하며, 세션 전환·너비 변경 시 캐시를 비웁니다.
원본 이미지가 같은 경로에서 바뀌면 `view.image_renderer.reset()` 후 화면을 갱신하세요.

`.ishrc.py`에서 실제 파일을 명시적으로 연결할 수도 있습니다.

```python
installation = hub_plugin.install(prompt)
installation.view.image_renderer.register_artifact("preview", "/path/to/result.png")
# 응답: <hub-image src="artifact:preview" alt="결과" />
```

아티팩트 매핑은 UI 실행 중에만 유지됩니다. 재시작 후에도 표시하려면 다시 등록하거나
프로젝트 상대 경로를 사용하세요. 새로운 렌더러는 다음처럼 등록합니다.

```python
class BadgeRenderer:
    def render(self, block, context):
        # context: width, theme, language, root, invalidate
        return [[("class:hub.accent", dict(block.attributes).get("text", ""))]]

installation.view.output_renderers.register("hub-badge", BadgeRenderer())
# 응답: <hub-badge text="완료" />
```

렌더러는 PTK `(style, text)` 조각의 행 목록을 반환하며 터미널에 직접 출력하지 않습니다.
시간이 걸리는 작업은 백그라운드에서 처리하고, 완료 시 `context.invalidate()`를 호출합니다.
캐시 갱신 시 렌더러의 `revision` 값을 증가시키면 대화 출력 캐시도 갱신됩니다.

### 설정과 초기 선택의 소유권

llm은 기본 Project/Session을 만들거나 선택하지 않습니다. Hub가 최초 템플릿과 마지막
선택을 소유합니다. 명시한 Project ID, 복원 가능한 마지막 Project, 가장 오래된 활성
Project 순으로 선택하며, 활성 Project가 없을 때만 `projects.acreate()`를 호출합니다.
`.hub/startup.lock`으로 동시 Hub 시작을 직렬화합니다. llm의 default-project 포인터는 없습니다.
Session은 사용자가 생성합니다. Session 설정은 `parameters.engines.<이름>`에서 명시적으로
덮어쓰고 Project 값을 생성 시 복사하지 않습니다. 프로젝트 공용 전달 인자는 대상별
딕셔너리이며, Loop 모델 설정은 `config.parameters.engines.loop.config.completion`에 둡니다.

HubConfig.model/api_base는 **신규 Project**의 loop에 적용하는
Hub 템플릿입니다. 다른 사용자 엔진에는 자동 전파하지 않습니다. 이름이 다른 엔진은
project_config.parameters.engines에 명시하세요. 자동 제목의 모델 역시 `_hub_title.config.completion`에서
별도로 편집할 수 있습니다. 구성 API는 모든 컴포넌트 설정과 출처를 계속 한 번에 제공합니다.

### 기본 시스템 프롬프트와 스킬

새 프로젝트에는 `asset/guides.py`의 기본 시스템 프롬프트와 세 가지 Skill을 복사합니다.
초기 실행과 설정 화면의 프로젝트 생성 모두 동일한 템플릿을 사용합니다.
모델 이름이나 API 키는 기본값으로 채우지 않습니다. 기존 프로젝트는 자동 변경하지 않습니다.

| Skill ID | 용도 |
| --- | --- |
| `hub-code-change` | 코드 맥락 확인, 필요한 수정, 검증과 결과 보고 |
| `hub-diagnose` | 재현, 증거와 가설 구분, 원인 수정과 재검증 |
| `hub-document` | 독자·목적에 맞춘 작성, 근거·예시·형식 확인 |

시스템 프롬프트는 `config.parameters.engines.loop.config.system_prompt`에 저장하며 설정 화면에서
편집할 수 있습니다. 생성 시 명시한 값(빈 문자열·null 포함)을 유지합니다. 프롬프트는 사용자의
언어로 응답하고, 실제 가능한 작업을 수행·검증하며, 관련 Skill을 먼저 찾아 읽도록 안내합니다.
등록된 Skill은 `/skills list`, `/skills get hub-code-change`, `/skills update`, `/skills delete`로
관리합니다. 수정·삭제한 내용을 다시 열 때 복원하거나 덮어쓰지 않습니다.

일반 Loop는 llm이 제공하는 `skill_list`·`skill_read` Tool로 필요한 지침을 읽을 수 있습니다.
Graph Agent는 `tools` 허용 목록에 이 두 도구를 포함하거나 `resources.skills`에 Skill ID를 연결합니다.
Skill은 지침이며 실행 도구나 권한을 제공하지 않습니다. 파일 수정·셸 실행·웹 검색은 해당 Tool이
별도로 등록되고 실행 정책에서 허용되어야 합니다. Skill 준수나 결과의 품질을 강제로 보장하지는 않습니다.

진행 중인 컴포넌트 명령은 입력창 아래에 Rich 진행 막대와 작업명·경과 시간을 표시합니다.
설정 불러오기·저장에도 같은 표시를 화면 하단에 사용하며, 작업이 끝나거나 실패하면 알림으로 바뀝니다.
진행 표시는 포커스를 받지 않으며, 대화·설정 화면에서 항상 한 줄의 공간을 확보합니다.
작업이 없으면 알림 또는 빈 줄을 유지하고, 실행 중에는 Rich 기본 길이(40칸)의 막대를 표시합니다.
여러 작업이 겹치면 최근 시작한 작업과 나머지 작업 수를 한 줄에 표시합니다.
설정 화면의 별도 로딩 문구는 표시하지 않으며, 오류·저장 결과 안내는 유지합니다.
프로젝트·모양 설정은 화면에 보이는 항목 위주로 렌더링하고 항목 높이를 캐시하여,
컴포넌트 설정이 많아져도 화면 밖 입력칸을 매번 그리지 않습니다.
현재 컴포넌트·설정 API는 세부 진행률을 전달하지 않으므로 퍼센트 없는 진행 막대를 사용합니다.
`/rag create`, `/rag update`는 대기 팝업 대신 이 표시와 완료 알림을 사용합니다.
이는 진행 UI 연결이며, `/rag add file/run` 및 RAG 작업 큐 연결은 아직 지원하지 않습니다.
현재 백엔드 공용 잠금 때문에 등록 중 다른 백엔드 명령은 여전히 기다릴 수 있습니다.

- `hub.py`: 플러그인 메타데이터, 설치/해제, 호스트 키·스타일·종료 연결.
- `backend/runtime.py`: 공개 llm API를 통한 Project/Session/요청/조회.
- `backend/worker.py`: 백엔드 전용 스레드와 이벤트 루프, 작업·구독 정리.
- `ui/live.py`: 실제 대화 UI 상태, 요청 접수와 오류 처리.
- `ui/application.py`, `__main__.py`: 독립 실행 Application과 CLI.
- `ui/view.py`: 컨테이너, 입력창, 대화상자, 포커스와 키 동작.
- `ui/mockup.py`: 백엔드 없는 샘플 데이터와 미리보기 뷰.
- `ui/chat/completion.py`: 명령·엔진·프로젝트 경로 자동완성.
- `backend/naming.py`: 분리된 Session/Run을 통한 모델 제목 생성.
- `locales/`: 한국어/영어 언어팩과 문구 조회.
- `widget/conversation.py`: 메시지별 정렬, Rich Markdown 변환, 스크롤과 렌더 캐시.
- `widget/controls.py`, `widget/buttons.py`, `widget/actions.py`: 버튼·입력·선택·스크롤 기본 위젯, 저장 버튼과 버튼 행.
- `widget/settings.py`, `widget/settings_viewport.py`: 이름·설명·타입·값으로 구성한 설정 필드와 가상 스크롤.
- `widget/general_settings.py`: 일반 설정 카테고리와 언어 팩 편집 폼.
- `widget/sidebar.py`, `widget/shortcuts.py`, `widget/section.py`: 좌측 목록, 하단 단축키 표시, 섹션 제목.
- `widget/dialog.py`, `widget/reader.py`, `widget/search.py`, `widget/execution.py`, `widget/notifications.py`: 팝업과 알림.
- `widget/text_viewport.py`, `widget/progress.py`, `widget/welcome.py`: 읽기 전용 출력, 진행 표시, 안내 화면.
- `model.py`: 백엔드 어댑터와 렌더링이 함께 사용하는 메시지 표시 데이터.
- `config/theme.py`: 터미널 기본색과 사용자 색상 설정, PTK 스타일 생성.
- `config/profile.py`: 현재 계정명을 기본값으로 하는 사용자 표시 프로필.
- `config/view_state.py`: Project/Session별 읽기 위치의 원자적 저장.
- `ui/settings/screen.py`, `ui/settings/pages.py`: 패널·영역 이동, 페이지 초안과 저장 동작.
- `ui/input/`: 포커스 문맥, 키·조건·동작·안내의 단일 등록부, 기본 명령 처리.
- `ui/dialogs.py`, `ui/chat/session_actions.py`: 팝업 수명과 포커스 복원, 세션 작업 흐름.
- `backend/snapshot.py`, `ui/presentation.py`: 공개 API 조회와 언어·아이콘·시간 표시 변환.
- `widget/header.py`, `widget/session_form.py`: 공통 상단 바와 세션 생성/복제 폼.
- `ui/layout/`: 대화·설정 화면에서 공유하는 페이지 배치.
- `ui/output/`: Markdown/XML 파싱과 출력 렌더러 레지스트리.
- `backend/engine_selection.py`: 등록된 공개 Hub 엔진 목록.
- `backend/settings_service.py`: 공개 llm API를 통한 설정 검증·저장·프로젝트 관리.
- `config/preferences.py`: workspace 공통 프로필·모양 설정 저장.
- `__init__.py`: 플러그인 패키지.

테스트는 `tests/hub/`, 독립 실행 예제는 `examples/hub/`, 설계 문서는 `docs/hub/`에
둡니다. 별도 예제 Application은 개발용이며 ish 안에서는 새 Application을 실행하지 않습니다.

설치·실행 진입점과 공유 데이터 모델 외에는 `backend/`, `config/`, `ui/`, `widget/`로 역할을 구분합니다.
대화 동작은 `ui/chat/`, 설정 페이지 제어는 `ui/settings/`, UI 표시 위젯은 사용 횟수와 무관하게
`widget/`에 있습니다. 위젯은 llm 서비스를 호출하지 않고 데이터와 콜백을 전달받습니다.
`.ishrc.py`의 `hub_plugin.install(prompt)`와 플러그인이 제공하는 설정 클래스는 그대로 사용할 수 있습니다.
내부 모듈을 직접 import하는 코드는 새 패키지 경로를 사용합니다.
