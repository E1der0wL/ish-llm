# 최대화 정보 영역

Ctrl+W는 숨김 ↔ 펼침을 전환한다. 처음 펼칠 때 최대화를 요청하고, 프롬프트 영역의 공간이 부족하면 기본 크기로 표시한다. 공간이 확보되면 최대화로 돌아온다. 토글 키는 `dock.keys.toggle`로 수정한다. 크기 전환용 단축키와 `dock.keys.maximize` 설정은 제거했으며 F9는 예약하지 않는다.

## 설정 예시

다음 예시의 `dock`, `clock`, `widgets`를 현재 형식의 설정에 반영한다. 기본 위치는 미설정(`null`), `clock.timezone` 기본값은 `null`로 시스템 시간대를 따른다. WSL에서는 dok가 실행되는 Linux 환경의 시간대를 사용한다. `"Asia/Seoul"` 같은 IANA 이름을 지정하면 해당 시간대를 사용한다. 예보를 조회하려면 `location`에 자신의 `latitude`, `longitude`, 선택적인 `label`을 지정한다. `location: null`이면 현재 시계 날짜부터 요일, `icons.unknown` 아이콘, `--° --°` 온도를 표시하며 네트워크 요청을 하지 않는다.

```json
{
  "dock": {
    "keys": {
      "toggle": [
        "c-w"
      ]
    }
  },
  "widgets": {
    "date": {
      "enabled": true,
      "spacing": {
        "after_rows": 0
      },
      "format": "%d %B, %Y.",
      "style": {
        "bold": true,
        "color": "#ffffff"
      }
    },
    "time": {
      "enabled": true,
      "spacing": {
        "after_rows": 2
      },
      "format": "-%H:%M-",
      "style": {
        "color": "#ffffff"
      }
    },
    "weekday_art": {
      "enabled": true,
      "spacing": {
        "after_rows": 1
      },
      "font": "future",
      "style": {
        "color": "#ffffff"
      },
      "abbreviated": false
    },
    "weather": {
      "enabled": true,
      "spacing": {
        "after_rows": 1
      },
      "weekday_labels": [
        "ᴍᴏɴ",
        "ᴛᴜᴇ",
        "ᴡᴇᴅ",
        "ᴛʜᴜ",
        "ꜰʀɪ",
        "ꜱᴀᴛ",
        "ꜱᴜɴ"
      ],
      "location": null,
      "temperature_unit": "celsius",
      "days": 7,
      "refresh_minutes": 30,
      "style": {
        "color": "#d1d5db",
        "low_temperature_color": "#77818d"
      },
      "width_ratio": 0.5,
      "icon_offset_columns": -1,
      "texts": {
        "unavailable": "날씨 연결 실패"
      },
      "icons": {
        "clear": "☀",
        "partly_cloudy": "🌤",
        "cloudy": "☁",
        "fog": "🌫",
        "rain": "🌧",
        "snow": "❄",
        "thunder": "⛈",
        "unknown": "?"
      }
    }
  },
  "clock": {
    "timezone": null
  }
}
```

설정 변경은 `ish_reload`로 반영한다. 생략한 필드는 각 Owner의 기본값을 사용하며 `DockConfig.schema()`로 현재 환경의 폰트·시간대 목록과 설정 제약을 조회할 수 있다. `pyfiglet`, `httpx`가 필요하며 ish 플러그인 의존성에 선언되어 있다.

날짜·시간 형식을 바꾸려면 `widgets.date.format`, `widgets.time.format`을 위 예시로 수정한다. 코드 교체 후에는 ish를 재시작한다.

## 표시와 공간 부족

최대화 기본값은 지역 이름 없이 날씨 3줄(요일 / 아이콘 / 최고·최저 온도), 여백 1줄, 기존 요일 아트, 여백 1줄, 날짜와 시간을 표시한다. 날짜는 기본 **03 NOVEMBER, 2026.**처럼 굵게, 시간은 `-06:29-`처럼 표시한다. 그 아래 여백 2줄 → 비주얼라이저 → 재생바 → 타이틀 → 여백 1줄 → 버튼 → 여백 2줄 → 바로가기 → 여백 1줄 → 기존 bottom-toolbar 순서다. 모든 여백은 뒤 내용과 클릭을 보존하는 투명 행이다. 각 요소의 활성화와 간격은 [요소 표시와 세로 간격](layout.md)에서 설정한다.

기본 펼침의 기존 간격은 유지한다. 최대화 기본 본문은 하단 여백까지 9줄이며 여기에 날씨·요일 아트·날짜·시간 영역의 실제 높이를 더한다. 예를 들어 `slant`의 5줄 요일 아트, 정상 날씨 3줄, 날짜·시간 2줄이면 기본 간격에서 입력 컨테이너 최소 24줄이 필요하다. toolbar·프레임은 별도다. 요소를 끄거나 간격을 바꾸면 필요한 높이도 다시 계산한다. 높이가 부족하면 기본 펼침으로 표시한다.

`DockGeometry`가 실제 입력 컨테이너 높이와 추가 행 수로 표시 모드를 결정한다. 최대화가 들어가지 않으면 기본 dock을 표시하고, 기본 dock도 들어가지 않으면 숨긴다. 사용자가 요청한 모드는 유지하므로 창을 키우면 최대화가 복원된다. Float는 셸 공간을 추가로 확보하지 않으며 입력·자동완성 일부를 가릴 수 있다. 본문 너비는 기존 `dock.width` 설정을 사용한다.

요일 아트는 [pyfiglet](https://github.com/pwaller/pyfiglet)의 설치된 폰트를 사용한다. 기본은 선 문자 폰트 `future`이며 `slant`, `small` 등으로 바꿀 수 있다. 영문 전체 요일 또는 `abbreviated: true`로 약어를 표시한다. 폭이 부족하면 약어 아트, 그래도 부족하거나 렌더링이 불가능하면 일반 약어 한 줄로 대체한다. 과도한 높이(20줄 초과)도 한 줄로 대체한다. 아트 내부 공백은 유지하며 블록 전체를 중앙 정렬한다. 결과는 요일·폰트 설정·폭을 기준으로 한 개만 캐시하여 매 오디오 프레임 다시 생성하지 않는다.

아트 타입은 `widgets.weekday_art.font`로 지정한다. 사용자 참고 이미지의 굵고 기하학적인 느낌에는 `ansi_regular`를 추천한다. 실제 설치된 pyfiglet에서 출력을 비교한 제안이며, 사진의 곡선이나 끊어진 획을 정확히 재현하는 폰트는 아니다. 기본값은 현재 사용자 설정을 반영한 `future`다. 다음 값을 기존 `widgets.weekday_art` 설정에 합치고 `ish_reload`로 반영한다.

```json
{
  "widgets": {
    "weekday_art": {
      "enabled": true,
      "font": "ansi_regular",
      "abbreviated": false
    }
  }
}
```

`widgets.weekday_art.enabled: false`는 요일 그룹을 숨긴다. 기본은 `true`이며 요일 아트를 유지한다. `widgets.date.format` 또는 `widgets.time.format`을 빈 문자열로 두면 해당 행을 숨긴다. 형식은 `%Y %y %m %d %A %a %B %b %H %I %M %S %p %z %Z %%`를 지원한다. 개행·제어 문자나 그 외 지시자는 거부한다. `%B`와 `%b`는 로케일에 관계없이 대문자 영문 월 전체 이름·약어를 표시하며, `%%B`는 그대로 `%B`를 표시한다. `widgets.date.style.bold`는 날짜 굵기이며 기본 `true`다. 시간에는 날짜 굵기를 적용하지 않는다. 날짜·시간은 지정 IANA 시간대 또는 `null`인 경우 시스템 현지 시간대를 사용한다. 영문 요일 아트는 시스템 로케일과 무관하고, 날짜 형식의 요일 이름은 Python 로케일을 따른다. 기본은 분 경계, `%S`를 사용할 때만 초 경계마다 시계 갱신을 요청한다.

## 날씨 조회와 캐시

날씨는 [Open-Meteo Forecast API](https://open-meteo.com/en/docs)의 일별 예보를 사용한다. 위치 좌표를 해당 서비스에 전달하며 날짜 경계는 위치의 시간대로 자동 해석한다. 따라서 시계 시간대를 다른 지역으로 설정하면 날씨의 날짜와 다를 수 있다. API 사용 조건은 [Open-Meteo 이용약관](https://open-meteo.com/en/terms)을 따른다. 날씨 데이터 출처는 Open-Meteo이며 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)으로 제공된다. UI는 WMO 코드를 아이콘으로 변환하고 온도를 반올림한다. 지역·단위·출처를 나열하던 상단 행은 제거하고 출처를 이 문서에 명시한다.

- 위도는 -90~90, 경도는 -180~180이다. `label`은 설정에서 식별하는 이름이며 화면 표시나 위치 검색에는 사용하지 않는다.
- `temperature_unit`은 `celsius` 또는 `fahrenheit`다. 최고·최저 온도 각각에 `°`를 붙이고 `/` 없이 공백으로 구분한다. 예: `16° 4°`. 최저 온도는 `style.low_temperature_color`(기본 `#77818d`)로 표시한다. 최고 온도와 요일·아이콘은 `style.color`를 사용한다.
- `weekday_labels`는 월요일부터 일요일 순서의 문구 7개다. 기본은 작은 대문자 모양의 `ᴍᴏɴ`~`ꜱᴜɴ`이며 일반 영문·한글 등으로 바꿀 수 있다. [설정 예시와 폰트 안내](layout.md#날씨-위의-요일-문구)를 참고한다.
- `width_ratio`는 날씨 영역의 목표 폭 비율이며 0 초과 1 이하, 기본 0.5다. 기존 전체 폭의 절반을 목표로 간격을 줄이되 온도가 겹치지 않게 필요한 최소 열 폭은 확보한다. `icon_offset_columns`은 아이콘의 수평 이동 셀 수이며 기본 -1(왼쪽 한 칸)이다. 정수만 허용하고 열 밖으로 나가지 않도록 제한한다.
- `days`는 1~7, 기본 7이다. 폭이 좁으면 각 열의 최소 폭을 위해 표시 일수를 줄이고, 창이 넓어지면 복원한다. 날짜는 가까운 순서이며 온도가 누락되면 `--°`를 표시한다.
- `refresh_minutes`는 5~1440, 기본 30이다. 새 위치의 최대화 첫 표시 시 조회하고 이후 주기적으로 갱신한다. 정상 캐시가 남아 있으면 닫았다 열어도 즉시 재조회하지 않는다.
- 정상 상태는 날씨 3줄만 표시한다. 실패 시 이전 예보가 있으면 그 아래 `texts.unavailable`와 성공 갱신 시각을 안내하는 한 줄을 추가한다. 다음 시도는 설정 주기 이후다. 상세 원인은 `dock.weather.error`에서 확인한다. 이전 예보가 없다면 날씨 영역에 실패 안내를 표시한다. 추가 안내로 높이가 부족해지면 기본 모드로 표시할 수 있다.
- 캐시는 현재 설정 한 개에 대한 메모리 캐시이며 재시작 시 사라진다. 위치·단위·예보 일수 변경 시 이전 데이터를 분리하여 다른 지역 예보를 잘못 표시하지 않는다.
- 실제 최대화가 표시될 때만 조회와 시계 작업을 유지한다. 기본 모드·숨김·공간 부족·프롬프트 종료·해제 시 작업을 취소한다. HTTP 요청 제한은 10초이며 렌더링 스레드에서 네트워크를 기다리지 않는다.

사운드 연결 실패의 “닫았다 다시 열 때만 재시도” 정책은 그대로다. 날씨 실패는 별도의 Owner와 갱신 주기를 사용한다.

## 공개 API와 책임

```python
dock.set_mode("hidden")
dock.set_mode("basic")
dock.set_mode("maximized")
dock.toggle()       # 숨김/마지막 펼침
dock.toggle_size()  # 기본/최대화
dock.requested_mode # 사용자 선택
dock.display_mode   # 직전 렌더링의 실제 모드; 보이지 않으면 hidden
dock.weather        # WeatherSnapshot
```

`DockState`가 상태 전이를, `DockGeometry`가 공간에 따른 실제 모드를 소유한다. `WidgetsConfig`는 날짜·시간·요일·날씨 등 화면 요소를 조합하고 `ClockConfig`는 공통 시간대를 소유하며 각 Owner가 기본값·검증·schema·표시 해석을 제공한다. `OpenMeteo`만 외부 JSON 필드와 WMO 코드·HTTP 인자를 해석한다. `app/lifecycle.py`의 `DashboardRuntime`은 공통 `TaskSlot`을 사용해 날씨·시계 작업의 시작·취소를 독립적으로 조율하고, 각 기능의 `ClockRuntime`과 `WeatherRuntime`이 시각 갱신과 예보 갱신·캐시를 담당한다. `WeekdayRenderer`는 아트 생성·폭 제한·캐시를 소유한다. 구체적인 파일 위치는 [폴더 구조](architecture.md)를 따른다.

`TextRow`와 `TextSpan`은 행 안의 색상·굵기와 표시 폭을 전달한다. `DockView`만 이를 터미널 스타일로 변환한다. 날짜 Owner와 날씨 Owner의 스타일 선언을 렌더러에서 재해석하지 않는다.
