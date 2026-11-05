# 설정 구조

설정은 `dock`, `audio`, `media`, `clock`, `widgets`, `system`, `toolbar`로 구성한다. [전체 기본 설정과 영어 설명](config.example.jsonc)은 실제 Owner에서 생성한 JSONC다. 이전 필드명·구조·버전은 읽거나 변환하지 않는다. 이미 있는 파일은 자동으로 새 형식으로 바뀌지 않는다.

| 그룹 | 책임 |
| --- | --- |
| `dock.keys` | `toggle` 키 시퀀스, 기본 Ctrl+W. 펼침 크기는 공간에 따라 자동 조절 |
| `dock.width` | `mode: fixed/ratio`, `value` |
| `dock.mouse.enabled` | 독 마우스 켜기·끄기, 기본 false |
| `media` | MPRIS 활성화·플레이어 선택·조회 주기·요청 제한 시간. [설명](media.md) |
| `audio` | Linux 출력 캡처 활성화와 모니터 소스 |
| `clock.timezone` | 날짜·시간·요일과 날씨 갱신 시각의 공통 시간대. 기본 `null`은 시스템 시간대 |
| `widgets` | 화면 순서대로 기능별 내용·스타일·간격 |
| `system` | Linux 측정 활성화·주기·기록 한도·네트워크 인터페이스 |
| `toolbar` | 날짜·입력 상태·숫자 합계(평균)와 오른쪽 시스템 정보. [설명](toolbar.md) |

`widgets`에는 `weather`, `weekday_art`, `date`, `time`, `visualizer`, `progress`, `title`, `media_controls`, `shortcuts`가 있다. 정보 영역의 네 위젯은 최대화에서 표시한다. 순서를 바꾸는 옵션은 추가하지 않았다.

## 공통 이름과 단위

- `enabled`: 요소 활성화. 화면 요소를 끄면 그 요소 뒤의 여백도 제거한다.
- `spacing.after_rows`: 요소 뒤에 넣을 세로 빈 줄 수.
- `spacing.basic_after_rows`: 기본 펼침에서 사용할 줄 수. `null`이면 `after_rows` 사용.
- `spacing.item_gap_columns`: 바로가기 항목 사이 가로 셀 수.
- `style.color`: 전경색. 비주얼라이저는 `style.colors` 배열로 단색 또는 가로 그라데이션을 지정한다.
- `style.bold`: 날짜의 굵기.
- `texts`: 타이틀 접두 문구와 상태 안내 문구.
- `width_columns`: 비주얼라이저·재생바의 가로 셀 수. 재생바 시간 표시는 제외한다.

각 중첩 객체에서 일부 값만 지정하면 해당 Owner의 나머지 기본값을 사용한다. 예를 들어 날짜의 `style.color`만 지정해도 기본 굵기는 유지한다. 타이틀의 `spacing.after_rows`만 바꿔도 기본 펼침 여백은 기존 기본값 0이다. 두 모드에서 같은 여백을 쓰려면 `basic_after_rows`를 `null`로 지정한다.

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
    "time": {"format": "-%H:%M-", "spacing": {"after_rows": 2}},
    "visualizer": {
      "width_columns": 38,
      "style": {"colors": ["#67e8f9", "#c084fc", "#fb7185"]}
    },
    "progress": {"width_columns": 48, "style": {"color": "#6b7280"}},
    "title": {"texts": {"prefix": "NOW: ", "audio_error": "오디오 오프라인"}},
    "shortcuts": {
      "spacing": {"item_gap_columns": 2},
      "items": [
        {"type": "shortcut", "slot": 1, "command": "git", "icon": "\ue702", "label": "Git", "key": ["c-1"], "style": {"color": "#7fbbb3"}},
        {"type": "separator", "slot": 2, "text": "│", "style": {"color": "#6b7280"}}
      ]
    }
  }
}
```

`items`의 기본값은 빈 배열이다. 생략하거나 `items: []`로 지정하면 빈 숫자 슬롯만 표시한다. 파일이 없어서 새로 생성할 때만 초기 예시 10개가 파일에 명시된다. 기존 파일에는 예시를 자동 추가하지 않는다. 모든 항목에는 `type`을 명시한다. 바로가기는 `shortcut`, 구분선은 `separator`다. `icon`은 이모지·Nerd Font 문자를 모두 받을 수 있다. 버튼과 구분선의 표시 문자열은 `text`를 사용한다.

위치 기본값은 `widgets.weather.location: null`이다. 이때 설정된 시간대의 오늘부터 요일을 유지하고, 온도는 `--° --°`, 아이콘은 `icons.unknown`(기본 `?`)으로 표시한다. 최저 온도의 `--°`에도 `style.low_temperature_color`가 적용된다. 날씨 단위는 `temperature_unit`, 아이콘 가로 이동은 `icon_offset_columns`, 연결 실패 문구는 `texts.unavailable`로 지정한다. 사용하지 않는 `texts.location_missing` 설정은 제거했다.

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

`DockConfig`는 `DockSettings`, `AudioConfig`, `ClockConfig`, `WidgetsConfig`, `MediaConfig`, `SystemConfig`, `ToolbarConfig`를 조합한다. `ShortcutsConfig`는 기본 항목·슬롯·키 검증과 조회를 소유하고, 각 화면 요소는 자신에게 필요한 `Spacing`·스타일·문구 Owner를 조합한다. 저장·schema·JSONC 주석은 같은 `Record` 필드 선언에서 생성한다. 화면과 서비스는 typed 객체와 공개 메서드를 사용한다.

```python
from dok import DockConfig

config = DockConfig.from_dict({"widgets": {"shortcuts": {"items": []}}})
width = config.dock.width.resolve(120, 120)
shortcut = config.widgets.shortcuts.shortcut(1)
schema = DockConfig.schema()
```

설정 구조를 설명하기 위한 별칭이나 구형 Python 설정 생성자는 제공하지 않는다. `install`, `uninstall`, 표시 전환·바로가기 실행·설정 저장 등 컨트롤러의 동작은 유지한다. 새 코드를 배포한 뒤에는 현재 형식의 설정을 사용해야 한다. 재로드 오류는 실행 중인 정상 설정을 보존하며, 개인 설정을 자동 변환하거나 덮어쓰지 않는다.
