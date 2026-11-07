# 팝업과 알림

일반 팝업은 ish 입력 영역 중앙에 표시하고 포커스를 받는다. 알림은 같은 영역의 우측 하단, bottom-toolbar 위에 표시하며 프롬프트 입력을 유지한다. dock을 숨긴 상태에서도 두 팝업을 사용할 수 있다. dock이 열려 있으면 그 위에 팝업을 그린다.

`set_float()`의 좌표는 터미널 전체가 아니라 ish 입력 컨테이너 기준이다. 창을 줄이거나 프롬프트가 화면 아래에 있으면 팝업도 가용 영역에 맞게 작아진다. 큰 팝업을 위해 셸의 예약 공간이나 호스트 레이아웃을 확장하지 않는다.

## 기본 조작

| 키 | 동작 |
| --- | --- |
| Alt+C | 월간 캘린더 열기 |
| Alt+W | 현재·시간별 날씨 상세 열기 |
| Esc | 일반 팝업 닫기 |
| Tab / Shift+Tab, ← / → | 팝업 아래 버튼 선택 |
| Enter | 선택한 버튼 실행 |
| ↑ / ↓, PageUp / PageDown, Home / End | 팝업 본문 스크롤 |
| Alt+N | 표시 중인 알림의 상세 팝업 열기 |
| Alt+X | 표시 중인 알림 닫기 |

Alt 단축키는 `escape` 접두 시퀀스다. 터미널이 키를 자체 사용하면 해당 동작을 해제하거나 dok 설정에서 다른 키로 바꾼다. `popups.input.escape_s`의 기본값은 0.05초이며 팝업 포커스 중 터미널 시퀀스와 키 조합의 각 대기에 적용한다. 닫기·포커스 이탈·해제 시 dok이 적용한 값만 원래대로 복원한다. 느린 원격 연결에서는 이 값을 늘릴 수 있다. 팝업 안에서 처리하지 않는 Alt 조합이 프롬프트에 글자를 남기지 않는다.

위의 버튼·본문 이동은 일반 정보 팝업 기준이다. 캘린더의 방향키는 날짜 선택, Enter/Space/Tab은 편집 이동, F2는 저장이다. [캘린더 키와 저장 규칙](calendar.md)을 따른다. 날씨에서는 ←/→가 시간 선택, PageUp/PageDown이 표시 구간 이동이며 Home은 현재 시간대, ↑/↓는 세로 스크롤이다. Tab/Shift+Tab은 시간대와 버튼 영역을 전환한다. 버튼 영역에서는 ←/→로 버튼을 선택하고 Enter/Space로 실행한다. 마지막 내부 행은 고정 하단 바로 사용하며 현재 영역·필드·드롭다운에 맞는 키 안내를 매번 다시 그린다.

일반 팝업이 열린 동안 셸 바인딩과 dock 바로가기·음악 단축키는 실행하지 않는다. 닫으면 이전 포커스로 돌아가며 프롬프트 내용·커서·선택 영역을 보존한다. 다른 플러그인의 입력창이 포커스를 가진 상태에서 API로 팝업을 요청하면 셸 입력으로 돌아올 때까지 기다린다. 일반 팝업을 추가로 열면 같은 창의 내용을 교체한다.

일반 팝업(캘린더·날씨 상세 포함)은 포커스를 가진 동안 `dock.mouse.enabled`와 관계없이 마우스를 자동 활성화한다. 닫거나 다른 UI로 포커스가 이동하면 원래 마우스 상태로 돌아가며 설정값은 변경하지 않는다. 일반 팝업 밖의 클릭은 뒤 프롬프트나 dock으로 전달하지 않는다. `dock.mouse.enabled`는 기본 `false`이며 독과 작은 알림의 마우스 동작을 제어한다. 알림은 포커스를 가져가지 않으며, 해당 설정을 켜고 dock이 열려 있으면 알림 밖의 dock 조작을 유지한다. 터미널 출력 선택은 Shift+드래그를 사용한다. 날씨 아이콘 클릭 연결은 이번에 추가하지 않았다.

## 캘린더와 날씨

캘린더는 `clock.timezone`의 현재 날짜를 기준으로 월을 표시하고 오늘에 점을 붙인다. 좌측의 날짜 선택·일정 제목과 우측 편집 폼을 연결하며 저장·삭제·반복·사용자 휴일·예약 알림을 지원한다. `week_start`는 0 월요일부터 6 일요일까지이고 기본값은 6이다. 날짜 범위는 Python `date`의 1년부터 9999년까지이며 경계에서 이동을 멈춘다. 자세한 의미와 제한은 [캘린더](calendar.md)에 정리했다.

날씨 상세는 기존 `WeatherRuntime`과 캐시를 공유한다. 한 요청에서 현재·일별·시간별 예보를 받아 위치, 현재 기온 ASCII 아트, 최고/최저·체감온도, 시간대별 아이콘·기온·연속 선 그래프·강수확률을 표시한다. 선택 시간의 체감온도·바람(km/h)·습도(%)·강우량(mm)·자외선 지수는 구분선이 있는 상세 행에 표시한다. 시간대 글자·아이콘·기온·확률 클릭도 해당 시간을 선택한다. 누락 값은 `--`와 단위로 표시한다. 0%는 muted, 그 외 확률은 foreground다. 기온이 누락된 그래프 구간은 임의로 잇지 않는다. 날짜·시간은 예보 위치의 IANA 시간대를 사용하며 Unix 시각을 해석하므로 일광절약 시간의 중복 시각도 구분한다.

날씨 상단 영역의 마지막 체감온도와 구분선 사이, 강수확률과 선택 시간의 상세 행 사이에는 각각 한 줄 여백을 둔다.

날씨 실패 후 dock을 숨겼다가 다시 열면 즉시 한 번 재시도한다. 성공한 예보는 기존 갱신 주기를 유지한다. 날씨 팝업만 다시 열거나 화면을 다시 그리는 것은 별도의 재시도 계기가 아니며, 진행 중인 요청도 중복하지 않는다.

`popups.weather.font`의 기본값은 `future`이고 좁은 창에서 그릴 수 없으면 일반 숫자로 표시한다. `hour_width`, `graph_height`로 열 너비와 그래프 높이를 정한다. 위치·아이콘·단위·갱신 주기·실패 문구·최저 기온 색상은 `widgets.weather`를 따른다. 지역은 자동 탐지하지 않으며 `location.label` 또는 위도·경도를 표시한다. 위치가 없으면 안내와 Unknown 아이콘, `--°`를 표시한다. 실패 시 같은 위치·단위의 캐시를 유지하고 실패 안내를 표시한다.

dock이 숨겨져 있어도 날씨 팝업이 열려 있으면 기존 설정에 따라 조회한다. 중복 조회 작업은 만들지 않는다. 데이터 출처는 [Open-Meteo 공식 API](https://open-meteo.com/en/docs)다. 상단의 현재 기온과 선택 시간의 예보는 구분한다. 시간대 위의 날짜·위치, 업데이트 시각과 출처 문구는 화면에서 생략하고 선택 시간의 날짜는 하단 바에서 확인한다. 아래 탐색 버튼은 본문 맨 아래에 고정하고 위·아래에 한 줄 여백을 둔다. 좁은 화면에서는 버튼·하단 바를 유지한 채 본문을 스크롤한다.

## 크기와 색상 설정

```jsonc
{
  "popups": {
    "general": {
      "width": {"mode": "fixed", "value": 72},
      "height": {"mode": "fixed", "value": 20},
      "margin": {"top": 1, "right": 1, "bottom": 1, "left": 1},
      "padding": {"top": 0, "right": 1, "bottom": 0, "left": 1},
      "border": true,
      "style": {"color": "foreground", "background": "background", "border_color": "separator"}
    },
    "calendar": {"enabled": true, "key": ["escape", "c"], "panel": null, "week_start": 6},
    "weather": {"enabled": true, "key": ["escape", "w"], "panel": null},
    "notifications": {
      "enabled": true,
      "panel": {"width": {"value": 40}, "height": {"value": 7}},
      "duration_s": 6,
      "dismiss_key": ["escape", "x"],
      "details_key": ["escape", "n"]
    }
  }
}
```

`fixed`는 양의 정수 셀 수, `ratio`는 0보다 크고 1 이하인 비율이다. 비율은 외부 margin을 제외한 가용 입력 영역을 기준으로 계산한다. 가로는 셀, 세로는 행 단위다. 여백에는 음수가 아닌 정수를 사용하며 임의의 64칸 같은 상한은 없다. 실제 화면 크기로 제한한 후에만 공백을 생성한다. 작은 화면에서는 테두리·여백을 줄이고 본문 스크롤을 유지한다.

예를 들어 캘린더만 가로 80%, 세로 70%로 지정하려면 다음을 사용한다.

```jsonc
{
  "popups": {
    "calendar": {
      "panel": {
        "width": {"mode": "ratio", "value": 0.8},
        "height": {"mode": "ratio", "value": 0.7}
      }
    }
  }
}
```

`panel: null`은 `popups.general` 전체 설정을 사용한다. 기본 캘린더 패널은 136×44, 날씨는 112×36이며 실제 가용 입력 영역에 맞게 줄인다. 객체를 지정하면 그 기능 패널의 기본값을 사용한다. `style`에는 [테마](theme.md)의 역할/팔레트 이름을 지정한다. 제목 바는 위 테두리 바로 아래에 위치하며 title 글자·button_bg 배경을 사용한다. 일반 팝업 제목 바로 아래에는 한 줄 여백을 둔다. 닫기는 ``, 하단 버튼은 대괄호 없이 배경색으로 구분한다. 버튼 포커스는 배경을 반전시키지 않고 title 글자색을 적용한다. 드롭다운 목록의 선택 색은 기존 selected_fg/selection_background를 유지한다. 팝업 사각형은 문자를 덮어 그리므로 터미널 기본 배경 사용이 뒤 글자를 비추는 의미는 아니다.

## 알림 수명과 호출 API

알림은 FIFO 순서로 하나씩 표시한다. 자동 닫기 기본값은 **실제로 표시된 6초**다. 각 알림은 등록할 때의 표시 시간을 기억한다. 명령 실행 중, 다른 플러그인 입력 중, 일반 팝업이 열린 동안에는 남은 시간을 보존하고 대기한다. `duration_s: null`은 자동 닫기를 끈다. 알림을 비활성화하거나 dok을 해제하면 대기열과 타이머를 정리한다.

일반 팝업과 표시 대기열은 현재 프로세스의 메모리 상태다. 캘린더는 별도의 저장소와 예약 작업으로 알림 시점을 계산하고 이 표시 API를 호출한다. 일정·알림 처리 기록의 재시작 규칙은 [캘린더](calendar.md)를 따른다. 반복 셸 작업 실행과 Hub 에이전트 호출은 하지 않는다.

```python
from dok import PopupContent

# dock is the controller returned by dok.install(prompt).
dock.open_calendar()
dock.open_weather()
dock.open_popup(PopupContent.plain("Information", "First line\nSecond line"))
dock.close_popup()

dock.notify("Reminder", "A meeting starts soon.",
            details=PopupContent.plain("Meeting", "The complete reminder text."))
pending = dock.pending_notifications
dock.dismiss_notification()
```

`open_weather(day=...)`는 `datetime.date`를 받는다. 해당 날짜가 현재 예보에 없으면 첫 예보를 표시한다. `open_popup()`과 알림 `details`는 `content() -> PopupContent`를 제공하는 `PopupPage`도 받는다. `PopupContent`는 제목, `TextRow` 행, `PopupAction(label, invoke)`를 전달한다. `content()`와 버튼 콜백은 UI 루프에서 실행하므로 I/O를 수행하지 않고 준비된 상태를 사용해야 한다. 긴 작업은 기능 Owner가 비동기로 실행한 뒤 호스트를 갱신한다.

## 소유권과 검증

`PopupPanel`과 하위 설정이 크기·색상·맞춤 규칙을 선언하고 schema·저장·영어 JSONC 주석이 같은 선언을 사용한다. `NotificationQueue`는 순서·가시 시간·종료 전이를, `PopupHost`는 포커스·호스트 생명주기·타이머 연결을 소유한다. 캘린더와 날씨는 내용과 기능별 이동만 제공한다. 기존 Float 하나에 `OverlayLayers`로 dock과 팝업을 조합하므로 열기·닫기마다 호스트 레이아웃을 재생성하지 않는다.

검증에는 월 경계·윤년, 설정/schema 일치, 큰 여백과 화면 축소, 알림 대기·중지·만료, 포커스·선택 복원, 키·마우스 범위, 기존 날씨 캐시 공유, 해제와 실제 PTY 입력을 포함한다.

DockMouse는 Application과 Renderer의 공개 `mouse_support` 필터를 각각 보존하고 활성 조건을 연결한다. Renderer는 생성 시 필터를 별도로 저장하므로 Application만 변경하면 실제 터미널의 마우스 보고 모드가 켜지지 않는다. 해제 시 dok이 설치한 필터인 경우에만 원본을 복원하여 다른 플러그인의 변경을 덮어쓰지 않는다. PTY 테스트는 클릭 처리 외에도 SGR 마우스 활성/해제 출력 신호를 확인한다.

기본 버튼·키 안내·날짜 표시는 [UI 언어](localization.md)를 따른다. 날씨 시간대 표 바로 위에는 선택한 시간대의 현지 날짜를 표시하고, 데이터가 없으면 `--`를 사용한다.
