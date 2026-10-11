# 출력 객체와 컴포넌트 관리

Hub의 기본 대화 출력은 Markdown입니다. 일반 Markdown의 코드 블록과 표,
등록된 `hub-*` XML 블록은 `OutputObject`로도 관리합니다.
객체에는 안정적인 ID(메시지 ID + 원문 오프셋), `OutputBlock`, 제목,
PTK 렌더링 행, 화면 시작/끝 행, 손실 없는 `raw` 원문이 있습니다.
화면 너비·테마·이미지 변환 결과가 바뀌면 렌더링 좌표만 다시 계산합니다.

`Ctrl+T`는 기존 오른쪽 상세 영역을 대체한 태그 바를 열고 포커스를 옮깁니다.
↑↓로 선택하면 해당 객체로 스크롤하고, `c`는 마크업을 포함한 원문을 복사합니다.
`Ctrl+T / ESC`로 닫고, `Tab / Space / Enter`로 선택한 객체를 전체 화면 팝업에서 봅니다.
태그 바는 제목 없이 왼쪽 구분선만 표시합니다. 팝업은 호스트가 할당한 전체 Hub 영역을 채웁니다.
확대 화면은 객체 종류에 맞는 상호작용 UI를 사용합니다. `ESC / Ctrl+T`로 닫으면
태그 바에 포커스가 돌아옵니다. 복사는 PTK 클립보드와 사용 가능한
wl-copy / xclip / xsel을 사용하며, 없으면 터미널 OSC 52에 요청합니다.
OSC 52 실제 시스템 복사는 터미널의 지원·허용 설정에 따라 달라집니다.

## 전체 화면에서 조작하기

일반 Markdown 표와 `hub-table`은 같은 표 뷰어를 사용합니다.

| 키 | 동작 |
| --- | --- |
| ↑ / ↓, Home / End, PgUp / PgDn | 행 선택 |
| ← / → | 열 선택, 해당 열로 가로 이동 |
| Enter / s | 선택한 열로 오름차순·내림차순 정렬 |
| Ctrl+F 또는 / | 전체 열의 문자열을 대상으로 필터 입력 |
| r | 표 화면과 원문 화면 전환 |
| c | 필터·정렬 전의 원본 전체 복사 |

숫자는 숫자 크기로 정렬하며, 문자열은 대소문자를 구분하지 않습니다.
정렬 후에도 같은 원본 행을 선택합니다. 필터에서 선택한 행이 사라질 때만 첫 행으로 이동합니다.
원본 행과 셀 값은 바꾸지 않으며, 표시가 긴 셀은 생략합니다. 전체 값은 원문 보기에서 확인합니다.
표는 최대 32열·500행·UTF-8 256 KiB로 제한합니다. XML 파서는 별도로 기존 입력 길이 제한을 유지합니다.
잘못된 표도 오류 안내와 원문 보기·복사는 사용할 수 있습니다.

코드·diff·카드 본문 등은 기존 렌더러를 다시 사용하며 다음 기능을 제공합니다.

| 키 | 동작 |
| --- | --- |
| 이동키 | 화면 스크롤 |
| Ctrl+F 또는 / | 표시된 내용에서 검색 |
| n / Ctrl+N, N / Ctrl+P | 다음·이전 일치 위치로 이동 |
| r | 렌더링과 원문 보기 전환 |
| c | 마크업을 포함한 원본 전체 복사 |

검색·필터 입력 중 Enter / Tab은 적용 후 뷰어로 돌아가고, ESC는 입력 전 검색어를 복구합니다.
뷰어에서 ESC를 한 번 더 누르면 닫힙니다. 원문 보기에서도 검색할 수 있습니다.
검색·정렬·필터 상태는 열린 팝업에만 존재하며 닫을 때 폐기합니다. 대화 검색 하이라이트,
대화 원문, 입력 초안, llm 상태에는 영향을 주지 않습니다.

Rich 렌더링·검색 인덱싱·표 해석은 공유 렌더 작업자의 `popup` 슬롯에서 처리합니다.
표는 화면에 보이는 행만 그립니다. 크기가 바뀌어도 선택·필터를 유지하며,
팝업을 닫으면 대기 중인 렌더 결과가 이후 화면에 적용되지 않습니다.

렌더 작업자, 캐시 수명, 최신 요청 병합과 성능 검증은
[렌더링 파이프라인](rendering-performance.md)을 참고하세요.

## 기본 XML 렌더러

태그는 독립된 줄에서 시작해야 합니다. XML의 `<`, `&`는 이스케이프합니다.
코드 펜스 내부의 XML은 실행하거나 해석하지 않습니다.

```xml
<hub-image src="images/result.png" alt="결과 이미지" />

<hub-code language="python" title="예제">print("hello")</hub-code>

<hub-diff title="변경 사항">--- before.py
+++ after.py
@@ -1 +1 @@
-old_value
+new_value</hub-diff>

<hub-table title="결과">{"columns":["항목","값"],"rows":[["A",10],["B",20]]}</hub-table>

<hub-chart title="처리량">{"values":{"A":10,"B":20}}</hub-chart>

<hub-card title="요약">**작업이 완료되었습니다.**</hub-card>
```

기본 XML UI는 터미널용 표시 객체입니다. 브라우저 HTML/JavaScript나 모델이
지정한 Python 클래스를 실행하지 않습니다. 등록되지 않은 태그는 원문으로 표시하고,
잘못된 구조는 오류 표시로 처리합니다. 이미지는 기존 ascii_magic 렌더러를 사용합니다.
카드는 Markdown, 표는 JSON columns/rows, 막대 그래프는 JSON values를 받습니다.

## 애플리케이션 렌더러 추가

```python
class MetricRenderer:
    def render(self, block, context):
        # context: width, theme, language, root, invalidate
        # 셀 너비에 맞춘 PTK fragment 행을 반환합니다.
        return [[("class:hub.accent bold", block.text)]]

installation = hub_plugin.install(prompt)
installation.view.output_renderers.register("hub-metric", MetricRenderer())
```

모델 출력 `<hub-metric title="지표">42</hub-metric>`는 등록한 렌더러로 표시되며
태그 바에 자동 등록됩니다. 비동기 렌더러는 캐시 변경 시 `revision`을 올리고
`context.invalidate()`를 호출할 수 있습니다. `OutputParser`는 분리,
`RendererRegistry`는 디스패치, `ConversationControl.objects`는 화면 객체 목록,
`TagBar`는 탐색·복사만 담당합니다.

### 태그 전용 확대 UI 등록

렌더러와 별도로 `interaction` 팩토리를 등록할 수 있습니다. 팩토리는 **로컬 애플리케이션 코드**입니다.
모델은 XML로 데이터만 전달하고, Python/HTML/JavaScript를 실행하거나 팩토리 이름을 지정하지 않습니다.
대화 출력에는 렌더러의 결과를 표시하고, 태그 바에서 확대했을 때만 팩토리를 호출합니다.

```python
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from hub.widget.output_inspector import ExpandedOutputDialog

class MetricInspector(ExpandedOutputDialog):
    def __init__(self, item, context):
        self.value = int(item.block.text)
        body = Window(FormattedTextControl(lambda: str(self.value)))
        super().__init__(item, context, context.viewport(item), body=body)
        self.shortcuts.add(["+"], "+", "increment", lambda e: self.change(1))
        self.shortcuts.add(["-"], "-", "decrement", lambda e: self.change(-1))

    def change(self, amount):
        self.value += amount

installation.view.output_renderers.register(
    "hub-metric", MetricRenderer(), interaction=MetricInspector,
)
```

`item`은 원본 `OutputObject`이며 `raw`, `block`, `lines`, `message_id`, `id`를 그대로 제공합니다.
`OutputUIContext`에는 `language`, `rows`, `columns`, `close`, 검색 가능한 `viewport(item)` 생성 함수,
`render_worker`가 있습니다. 팩토리는 PTK UI 스레드에서 호출되므로 무거운 처리는 작업자로 넘깁니다.
전체 화면 프레임·검색 입력·원문 복사·닫기·단축키 표시는 `ExpandedOutputDialog`로 재사용합니다.
추가 단축키의 이름은 언어 팩에 등록해 제공합니다.

별도 위젯을 구현할 경우 `__pt_container__()`, 최초 포커스 대상 `receiver`,
`shortcut_context = "reader"`를 제공하세요. 필요한 경우 `dispose()`로 자체 자원을 해제할 수 있습니다.
`dispose()`는 교체·닫기 시 호출되고 Hub가 예약된 `popup` 렌더를 취소합니다.
팩토리가 생성 중 실패하면 오류 안내와 원문을 검색할 수 있는 기본 뷰어로 돌아갑니다.
`interaction`을 생략한 태그는 기본 검색 뷰어를 사용합니다.

이 단계의 상호작용은 표 탐색·검색·로컬 UI 상태 변경입니다. 입력 폼 제출이나 모델에
후속 지시를 전송하는 액션은 아직 연결하지 않았습니다. 기존 백엔드에는 변경이 없습니다.

## Tool 관리

현재 프로젝트에 tools 컴포넌트가 등록되어 있으면 `/tools config`로 엽니다.
경로 입력 후 Enter를 누르면 다음 디렉토리의 내용을 복사 등록합니다.

```text
example/
  example.py         # async main과 설명 docstring
  requirements.txt   # 선택
```

백엔드의 공개 패키지 codec과 `ToolData.acreate()`를 사용합니다.
다른 파일, 심볼릭 링크, 잘못된 requirements, 중복 이름은 거부합니다.
원본 디렉토리는 변경하지 않습니다. 가져오기는 코드 실행, 의존성 설치,
활성화를 수행하지 않습니다. 사용 준비는 기존 tools.prepare/enable API와 설정을 사용합니다.

Tab으로 목록에 이동하면 이름·마지막 수정일·설명이 표시됩니다.
Enter는 관리 중인 디렉토리를 `xdg-open`으로 열고, 수정 후 불러오기 버튼으로 갱신합니다.
d는 확인 후 활성화를 해제하고 삭제합니다. 목록을 읽은 뒤 내용이 바뀌었다면
버전 검사로 삭제를 거부합니다. 설명은 AST로 읽으며 소스를 import하지 않습니다.
다른 컴포넌트의 전용 관리 창은 추후 추가할 수 있으며, 현재는 기존 settings/CRUD 명령을 사용합니다.

## 설정 검색과 표시 상태

설정 Ctrl+F는 키 경로·이름·설명·값의 대소문자 구분 없는 부분 문자열 검색입니다.
빈 검색어로 전체를 복원합니다. 숨겨진 필드도 편집값과 저장 대상에서 유지합니다.
대화 입력의 Ctrl+S는 선택 엔진의 configuration_key에 해당하는
`config.parameters.engines.<key>` 필터로 프로젝트 설정을 엽니다.
Ctrl+C는 저장 전 변경과 모양 미리보기를 취소하고 대화로 돌아갑니다.

제공자가 공개한 thinking은 현재 표시 단계만 유지하며 응답 본문이 시작되면 숨깁니다.
다음 thinking 증분이 오면 새 단계로 표시합니다. llm의 영속 completion 기록은 그대로입니다.
관찰 이벤트를 놓치거나 재시작한 경우, 본문이 이미 존재하면 예전 thinking을 다시 보여주지 않습니다.
