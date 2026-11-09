# 설정 구조

설정은 `dock`, `audio`, `media`, `clock`, `widgets`, `system`, `toolbar`, `popups`와 외부 연결을 위한 `connections` 등으로 구성한다. [전체 기본 설정과 영어 설명](config.example.jsonc)은 실제 Owner에서 생성한 JSONC다. 이전 필드명·구조·버전은 읽거나 변환하지 않는다. 이미 있는 파일은 자동으로 새 형식으로 바뀌지 않는다.

| 그룹 | 책임 |
| --- | --- |
| `connections.google` | Google 계정 ID·활성화·OAuth Desktop client 파일·HTTP/로그인 제한 시간. `write` 기본 false, 쓰기 권한을 켠 뒤 재로그인. `updates`는 Google 초대 대상 변경 알림 정책. [외부 연결](connections.md) |
| `popups.calendar.sources` | 연결 ID·캘린더 ID·표시 이름·활성화·갱신 간격. 기본 빈 목록, 생성 예시는 비활성. 쓰기는 계정 설정과 Google 권한으로 허용 |
| `language` | 기본 `null`: ish 선택 언어. `en`·`ko`로 고정 가능하며 미지원 언어는 영어. [언어와 사용자 문구](localization.md) |
| `dock.keys` | `toggle` 키 시퀀스, 기본 Ctrl+W. 펼침 크기는 공간에 따라 자동 조절 |
| `dock.width` | `mode: fixed/ratio`, `value` |
| `dock.mouse.enabled` | 독·알림 마우스 켜기·끄기, 기본 false. 일반 팝업은 포커스 중 자동 활성화 |
| `media` | MPRIS 활성화·플레이어 선택·조회 주기·요청 제한 시간. [설명](media.md) |
| `audio` | Linux 출력 캡처 활성화와 모니터 소스 |
| `clock.timezone` | 날짜·시간·요일과 날씨 갱신 시각의 공통 시간대. 기본 `null`은 시스템 시간대 |
| `widgets` | 화면 순서대로 기능별 내용·스타일·간격 |
| `system` | Linux 측정 활성화·주기·기록 한도·네트워크 인터페이스 |
| `system.idle_s` | dock 숨김·일반 팝업 중 측정 간격, 기본 3초. null이면 refresh_s 사용 |
| `toolbar` | 날짜·입력 상태·숫자 합계(평균)와 오른쪽 시스템 정보. [설명](toolbar.md) |
| `toolbar.pad_left`, `toolbar.pad_right` | 하단 바 전체의 좌우 끝 여백. 런타임 각각 0칸, 새 파일 각각 1칸 |
| `toolbar.background` | 테마 이름 또는 하단 바 전용 RGB. 런타임 background, 새 파일 #1D2035 |
| `popups` | 중앙 일반 팝업, 우측 하단 알림, 일정 편집·예약 알림과 현재/시간별 예보. [설명](popups.md) |
| `popups.calendar.poll_s`, `idle_s` | 캘린더 열림·닫힘 중 외부 일정 파일 확인 간격, 기본 1초·30초. 저장한 알림 기한과 별개 |
| `popups.calendar.clock_s` | 미래 알림 대기 중 가벼운 시계 변경 확인, 기본 1초. 파일 조회 없음 |
| `completion` | 커서 아래 자동완성 등록창과 Hub 우선 분석. `completion.hub: false`는 로컬 분석만 사용 |
| `completion.menu` | 기존·생성 자동완성 목록의 글자·설명·선택 행·스크롤바 테마. `enabled: false`면 ish 스타일 유지 |
| `completion.manage` | Alt+M 중앙 관리 팝업의 단축키·크기·테마. 접두사와 항목 수동 편집·삭제·잠금 |
| `toolbar.hints` | 중앙 키 안내 초기 표시 여부. 기본 false, 공간 부족 시 전체 안내 숨김 |
| `toolbar.hint_key` | 중앙 안내 토글. 기본 F1, null로 해제. 팝업 하단 안내는 유지 |
| `hub` | 새 dok 에이전트의 공통 초기 `model`, `api_key`, `api_base`. 기본 모두 null, 기존 Hub 에이전트는 유지 |

`widgets`에는 `weather`, `weekday`, `date`, `time`, `visualizer`, `progress`, `title`, `controls`, `shortcuts`가 있다. 정보 영역의 네 위젯은 최대화에서 표시한다. 순서를 바꾸는 옵션은 추가하지 않았다.

## 공통 이름과 단위

- 가로 크기는 `width`, 수치만의 표시 너비는 `value_width`, 좌우 여백은 `pad_left`·`pad_right`, 항목 간격은 `gap`이다. 모두 터미널 셀 수다. `toolbar.gap`은 왼쪽 그룹 사이, `toolbar.regions.<영역>.gap`은 영역 안 항목 사이 간격이다.
- 주기는 `refresh_s`(초)·`refresh_min`(분), 제한 시간은 `timeout_s`, 알림 지속 시간은 `duration_s`다. 단위 접미사는 유지한다.
- 숫자 표시는 `align`·`digits`·`decimals`, 날씨 위치는 `location.lat`·`location.lon`, 날씨 단위는 `unit`이다.
- 테마 역할은 `button_bg`·`editor_bg`·`input_bg`·`selected_fg`·`selected_bg`·`muted`·`low_temp`처럼 사용한다. `fg`는 글자색, `bg`는 배경색이다. 팔레트의 18개 색상 이름은 유지한다.
- `enabled`: 요소 활성화. 화면 요소를 끄면 그 요소 뒤의 여백도 제거한다.
- `spacing.after`: 요소 뒤에 넣을 세로 빈 줄 수.
- `spacing.basic_after`: 기본 펼침에서 사용할 줄 수. `null`이면 `after` 사용.
- `spacing.gap`: 바로가기 항목 사이 가로 셀 수.
- `style.color`: 공통 [테마](theme.md)의 역할 또는 팔레트 이름이다. 기본 `foreground`는 `theme.roles.foreground`를 따르며 그 기본값 `null`은 터미널 글자색이다. 바로가기·구분선 색만 직접 `#RRGGBB`를 유지하고 비주얼라이저는 RGB `style.colors` 배열을 사용한다. 이전 일반 위젯의 빈 문자열·null·RGB 색은 자동 변환하지 않는다.
- `style.bold`: 날짜의 굵기.
- 기본 재생·연결 상태 문구는 설정 항목이 아니며 언어팩에서 관리한다.
- `width`: 비주얼라이저·재생바의 가로 셀 수. 재생바 시간 표시는 제외한다.

각 중첩 객체에서 일부 값만 지정하면 해당 Owner의 나머지 기본값을 사용한다. 예를 들어 날짜의 `style.color`만 지정해도 기본 굵기는 유지한다. 타이틀의 `spacing.after`만 바꿔도 기본 펼침 여백은 기존 기본값 0이다. 두 모드에서 같은 여백을 쓰려면 `basic_after`를 `null`로 지정한다.

## 설정 예시

```jsonc
{
  "dock": {
    "keys": {"toggle": ["c-w"]},
    "width": {"mode": "ratio", "value": 0.6}
  },
  "clock": {"timezone": null},
  "widgets": {
    "date": {"format": "%d %B, %Y.", "style": {"bold": true}},
    "time": {"format": "-%H:%M-", "spacing": {"after": 2}},
    "visualizer": {
      "width": 38,
      "style": {"colors": ["#67e8f9", "#c084fc", "#fb7185"]}
    },
    "progress": {"width": 48, "style": {"color": "muted"}},
    "title": {"style": {"color": "foreground"}},
    "shortcuts": {
      "spacing": {"gap": 2},
      "items": [
        {"type": "shortcut", "slot": 1, "command": "git", "icon": "\ue702", "label": "Git", "key": ["c-1"], "style": {"color": "#7fbbb3"}},
        {"type": "separator", "slot": 2, "text": "│", "style": {"color": "#6b7280"}}
      ]
    }
  }
}
```

`items`의 기본값은 빈 배열이다. 생략하거나 `items: []`로 지정하면 빈 숫자 슬롯만 표시한다. 파일이 없어서 새로 생성할 때만 초기 예시 10개가 파일에 명시된다. 기존 파일에는 예시를 자동 추가하지 않는다. 모든 항목에는 `type`을 명시한다. 바로가기는 `shortcut`, 구분선은 `separator`다. `icon`은 이모지·Nerd Font 문자를 모두 받을 수 있다. 버튼과 구분선의 표시 문자열은 `text`를 사용한다.

위치 기본값은 `widgets.weather.location: null`이다. 이때 설정된 시간대의 오늘부터 요일을 유지하고, 온도는 `--° --°`, 아이콘은 `icons.unknown`(기본 `?`)으로 표시한다. 최저 온도의 `--°`에도 `style.low_color`가 적용된다. 날씨 단위는 `unit`, 아이콘 가로 이동은 `icon_offset`로 지정한다. 연결 실패·로딩·지역 미설정 문구는 언어팩을 따른다.

날씨 아이콘은 `widgets.weather.icons`에서 각각 수정한다. 생략한 종류는 기본값을 사용하며 이모지·Nerd Font 문자·한 줄 텍스트를 지원한다.

```jsonc
{
  "widgets": {
    "weather": {
      "icons": {
        "clear": "☀", "partly_cloudy": "🌤", "cloudy": "☁", "fog": "🌫",
        "rain": "🌧", "snow": "❄", "thunder": "⛈", "unknown": "❔"
      }
    }
  }
}
```

`dock.keys.maximize`는 제거했다. 기존 설정에서는 해당 항목을 삭제하고 `dock.keys.toggle`을 `["c-w"]`로 변경한다. 기존 파일을 자동 변환하거나 덮어쓰지는 않는다.

## Owner와 공개 API

### 간소화한 설정 이름

아래 표는 기존 파일을 직접 편집할 때의 안내다. 실행 중 별칭이나 자동 변환은 제공하지 않는다. 설정값·단위·기능은 유지하며, 날씨 제공자에 보내는 HTTP 파라미터 이름은 제공자 계약을 그대로 따른다.

| 위치 | 이전 이름 → 현재 이름 |
| --- | --- |
| 위젯·영역 너비 | `width_columns` → `width` |
| CPU·MEM·네트워크 그래프 | `graph.columns` → `graph.width` |
| 숫자 필드 너비 | `value_width_columns` → `value_width` |
| 하단 바 바깥 여백 | `padding_left_columns`·`padding_right_columns` → `pad_left`·`pad_right` |
| 하단 바 영역 여백 | `padding_before_columns`·`padding_after_columns` → `pad_left`·`pad_right` |
| 하단 바 그룹·영역 내부·바로가기 간격 | `group_gap_columns`·`item_gap_columns` → 각 Owner의 `gap` |
| 위젯 세로 간격 | `after_rows`·`basic_after_rows` → `after`·`basic_after` |
| 수집·요청 | `refresh_seconds`·`refresh_minutes`·`timeout_seconds` → `refresh_s`·`refresh_min`·`timeout_s` |
| 시스템 기록 | `history_samples` → `history` |
| 하단 바 날짜 숨김 | `hide_when_dock_visible` → `hide_with_dock` |
| 하단 바 숫자 | `alignment`·`max_digits`·`decimal_places` → `align`·`digits`·`decimals` |
| 합계 빈 값·계산 한도 표시 | `empty_text`·`limit_text` → `empty`·`overflow` |
| 위젯 이름 | `weekday_art`·`media_controls` → `weekday`·`controls` |
| 날씨 위치 | `latitude`·`longitude` → `lat`·`lon` |
| 날씨 표시 | `temperature_unit`·`weekday_labels`·`icon_offset_columns` → `unit`·`weekdays`·`icon_offset` |
| 날씨 최저 온도색 | `low_temperature_color` → `low_color` |
| 날씨 팝업 | `hour_columns`·`graph_rows` → `hour_width`·`graph_height` |
| 캘린더 | `first_weekday`·`reminders_enabled`·`reminder_poll_seconds` → `week_start`·`reminders`·`poll_s` |
| 팝업 키 판별·알림 지속 시간 | `escape_seconds`·`duration_seconds` → `escape_s`·`duration_s` |
| 테마 배경 역할 | `button_background`·`editor_background`·`input_background` → `button_bg`·`editor_bg`·`input_bg` |
| 테마 선택 역할 | `selection_foreground`·`selection_background` → `selected_fg`·`selected_bg` |
| 테마 주석·최저 온도 역할 | `secondary_text`·`low_temperature` → `muted`·`low_temp` |

테마 역할 이름을 바꾸면 해당 이름을 참조하던 `style.color` 등의 값도 함께 바꾼다. 예를 들어 `"color": "secondary_text"`는 `"color": "muted"`가 된다. 바로가기 명령·사용자 텍스트·18색 팔레트·독립 RGB 값은 이름 변경 대상이 아니다.

### 선언과 소비

`DockConfig`는 `Theme`, `DockSettings`, `AudioConfig`, `ClockConfig`, `WidgetsConfig`, `MediaConfig`, `SystemConfig`, `ToolbarConfig`, `PopupSettings`를 조합한다. `ShortcutsConfig`는 기본 항목·슬롯·키 검증과 조회를 소유하고, 각 화면 요소는 자신에게 필요한 `Spacing`·스타일·문구 Owner를 조합한다. 저장·schema·JSONC 주석은 같은 `Record` 필드 선언에서 생성한다. 화면과 서비스는 typed 객체와 공개 메서드를 사용한다.

```python
from dok import DockConfig

config = DockConfig.from_dict({"widgets": {"shortcuts": {"items": []}}})
width = config.dock.width.resolve(120, 120)
shortcut = config.widgets.shortcuts.shortcut(1)
schema = DockConfig.schema()
```

설정 구조를 설명하기 위한 별칭이나 구형 Python 설정 생성자는 제공하지 않는다. `install`, `uninstall`, 표시 전환·바로가기 실행·설정 저장 등 컨트롤러의 동작은 유지한다. 새 코드를 배포한 뒤에는 현재 형식의 설정을 사용해야 한다. 재로드 오류는 실행 중인 정상 설정을 보존하며, 개인 설정을 자동 변환하거나 덮어쓰지 않는다.
