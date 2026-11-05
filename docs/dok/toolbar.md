# 하단 toolbar와 시스템 정보

dok는 기존 `prompt.bottom_toolbar`에 한 줄 상태 표시 함수를 등록한다. ish의 toolbar 위치를 사용하며 Float 높이·프롬프트·키 바인딩을 변경하지 않는다. `toolbar.enabled` 기본값은 `true`다. 기존 개인 파일에 새 항목이 없으면 Owner 기본값을 사용하고 파일 자체는 다시 쓰지 않는다.

```text
2026-11-06(금)  Len:108  Lines:3  Ln:2  Col:21  Pos:48  Σ 600(200)       CPU 9% ⣀⣠⣀⣀  MEM …  NET ↓1.4 MiB/s ↑82 KiB/s
```

CPU/MEM은 기본 설정에서 Nerd Font 아이콘이다. 배경은 터미널 기본색이며 테두리는 없다. 터미널 투명도는 터미널 설정을 따른다. CPU는 Braille 점 그래프, 메모리는 얇은 용량 막대이며 네트워크 그래프는 선택적으로 켠다. 픽셀 그래픽은 아니다.

## 배치와 날짜

날짜 → 입력 정보 → 합계·평균은 왼쪽에, 시스템 정보는 터미널 오른쪽 끝에 정렬한다. 날짜·입력 길이가 바뀌어도 오른쪽 정렬은 유지된다. 창이 좁아지면 그래프부터 줄이고, 그래프 없이도 부족하면 날짜와 뒤쪽 입력 항목을 생략한다. 합계·평균은 가능한 한 유지한다. 오른쪽 수치 자체가 너비를 넘으면 `toolbar.system.order`의 뒤쪽 항목부터 생략한다. 마지막 항목도 들어가지 않으면 문자 경계를 지키며 줄인다. 줄 추가나 좌우 겹침은 없다.

날짜는 기본 `%Y-%m-%d(%a)`다. `%a`, `%A`는 월요일부터 일요일까지 `weekdays` 문구를 사용한다. `clock.timezone`을 공유하며 `null`은 시스템 시간대다. 기본 펼침·최대화 dock이 **실제로 그려지면** 날짜만 숨기고 공백은 없앤다. dock 숨김·공간 부족이면 다시 표시한다. 실제 Float 표시는 그리기 완료 후 확인하고 변경 시 한 번 추가 갱신한다.

## 입력 정보와 숫자 계산

`toolbar.input.items`에서 항목·문구·색상·순서를 지정한다. 기본은 `length`, `lines`, `line`, `column`, `position`이며 `mode`, `encoding`도 추가할 수 있다. 프롬프트 꾸밈과 자동 제안을 제외한 입력 내용만 측정한다.

- 문자 수·열·위치는 Unicode 코드 포인트 기준이다. 터미널 셀 너비나 결합 이모지의 개수와 같지는 않다.
- 실제 `\n` 기준으로 줄을 센다. 화면 자동 줄바꿈은 제외한다. 빈 입력은 1줄이다.
- 줄·열·전체 위치는 1부터 시작한다. 입력 끝 위치는 길이 + 1이다.
- 편집 모드는 Emacs 또는 Vi의 실제 입력·일반·교체 상태다. 인코딩은 파일 속성이 아닌 ish 셸 입출력 인코딩이다.

숫자 표시는 기본 `Σ 합계(평균)`이다. `numbers.scope: "selection"`은 prompt-toolkit의 프롬프트 선택 범위를 사용한다. 블록 선택은 행마다 따로 해석해 숫자를 이어 붙이지 않는다. **Windows Terminal의 Shift+드래그 같은 터미널 자체 선택은 dok에 전달되지 않는다.** 기존 셸 선택 키를 사용하거나 `scope: "input"`으로 전체 입력을 계산한다. 새 편집 키나 마우스 선택 기능은 추가하지 않는다.

ASCII 숫자, 부호, 소수, 천 단위 쉼표를 지원한다. `사과100`, `-20.5`, `1,200.50`, `.5`를 숫자로 읽으며 끝의 쉼표·마침표는 문장 부호로 처리한다. `12,34`, 여러 소수점, 지수 표기는 통째로 제외한다. 날짜·버전·코드의 의미를 판별하거나 수식을 실행하지 않는다. 평균은 인식한 숫자의 개수로 나눈다.

Decimal로 합산하고 표시 시 half-up으로 반올림한다. 기본 최대 2자리이며 뒤쪽 0은 제거한다. 선택/숫자가 없으면 `Σ —`다. 계산 Owner 한도는 100,000자, 10,000개 숫자, 숫자당 256자리다. 초과하면 부분 합계 대신 `Σ …`를 표시한다. 마지막 계산 한 건만 캐시한다.

## 측정의 의미와 수명

`system`은 수집 정책, `toolbar.system`은 표시를 소유한다. 기본 1초 주기로 최근 120개 스냅샷만 메모리에 보관한다. 파일 기록·프로세스 목록 순회·셸 명령·네트워크 요청은 하지 않는다. `psutil` 조회는 UI 스레드 밖에서 한 번에 하나씩 실행한다. 작업 교체·취소 시 기존 읽기가 끝나기 전에 다음 읽기를 시작하지 않는다.

| 값 | 의미 |
| --- | --- |
| CPU | 전체 시간 대비 busy 시간 증가량, 0–100%. Linux guest 중복 합산과 idle/iowait 제외 |
| 메모리 | `total - available`, 전체 용량, 사용률. 단순 free 메모리 기준이 아님 |
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
    "refresh_seconds": 1,
    "history_samples": 120,
    "interfaces": null
  },
  "toolbar": {
    "enabled": true,
    "separator": "  ",
    "date": {
      "format": "%Y-%m-%d(%a)",
      "weekdays": ["월", "화", "수", "목", "금", "토", "일"],
      "hide_when_dock_visible": true,
      "style": {"color": "#d1d5db"}
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
    "numbers": {"scope": "selection", "decimal_places": 2, "label": "Σ "},
    "system": {
      "order": ["cpu", "memory", "network"],
      "cpu": {"label": "", "graph": {"columns": 12}},
      "memory": {"label": "", "graph": {"columns": 12}},
      "network": {"label": "NET", "graph": {"enabled": false}},
      "download_label": "↓",
      "upload_label": "↑"
    }
  }
}
```

`toolbar.enabled: false`는 설치 전 toolbar를 복원한다. 다른 플러그인이 나중에 등록한 값은 덮어쓰지 않는다. 설정을 명시적으로 껐다 켜면 그 시점의 toolbar를 저장하고 dok를 다시 등록한다. `prompt.style`은 변경하지 않고 반환하는 각 셀에만 스타일을 지정한다.

공개 읽기 API `dock.system`은 불변 `SystemSnapshot`, `dock.system_history`는 오래된 것부터 정렬된 제한 길이의 튜플이다. 조회는 측정·상태 전이를 발생시키지 않는다. 설정·검증·schema·영어 주석은 같은 Owner의 `Record` 선언에서 나온다.
