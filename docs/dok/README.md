# dok 셸 dock

`dok`는 ish 입력 대기 화면에 음악 정보 자리와 바로가기를 표시하는 Linux 플러그인이다. ish 본체를 수정하지 않고 `prompt.set_float()`로 기존 입력 영역 위에 dock을 겹쳐 그린다.

구현 코드는 `dok/`, 문서는 `docs/dok/`, 자동 테스트와 독립 미리보기는 `tests/dok/`에서 관리한다. 검증 결과는 `tests/dok/reports/`에 생성한다. 개인 스크립트인 `manual/`과 생성된 보고서인 `reports/`는 Git에 포함하지 않는다.

[Float dock 설계](design.md)에 배치, 표시 우선순위, 사용자 설정, 입력 처리, 재생 연동과 구현 검증 기준을 정리했다. 토글 키·명령어·이모지는 사용자가 수정하고, 너비는 고정 셀 수 또는 ish PTY 너비 비율로 지정한다. 바로가기는 등록 명령어와 현재 프롬프트 버퍼 전체를 이어 붙여 즉시 제출한다.

현재 구현은 dock, 바로가기와 Linux 출력 오디오 비주얼라이저를 제공한다. 재생 바·제목·재생 버튼은 아직 외부 플레이어에 연결하지 않았다. 비주얼라이저는 플레이어 상태와 독립적으로 Linux 오디오 서버의 출력 모니터를 읽는다.

## 설치

Python 3.12.14와 prompt-toolkit 3.0.53을 사용하는 ish에서 실행한다. 저장소의 `dok/` 디렉터리만 ish 설정 홈의 `plugin/script/dok/`로 복사한다. 기본 경로는 `~/ish/plugin/script/dok/`이며 `--home`을 사용하면 해당 홈 아래에 설치한다. 다른 플러그인이나 테스트 파일은 필요하지 않다.

`.ishrc.py`에서 다음과 같이 연결한다.

```python
dok_plugin = plugin.get("dok")
if dok_plugin is not None:
    dock = dok_plugin.install(prompt)
```

처음 설치하면 `<ish home>/dok/config.json`에 기본 설정을 저장한다. 기본 토글은 F8이고 초기 dock은 숨김이다. 등록된 명령어는 없으며 10개 슬롯을 빈 자리로 표시한다. 다른 설정 파일을 사용하려면 `install(prompt, config_path="/absolute/path/config.json")`으로 지정한다.

마우스 클릭을 사용하려면 사용자 설정에서 `prompt.mouse_support = True`를 지정한다. 플러그인은 이 호스트 설정을 바꾸지 않는다. 토글과 등록한 단축키는 마우스 설정과 무관하게 동작한다. 마우스를 켜면 일반 터미널 텍스트 선택 방식이 달라질 수 있다.

## 사용자 설정

설정 파일을 수정한 뒤 ish에서 `ish_reload`를 실행한다. `.ishrc.py`의 `install()` 재호출은 같은 인스턴스의 설정을 갱신하며 Float를 추가하지 않는다. 잘못된 설정이나 dok 내부 키 겹침은 기존 구성을 유지하고 로그와 `dock.diagnostic`에 이유를 남긴다. 기존 셸 바인딩과 같은 키는 등록할 수 있으며 원본 바인딩을 보존한다.

```json
{
  "toggle_key": ["f8"],
  "width": {"mode": "ratio", "value": 0.6},
  "audio": {"enabled": true, "sources": []},
  "shortcuts": [
    {"slot": 1, "command": "git", "emoji": "🌿", "label": "Git", "key": ["c-g"]},
    {"slot": 2, "command": "rg -n", "emoji": "🔎", "label": "검색", "key": ["escape", "2"]},
    {"slot": 3, "command": "ls -la", "emoji": "📁", "label": "파일", "key": ["escape", "3"]}
  ]
}
```

`["c-g"]`는 Ctrl+G, `["escape", "1"]`은 Alt+1, `["c-x", "c-g"]`는 Ctrl+X 다음 Ctrl+G다. Ctrl+C도 등록할 수 있다. 일반 숫자와 `enter`, `tab`, `backspace` 같은 일반 입력 키 이름은 허용하지 않는다. Ctrl+I/Tab, Ctrl+M/Enter처럼 터미널에서 같은 입력으로 전달되는 조합은 구분할 수 없다.

바로가기 키는 dock이 표시되고 기본 프롬프트에 포커스가 있을 때 우선 동작한다. 같은 키와 겹치는 기존 바인딩은 그동안만 비활성화되며, dock을 닫거나 표시 공간이 부족하면 원래 동작으로 돌아온다. Ctrl+C를 등록했다면 dock이 열린 동안 해당 키는 바로가기를 실행한다. 토글 키는 닫힌 상태에서도 동작한다. 키를 생략한 바로가기는 마우스 클릭이나 `dock.execute(slot)`으로 실행할 수 있다.

slot은 1부터 10까지며 10번 표시 번호는 0이다. 명령어는 필수이고 이모지와 이름은 생략할 수 있다. `enabled: false`로 비활성화하거나 레코드를 삭제해 등록을 해제한다. 이름이 길면 표시를 축약하고 그래도 공간이 부족하면 번호 10개만 표시한다.

고정 80셀은 `"width": {"mode": "fixed", "value": 80}`이다. 고정값은 39 이상 정수, 비율은 0 초과 1 이하의 유한한 수다. 비율은 PTY 열 수에 곱한 뒤 내림하며 실제 입력 영역 너비로 제한한다. 실제 본문이 39셀 미만이거나 입력 영역이 7줄 미만이면 dock을 잠시 숨긴다. 각 줄의 실제 내용 영역을 설정 너비 안에서 가운데 정렬한다.

`Float.transparent=True`를 사용하고 좌우 여백·하단 padding을 그리지 않는다. 따라서 그 부분의 입력·자동완성이 실제로 보이며 클릭도 기존 컨트롤로 전달된다. 각 줄의 내용 영역(문자 사이 공백 포함)만 뒤 내용을 덮고 클릭을 처리한다. 내용의 배경색은 터미널 기본 배경(`bg:default`)을 사용한다.

비주얼라이저의 기본 길이는 24칸이고 재생 막대는 시간 표시를 제외하고 최대 48칸이다. 각 줄은 가운데 정렬하며, 설정한 본문 너비가 부족하면 표시를 줄인다.

## Linux 전체 출력 비주얼라이저

PulseAudio 또는 PipeWire의 PulseAudio 호환 서버에 연결된 모든 출력 장치의 모니터 소스를 자동 수집한다. 여러 앱의 소리를 포함하고, 출력 장치가 바뀌면 다시 탐색한다. 마이크 소스는 선택하지 않는다. WSLg에서는 `RDPSink.monitor`를 통해 Linux 앱의 출력을 읽는다. Windows 네이티브 앱 소리나 해당 오디오 서버를 거치지 않는 ALSA 직접 출력은 이 경로에 포함되지 않는다.

실행 환경에는 `pactl`과 `parec`가 필요하다. Ubuntu/WSL에서는 다음으로 클라이언트 도구를 설치한다. WSLg를 사용할 때 별도 PulseAudio 서버를 시작할 필요는 없다.

```sh
sudo apt-get install --no-install-recommends pulseaudio-utils
```

기본 `audio.enabled`는 `true`이고 `sources: []`는 모든 출력 모니터를 뜻한다. `"sources": ["RDPSink.monitor"]`처럼 범위를 지정하거나 `"enabled": false`로 끌 수 있다. 지정한 이름도 서버의 출력 모니터인지 확인하며 일반 마이크로 대체하지 않는다. 현재 연결 상태·대상·오류는 `dock.audio`로 조회한다.

타이틀에는 `재생 중: Firefox · VLC`처럼 선택한 출력으로 재생 중인 프로그램을 표시한다. 같은 프로그램의 여러 스트림은 하나로 묶고 일시정지·음소거·볼륨 0인 스트림은 제외한다. 프로그램 이름은 약 0.5초마다 갱신하며 조회가 느려도 스펙트럼 수집을 막지 않는다. 플레이어의 곡 정보가 연결돼 있으면 곡 제목을 우선 표시한다.

이름은 오디오 서버에 등록된 앱 이름, 실행 파일 이름, 미디어 이름 순으로 선택한다. Python에서 PCM을 생성해 `pacat`으로 보낸 경우 오디오를 전송하는 `pacat` 이름이 표시된다. 이름은 활성 재생 스트림 기준이므로 앱이 무음 PCM을 보내는 순간에도 표시될 수 있다. 등록된 이름을 구할 수 없으면 `이름 없는 프로그램`으로 표시한다.

프로그램 이름 조회만으로 재생을 제어할 수는 없다. 현재 재생 버튼은 미연결 상태다. Linux 앱이 [MPRIS](https://specifications.freedesktop.org/mpris/latest/Player_Interface.html)를 제공한다면 별도 어댑터로 재생·일시정지·정지를 연결할 수 있다. 여러 앱이 있으면 제어 대상을 선택하고, 일시정지로 오디오 스트림이 사라져도 그 대상을 유지해야 다시 재생할 수 있다. 단순 PCM 출력 앱의 음소거는 재생 일시정지와 다르다.

장시간 표시 시 비주얼라이저 조합 문자열을 터미널의 무제한 폭 캐시에 넣지 않고 글자 단위로 폭을 계산한다. 무음 PCM은 FFT를 건너뛰며, 캡처 읽기와 분석을 분리해 분석이 늦어질 때는 최신 PCM 블록만 보관한다. 실제 소리가 있는 프레임의 FFT 방식과 24개 대역은 유지한다.

48 kHz 스테레오 PCM을 24개 주파수 대역으로 분석하고 최대 초당 20회 화면을 갱신한다. 좌우 채널과 여러 출력의 대역별 최댓값을 사용하므로 위상이 반대인 신호가 상쇄되지 않는다. 음성·음원 파일을 저장하거나 전송하지 않는다. dock이 표시된 입력 대기 중에만 수집하며 숨김·프롬프트 종료·해제 시 캡처 프로세스를 정리한다. 도구 누락이나 연결 실패는 비주얼라이저 줄에 표시하고 재연결한다.

## 바로가기 실행

`git` 바로가기에서 프롬프트 버퍼가 `status --short`이면 `git status --short`를 즉시 제출한다. 버퍼가 비어 있으면 등록 명령어만 실행한다. 경계에 공백이 없을 때만 한 칸을 추가하며 커서 위치와 상관없이 전체 버퍼를 사용한다. 회색 자동 제안은 포함하지 않는다.

따옴표, 파이프, 변수 확장, 개행은 셸 구문 그대로 전달한다. 예를 들어 `echo`에 `hello; pwd`를 붙이면 `echo hello; pwd`가 된다. 버퍼 전체를 자동으로 하나의 인자로 인용하지 않는다.

ish 기본 버퍼의 검증·수락 경로를 사용한다. 검증 실패 시 원본 입력과 커서·선택을 복원하고 오류를 표시한다. 수락한 명령은 기존 ish 분배 경로로 처리되며 히스토리 설정도 따른다. 검색창이나 다른 컨트롤에 포커스가 있으면 바로가기를 실행하지 않는다.

## 공개 API와 값의 소유권

| Owner | 공개 계약 |
| --- | --- |
| `DockConfig` | 루트 필드, 기본값, 슬롯 중복·키 겹침 검증, `from_dict`, `to_dict`, `schema`, `shortcut` |
| `Width` | fixed/ratio의 값 범위, 기본 너비, `resolve`와 같은 규칙에서 생성한 스키마 |
| `Shortcut` | 슬롯 수, 명령어·이모지 필드, 최소 축약 폭, `compose`, `display` |
| `KeySequence` | 키 정규화, 허용 접두 키와 시퀀스 겹침 판정 |
| `ConfigStore` | 사용자 파일 위치, JSON 읽기와 원자적 저장 |
| `DockState` | 표시 의도, 프롬프트 전환과 제출 상태 전이 |
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

`PlaybackSnapshot`과 `dock.set_playback(snapshot)`은 향후 플레이어의 제목·재생 진행 정보를 전달하는 계약이다. 스펙트럼과 오디오 수집 상태는 별도의 `AudioSnapshot`이 소유한다. 플레이어 외부 연결·재생 제어·자동 시간 보간은 아직 없다.

## 검증과 수동 미리보기

Linux Python 3.12.14 환경에서 실행한다. runner는 소스를 Linux 파일시스템에 복사하고 SHA256을 확인한 뒤 테스트하며 결과를 `tests/dok/reports/` 아래에 기록한다.

이번 구현의 테스트 결과와 검증 범위는 [구현 검증](validation.md)에 정리했다.

```sh
.venv-linux312/bin/python tests/dok/run_linux.py
```

이 실행기는 dok 테스트만 실행하며 llm/hub 전체 회귀를 시작하지 않는다. 사용 중인 환경에 prompt-toolkit과 저장소 테스트 의존성이 설치되어 있어야 한다.

실제 터미널에서 독립 미리보기를 실행할 수 있다.

```sh
.venv-linux312/bin/python -m tests.dok.terminal_fixture --no-audio
```

미리보기는 F8, Alt+1..3, 마우스와 창 크기 변경을 확인하는 용도이며 조합한 명령을 출력만 한다. 실제 셸 명령은 실행하지 않는다. Ctrl+D로 종료한다.

자동 테스트와 위 미리보기 명령은 실제 시스템 오디오를 수집하지 않는다. 오디오를 포함한 수동 미리보기에는 `--no-audio`를 생략한다. 오디오 도구와 출력 모니터 설정은 위 Linux 전체 출력 비주얼라이저 안내를 따른다.

Float는 입력이나 자동완성의 공간을 예약하지 않는다. 겹친 내용은 가려진 채 편집될 수 있으며 토글로 확인한다. 다른 플러그인의 입력 컨트롤에 포커스가 옮겨가면 dock을 잠시 숨기고, 셸 입력으로 돌아오면 표시 의도를 복원한다. 입력 커서는 dock 위에 남을 수 있고, 이모지 폭은 터미널·폰트에 따라 달라질 수 있다. 필요한 경우 사용자가 이모지를 단순 기호로 바꾼다.
