# 하단 toolbar와 시스템 정보

dok는 기존 `prompt.bottom_toolbar`에 한 줄 상태 표시 함수를 등록한다. ish의 toolbar 위치를 사용하며 Float 높이·프롬프트·키 바인딩을 변경하지 않는다. `toolbar.enabled` 기본값은 `true`다. 기존 개인 파일에 새 항목이 없으면 Owner 기본값을 사용하고 파일 자체는 다시 쓰지 않는다.

```text
2026-11-06(Fri)    Len:108    Lines:3        Ln:2      Col:21     Pos:48       Total:  600(200)                9% ▁▁▂▃▅▇▄▂▁▁▂▁    25% ━━━─────────  󰛳    ↓1.4M ↑82K/s
```

CPU/MEM은 기본 설정에서 Nerd Font 아이콘이다. 런타임 배경 기본값은 공통 테마 배경이며 그 기본값은 터미널 배경이다. 새로 생성하는 설정 파일에서는 하단 바에만 `#1D2035`를 지정한다. 테두리는 없으며 CPU는 8단계 블록 그래프, 메모리는 얇은 용량 막대이고 네트워크 Braille 그래프는 선택적으로 켠다. 픽셀 그래픽은 아니다.

CPU 그래프는 `▁▂▃▄▅▆▇█` 8단계이며 12.5% 간격으로 높이가 바뀐다. 0–12.5% 미만은 `▁`, 12.5–25% 미만은 `▂`, 87.5–100%는 `█`다. 한 칸에 한 번의 측정을 표시하고, 수집 주기가 1초라면 12칸은 최근 12초에 해당한다. 시작부터 사용 가능한 전체 너비를 `▁▁▁▁…` 기준선으로 채우고 오른쪽부터 실제 측정값을 반영한다. 이력에 가짜 0%를 넣지 않으며, 수치가 아직 없으면 `—`를 유지한다. 측정이 시작된 뒤의 누락값은 빈칸으로 구분한다.

## 배치와 날짜

`toolbar.pad_left`와 `toolbar.pad_right`로 전체 하단 바 양 끝의 빈칸을 각각 지정한다. 둘 다 런타임 기본값은 **0**, 새 설정 파일의 값은 **1**이다. 0 이상의 정수를 받으며 임의 상한은 없다. 여백을 제외한 공간에서 왼쪽 그룹과 오른쪽 시스템 정보를 정렬한다. 두 여백을 모두 넣을 수 없을 만큼 화면이 좁으면 왼쪽 여백부터 가용 너비에 맞추고 오른쪽은 남은 너비에 맞춘다. 내용 공간이 없으면 배경만 그린다.

```jsonc
{
  "toolbar": {
    "pad_left": 1,
    "pad_right": 1,
    "background": "#1D2035"
  }
}
```

`toolbar.background`는 기존 테마 역할·팔레트 이름과 하단 바 전용 `#RRGGBB`를 받는다. 양 끝 여백과 가운데 빈 공간, 배경을 상속하는 각 영역에 적용한다. 명시적인 영역 배경은 그대로 우선한다. `"background"`를 지정하면 공통 테마 배경을 다시 따른다. 기존 개인 파일은 다시 쓰지 않으므로 새 필드를 생략하면 런타임 기본값을 사용한다.

오른쪽의 CPU·MEM·NET은 기본적으로 각각 **19칸**이다. `toolbar.regions.<항목>.width`에는 라벨·수치·그래프가 모두 들어간다. 내부 수치 너비는 `toolbar.system.<항목>.value_width`로 지정하며 실제로 남은 칸까지 사용한다. 기본 너비에서 긴 NET 속도는 `↓1.4M ↑82K/s`처럼 줄인다. K·M·G는 KiB·MiB·GiB이고 끝의 `/s`는 수신·송신 양쪽에 적용된다.

날짜·입력 정보·합계는 왼쪽, 시스템 정보는 오른쪽 끝에 정렬한다. 고정 너비 영역은 그 안에 그래프를 맞춘다. 오른쪽 기본 배치는 구분 공백을 포함해 61칸을 사용한다. 터미널이 이보다 좁으면 `toolbar.system.order`의 뒤쪽 항목부터 생략한다. 왼쪽 공간이 부족하면 날짜와 뒤쪽 입력 항목을 생략하고 합계·평균을 우선 표시한다.

날짜는 기본 `%Y-%m-%d(%a)`이며 `Mon`부터 `Sun`까지 영문 약어를 사용한다. `%a`, `%A`의 요일 문구는 `weekdays`에서 바꿀 수 있다. `clock.timezone`을 공유하며 `null`은 시스템 시간대다. dock이 그려지면 날짜와 해당 여백이 함께 숨겨진다.

`toolbar.regions`에서 각 영역을 설정한다. 왼쪽은 `date` → `size`(Len·Lines) → `cursor`(Ln·Col·Pos 및 선택한 편집 정보) → `numbers`(Total) 순서다. 기본값과 새 설정 파일 모두 `gap: 4`이고, 세 숫자 그룹의 `gap`는 2다. 날짜 뒤에도 4칸을 둔다. 숫자 표시칸 안의 정렬용 공백은 이 간격에 포함하지 않는다. 네트워크 라벨은 기본 Nerd Font 아이콘 `󰛳`이며 `toolbar.system.network.label`로 바꿀 수 있다.

| 영역 설정 | 의미 |
| --- | --- |
| `width` | 라벨·값·내부 간격·그래프를 포함한 내용 너비. 1칸 이상, `null`은 자동. 고정 너비의 남는 칸은 영역 배경으로 채운다. |
| `pad_left`, `pad_right` | 내용 바깥의 앞뒤 여백. 0칸 이상. 뒤 여백 `null`은 다음 영역이 있을 때 왼쪽은 그룹 간격, 오른쪽은 `toolbar.separator`를 사용한다. 직접 설정하면 마지막 영역에도 적용한다. |
| `gap` | 영역 내부 요소 사이 공백. 0칸 이상. 길이·위치 영역에서는 필드 사이, Total에서는 라벨과 결과 사이, 시스템 영역에서는 아이콘·수치·그래프 사이에 적용한다. NET의 수신·송신 사이에도 적용한다. 날짜 문자열 내부 간격은 `date.format`으로 지정한다. |
| `style.color`, `style.background` | 테마 역할 또는 팔레트 이름. 영역의 앞뒤 여백과 내용 전체에 적용한다. `null`이면 개별 요소 전경색과 `toolbar.background`를 상속한다. toolbar 배경의 기본값 background는 공통 테마 배경을 따른다. |

길이(`regions.size`), 커서(`regions.cursor`), 합계·평균(`regions.numbers`)에는 각각 숫자의 `align`와 `digits`가 있다. 런타임과 새 파일의 기본값은 `"left"`, `null`이다. `"right"`로 바꾸면 남는 칸이 숫자 앞에 온다. Total은 합계와 괄호 안 평균을 묶어서 정렬한다.

`digits`에 양의 정수를 지정하면 각 숫자에 따로 적용한다. 부호·쉼표·소수점·단위는 자릿수에서 제외한다. 예를 들어 3자리에서 12345는 `12.3K`이며, 2자리에서 123456은 K 단위로 줄여도 정수부가 세 자리여서 `12...`가 된다. 음수의 부호를 보존한다. 합계와 평균은 원래 수로 먼저 계산한다.

기본 `value_width: null`에서는 입력 값이 5칸, Total 값이 15칸부터 필요한 만큼 늘어난다. 숫자를 명시하면 해당 값의 너비를 고정한다. 고정한 너비나 터미널 공간이 부족할 때도 단위 축약을 우선하며, 점 세 개와 숫자가 함께 들어가기 어려운 칸에서는 한 칸짜리 `…`를 쓴다.

## 입력 정보와 숫자 계산

`toolbar.input.items`에서 항목·문구·색상·순서를 지정한다. 기본은 `length`, `lines`, `line`, `column`, `position`이며 `mode`, `encoding`도 추가할 수 있다. 프롬프트 꾸밈과 자동 제안을 제외한 입력 내용만 측정한다.

- 문자 수·열·위치는 Unicode 코드 포인트 기준이다. 터미널 셀 너비나 결합 이모지의 개수와 같지는 않다.
- 실제 `\n` 기준으로 줄을 센다. 화면 자동 줄바꿈은 제외한다. 빈 입력은 1줄이다.
- 줄·열·전체 위치는 1부터 시작한다. 입력 끝 위치는 길이 + 1이다.
- 편집 모드는 Emacs 또는 Vi의 실제 입력·일반·교체 상태다. 인코딩은 파일 속성이 아닌 ish 셸 입출력 인코딩이다.

숫자 계산의 기본 범위는 `input`이다. 프롬프트에 `10 20`을 입력하면 드래그 없이 `Total:  30(15)`를 표시한다. `selection`으로 바꾸면 prompt-toolkit의 선택 범위만 계산하며, 블록 선택은 행마다 따로 해석한다. 터미널 자체의 Shift+드래그 선택은 셸 선택 범위와 독립적이다.

ASCII 숫자, 부호, 소수, 천 단위 쉼표를 지원한다. `사과100`, `-20.5`, `1,200.50`, `.5`를 숫자로 읽으며 끝의 쉼표·마침표는 문장 부호로 처리한다. `12,34`, 여러 소수점, 지수 표기는 통째로 제외한다. 날짜·버전·코드의 의미를 판별하거나 수식을 실행하지 않는다. 평균은 인식한 숫자의 개수로 나눈다.

Decimal로 합산하며 `numbers.decimals`의 기본값 `null`은 계산된 소수 자릿수를 사용한다. 순환소수는 계산 정밀도와 화면 너비 안에서 표시한다. 0 이상의 정수를 지정하면 표시할 때 해당 소수 자릿수로 반올림한다(half-up). 뒤쪽 소수 0은 제거한다. 계산 범위에 숫자가 없으면 `Total:  —`다. 계산 한도는 100,000자, 10,000개 숫자, 숫자당 256자리다. 초과하면 `Total:  …`를 표시한다. 마지막 계산 한 건만 캐시한다.

## 측정의 의미와 수명

`system`은 수집 정책, `toolbar.system`은 표시를 소유한다. 기본 1초 주기로 최근 120개 스냅샷만 메모리에 보관한다. 파일 기록·프로세스 목록 순회·셸 명령·네트워크 요청은 하지 않는다. `psutil` 조회는 UI 스레드 밖에서 한 번에 하나씩 실행한다. 작업 교체·취소 시 기존 읽기가 끝나기 전에 다음 읽기를 시작하지 않는다.

| 값 | 의미 |
| --- | --- |
| CPU | 전체 시간 대비 busy 시간 증가량, 0–100%. Linux guest 중복 합산과 idle/iowait 제외 |
| 메모리 | `(total - available) / total × 100`으로 계산한 사용률을 정수 퍼센트로 표시 |
| 네트워크 | 인터페이스 송수신 byte 증가량 / 실제 monotonic 경과 초. B/s·KiB/s·MiB/s 등 이진 단위 |

첫 CPU·네트워크 측정은 기준만 저장하고 `—`로 표시한다. 카운터 감소·인터페이스 변경 때도 새 기준을 잡는다. 메모리는 첫 측정부터 표시한다. 네트워크 그래프는 수신+송신 합계이며 보관된 구간의 최대값에 맞춘 상대 크기다. CPU 그래프는 항상 0–100%다.

`system.interfaces: null`은 활성화된 비-loopback 인터페이스를 합산한다. VPN·브리지에서는 중복 집계될 수 있으므로 필요하면 `["eth0"]`처럼 명시한다. 명시한 인터페이스가 없으면 대체하지 않고 네트워크 값을 `—`로 표시한다. 부분 조회 실패는 해당 값과 `dock.system.diagnostic`에 반영하고 다른 값은 유지한다. 다음 정규 수집 주기에 다시 시도한다.

WSL에서는 Linux에서 보이는 자원을 측정한다. Windows 전체 사용량과 같지 않으며 Windows 수집기는 포함하지 않는다. [psutil API](https://psutil.io/), [WSL 자원 설정](https://learn.microsoft.com/en-us/windows/wsl/wsl-config#wslconfig)을 참고한다.

수집은 dock 숨김/펼침과 독립적이다. dok의 시스템 toolbar가 있는 프롬프트에서는 유지하고, 프롬프트 종료·비활성화·플러그인 해제·다른 플러그인의 toolbar 교체 때 멈춘다. 명령 실행 중 별도 고정 UI는 만들지 않는다. 다음 프롬프트에서는 기준값·기록을 새로 시작한다. 수집을 꺼도 날짜는 자체 시계로 갱신한다.

## 설정 예시

기존 JSONC의 같은 이름 그룹에 병합한다. 모든 필드와 영어 주석은 [생성 예시](config.example.jsonc)에 있다.

```jsonc
{
  "system": {
    "enabled": true,
    "refresh_s": 1,
    "history": 120,
    "interfaces": null
  },
  "toolbar": {
    "enabled": true,
    "separator": "  ",
    "gap": 4,
    "background": "background",
    "regions": {
      "date": {"pad_right": 4},
      "size": {"align": "left", "digits": null, "gap": 2},
      "cursor": {"align": "left", "digits": null, "gap": 2},
      "numbers": {"align": "left", "digits": null, "gap": 2},
      "cpu": {"width": 19},
      "memory": {"width": 19},
      "network": {"width": 19}
    },
    "date": {
      "format": "%Y-%m-%d(%a)",
      "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
      "hide_with_dock": true,
      "style": {"color": "foreground"}
    },
    "input": {
      "items": [
        {"field": "length", "label": "Len:"},
        {"field": "lines", "label": "Lines:"},
        {"field": "line", "label": "Ln:"},
        {"field": "column", "label": "Col:"},
        {"field": "position", "label": "Pos:"}
      ]
    },
    "numbers": {"scope": "input", "decimals": null, "label": "Total: ", "value_width": null},
    "system": {
      "order": ["cpu", "memory", "network"],
      "cpu": {"label": "", "value_width": 4, "graph": {"width": 12}},
      "memory": {"label": "", "value_width": 4, "graph": {"width": 12}},
      "network": {"label": "󰛳", "value_width": 27, "graph": {"enabled": false}},
      "download_label": "↓",
      "upload_label": "↑"
    }
  }
}
```

`toolbar.enabled: false`는 설치 전 toolbar를 복원한다. 다른 플러그인이 나중에 등록한 값은 덮어쓰지 않는다. 설정을 명시적으로 껐다 켜면 그 시점의 toolbar를 저장하고 dok를 다시 등록한다. `prompt.style`은 변경하지 않고 반환하는 각 셀에만 스타일을 지정한다.

공개 읽기 API `dock.system`은 불변 `SystemSnapshot`, `dock.system_history`는 오래된 것부터 정렬된 제한 길이의 튜플이다. 조회는 측정·상태 전이를 발생시키지 않는다. 설정·검증·schema·영어 주석은 같은 Owner의 `Record` 선언에서 나온다.
