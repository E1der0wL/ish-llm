# dok 셸 dock

`dok`는 ish 입력 대기 화면에 음악 정보 자리와 바로가기를 표시하는 Linux 플러그인이다. ish 본체를 수정하지 않고 `prompt.set_float()`로 기존 입력 영역 위에 dock을 겹쳐 그린다.

구현 코드는 `dok/`, 문서는 `docs/dok/`, 자동 테스트와 독립 미리보기는 `tests/dok/`에서 관리한다. 검증 결과는 `tests/dok/reports/`에 생성한다. 개인 스크립트인 `manual/`과 생성된 보고서인 `reports/`는 Git에 포함하지 않는다.

[폴더 구조와 책임](architecture.md)에 기능별 구현, 외부 연동, 화면 조합, 설정 저장의 경계를 정리했다. 공개 import는 계속 `from dok import ...`를 사용하며 ish 진입점은 `dok/dok.py`다.

[Float dock 설계](design.md)에 배치, 표시 우선순위, 사용자 설정, 입력 처리, 재생 연동과 구현 검증 기준을 정리했다. 토글 키·명령어·이모지는 사용자가 수정하고, 너비는 고정 셀 수 또는 ish PTY 너비 비율로 지정한다. 바로가기는 등록 명령어와 현재 프롬프트 버퍼 전체를 이어 붙여 즉시 제출한다.

현재 구현은 dock, 바로가기, Linux 출력 오디오 비주얼라이저, MPRIS 재생 제어와 하단 상태 표시줄을 제공한다. 비주얼라이저는 Linux 오디오 서버의 출력 모니터를 읽는다. 하단에는 왼쪽에 날짜·입력 정보·프롬프트 숫자의 합계(평균), 오른쪽 끝에 CPU·메모리·네트워크가 표시된다. dock을 열면 toolbar 날짜만 숨긴다. [하단 toolbar 설정](toolbar.md)

기본 dock은 비주얼라이저 → 재생 바 → 타이틀 → 음악 버튼 → 투명 padding 1줄 → 바로가기 순서의 6줄이다. 그 아래 투명 padding 1줄과 bottom-toolbar가 이어진다. 최대화하면 위쪽에 날씨·ASCII 아트 요일·날짜·시간을 추가한다. 기본 설정에서는 입력 영역 높이가 8줄 미만이면 dock을 잠시 숨긴다. [요소 표시와 세로 간격](layout.md)에서 각 요소를 켜고 끄거나 여백을 바꿀 수 있으며, 실제 설정에 맞춰 필요한 높이도 계산한다. 날씨 위의 작은 영문 요일 문구도 수정할 수 있다.

## 설치

Python 3.12.14와 prompt-toolkit 3.0.53을 사용하는 ish에서 실행한다. 저장소의 `dok/` 디렉터리만 ish 설정 홈의 `plugin/script/dok/`로 복사한다. 기본 경로는 `~/ish/plugin/script/dok/`이며 `--home`을 사용하면 해당 홈 아래에 설치한다. 의존성은 `PLUGIN_META.dependencies`가 선언한다. 독립 실행 환경에서는 해당 Python에 `python -m pip install pyfiglet httpx 'dbus-fast>=5.2,<6' 'psutil>=7,<8'`로 설치한다. 플러그인 코드 교체 후 ish를 다시 시작한다.

`.ishrc.py`에서 다음과 같이 연결한다.

```python
dok_plugin = plugin.get("dok")
if dok_plugin is not None:
    dock = dok_plugin.install(prompt)
```

설치·재로드 시 설정 파일이 없으면 `<ish home>/dok/config.jsonc`에 모든 설정값·바로가기 예시 10개와 항목별 영어 설명 주석을 저장한 뒤 읽어 적용한다. 기본 경로는 항상 `config.jsonc`이며 이전 `config.json`은 자동으로 읽지 않는다. [주석 있는 설정 파일](configuration-file.md)에 생성·편집·기존 파일 처리 규칙을 정리했다. Ctrl+W는 숨김과 펼침을 전환한다. 초기 dock은 숨김이며 처음 펼치면 최대화를 요청하고, 공간이 부족하면 기본 크기로 표시한다. 크기 전환용 단축키는 없다. 다른 파일은 `install(prompt, config_path="/absolute/path/config.jsonc")`로 지정한다.

`dock.mouse.enabled`로 독 마우스를 켜거나 끈다(기본 `false`). `true`로 지정한 경우 독이 보일 때만 마우스를 활성화하고 숨김·해제 시 기존 호스트 설정으로 복원한다. 바로가기·음악 버튼·재생바만 조작하며 날씨 클릭 기능은 아직 없다. 터미널 텍스트 선택은 Shift+드래그를 사용한다. [MPRIS와 마우스 설정](media.md)을 참고한다.

## 사용자 설정

설정은 `dock`, `audio`, `media`, `clock`, `widgets`, `system`, `toolbar`로 나눈다. [옵션 구조](options.md)와 [전체 JSONC 예시](config.example.jsonc)를 참고한다. 이전 설정 이름과 구조는 지원하지 않으며 기존 파일을 자동 변환하지 않는다.

바로가기의 런타임 기본값은 빈 목록이며 `[1]`~`[0]`으로 표시한다. 새로 생성하는 파일에는 바로가기 예시 9개와 구분선이 명시된다. 기존 파일에서 항목을 생략하면 예시를 채우지 않는다. 나머지 [현재 기본 설정](defaults.md)에는 Nerd Font 버튼, 38칸 단색 비주얼라이저, `future` 요일 아트가 포함된다. 날씨 위치는 미설정이며 사용자가 직접 지정한다. `widgets.shortcuts.items: []`는 기본 바로가기를 사용하지 않고 빈 슬롯으로 표시한다.

최대화 날씨 위치·날짜·시간·요일 폰트의 전체 예시는 [최대화 정보 영역](dashboard.md)을 따른다.

설정 파일을 수정한 뒤 ish에서 `ish_reload`를 실행한다. `.ishrc.py`의 `install()` 재호출은 같은 인스턴스의 설정을 갱신하며 Float를 추가하지 않는다. 잘못된 설정이나 dok 내부 키 겹침은 기존 구성을 유지하고 로그와 `dock.diagnostic`에 이유를 남긴다. 기존 셸 바인딩과 같은 키는 등록할 수 있으며 원본 바인딩을 보존한다.

```json
{
  "dock": {
    "keys": {
      "toggle": [
        "c-w"
      ]
    },
    "width": {
      "mode": "ratio",
      "value": 0.6
    }
  },
  "audio": {
    "enabled": true,
    "sources": []
  },
  "widgets": {
    "visualizer": {
      "width_columns": 48,
      "style": {
        "colors": [
          "#67e8f9",
          "#c084fc",
          "#fb7185"
        ]
      }
    },
    "title": {
      "style": {
        "color": "#facc15"
      },
      "texts": {
        "prefix": "NOW: ",
        "audio_error": "오디오 오프라인"
      }
    },
    "progress": {
      "style": {
        "color": "#22c55e"
      },
      "width_columns": 40
    },
    "media_controls": {
      "buttons": [
        {
          "action": "previous",
          "text": "⏮",
          "style": {
            "color": "#67e8f9"
          }
        },
        {
          "action": "play_pause",
          "text": "⏯",
          "style": {
            "color": "#a3e635"
          }
        },
        {
          "action": "next",
          "text": "⏭",
          "style": {
            "color": "#67e8f9"
          }
        }
      ]
    },
    "shortcuts": {
      "spacing": {
        "item_gap_columns": 2
      },
      "items": [
        {
          "slot": 1,
          "command": "git",
          "icon": "🌿",
          "label": "Git",
          "style": {
            "color": "#a3e635"
          },
          "key": [
            "c-g"
          ],
          "type": "shortcut"
        },
        {
          "slot": 2,
          "type": "separator",
          "text": "│",
          "style": {
            "color": "#6b7280"
          }
        },
        {
          "slot": 3,
          "command": "rg -n",
          "icon": "🔎",
          "label": "검색",
          "key": [
            "escape",
            "3"
          ],
          "type": "shortcut"
        },
        {
          "slot": 4,
          "command": "ls -la",
          "icon": "📁",
          "label": "파일",
          "key": [
            "escape",
            "4"
          ],
          "type": "shortcut"
        }
      ]
    }
  }
}
```

`["c-g"]`는 Ctrl+G, `["escape", "1"]`은 Alt+1, `["c-x", "c-g"]`는 Ctrl+X 다음 Ctrl+G다. Ctrl+C도 등록할 수 있다. 일반 숫자와 `enter`, `tab`, `backspace` 같은 일반 입력 키 이름은 허용하지 않는다. Ctrl+I/Tab, Ctrl+M/Enter처럼 터미널에서 같은 입력으로 전달되는 조합은 구분할 수 없다.

바로가기 키는 dock이 표시되고 기본 프롬프트에 포커스가 있을 때 우선 동작한다. 같은 키와 겹치는 기존 바인딩은 그동안만 비활성화되며, dock을 닫거나 표시 공간이 부족하면 원래 동작으로 돌아온다. Ctrl+C를 등록했다면 dock이 열린 동안 해당 키는 바로가기를 실행한다. 토글 키는 닫힌 상태에서도 동작한다. 키를 생략한 바로가기는 마우스 클릭이나 `dock.execute(slot)`으로 실행할 수 있다.

slot은 1부터 10까지며 빈 슬롯은 `[1]`부터 `[0]`까지 표시한다. 등록하면 번호를 없애고 `🌿 Git`처럼 이모지와 이름을 표시한다. 명령어는 필수이고 이모지·이름은 생략할 수 있다. 둘 다 생략하면 명령어를 표시한다. `enabled: false`는 이름을 유지한 채 회색으로 표시하고 실행을 막으며, 레코드를 삭제하면 빈 번호 슬롯으로 돌아간다. 폭이 부족하면 이름을 먼저 생략한 뒤 남은 표시를 말줄임하지만 등록된 슬롯을 번호로 되돌리지 않는다.

바로가기의 `style.color`는 아이콘·텍스트의 전경색이며 `#RRGGBB` 형식이다. 컬러 이모지는 터미널·폰트가 가진 고유 색으로 표시될 수 있다. 배경은 기존 투명 배치를 유지한다. 색을 생략하면 기본 청록색을 사용한다.

`widgets.shortcuts.spacing.item_gap_columns`은 바로가기·빈 슬롯·구분선 사이의 공백 칸 수다. 0 이상 정수이며 기본 2칸이다. 큰 값은 화면 안에 10개 슬롯이 들어가도록 자동으로 줄고 넓어지면 복원한다. 명령어와 프롬프트 버퍼를 합칠 때의 공백에는 영향을 주지 않는다.

구분선은 같은 `widgets.shortcuts.items` 목록에 `{"slot": 2, "type": "separator"}`로 넣는다. 슬롯 하나를 차지하며 번호 대신 기본 문자 `│`를 표시한다. `text`로 `┃`·`─` 같은 다른 표시를, `style.color`로 색을 지정할 수 있다. `command`·`key`는 허용하지 않으며 클릭이나 `dock.execute(slot)`으로 실행되지 않는다. 구분선으로 바꾸면 그 슬롯에 있던 바로가기 키 등록도 해제된다. 다음 슬롯의 번호를 당기거나 전체 10개 슬롯 수를 늘리지 않는다.

비주얼라이저의 `widgets.visualizer.style.colors`에 색 하나를 넣으면 단색, 둘 이상을 넣으면 나열한 색을 왼쪽에서 오른쪽으로 균등 배치한 RGB 그라데이션이 된다. 예를 들어 `{"style.colors": ["#22c55e"]}`는 초록 단색이다. 설정 생략 시 기본은 청록 `#67e8f9` 단색이다. 새로 생성하는 파일에는 청록 `#67e8f9` → 보라 `#c084fc` → 분홍 `#fb7185` 그라데이션을 명시한다. 색은 막대 높이가 아닌 가로 위치에 따라 정해진다. 배열은 비어 있을 수 없고 색 형식은 바로가기와 동일하다. 실제 표시 색상 수는 호스트·터미널의 색상 지원 범위를 따른다.

고정 80셀은 `"dock.width": {"mode": "fixed", "value": 80}`이다. 고정값은 39 이상 정수, 비율은 0 초과 1 이하의 유한한 수다. 비율은 PTY 열 수에 곱한 뒤 내림하며 실제 입력 영역 너비로 제한한다. 실제 본문이 39셀 미만이거나 입력 영역이 8줄 미만이면 dock을 잠시 숨긴다. 각 줄의 실제 내용 영역을 설정 너비 안에서 가운데 정렬한다.

`Float.transparent=True`를 사용하고 좌우 여백·바로가기 위 padding·하단 padding을 그리지 않는다. 따라서 그 부분의 입력·자동완성이 실제로 보이며 클릭도 기존 컨트롤로 전달된다. 각 줄의 내용 영역(문자 사이 공백 포함)만 뒤 내용을 덮고 클릭을 처리한다. 내용의 배경색은 터미널 기본 배경(`bg:default`)을 사용한다.

비주얼라이저는 `widgets.visualizer.width_columns`에 1 이상 정수로 표시 길이를 지정하며 기본값은 38칸이다. 예를 들어 `"width_columns": 48`이면 48칸으로 표시한다. dock 본문보다 길면 실제 본문 너비까지만 표시하고 창이 넓어지면 설정 길이로 복원한다. 전체 주파수 범위를 짧은 길이에 모을 때는 구간별 최댓값을 사용하고, 길게 펼칠 때는 인접 대역을 보간한다. 분석 대역 수나 FFT 작업량은 바꾸지 않으며 그라데이션은 실제 표시 길이 전체에 적용한다. 각 줄은 가운데 정렬한다.

`widgets.title.style.color`는 타이틀의 전경색이다. `widgets.title.texts.prefix`는 프로그램 이름 앞 문구이며 기본 `"Now playing: "`다. `"NOW: "`로 바꾸거나 `""`로 지정해 프로그램 이름만 표시할 수 있다. 문구의 공백은 지정한 그대로 사용한다. 플레이어 곡 제목이 연결되면 기존처럼 곡 정보를 우선하며, 오류 안내는 별도의 오류 색을 사용한다.

`widgets.title.texts.audio_error`는 사운드 연결 실패 시 타이틀에 표시할 문구이며 기본 `"Audio connection failed"`다. 한 줄 문자열을 지정하고, `""`로 비우면 오디오 오류 안내 대신 일반 타이틀을 표시한다. 상세 원인은 문구와 별개로 `dock.audio.message`에 유지된다. 설정 오류는 이 문구보다 우선한다. 실패 후 재시도는 기존처럼 dock을 닫았다 다시 열 때만 허용한다.

dock 높이를 더 늘릴 때의 공간 조건과 입력 가림은 [높이 확대 검토](dock-height-review.md)에 정리했다. 최대화도 Float이므로 여러 줄 입력과 자동완성을 가릴 수 있다. 공간이 부족하면 기본 크기로 표시하며 Ctrl+W로 숨길 수 있다.

`widgets.progress.style.color`는 시간 표시와 재생바의 전경색이다. `widgets.progress.width_columns`는 시간 표시를 제외한 막대 칸 수이며 1 이상 정수, 기본 48이다. 실제 본문에서 양쪽 시간과 공백을 제외한 공간 안으로 제한한다.

`widgets.media_controls.buttons`에 원하는 음악 버튼을 표시 순서대로 넣는다. 지원하는 `action`은 `previous`, `play_pause`, `next`, `play`, `pause`, `stop`이다. 각각 한 번씩 넣을 수 있다. `text`는 아이콘·문자이며 생략하거나 비워 두면 해당 액션의 기본 기호를 사용한다. `style.color`는 버튼별 전경색이다. 생략하거나 빈 문자열 또는 `null`로 두면 터미널 기본 글자색을 사용한다. 목록에서 빼면 숨겨지고 `"buttons": []`는 음악 버튼 줄을 그리지 않는다. 긴 표시는 한 줄 안에 맞춰 말줄임한다. 이 설정은 표시 선택이며 플레이어 연결이나 실제 재생 제어를 추가하지 않는다.

## Linux 전체 출력 비주얼라이저

PulseAudio 또는 PipeWire의 PulseAudio 호환 서버에 연결된 모든 출력 장치의 모니터 소스를 자동 수집한다. 여러 앱의 소리를 포함하고, 출력 장치가 바뀌면 다시 탐색한다. 마이크 소스는 선택하지 않는다. WSLg에서는 `RDPSink.monitor`를 통해 Linux 앱의 출력을 읽는다. Windows 네이티브 앱 소리나 해당 오디오 서버를 거치지 않는 ALSA 직접 출력은 이 경로에 포함되지 않는다.

실행 환경에는 `pactl`과 `parec`가 필요하다. Ubuntu/WSL에서는 다음으로 클라이언트 도구를 설치한다. WSLg를 사용할 때 별도 PulseAudio 서버를 시작할 필요는 없다.

```sh
sudo apt-get install --no-install-recommends pulseaudio-utils
```

기본 `audio.enabled`는 `true`이고 `sources: []`는 모든 출력 모니터를 뜻한다. `"sources": ["RDPSink.monitor"]`처럼 범위를 지정하거나 `"enabled": false`로 끌 수 있다. 지정한 이름도 서버의 출력 모니터인지 확인하며 일반 마이크로 대체하지 않는다. 현재 연결 상태·대상·오류는 `dock.audio`로 조회한다.

타이틀에는 `Now playing: Firefox · VLC`처럼 선택한 출력으로 재생 중인 프로그램을 표시한다. 같은 프로그램의 여러 스트림은 하나로 묶고 일시정지·음소거·볼륨 0인 스트림은 제외한다. 프로그램 이름은 약 0.5초마다 갱신하며 조회가 느려도 스펙트럼 수집을 막지 않는다. 플레이어의 곡 정보가 연결돼 있으면 곡 제목을 우선 표시한다.

이름은 오디오 서버에 등록된 앱 이름, 실행 파일 이름, 미디어 이름 순으로 선택한다. Python에서 PCM을 생성해 `pacat`으로 보낸 경우 오디오를 전송하는 `pacat` 이름이 표시된다. 이름은 활성 재생 스트림 기준이므로 앱이 무음 PCM을 보내는 순간에도 표시될 수 있다. 등록된 이름을 구할 수 없으면 `이름 없는 프로그램`으로 표시한다.

프로그램 이름 조회와 별도로 Linux MPRIS 플레이어를 연결해 지원되는 음악 버튼과 재생바를 활성화한다. `media.player`로 대상을 지정하거나 자동 선택할 수 있으며 일시정지해도 선택을 유지한다. 단순 PCM 출력 앱은 재생 제어 대상이 아니다. [연결·선택·오류 처리 계약](media.md)을 따른다.

장시간 표시 시 비주얼라이저 조합 문자열을 터미널의 무제한 폭 캐시에 넣지 않고 글자 단위로 폭을 계산한다. 무음 PCM은 FFT를 건너뛰며, 캡처 읽기와 분석을 분리해 분석이 늦어질 때는 최신 PCM 블록만 보관한다. 실제 소리가 있는 프레임의 FFT 방식과 24개 대역은 유지한다.

48 kHz 스테레오 PCM을 24개 주파수 대역으로 분석하고 최대 초당 20회 화면을 갱신한다. 좌우 채널과 여러 출력의 대역별 최댓값을 사용하므로 위상이 반대인 신호가 상쇄되지 않는다. 음성·음원 파일을 저장하거나 전송하지 않는다. dock이 표시된 입력 대기 중에만 수집하며 숨김·프롬프트 종료·해제 시 캡처 프로세스를 정리한다. 도구 누락·출력 장치 없음·연결 실패는 타이틀 행에 표시하고 비주얼라이저는 정지 상태로 유지한다. 실패 후 캡처와 조회 작업을 종료하며 자동 재연결하지 않는다. dock을 닫았다 다시 열면 한 번 새로 연결을 시도한다. 단순 화면 갱신·크기 변경·프롬프트 재진입은 실패 후 재시도를 발생시키지 않는다. 정상 연결 중에는 출력 장치와 프로그램 정보를 계속 조회한다.

## 바로가기 실행

`git` 바로가기에서 프롬프트 버퍼가 `status --short`이면 `git status --short`를 즉시 제출한다. 버퍼가 비어 있으면 등록 명령어만 실행한다. 경계에 공백이 없을 때만 한 칸을 추가하며 커서 위치와 상관없이 전체 버퍼를 사용한다. 회색 자동 제안은 포함하지 않는다.

따옴표, 파이프, 변수 확장, 개행은 셸 구문 그대로 전달한다. 예를 들어 `echo`에 `hello; pwd`를 붙이면 `echo hello; pwd`가 된다. 버퍼 전체를 자동으로 하나의 인자로 인용하지 않는다.

ish 기본 버퍼의 검증·수락 경로를 사용한다. 검증 실패 시 원본 입력과 커서·선택을 복원하고 오류를 표시한다. 수락한 명령은 기존 ish 분배 경로로 처리되며 히스토리 설정도 따른다. 검색창이나 다른 컨트롤에 포커스가 있으면 바로가기를 실행하지 않는다.

## 공개 API와 값의 소유권

| Owner | 공개 계약 |
| --- | --- |
| `DockConfig` | 루트 설정 조합, 모드 키와 바로가기 키의 교차 검증, `from_dict`, `to_dict`, `schema` |
| `Width` | fixed/ratio의 값 범위, 기본 너비, `resolve`와 같은 규칙에서 생성한 스키마 |
| `Shortcut` | 슬롯 수, 명령어·이모지 필드, 최소 축약 폭, `compose`, `display` |
| `Separator`, `SlotEntryRule` | 구분선 문자·색·비실행 상태, 슬롯 레코드 종류 판별과 같은 규칙의 스키마 |
| `ShortcutsConfig` | 항목 기본값·슬롯·키 검증, `entry`·`shortcut`, 화면 폭에 맞춘 간격 제한 |
| `DockPalette`, `HexColor`, `VisualizerConfig`, `VisualizerStyle` | 기본 색상, RGB 색 검증·스키마·정규화, 비주얼라이저 색 목록·길이·가로 보간·표시 대역 배치 |
| `TitleConfig`, `ProgressConfig` | 타이틀 색·접두 문구·프로그램 이름 조합, 재생바 색·길이·표시 제한 |
| `PlaybackAction`, `TransportButton`, `TransportConfig` | 버튼 종류·기본 기호, 표시 문자·색, 버튼 순서·중복 검증·스키마 |
| `KeySequence` | 키 정규화, 허용 접두 키와 시퀀스 겹침 판정 |
| `ConfigFiles`, `ConfigStore` | 기본 설정 경로, JSONC 읽기와 원자적 저장, 최초 생성 |
| `Jsonc`, `Record` | 주석 문법 처리와 Owner가 선언한 항목 설명으로 문서 생성 |
| `DockState` | 표시 의도, 프롬프트 전환과 제출 상태 전이 |
| `WidgetsConfig`, `ClockConfig`, `WeekdayConfig`, `WeatherConfig` | 최대화 정보 설정, 날짜·시간·폰트·위치·갱신 주기의 검증·schema |
| `WeekdayRenderer`, `DashboardRuntime`, `OpenMeteo` | 아트 캐시, 정보 갱신 작업, 날씨 제공자 프로토콜 해석 |
| `DockGeometry`와 `DockView` | 행 구성, 공간 조건, 가운데 정렬과 클릭 영역 |
| `HostAccess` | ish PTY 크기와 UI 스레드 접근 |
| `ShortcutSubmission` | 기본 버퍼를 통한 제출과 검증 거부 복원 |
| `AudioConfig`와 `AudioSnapshot` | 오디오 설정, 출력 소스 선택, 상태·측정값 검증 |
| `PulseBackend` | 출력 모니터 조회, PulseAudio 필드·명령 해석, 캡처 프로세스 정리 |
| `SpectrumAnalyzer` | PCM 형식·샘플링·대역·정규화·여러 출력 결합 |
| `AudioRuntime` | 표시 수명에 맞춘 캡처 작업, 장치 재탐색과 연결 복구 |

설정 dataclass의 필드와 Rule 선언을 JSON 읽기·쓰기·스키마에 공동 사용한다. UI와 실행 계층은 JSON 딕셔너리를 해석하지 않는다. 스키마는 구조와 선언된 범위를 제공하며 키 정규화·슬롯 중복·키 시퀀스 충돌까지 포함한 최종 해석은 반드시 `DockConfig.from_dict()`로 수행한다.

프로그램에서 변경할 때도 같은 공개 Owner를 사용한다.

```python
from dataclasses import replace
from dok import Shortcut, Width

dock.configure(replace(
    dock.config,
    width=Width(mode="ratio", value=0.75),
    shortcuts=(Shortcut(1, "git", emoji="🌿", label="Git", key=("escape", "1")),),
))
dock.save()  # 적용된 설정을 현재 config_path에 저장
dock.show()
```

`configure()`는 타입이 있는 `DockConfig`만 받는다. JSON 딕셔너리는 먼저 `DockConfig.from_dict(data)`로 해석한다. `configure()` 실패 시 기존 설정과 키를 유지한다. `save()`와 파일 편집 후 `reload()`는 명시적인 별도 동작이다.

`dock.toggle()`, `dock.show(False)`, `dock.reload()`, `dock.execute(1)`, `dock.uninstall()`을 제공한다. `uninstall()`은 소유한 Float와 핸들러만 제거한다. 해제 후 다시 설치할 때는 `install()`을 호출한다.

`PlaybackSnapshot`과 `dock.set_playback(snapshot)`은 외부 표시값을 전달하는 공개 계약으로 유지한다. MPRIS 연결 시 실제 플레이어 상태를 우선 표시하고, 미연결 시 외부 표시값을 사용한다. `dock.media_players`와 `dock.media_diagnostic`으로 연결을 관찰한다. 스펙트럼과 오디오 수집 상태는 별도의 `AudioSnapshot`이 소유한다.

## 검증과 수동 미리보기

Linux Python 3.12.14 환경에서 실행한다. runner는 소스를 Linux 파일시스템에 복사하고 SHA256을 확인한 뒤 테스트하며 결과를 `tests/dok/reports/` 아래에 기록한다.

이번 구현의 테스트 결과와 검증 범위는 [구현 검증](validation.md)에 정리했다.

```sh
.venv-linux312/bin/python tests/dok/run_linux.py
```

이 실행기는 dok 테스트만 실행하며 llm/hub 전체 회귀를 시작하지 않는다. 사용 중인 환경에 prompt-toolkit과 저장소 테스트 의존성이 설치되어 있어야 한다.

실제 터미널에서 독립 미리보기를 실행할 수 있다.

```sh
.venv-linux312/bin/python -m tests.dok.terminal_fixture --offline
```

미리보기는 Ctrl+W, Alt+1..3과 창 크기 변경을 확인하는 용도이며 조합한 명령을 출력만 한다. 실제 셸 명령은 실행하지 않는다. Ctrl+D로 종료한다.

자동 테스트와 `--offline` 미리보기는 실제 시스템 오디오를 수집하거나 날씨 서버에 접속하지 않는다. 실제 오디오와 날씨를 포함한 수동 미리보기에는 `--offline`을 생략한다. 오디오만 끄려면 `--no-audio`를 사용한다. 오디오 도구와 출력 모니터 설정은 위 Linux 전체 출력 비주얼라이저 안내를 따른다.

Float는 입력이나 자동완성의 공간을 예약하지 않는다. 겹친 내용은 가려진 채 편집될 수 있으며 토글로 확인한다. 다른 플러그인의 입력 컨트롤에 포커스가 옮겨가면 dock을 잠시 숨기고, 셸 입력으로 돌아오면 표시 의도를 복원한다. 입력 커서는 dock 위에 남을 수 있고, 이모지 폭은 터미널·폰트에 따라 달라질 수 있다. 필요한 경우 사용자가 이모지를 단순 기호로 바꾼다.
