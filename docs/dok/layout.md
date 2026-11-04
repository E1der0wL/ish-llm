# 요소 표시와 세로 간격

각 요소의 `enabled`로 표시 여부를, `spacing.after_rows`로 그 요소 **뒤에 넣을 빈 줄 수**를 지정한다. 세로 여백은 0~20줄의 정수다. `enabled: false`이면 요소와 그 뒤 여백이 함께 빠진다. 앞 요소가 지정한 여백은 유지된다. 마지막으로 표시되는 요소의 여백은 dock 하단 여백이 된다.

| 요소 | 설정 경로 | 기본 펼침의 뒤 여백 | 최대화의 뒤 여백 |
| --- | --- | ---: | ---: |
| 날씨 | `widgets.weather` | 표시 안 함 | 1 |
| 요일 아트 | `widgets.weekday_art` | 표시 안 함 | 1 |
| 날짜 | `widgets.date` | 표시 안 함 | 0 |
| 시간 | `widgets.time` | 표시 안 함 | 2 |
| 비주얼라이저 | `widgets.visualizer` | 0 | 0 |
| 재생바 | `widgets.progress` | 0 | 0 |
| 타이틀 | `widgets.title` | 0 | 1 |
| 음악 버튼 | `widgets.media_controls` | 1 | 2 |
| 바로가기 | `widgets.shortcuts` | 1 | 1 |

모든 요소의 `enabled` 기본값은 `true`다. 기본 펼침에서도 최대화와 같은 여백을 사용하려면 `spacing.basic_after_rows: null`로 둔다. 정수를 지정하면 기본 펼침에서만 해당 값으로 대체한다. 기존 화면을 유지하기 위해 타이틀은 `spacing.basic_after_rows: 0`, 음악 버튼은 `spacing.basic_after_rows: 1`이며, 나머지는 `null`이다.

다음은 기존 설정에 합칠 수 있는 예시다. 재생바를 숨기고, 시간 뒤 여백을 1줄로 줄이고, 타이틀 뒤 여백을 두 모드 모두 2줄로 바꾼다.

```json
{
  "widgets": {
    "progress": {
      "enabled": false
    },
    "title": {
      "spacing": {
        "after_rows": 2,
        "basic_after_rows": null
      }
    },
    "shortcuts": {
      "enabled": true,
      "spacing": {
        "item_gap_columns": 2,
        "after_rows": 1
      }
    },
    "weather": {
      "enabled": true,
      "spacing": {
        "after_rows": 1
      }
    },
    "weekday_art": {
      "enabled": true,
      "spacing": {
        "after_rows": 1
      },
      "font": "future"
    },
    "date": {
      "enabled": true,
      "spacing": {
        "after_rows": 0
      }
    },
    "time": {
      "enabled": true,
      "spacing": {
        "after_rows": 1
      }
    }
  }
}
```

`widgets.shortcuts.spacing.item_gap_columns`은 기존 바로가기 사이의 **가로 간격**이다. `spacing.after_rows`는 줄 전체 뒤의 세로 간격이다. 바로가기 줄을 비활성화하면 해당 바로가기 단축키의 우선 처리와 `dock.execute()`도 중단되며, 원래 셸 키가 동작한다. 다시 활성화하면 등록한 바로가기를 그대로 사용한다.

`widgets.weather.enabled: false`이면 날씨 조회도 중단한다. 비주얼라이저와 타이틀을 모두 끄면 오디오 작업을 중단한다. 타이틀만 켜 두면 소리를 내는 프로그램 이름을 확인하기 위해 오디오 작업을 유지한다. 모든 요소를 끄면 dock은 그리지 않으며 표시 모드 전환 키는 유지한다.

날짜·시간의 형식과 굵기는 기존 `widgets.date.format`, `widgets.time.format`, `widgets.date.style.bold`를 사용한다. 빈 형식 문자열도 해당 요소와 여백을 숨긴다. 각 날짜·시간 위젯이 표시·간격·형식·색상을 함께 소유하며 날짜는 굵기도 소유한다.

여백은 투명하게 남겨 뒤의 셸 입력·자동완성과 마우스 처리를 보존한다. `bottom-toolbar`는 ish 소유이므로 이 설정으로 끄거나 변경하지 않는다. 바로가기 뒤의 기본 여백은 1줄이다.

## 날씨 위의 요일 문구

`widgets.weather.weekday_labels`에 **월요일부터 일요일 순서로 정확히 7개**의 한 줄 문구를 지정한다. 공백만 있거나 빈 문구, 줄바꿈은 허용하지 않는다. 기본값은 작은 대문자 모양의 유니코드 문자다.

```json
{
  "widgets": {
    "weather": {
      "weekday_labels": [
        "ᴍᴏɴ",
        "ᴛᴜᴇ",
        "ᴡᴇᴅ",
        "ᴛʜᴜ",
        "ꜰʀɪ",
        "ꜱᴀᴛ",
        "ꜱᴜɴ"
      ],
      "spacing": {
        "after_rows": 1
      }
    }
  }
}
```

문자 크기는 터미널 폰트에 따라 다르다. 글꼴에서 지원하지 않거나 일반 영문이 더 잘 보이면 `["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]`으로 바꾼다. 한글 등 다른 문구도 가능하며 긴 문구는 날씨 열 너비에 맞춰 잘린다. 예보 날짜에 맞는 문구를 고르므로 오늘이 화요일이면 화요일부터 표시한다. 큰 요일 아트의 문구와는 별개다.

코드 교체 후 ish를 재시작하고, 이후 현재 형식의 설정 변경은 `ish_reload`로 반영한다. 생략한 필드는 기본값을 사용하고 이미 저장된 명시적 값은 유지한다. 날씨와 요일 아트 사이에 기본 1줄을 적용하려면 `widgets.weather.spacing.after_rows`를 1로 둔다.

`ElementConfig`가 표시·요소 제거를, `Spacing`이 세로 간격과 기본 모드 재정의 해석을 소유한다. 각 요소의 설정 구현체는 필요한 기본값만 선언한다. schema·저장·로드는 같은 선언을 사용하고, `DashboardView`와 `DockGeometry`는 `arrange()`로 계산한 실제 행을 소비한다. 높이가 부족하면 최대화 → 기본 펼침 → 숨김 순으로 표시하고, 크기가 복원되면 요청한 모드로 돌아온다.
