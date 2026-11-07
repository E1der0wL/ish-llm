# dok 구현 검증

2026-11-01 기준. 구현은 `dok/`, 문서는 `docs/dok/`, 검증 코드는 `tests/dok/`에 있다. ish 본체는 수정하지 않았다.

## 최초 구현 검증

Linux Python 3.12.14와 prompt-toolkit 3.0.53에서 **36개 테스트가 모두 통과**했다. 건너뛴 테스트는 없다. Windows 파일시스템에서 읽은 소스를 LF 기준 SHA256으로 검증한 뒤 Linux 파일시스템의 독립 스냅샷에서 실행했다.

- [실행 로그](../../tests/dok/reports/dok-qdqtxlr9/dok.txt)
- [실행 결과](../../tests/dok/reports/dok-qdqtxlr9/results.json)
- [스냅샷 목록과 해시](../../tests/dok/reports/dok-qdqtxlr9/snapshot.json)

검증한 항목은 다음과 같다.

- 실제 ish 레이아웃·Float·키 등록·기본 버퍼 경로와 플러그인 로더 연동
- 일반/여러 열 자동완성, 프레임, 긴 다중 행 입력 위 표시 및 토글 후 원상 복원
- 토글 시 입력·포커스 보존, 다른 플러그인 입력 중 임시 숨김, 해제 시 외부 핸들 보존
- 실제 PTY 너비 조회, 고정/비율 너비, 작은 화면에서 숨김과 복귀, 한글·결합 이모지 축약
- 클릭 영역, 가려진 내용으로 클릭 전달 방지, 오른쪽 클릭으로 실행하지 않음
- 명령어와 전체 버퍼 조합, 히스토리, 검증/전송 거부 복원, 중복 제출 방지
- Ctrl+C 후 다음 프롬프트와 터미널 임시 양도 후 복귀
- 설정 Owner 선언에서 생성한 검증·스키마·저장·조회, 키 충돌과 잘못된 재적용 보존
- 실제 PTY 자식 프로세스에서 dock 표시, Alt+1 제출, Ctrl+D 종료

PTY 미리보기는 조합된 명령을 출력하는 fixture다. 실제 사용자 셸 명령이나 외부 플레이어는 실행하지 않는다. 모든 터미널·폰트에서의 이모지 모양, 외부 전체 화면 프로그램과 실제 플레이어 연동은 이 자동 검증의 범위가 아니다.

## 전체 회귀 중단

사용자 요청에 따라 이 작업에서 시작한 llm/hub 전체 회귀 프로세스를 중단했다. [중단 기록](../../tests/dok/reports/dok-ecvjungf/interruption.json)의 부분 실행은 전체 회귀 완료 결과로 취급하지 않는다. 현재 `tests/dok/run_linux.py`는 dok만 검사하며 전체 회귀 옵션을 제공하지 않는다.

설치·설정과 수동 미리보기 방법은 [사용 안내](README.md)를 따른다.

## 터미널 기본 배경 적용

별도 배경색을 `bg:default`로 바꾼 뒤 dok 전용 테스트 36개가 다시 통과했다. [변경 후 로그](../../tests/dok/reports/dok-75gxy6c1/dok.txt)와 [변경 후 스냅샷](../../tests/dok/reports/dok-75gxy6c1/snapshot.json)을 참조한다. 부모 스타일에 배경색이 있어도 최종 배경이 `default`로 해석되는 것을 확인했다. llm/hub 전체 회귀는 실행하지 않았다.

WSL에서는 WSLg PulseServer 소켓과 Windows PowerShell 실행 파일의 존재만 읽기 전용으로 확인했다. Windows 오디오 캡처·분석·전달은 아직 구현하거나 실행하지 않았다.

## 최신: 실제 투명 표시·Ctrl 우선권·Linux 출력 오디오

최종 dok 전용 테스트 **47개가 모두 통과**했다. [로그](../../tests/dok/reports/dok-_eypcaod/dok.txt), [결과](../../tests/dok/reports/dok-_eypcaod/results.json), [소스 해시](../../tests/dok/reports/dok-_eypcaod/snapshot.json)를 보관했다. llm/hub 전체 회귀는 실행하지 않았다.

- `Float.transparent=True`와 내용 폭의 Window를 사용한다. 좌우 여백·padding의 입력/자동완성과 클릭 동작이 유지되는지 검증했다.
- 명시적 Ctrl 조합의 저장·재로드, dock 표시 중 기존 키보다 우선 실행, 숨김·해제 후 원본 복원을 확인했다. 기존 eager 접두 키가 여러 단계의 dok 단축키를 먼저 소비하지 않는 경우도 포함했다.
- PCM 무음·서로 다른 주파수·좌우 반대 위상·다중 출력 결합, 장치 추가/제거, 입력 정지 시 무음 처리, 서버 오류와 복구를 검증했다.
- 캡처 대기 중 취소하면 실제 자식 프로세스가 종료되고 회수되는 것을 확인했다. 자동 테스트는 사용자 오디오를 수집하지 않는다.

추가로 실제 WSLg PulseAudio 서버에서 임시 null sink를 만들고 1,500Hz 신호를 해당 장치에만 재생했다. 48 kHz 스테레오 구성에서 스펙트럼 피크 약 0.867과 `running` 상태를 확인했다. `RDPSink.monitor`와 임시 출력 모니터가 함께 탐색됐다. 스피커로 테스트 신호를 출력하지 않았으며 종료 후 캡처 작업과 임시 장치가 모두 정리됐다. [실제 오디오 검증 결과](../../tests/dok/reports/dok-audio-live-whtpdc_j/result.json)

WSL에 `pulseaudio-utils` 클라이언트 도구와 필수 라이브러리를 설치했다. 별도 오디오 서버를 설치하거나 ish 본체를 수정하지 않았다. Windows 네이티브 출력과 오디오 서버를 우회하는 직접 ALSA 출력은 이번 구현 범위에 포함되지 않는다.

## 기본 길이 확대와 프로그램 이름 타이틀

재생 막대는 32→48칸, 비주얼라이저는 16→24칸으로 확대했다. 출력 프로그램 이름을 타이틀에 표시하며 여러 스트림의 중복 제거, 일시정지·음소거·선택하지 않은 출력의 제외, 실행 파일 이름 대체와 곡 정보 우선 표시를 검증했다.

현재 참조 ish의 입력 API 변경에 맞춰 dok 테스트·미리보기에서 `read_and_dispatch()`를 사용하도록 갱신했다. ish 본체는 수정하지 않았다. dok 전용 **50개 테스트가 모두 통과**했다. [실행 로그](../../tests/dok/reports/dok-c3_632u0/dok.txt), [소스 해시](../../tests/dok/reports/dok-c3_632u0/snapshot.json)

실제 WSLg 서버의 임시 무음 출력 장치에서도 1,500Hz 신호와 `재생 중: dok audio smoke`를 함께 확인했다. 캡처 작업과 임시 장치를 정상 정리했다. [실제 오디오·프로그램 이름 검증](../../tests/dok/reports/dok-audio-live-ryahioy3/result.json)

## 장시간 표시 성능 점검

느려짐을 유발할 수 있는 세 경로를 수정했다. 비주얼라이저 문자열이 prompt-toolkit의 무제한 짧은 문자열 폭 캐시에 쌓이던 경로를 글자 단위 계산과 한 줄 전용 Window로 제거했다. 무음 PCM의 FFT를 생략하고, 분석이 느려질 때 읽기 작업이 최신 블록만 남기도록 분리했다. 호스트와 llm/hub 코드는 수정하지 않았다.

`tests/dok/manual/performance_probe.py`로 기존·수정 코드의 실제 Float를 각각 10,000회 그렸다. 아래 메모리는 tracemalloc이 관찰한 유지 메모리이며 전체 프로세스 RSS가 아니다.

| 측정 | 기존 | 수정 |
| --- | ---: | ---: |
| 5,000회 뒤 폭 캐시 증가 | 5,006개 | 7개 |
| 10,000회 뒤 폭 캐시 증가 | 10,006개 | 7개 |
| 5,000회 뒤 유지 메모리 증가 | 711,848바이트 | 81,558바이트 |
| 10,000회 뒤 유지 메모리 증가 | 1,345,920바이트 | 81,838바이트 |
| 무음 1프레임 분석 CPU 시간 | 약 7.04ms | 약 0.0335ms |

[변경 전 측정](../../tests/dok/reports/dok-performance-al092xww/before.json), [변경 후 측정](../../tests/dok/reports/dok-performance-al092xww/after.json)에 소스 해시를 포함했다. 10,000프레임은 20FPS에서 약 8분 20초 분량이지만 가속 반복 검사이며 실제 8분 20초 경과 검사는 아니다. 소리가 있는 PCM의 FFT 방식과 비용은 유지된다.

`tests/dok/manual/terminal_soak.py`로 오디오를 켠 실제 PTY 프리뷰를 60초 유지하고 이후 5회 숨김·표시를 반복했다. 입력 반영은 약 4.9~6.2ms, 열린 FD 8개·스레드 2개·캡처 자식 1개가 관찰 구간에서 유지됐다. RSS는 약 37.7~37.9MiB 범위였고 정상 종료했다. [PTY 결과](../../tests/dok/reports/dok-soak-9yu0k4py/terminal.json). 사용자의 Windows Terminal 장시간 세션 전체를 재현한 검사는 아니다.

dok 자동 테스트 **54개가 통과**했다. 실제 Float의 반복 렌더링에서도 문자열 캐시가 누적되지 않는지 확인했으며, 분석을 지연시키고 100개의 새 블록을 전달했을 때 다음 분석은 최신 블록만 처리하고 닫기·EOF에서 읽기 작업을 회수하는지 검증했다. [테스트 로그](../../tests/dok/reports/dok-pst9t0v5/dok.txt), [소스 해시](../../tests/dok/reports/dok-pst9t0v5/snapshot.json).

실제 WSLg 임시 null sink의 1,500Hz 신호·프로그램 이름·종료 정리도 재검증했다. 스피커로 테스트 신호를 보내지 않았다. [실제 오디오 검증](../../tests/dok/reports/dok-audio-live-_kgelfme/result.json). llm/hub 전체 회귀는 실행하지 않았다.

## 바로가기 표시와 사용자 색상

빈 슬롯만 `[숫자]`로 표시하고 등록된 슬롯은 이모지·이름 또는 명령어를 표시한다. 좁은 화면에서도 등록된 항목을 번호로 바꾸지 않고 말줄임하며, 빈 슬롯과 클릭 대상은 유지한다. 바로가기별 `color`, 비주얼라이저의 `visualizer.colors` 단색·가로 그라데이션을 추가했다. 색 검증·정규화·스키마·RGB 보간은 `dok/appearance.py`가 소유한다.

dok 테스트 **60개가 통과**했다. 잘못된 색에 대한 schema/runtime 거부 일치, 설정 저장·조회, 다중 그라데이션의 끝점과 중간 색, 실제 화면 색·투명 배경, 좁은 화면의 클릭 좌표, 그라데이션 반복 렌더링의 폭 캐시 한도를 검증했다. [실행 로그](../../tests/dok/reports/dok-ekxtagl0/dok.txt), [소스 해시](../../tests/dok/reports/dok-ekxtagl0/snapshot.json).

ish 본체 수정이나 llm/hub 전체 회귀는 수행하지 않았다. 실제 RGB 색의 표시 정밀도와 컬러 이모지의 고유 색은 호스트·터미널·폰트 설정을 따른다.

## 비주얼라이저 길이와 구분선 슬롯

`visualizer.length`의 기본값·양의 정수 검증·화면 폭 제한·대역 축소/확대를 `VisualizerConfig`에 추가했다. 길이가 1인 경우, 축소 시 피크 보존, 확대 시 보간, 매우 큰 설정의 화면 폭 제한, 리사이즈 뒤 복원과 그라데이션 끝점을 확인했다.

같은 슬롯 목록에 `Separator`를 등록할 수 있다. `SlotEntryRule`이 `kind: separator` 판별과 schema를 소유하며, 설정 조회는 `entry()`와 실행 가능한 `shortcut()`으로 구분한다. 저장·조회 왕복, 중복 슬롯 거부, 명령·키·잘못된 문자 거부, 구분선의 색상과 클릭 비실행, 기존 바로가기를 구분선으로 바꾼 뒤 호스트 키 복원을 검증했다.

dok 테스트 **67개가 통과**했다. [실행 로그](../../tests/dok/reports/dok-w3es3ihi/dok.txt), [소스 해시](../../tests/dok/reports/dok-w3es3ihi/snapshot.json). ish 본체 변경과 llm/hub 전체 회귀는 수행하지 않았다.

## 타이틀·재생바·음악 버튼·항목 간격 설정

`TitleConfig`가 타이틀 전경색과 프로그램 이름 앞 접두 문구를 소유한다. 기존 `AudioSnapshot.application_caption`도 기본 `TitleConfig`의 조합 규칙을 사용한다. `ProgressConfig`는 시간 표시를 제외한 막대 길이와 행 전경색을 소유한다. `PlaybackAction`의 기본 기호를 `TransportButton`이 사용하고 `TransportConfig`가 버튼 순서·중복 검증과 schema를 제공한다. `ShortcutLayout`의 padding은 명령 조합 공백과 분리했다.

dok 테스트 **77개가 통과**했다. 새 설정의 저장·조회, 기존 설정의 기본값, schema/runtime 거부 일치, 접두 문구 제거, 실제 타이틀·진행바 셀 색, 버튼 순서·사용자 문자·숨김·비실행 클릭, 긴 버튼 말줄임, 0칸 간격 및 큰 길이·간격의 화면 폭 제한을 검증했다. [실행 로그](../../tests/dok/reports/dok-s_t2h1qi/dok.txt), [소스 해시](../../tests/dok/reports/dok-s_t2h1qi/snapshot.json).

이번 변경은 표시 설정이다. 음악 버튼의 플레이어 연결은 추가하지 않았으며 ish 본체 변경과 llm/hub 전체 회귀도 수행하지 않았다.

## 바로가기 위 여백과 오디오 연결 실패 처리

`DockRow`에 바로가기 위 투명 padding 행을 추가했다. 본문은 6줄, 하단 padding까지 7줄이며 최소 입력 영역 높이는 8줄이다. 기존 `DockGeometry` 선언으로 배치와 표시 가능 여부를 계산한다. 빈 행의 뒤 화면과 클릭 처리는 유지한다.

오디오 오류는 `AudioSnapshot.diagnostic`을 통해 타이틀 행에 표시하고 비주얼라이저는 정지 상태로 유지한다. 설정 오류가 함께 있으면 설정 오류를 우선 표시한다. `AudioRuntime`이 연결 실패를 보관하고 캡처·조회 작업을 종료한다. 사용자가 dock을 닫았다 다시 열 때만 `begin_activation()`으로 실패 후 새 시도를 허용한다. 정상 연결 중 장치·프로그램 조회는 유지한다.

Linux Python 3.12.14의 네이티브 파일시스템 스냅샷에서 dok 테스트 **81개가 통과**했다. 실제 Float의 두 padding 행과 자동완성·프레임 복원, 타이틀 오류 표시, 서버·장치·캡처·메타데이터 조회 실패 후 작업 종료, 반복 렌더링과 일시 비활성화 시 재연결 방지, 재열기 시 새 연결을 검증했다. [실행 로그](../../tests/dok/reports/dok-h4724jqz/dok.txt), [소스 해시](../../tests/dok/reports/dok-h4724jqz/snapshot.json).

ish 본체는 수정하지 않았으며 llm/hub 전체 회귀는 수행하지 않았다.

## 사운드 연결 실패 문구 설정과 높이 검토

`TitleConfig.audio_error_text`가 오류 안내 문구의 기본값·한 줄 검증·schema와 표시 해석을 소유한다. 기본은 `사운드 연결 실패`, 빈 문자열은 오류 안내 대신 일반 타이틀을 표시한다. 상세 오류는 `AudioSnapshot`에 유지하며 재연결 수명주기는 변경하지 않았다.

Linux Python 3.12.14에서 dok 테스트 **82개가 통과**했다. 설정 저장·조회, 기본값, schema/runtime 거부 일치, 사용자 문구·빈 문구의 실제 렌더링, 상세 원인 보존과 설정 오류 우선순위를 검증했다. [실행 로그](../../tests/dok/reports/dok-0zp77meg/dok.txt), [소스 해시](../../tests/dok/reports/dok-0zp77meg/snapshot.json).

호스트 레이아웃과 설치된 prompt-toolkit의 높이 계산을 읽어 [높이 확대 검토](dock-height-review.md)를 작성했다. 높이 확대 자체는 구현하거나 실기 검증하지 않았다. ish 본체 변경과 llm/hub 전체 회귀는 수행하지 않았다.

## 숨김·기본·최대화와 날씨·날짜·요일 아트

`DockState`에 3단계 모드와 마지막 펼침 상태 복원을 추가했다. `DockGeometry`는 최대화 높이가 부족하면 기본 모드로 표시하고 사용자 선택은 유지한다. `DashboardConfig`가 날짜·시간·요일·날씨 Owner를 조합하고, `WeekdayRenderer`가 실제 pyfiglet 아트·약어 대체·블록 정렬·한 항목 캐시를 제공한다. 날씨 조회는 httpx 비동기 요청이며 작업 취소와 한 항목 예보 캐시는 `DashboardRuntime`이 소유한다.

사용자 요청에 따라 기본 위치는 화성 동탄(동탄1동 대표 좌표 37.20754, 127.07335), 시간대는 `Asia/Seoul`로 설정했다. 위치 미설정과 네트워크 없는 미리보기도 지원한다. 새 의존성 `pyfiglet`, `httpx`는 플러그인 메타데이터에 선언했다.

Linux Python 3.12.14 네이티브 파일시스템 스냅샷에서 dok 테스트 **94개가 통과**했다. [실행 로그](../../tests/dok/reports/dok-mcrfnrr7/dok.txt), [소스 해시](../../tests/dok/reports/dok-mcrfnrr7/snapshot.json).

- 설정 저장·조회, 폰트·시간대·시간 형식·좌표·주기·키 충돌 검증
- 실제 pyfiglet 출력의 내부 정렬, 모든 요일의 폭 제한과 아트 캐시 재사용
- 날짜·시간 형식과 분/초 경계 갱신 계산
- 날씨 HTTP 요청·응답 해석, 누락 온도, 실패와 갱신 시각 표시
- 최대화 숨김 시 요청 취소, 위치 변경 시 캐시 분리, 재열기 시 캐시 재사용과 실패 후 조회 주기 유지
- 실제 Float 최대화 행·투명 여백 클릭·창 축소/복원·키 전환·입력 버퍼 보존
- 실제 Linux PTY에서 F9 최대화 후 바로가기 제출과 정상 종료

자동 테스트는 날씨 서버에 접속하지 않는다. 별도 실제 조회에서 동탄 좌표의 Open-Meteo 일별 예보 **7일**을 성공적으로 수신·해석했다(2026-11-03 확인). 장시간 최대화 성능 soak는 별도로 수행하지 않았다. ish 본체는 수정하지 않았고 llm/hub 전체 회귀도 실행하지 않았다.

## 최대화 UI 미리보기 반영 — 요일 아트 유지

지역 헤더를 제거하고 날씨 영역을 3줄로 정리했다. 날씨 목표 폭은 본문 절반, 아이콘 이동은 왼쪽 한 셀이다. 온도는 `16° 4°` 형식이며 최저 온도만 기본 회색으로 표시한다. 설정·schema·표시 해석은 `WeatherConfig`와 `WeatherPresentation`이 소유한다. 날짜는 기본 `03 NOVEMBER, 2026.`와 볼드, 시간은 `-06:29-`다. `ClockFormat`이 로케일과 독립적인 영문 월을 해석하고 `CalendarConfig`가 날짜 굵기를 소유한다.

사용자 후속 요청에 따라 기존 pyfiglet 요일 아트를 날씨와 날짜 사이에 유지했다. 시간 아래 3줄, 타이틀 아래 1줄, 버튼 아래 2줄, 바로가기 아래 1줄을 비운다. `DashboardView`와 `DockGeometry`의 공개 행 구성으로 실제 그리기 위치와 최소 높이를 함께 계산한다. 기본 모드 배치는 유지한다. `TextRow`/`TextSpan`을 통해 날씨의 부분 색상과 날짜 굵기를 렌더러까지 전달한다.

Linux Python 3.12.14 네이티브 스냅샷에서 dok 테스트 **96개가 통과**했다. [실행 로그](../../tests/dok/reports/dok-p6vdef23/dok.txt), [소스 해시](../../tests/dok/reports/dok-p6vdef23/snapshot.json).

실제 Float 셀에서 날짜 볼드·시간 일반체·최저 온도 색·요일 아트·각 여백 좌표·클릭 통과를 확인했다. 날씨 폭 축소·아이콘 이동·좁은 폭·음수 및 누락 온도·설정 검증과 실패 안내도 확인했다. 실제 PTY 미리보기는 새 높이를 수용하는 32행 화면에서 최대화 후 바로가기 제출과 정상 종료를 검증했다. 기본 `slant` 5행 아트·정상 날씨·날짜/시간을 모두 표시할 때는 입력 컨테이너 최소 25줄이 필요하다.

이전 설정 파일에 날짜/시간 형식을 명시한 경우 자동으로 덮어쓰지 않는다. 새 형식 설정은 [최대화 정보 영역](dashboard.md)의 예시를 따른다. ish 본체 변경과 llm/hub 전체 회귀는 수행하지 않았다.

## 날씨·요일 아트 사이 여백 제거와 시간 아래 2줄

`DashboardView`가 날씨 직후 요일 아트를 배치하고, 시간 아래 투명 여백은 2줄로 줄였다. 요일 아트와 날짜 사이 여백 1줄은 유지한다. 기본 `slant` 5행 아트·정상 날씨·날짜/시간 기준으로 입력 컨테이너 최소 높이는 23줄이다.

아트 타입은 기존 `dashboard.weekday.font` 계약으로 선택한다. 실제 설치된 pyfiglet의 `ansi_regular`, `ansi_shadow`, `future`, `cyberlarge`, `modular`, `small` 출력을 비교하고 참고 이미지의 굵은 기하학적 느낌에 가까운 `ansi_regular`를 문서에 추천했다. 기본 폰트와 사용자 선택은 변경하지 않았다.

dok 테스트 **96개가 통과**했다. 실제 화면에서 날씨 다음 행에 아트가 시작하는지, 시간과 비주얼라이저 사이에 정확히 2줄이 있는지 확인했다. [실행 로그](../../tests/dok/reports/dok-wn2kvkwh/dok.txt), [소스 해시](../../tests/dok/reports/dok-wn2kvkwh/snapshot.json). ish 본체 변경과 llm/hub 전체 회귀는 수행하지 않았다.

## 요소별 표시·간격과 날씨 요일 문구

`ElementConfig`가 `enabled`, `padding_after`, `basic_padding_after`의 선언·검증·해석을 소유한다. 날짜와 시간은 `calendar.date`/`calendar.time`으로 따로 제어하며, 비활성 요소와 뒤 여백은 실제 행 계산에서 제거한다. 기본 펼침의 기존 간격은 유지하고 사용자 추가 요청에 따라 날씨와 요일 아트 사이의 기본 여백은 다시 1줄로 설정했다. 정상 날씨·5행 아트의 최대화 기본 최소 입력 높이는 24줄이다.

날씨 위 요일은 월~일 순서의 `weekday_labels` 7개로 설정한다. 기본값은 `ᴍᴏɴ`, `ᴛᴜᴇ`, `ᴡᴇᴅ`, `ᴛʜᴜ`, `ꜰʀɪ`, `ꜱᴀᴛ`, `ꜱᴜɴ`이며 실제 글자 모양은 사용자 터미널 폰트에 따른다. 설정과 예시는 [요소 표시와 세로 간격](layout.md)에 정리했다.

Linux Python 3.12.14의 Linux 파일시스템 스냅샷에서 dok 테스트 **104개가 통과**했다(13.108초). [실행 로그](../../tests/dok/reports/dok-hssspwft/dok.txt), [소스 해시](../../tests/dok/reports/dok-hssspwft/snapshot.json).

- schema·생성자 검증·저장/로드의 일치, 여백 범위 및 모드별 해석
- 요소 제거에 따른 높이 축소, 정보 영역만 남는 경우, 전부 비활성인 경우
- 실제 Float 셀의 사용자 여백·날짜 굵기·온도 색상·기존 자동완성/편집 내용과 클릭 보존
- 바로가기 비활성화 시 키 우선 처리와 직접 제출 중단, 재활성화 후 정상 제출
- 모든 정보 요소 비활성화 시 비동기 작업 종료 및 반복 생성 방지
- 작은 영문 요일 기본값·문자 폭·날짜별 매핑·사용자 문구·유효성 검증
- 실제 Linux PTY의 최대화·바로가기 제출·정상 종료

ish 본체 변경과 llm/hub 전체 회귀는 수행하지 않았다. 기존 보고서는 해당 시점의 결과로 보존한다.

## 현재 사용자 설정을 기본값으로 반영

WSL의 `~/ish/dok/config.json`을 설치된 dok Owner로 해석한 전체 설정과 새 `DockConfig()`를 비교했다. 사용자 요청으로 제외한 날씨 위치(`null`) 외의 모든 값이 일치한다. 원본 개인 설정 파일의 SHA-256도 변경 전후 일치한다.

기본값은 바로가기 9개와 구분선, 각 Nerd Font 아이콘·색·Ctrl+숫자 키, 38칸 3색 비주얼라이저, Nerd Font 음악 버튼, `future` 아트, 가로 간격 2칸이다. 읽은 파일의 날씨 뒤 여백 1줄을 유지했다. 위치는 기본값에서 제거하여 사용자가 명시하기 전에는 날씨를 요청하지 않는다. [기본 설정 안내](defaults.md)에 전체 구성을 정리했다.

설정 Owner의 선언을 바꿨으며 별도 기본 JSON 파일이나 기본값 재해석 계층을 추가하지 않았다. 기본 schema, `{}` 및 설정 파일 부재 시 로드, 명시적인 빈 바로가기 목록, 기본 위치 미설정 시 조회 방지를 검증했다. 빈 슬롯을 검증하는 렌더링 테스트는 `shortcuts=()`를 명시한다.

Linux Python 3.12.14의 Linux 파일시스템 스냅샷에서 dok 테스트 **104개 통과**(13.131초). [실행 로그](../../tests/dok/reports/dok-i75_qh23/dok.txt), [소스 해시](../../tests/dok/reports/dok-i75_qh23/snapshot.json). 실제 Linux PTY, 투명 Float 배치와 키 우선 처리 검증도 포함된다. ish 본체 변경이나 llm/hub 전체 회귀는 수행하지 않았다. WSL의 설치된 플러그인 코드는 이번 작업에서 교체하지 않았다.

## 모든 설정값과 Owner 주석을 포함하는 JSONC

처음 설치할 때 모든 기본값과 한글 설명이 담긴 `config.jsonc`를 생성한다. `setting`의 설명 선언을 schema와 `Record.documented_fields()`가 공유하며, 중첩 레코드·배열 항목에도 설명을 붙인다. 기본 독 편집 바로가기의 파일명도 JSONC로 변경했다. 기존 JSON 파일만 있으면 그대로 사용하며 두 파일이 있으면 JSONC가 우선한다. 기존 파일이나 주석을 시작·재로드 시 덮어쓰지 않는다. [사용법](configuration-file.md).

JSONC는 문자열 밖의 줄·블록 주석만 공백으로 대체한 뒤 표준 JSON 파서와 기존 Owner 검증에 전달한다. 원래 오류 위치, URL·셸 명령·이스케이프·한글·Nerd Font 문자를 보존한다. 잘못 닫힌 주석, 중복 키와 허용하지 않는 추가 문법은 거부한다. 최초 파일 생성은 기존 파일이 있을 때 실패하는 원자적 배치를 사용하고 임시 파일을 정리한다.

Linux Python 3.12.14의 Linux 파일시스템 스냅샷에서 dok 테스트 **111개 통과**(13.397초). 전체 생성값 왕복, 모든 필드 설명과 schema 일치, 파일 우선순위, 최초 설치와 사용자 주석 보존, 오류 시 기존 실행 설정 유지, 저장 실패와 기존 파일 덮어쓰기 방지, 실제 PTY를 검증했다. [실행 로그](../../tests/dok/reports/dok-7kjxsyno/dok.txt), [소스 해시](../../tests/dok/reports/dok-7kjxsyno/snapshot.json).

ish 본체와 WSL 개인 설정 파일은 변경하지 않았으며 llm/hub 전체 회귀는 수행하지 않았다. 배포된 플러그인 코드는 이번 작업에서 교체하지 않았다.

## 영어 기본 라벨과 설정 설명

기본 바로가기 이름을 `Find`, `File`, `Sys`, `Vim`, `Git`, `Web`, `Dock`, `Conf`, `Rld`로 변경했다. 시스템 명령은 `htop`이며 아이콘·색상·키·구분선은 유지한다. 설정 Owner의 설명 77개와 JSONC 파일 머리말을 영어로 바꿨다. schema와 생성 주석은 같은 설명 선언을 사용한다.

dok 테스트 **111개 통과**. [실행 로그](../../tests/dok/reports/dok-y7dxeqzx/dok.txt), [소스 해시](../../tests/dok/reports/dok-y7dxeqzx/snapshot.json), [생성 예시](../../tests/dok/reports/dok-y7dxeqzx/config.generated.jsonc). 생성된 주석 164줄이 모두 영어이고, JSONC 왕복 시 값이 보존됨을 확인했다. 라벨 변경 직후 스냅샷과 비교하여 주석 번역이 실제 설정값을 바꾸지 않았음도 확인했다. 타이틀 접두 문구·연결 오류 안내 등 화면에 표시되는 기본 문자열은 그대로다.

기존 사용자 설정과 역사적 생성 예시는 다시 쓰지 않았다. ish 본체 변경과 llm/hub 전체 회귀는 수행하지 않았다.

## 책임별 폴더 정리 (2026-11-03)

런타임을 `app`, `core`, `settings`, `dashboard`, `features`, `integrations`, `rendering`으로 재배치했다. 기능별 설정·화면·작업과 외부 연결을 분리하고, 기본 영역의 순서와 높이 계산은 같은 위젯 선언을 사용한다. 테스트도 책임별 하위 패키지로 이동했다. [전체 구조](architecture.md).

Linux Python 3.12.14의 Linux 파일시스템 스냅샷에서 dok 테스트 **111개 통과**(15.412초). 재배치된 패키지를 포함한 실제 ish 플러그인 로딩, 공개 설정 타입의 동일성, 실제 PTY, 투명 Float, 입력 제출·복원, 비동기 작업 취소·캐시와 설정 저장을 검증했다. [실행 로그](../../tests/dok/reports/dok-apg7zqpu/dok.txt), [소스 해시](../../tests/dok/reports/dok-apg7zqpu/snapshot.json).

작업 전 원본과 별도 프로세스에서 **90가지 화면 조건**을 비교해 기본/최대화 모드, 폭, 요소 비활성화, 여백, 긴 바로가기 및 폰트 변경 시 문자열·스타일·위치·폭·높이가 같음을 확인했다. 공개 루트 이름 32개와 기존 진입점 노출 이름을 유지하고, 기본값·schema·생성 JSONC가 작업 전 결과와 동일함도 확인했다. [화면 비교 결과](../../tests/dok/reports/reorganization-20261103/render-comparison.json), [API·설정 비교 결과](../../tests/dok/reports/reorganization-20261103/compatibility.json).

향후 기능용 빈 폴더는 추가하지 않았다. ish 본체, 배포된 플러그인과 개인 설정은 수정하지 않았으며 llm/hub 전체 회귀는 실행하지 않았다. 기존 역사적 검증 결과는 보존했다.

## 이전 설정 파일 호환 분기 제거 (2026-11-03)

출시 전에는 이전 설정 호환을 유지하지 않는다는 방침에 따라 `config.json` 자동 선택과 확장자별 JSON/JSONC 읽기·쓰기 분기를 제거했다. 기본 경로는 항상 `config.jsonc`이며 명시적으로 지정한 경로도 같은 JSONC 처리기를 사용한다. 이전 파일 자체를 삭제하거나 변환하지 않는다. 필드명 별칭이나 버전 변환 계층은 없으며 옵션 이름·중첩 구조 개편은 아직 적용하지 않았다.

Linux Python 3.12.14의 Linux 파일시스템 스냅샷에서 dok 테스트 **112개 통과**(20.415초). 이전 JSON 파일이 있어도 새 경로를 선택하고 기존 파일을 보존하는 동작, 확장자와 무관한 주석 포함 저장·재로드, 원자적 저장, 실제 호스트 로딩과 PTY를 검증했다. [실행 로그](../../tests/dok/reports/dok-pign1f08/dok.txt), [소스 해시](../../tests/dok/reports/dok-pign1f08/snapshot.json). ish 본체와 개인 설정은 수정하지 않았고 llm/hub 전체 회귀는 실행하지 않았다.

## 옵션 구조 통일 (2026-11-03)

루트를 `dock`, `audio`, `clock`, `widgets`로 나누고 화면 옵션을 `enabled`, `spacing`, `style`, `texts`로 묶었다. 날짜·시간 위젯과 공통 시간대를 분리하고 바로가기 항목과 줄 설정을 `widgets.shortcuts`에 합쳤다. 단위가 있는 옵션은 `width_columns`, `after_rows`, `item_gap_columns`, `icon_offset_columns`처럼 명확하게 표시한다. 바로가기·구분선은 `type`으로 구분하며 `icon`으로 이모지와 Nerd Font를 받는다.

이전 설정 필드·생성자 별칭·버전 코덱·런타임 변환 계층은 추가하지 않았다. Owner의 실제 필드 구조를 변경하여 검증·schema·직렬화·영어 JSONC 설명이 한 선언을 사용한다. 중첩 `style`·`spacing`에서 일부 값만 지정해도 각 기능의 나머지 기본값을 유지한다. [구조 설명](options.md), [Owner에서 생성한 전체 JSONC](config.example.jsonc).

Linux Python 3.12.14의 Linux 파일시스템 스냅샷에서 dok 테스트 **116개 통과**(15.223초). 기존 렌더링·명령 제출·키 복원·오디오·날씨 수명주기·실제 ish 로더·PTY 검증을 새 설정으로 유지했고, 구형 경로 거부, 중첩 기본값, 독립 날짜·시간 스타일, 생성 순서 검증을 추가했다. 중복 키와 잘못된 너비 저장 파일도 새 경로로 검사한다. [실행 로그](../../tests/dok/reports/dok-i6kuqxhs/dok.txt), [소스 해시](../../tests/dok/reports/dok-i6kuqxhs/snapshot.json).

작업 전 원본을 별도 프로세스에서 실행하여 기본값을 대조하고 **90가지 화면 조건**의 문자열·색상·좌표·폭·높이가 동일함을 확인했다. 비교를 위한 일회성 구조 대응은 보고서 폴더에만 있으며 배포 코드에 포함되지 않는다. 현재 문서의 설정 예시 8개도 실제 Owner와 schema로 검증했다. [비교 결과](../../tests/dok/reports/options-restructure/comparison.json).

ish 본체, 배포된 플러그인과 개인 설정은 수정하지 않았고 llm/hub 전체 회귀는 실행하지 않았다. 기존 설정 파일은 자동으로 변환되지 않으며 새 코드에는 현재 형식의 설정이 필요하다.

## 위치 미설정 날씨와 자동 크기 토글 (2026-11-04)

위치가 없으면 설정 시간대의 오늘부터 요일과 Unknown 아이콘, `--° --°`를 표시한다. `WeatherSnapshot.unknown()`이 날짜별 빈 예보를 만들고 기존 온도·아이콘 렌더링을 공유한다. `widgets.weather.icons`에서 모든 아이콘과 `unknown`을 수정할 수 있으며 최저 온도 색과 폭 제한을 유지한다. 다른 시계 위젯을 모두 꺼도 날씨 요일의 자정 갱신을 위해 시계 작업이 활성화된다. 위치 미설정 시 네트워크 조회는 하지 않는다.

토글 기본값은 Ctrl+W이며 첫 펼침은 최대화를 요청한다. 공간에 따라 기본 크기로 줄어들고 공간이 복원되면 최대화로 돌아온다. 크기 전환용 키 바인딩과 `dock.keys.maximize`, 사용하지 않는 `widgets.weather.texts.location_missing`은 제거했다. 기존 공개 Python 모드 제어 API는 유지한다. 개인 설정은 자동 변환하거나 덮어쓰지 않았다.

Linux Python 3.12.14의 Linux 파일시스템 스냅샷에서 dok 테스트 **119개 통과**(17.064초). 날짜 경계, 기본·사용자 Unknown 아이콘, 기존 예보 제거, 최저 온도 색, 좁은 화면, 아이콘 설정 저장·schema·렌더링, Ctrl+W 입력 보존, 자동 크기 복원, F9 호스트 바인딩 복원과 실제 Linux PTY를 검증했다. [실행 로그](../../tests/dok/reports/dok-i4qca5bg/dok.txt), [소스 해시](../../tests/dok/reports/dok-i4qca5bg/snapshot.json). 현재 문서의 설정 예시 9개를 Owner와 schema로 검증하고 전체 기본 JSONC를 Owner에서 재생성했다. ish 본체는 수정하지 않았고 llm/hub 전체 회귀는 실행하지 않았다.

## 시스템 시간대와 영어 타이틀 기본값 (2026-11-04)

`ClockConfig.timezone` 기본값을 `null`로 변경했다. `Nullable(TimezoneRule())` 선언을 검증·schema·JSONC 저장이 공유하며 `null`이면 시스템 현지 시간대, IANA 이름이면 해당 시간대를 사용한다. 이전 빈 문자열 표기는 허용하지 않는다. WSL에서는 Linux 프로세스의 시스템 시간대를 따른다. `TitleTexts`의 기본 접두 문구는 `Now playing: `, 오디오 연결 실패 문구는 `Audio connection failed`다. 기존 사용자 설정은 덮어쓰지 않는다.

Linux Python 3.12.14 스냅샷에서 dok 테스트 **120개 통과**(22.229초). UTC·Asia/Seoul 시스템 환경의 시간대 반영, 명시 시간대 우선 적용, null 저장·로드와 schema, 영어 타이틀 렌더링을 검증했다. [실행 로그](../../tests/dok/reports/dok-nj136b25/dok.txt), [소스 해시](../../tests/dok/reports/dok-nj136b25/snapshot.json). 설정 예시 9개를 검증하고 전체 기본 JSONC를 Owner에서 재생성했다. ish 본체는 수정하지 않았고 llm/hub 전체 회귀는 실행하지 않았다.

## 기존 기능의 실행 설정과 작업 수명 분리 (2026-11-04)

신규 기능 없이 기존 날씨·시계·오디오의 실행 기반을 정리했다. `WeatherConfig.polling_key`와 `ClockConfig.schedule()`이 실제 실행에 필요한 설정만 제공한다. 날씨의 지역 표시명·아이콘·색상·여백, 같은 갱신 주기의 날짜 형식, 다른 도크 설정을 바꿔도 실행 중인 수집을 유지한다. 시간대·초/분 갱신 변경은 시계만 교체하고, 위치·단위·예보 일수·갱신 주기 변경은 날씨 작업만 교체한다. 진행 중인 날씨 조회의 완료 시각에는 최신 시계 설정을 사용한다.

`TaskSlot`은 오디오·날씨·시계에 실제로 공유되는 작업 중복 방지, 한 번의 취소, 비동기 정리 완료 후 교체, 최종 종료만 소유한다. 완료 콜백에서 자동 재시작하지 않고 다음 호스트 동기화에서 최신 활성화 조건을 적용한다. 오디오는 호스트 앱 객체 대신 작업 생성 함수를 받으며 오디오 실패 후 재연결 정책과 날씨 캐시 정책은 각 기능에 유지했다. 화면·스냅샷 조합, 기존 활성화 조건, 설정 형식과 공개 루트/컨트롤러 API는 유지했다. 팝업·캘린더·모니터링·스케줄러·Hub 연동이나 미래 기능용 빈 패키지는 추가하지 않았다.

Linux Python 3.12.14의 Linux 파일시스템 스냅샷에서 dok 테스트 **129개 통과**(16.717초). 기존 120개에 작업 생성 거부 정리, 호스트 취소 후 명시적 재시작, 최종 종료, 느린 정리 중 빠른 설정 변경·토글, 조회 중 표시 설정 변경, 시계·날씨 독립 재시작, 오디오 캡처 정리 검증을 추가했다. [실행 로그](../../tests/dok/reports/dok-asnbzpba/dok.txt), [소스 해시](../../tests/dok/reports/dok-asnbzpba/snapshot.json).

리팩터링 전 커밋 `1805d525979589f0793539c07fc2d8b8f99a3113`의 dok를 별도 디렉터리와 프로세스에서 실행하여 **60가지 화면 조건**의 문자열·스타일·좌표·폭·공간별 표시 모드가 동일함을 확인했다. 공개 루트 내보내기·생성자 및 컨트롤러 메서드 시그니처, 전체 기본값·schema·생성 JSONC도 동일하다. [비교 결과](../../tests/dok/reports/foundation-20261104/comparison.json). ish 본체와 hub/llm은 수정하지 않았고 llm/hub 전체 회귀는 실행하지 않았다. 개인 설정을 변경하거나 배포·커밋하지 않았다.

## 누락 설정 생성과 초기 예시 분리 (2026-11-04)

런타임의 생략된 설정은 빈 숫자 바로가기 슬롯과 청록 단색 비주얼라이저다. `ShortcutsConfig.initial()`과 `VisualizerStyle.initial()`이 새 파일에 넣는 예시를 소유하고 `DockConfig.initial()`이 조합한다. 초기 JSONC에는 요청된 9개 명령과 구분선, 청록·보라·분홍 그라데이션을 명시한다. schema의 누락 필드 기본값에는 초기 예시를 넣지 않는다.

설치·재설치·재로드는 `ConfigStore.load_or_create()`를 공유하여 `config.jsonc`가 없으면 원자적으로 생성하고 실제 파일을 읽어 적용한다. 동시에 다른 파일이 생성되면 그 내용을 사용하며, 기존 빈 설정·부분 설정·잘못된 파일을 덮어쓰지 않는다. 생성 실패 시 실행 중 설정을 보존한다. 읽기 전용 `load()`의 기존 계약은 유지한다.

Linux Python 3.12.14의 Linux 파일시스템 스냅샷에서 dok 테스트 **135개 통과**(16.574초). 파일 삭제 후 재설치·재로드, 동시 생성, 쓰기 실패와 재시도, 빈 슬롯 렌더링, 기본 단색과 초기 그라데이션의 schema·저장·로드를 검증했다. [실행 로그](../../tests/dok/reports/dok-95gkkp5a/dok.txt), [소스 해시](../../tests/dok/reports/dok-95gkkp5a/snapshot.json). 초기 JSONC와 현재 문서 예시 9개도 Owner 및 schema로 검증했다. ish 본체와 개인 설정은 수정하지 않았고 llm/hub 전체 회귀는 실행하지 않았다.

## MPRIS 연결과 독 마우스 (2026-11-05)

Linux 세션 D-Bus의 MPRIS 플레이어 검색·상태 조회·기능별 제어를 추가했다. `MediaConfig`, `PlayerState`, `MediaRuntime`, `MprisBackend`, `MediaInput`이 설정·상태 해석·작업 전이·외부 프로토콜·드래그를 각각 소유한다. `dock.mouse.enabled`로 독 마우스를 끌 수 있고, `media.enabled`는 연결만 별도로 제어한다. 날씨 아이콘 동작과 GUI 프롬프트 편집은 추가하지 않았다. 이전 공개 API와 설정 생성자의 위치 인자 순서를 유지한다.

Linux Python 3.12.14의 Linux 파일시스템 스냅샷에서 dok 테스트 **153개 통과**(19.884초). 실제 임시 D-Bus 서버와 dbus-fast 5.2.0을 사용해 검색·속성 변환·재생 명령·SetPosition·연결 종료·요청 제한 시간을 검증했다. 지원 기능 변경·플레이어 소유자 변경·곡 변경 시 제어 거부, 일시정지 후 대상 유지, 명시 대상 부재, 실패 후 재활성화, 숨김 직후 렌더 전 명령 취소를 검사했다. 마우스 테스트는 실제 셀 위치에서 드래그 미리보기·단일 전송·바깥 취소·비활성 버튼·설정 끄기·호스트 필터 복원·프롬프트 보존·날씨 무동작을 확인한다. 리소스 누수 경고와 건너뛴 테스트는 없었다.

[실행 로그](../../tests/dok/reports/dok-ol3ey4bz/dok.txt), [소스 해시](../../tests/dok/reports/dok-ol3ey4bz/snapshot.json). 초기 JSONC와 문서 예시 10개도 Owner 및 schema로 검증했다. 테스트 실행기는 사용자 세션 버스를 상속하지 않으며 MPRIS 통합 테스트는 전용 임시 버스를 사용한다. 실제 사용자 플레이어의 동작이나 Windows 플레이어 연결은 검증 범위에 포함하지 않는다. ish 본체, llm, hub, 개인 설정은 수정하지 않았고 llm/hub 전체 회귀도 실행하지 않았다.

## 마우스 기본 비활성화와 생성 포맷 (2026-11-05)

`MouseConfig.enabled` 기본값을 `false`로 변경하고 초기 JSONC를 Owner에서 재생성했다. 설정을 생략하거나 새 파일을 생성하면 독 마우스는 꺼져 있으며, 기존 파일의 명시적인 `true`는 유지한다. 최신 옵션·기본값·사용 안내도 같은 값으로 갱신했다.

Linux Python 3.12.14 스냅샷에서 dok 테스트 **153개 통과**(20.252초). 기본 상태로 펼쳐도 마우스가 꺼져 있고 명시적으로 켜기·끄기·숨김 복원이 동작함을 확인했다. 문서 예시 10개와 생성 JSONC를 검증했다. [실행 로그](../../tests/dok/reports/dok-fg0c4d2s/dok.txt), [소스 해시](../../tests/dok/reports/dok-fg0c4d2s/snapshot.json). ish 본체·llm·hub는 수정하지 않았고 llm/hub 전체 회귀는 실행하지 않았다.

## 시스템 정보와 하단 toolbar (2026-11-06)

왼쪽 날짜·입력 상태·선택 숫자의 합계(평균), 오른쪽 CPU·메모리·네트워크 표시를 추가했다. 시스템 측정은 psutil 7.2.2를 사용하며 toolbar가 있는 프롬프트에서만 한 작업으로 실행한다. `SystemConfig`·측정 모델·`ToolbarConfig`·`NumberCalculator`·ish 어댑터가 각 설정, 의미 해석, 계산, 호스트 등록·복원을 소유한다. 기존 생성자 위치 인자는 유지하고 루트 설정 끝에 `system`, `toolbar`를 추가했다. 새 기본 JSONC는 영어 주석을 포함해 Owner에서 생성했다.

Linux Python 3.12.14의 해시 검증된 Linux 스냅샷에서 dok 테스트 **175개 통과**(32.526초). [실행 로그](../../tests/dok/reports/dok-3qri7rxl/dok.txt), [소스 해시](../../tests/dok/reports/dok-3qri7rxl/snapshot.json). 명령은 `/home/user/.cache/ish-scope-deps-69rd8deo/.venv-linux312/bin/python /mnt/d/WorkSpace/ish/tests/dok/run_linux.py`다. 경고와 건너뛴 테스트는 없었다. 초기 JSONC와 문서 설정 예시 11개도 Owner·schema로 검증했다.

검증 범위는 실제 Linux 측정, CPU guest/iowait 해석, available 기반 메모리, monotonic 네트워크 속도, 초기 샘플·카운터 초기화·인터페이스 교체, 부분 오류 복구, 제한 길이 기록, 반복 취소 시 읽기 중첩 방지다. 실제 ish 레이아웃에서 기본/최대화/공간 부족에 따른 날짜 숨김·복원, 프롬프트 종료·재진입, 입력·선택 보존, 블록 선택의 합계·평균, 원래 toolbar·컨트롤·Filter 복원, 후속 플러그인 변경 보존, 1–240칸 화면과 오른쪽 정렬을 확인했다. 40칸 toolbar를 연속 갱신해 짧은 행 문자열이 prompt-toolkit 폭 캐시에 누적되지 않는 것도 검사했다. 기존 PTY·Float·MPRIS·마우스 검증도 통과했다.

수정 범위는 `dok/`, `docs/dok`, `tests/dok`다. ish 본체·llm·hub와 개인 설정·배포 경로는 수정하지 않았고 llm/hub 전체 회귀, 커밋·업로드는 수행하지 않았다. WSL 측정은 Linux에서 보이는 자원 기준이며 Windows 전체 수집은 포함하지 않는다.

## 커밋 전 계약 검토 (2026-11-06)

[계약 검토](contract-review-20261106.md)에서 날짜 해석·갱신 규칙의 중복과 빈 합계 문구의 좁은 화면 처리를 수정했다. 기존 공개 내보내기 39개·컨트롤러 시그니처·설정 생성자 인자 순서를 확인했고, 기본 JSONC와 문서 예시 11개도 Owner 선언에 부합했다.

최종 Linux Python 3.12.14 스냅샷에서 dok 테스트 **177개 통과**(38.774초). 경고·건너뛴 테스트는 없었다. [실행 로그](../../tests/dok/reports/dok-3xuiyxqb/dok.txt), [소스 해시](../../tests/dok/reports/dok-3xuiyxqb/snapshot.json), [API·설정 검사 결과](../../tests/dok/reports/review-20261106/audit.json). ish 본체·llm·hub는 수정하지 않았고 llm/hub 전체 회귀는 실행하지 않았다.

## 일반 팝업·알림과 캘린더·일별 날씨 (2026-11-07)

일반 팝업의 중앙 배치·포커스 복원과 우측 하단 알림의 FIFO·가시 시간 타이머를 추가했다. 월간 캘린더와 기존 캐시를 공유하는 일별 날씨 상세를 연결했다. 크기·여백·스타일·키는 Owner 선언에서 검증·schema·영어 JSONC 주석을 생성한다. 하나의 기존 Float에 레이어를 조합하며 호스트 소스를 변경하지 않는다.

Linux Python 3.12.14의 해시 검증 스냅샷에서 dok 테스트 **214개 통과**(unittest 69.202초). [실행 로그](../../tests/dok/reports/dok-_3y55s8_/dok.txt), [소스 해시](../../tests/dok/reports/dok-_3y55s8_/snapshot.json). 문서 설정 예시 13개를 Owner와 JSON schema로 검증했다. 실제 PTY에서 팝업 열기·닫기 후 기존 바로가기 제출, 문서·선택·검색 포커스 보존, 키 충돌 차단, 알림 중 타이핑·프롬프트 종료·대기·만료, 마우스 상세 버튼·색상·우측 하단 위치, 작은 화면 스크롤과 날씨 조회 공유를 확인했다.

화면 검토용 실제 prompt-toolkit 셀 렌더링 PNG는 `tests/dok/reports/popup-preview-2026-11-07/`에 저장했다. 예보가 있는 캡처는 합성 샘플 데이터다. UI 확정은 사용자 검토를 기다린다. ish 본체·llm·hub 코드와 개인 WSL 설치·설정은 수정하지 않았으며 llm/hub 전체 회귀, 커밋·업로드는 수행하지 않았다.

## 공통 테마와 확정된 하단 바 UI (2026-11-07)

사용자가 합계 라벨을 `Total`로 바꾸는 조건으로 UI를 확정하고 테스트를 요청했다. `NumbersConfig`의 기본 선언을 변경하고 기본 JSONC·현재 문서·테스트에 반영했다. 네트워크 기본 라벨은 Nerd Font 아이콘 `󰛳`, 왼쪽 세 숫자 그룹 사이는 4칸, 그룹 내부 간격은 2칸이다. 숫자 표시칸의 정렬용 여백은 별도로 유지한다. 새 Theme 필드는 기존 `DockConfig` 위치 인자를 바꾸지 않도록 끝에 추가했다.

Linux Python 3.12.14의 해시 검증된 Linux 스냅샷에서 **dok 테스트 218개 통과**(unittest 63.467초), 건너뛴 테스트는 없다. [최종 실행 로그](../../tests/dok/reports/dok-4_58cjkb/dok.txt), [소스 해시](../../tests/dok/reports/dok-4_58cjkb/snapshot.json). 선택적 dok→Hub 등록 테스트에는 Hub·llm 소스의 읽기 전용 복사본을 의존성으로 제공했다. [의존성 해시](../../tests/dok/reports/dok-optional-9lnmns4t/dependencies.json). `tests/dok/manual/theme_validation.py`가 복사를 준비하고 `tests/dok/run_linux.py`가 **tests/dok만** 발견·실행한다. 사용자 Hub 작업공간이나 설치된 플러그인은 사용하지 않는다.

공통 색상 선언·참조 검증·schema·설정 왕복, 터미널 기본색, 독 여백 배경, 바로가기/비주얼라이저의 독립 색상 보존, 팝업 테마 변경 시 캐시 갱신, 하단 바의 좁은 폭·고정 폭·우측 정렬, 실제 PTY 입력·팝업 닫기·바로가기 제출을 검증했다. 초기 실행의 실패 1건은 이전 NET 문자 라벨에 맞춘 테스트 기대값이었다. 아이콘으로 짧아진 라벨 뒤에는 원래 너비 적응 규칙에 따라 `↓1 MiB/s ↑1 KiB/s`가 들어가므로 기대값을 갱신했다. 초기 로그도 `dok-m8deqe32/`에 보존한다. asyncio 디버그의 일부 느린 콜백 진단은 있었으나 최종 실패·오류는 없다.

초기 JSONC가 `DockConfig.initial()`의 생성물과 정확히 일치하며, 현재 문서의 설정 예시 **14개**가 Owner와 JSON schema 검증을 통과했다. [설정 검사 결과](../../tests/dok/reports/theme-preview-2026-11-07-final/docs-validation.json), [최종 하단 바 렌더링](../../tests/dok/reports/theme-preview-2026-11-07-final/toolbar.png). 캘린더 편집·일정 저장/예약 알림·시간별 날씨의 개편 화면은 아직 시안이며, 이번 결과는 그 미연결 기능의 동작 검증을 포함하지 않는다.

이번 수정은 `dok/`, `docs/dok/`, `tests/dok/`에 한정했다. ish 본체·llm·hub 수정, llm/hub 전체 회귀, 개인 WSL 배포·설정 변경, 커밋·업로드는 수행하지 않았다.

## 캘린더 편집·예약 알림·현재/시간별 날씨 연결 (2026-11-07)

확정된 시안을 실행되는 팝업에 연결했다. 날짜 선택·일정 선택·입력·저장·삭제·휴일·반복·알림 시각을 캘린더 Owner API와 연결하고, 원자적 저장·revision 충돌·프로세스 간 잠금·알림 처리 기록을 추가했다. 알림 작업은 프롬프트 사이에도 살아 있는 이벤트 루프에서 실행하고 기존 가시 시간 대기열을 사용한다. 현재·일별·시간별 날씨는 한 Open-Meteo 요청과 기존 캐시를 공유한다. 일반 팝업 API와 하나의 Float·포커스 복원 계약은 유지한다.

Linux Python 3.12.14의 해시 검증된 Linux 스냅샷에서 **dok 테스트 237개 전부 통과**(unittest 68.552초), 건너뛴 테스트는 없다. [최종 로그](../../tests/dok/reports/dok-tacroc19/dok.txt), [소스 해시](../../tests/dok/reports/dok-tacroc19/snapshot.json), [선택적 Hub/llm 의존성 복사 해시](../../tests/dok/reports/dok-optional-7fw952xu/dependencies.json). 명령은 `python tests/dok/manual/theme_validation.py`이며 실제 탐색 대상은 `tests/dok`뿐이다. llm/hub 테스트 모음은 실행하지 않았다.

추가 검증은 월말·윤년·일광절약 시간, 날짜 범위·다일 일정, 저장 재열기·수정 충돌·저장 실패 시 원본 유지, 손상 파일 보존, 반복 알림의 최신 회차 계산·재시작/시계 역행 중복 방지·비활성화·종료, 실제 키 입력/한글/F2 저장·마우스 저장·미저장 확인·작은 창, 명령 실행 중 알림 대기와 복귀 후 타이핑 보존이다. 날씨는 현재/시간별 배열·누락값·범위·시간 순서·DST 중복 시각·강수확률 색·단절 그래프·좁은 화면의 FIGlet 폴백을 검사했다. 기존 dok PTY·MPRIS·오디오·toolbar·팝업·Hub 등록 검증도 통과했다.

초기 검증에서 패널 하위 타입 기본값의 저장 왕복, 미설정 날씨 문구 색, 너무 좁은 FIGlet 폭을 수정했다. 최종 생성 JSONC는 Owner와 완전히 일치하며 문서 설정 예시 **14개**도 검증했다. [설정 검사](../../tests/dok/reports/connected-popups-2026-11-07/docs-validation.json). 자동 테스트 외에 Open-Meteo에 동탄 좌표를 한 번 요청해 현재 날씨·2일/48시간 예보·Asia/Seoul 시간대·강수확률의 실제 수신을 확인했다. [실제 조회 결과](../../tests/dok/reports/connected-popups-2026-11-07/live-weather.json).

연결된 실제 렌더러의 [캘린더](../../tests/dok/reports/connected-popups-2026-11-07/calendar-editor.png), [드롭다운](../../tests/dok/reports/connected-popups-2026-11-07/calendar-dropdown.png), [날씨](../../tests/dok/reports/connected-popups-2026-11-07/weather-hourly.png)는 합성 데이터로 생성했다. 캘린더 캡처는 임시 CalendarStore에 저장한 일정을 읽는다. 개인 일정·설정 파일이나 WSL 설치 플러그인에는 쓰지 않았다.

알림은 OS 데몬이 아니며 ish가 꺼져 있으면 발생하지 않는다. 재시작 시 지나간 알림은 이벤트별 최신 회차로 모으고, 처리 기록 저장 후 화면에 표시하기 전에 강제 종료되면 그 회차가 표시되지 않을 수 있다. 자세한 계약은 [캘린더](calendar.md)를 따른다. 수정은 `dok/`, `docs/dok/`, `tests/dok/`로 제한했으며 ish 본체·llm·hub 변경, 개인 WSL 배포, 커밋·업로드는 수행하지 않았다.

## 확정된 팝업 UI·ESC 입력 지연 검증 (2026-11-07)

사용자가 `popup-layout-2026-11-07-v3/` UI를 확정한 뒤 검증했다. 캘린더의 제목·하단 바 여백, 전체 일정 배경, 날짜/시각 분리, 하루 종일의 포함 종료일 편집, 한 행 반복 설정, 확장 메모와 고정 버튼을 검사한다. 날씨는 상단·강수확률 아래 여백, 선택 시간의 체감온도·바람·습도·강우량·자외선 지수, 영역 전환에 따른 하단 키 안내를 검사한다. 버튼 선택은 배경을 유지하고 title 전경색으로 표시한다. 하단 툴바의 기본 라벨은 `Total:`다.

Linux Python 3.12.14의 해시 검증된 Linux 파일시스템 스냅샷에서 **dok 테스트 246개 전부 통과**(unittest 59.125초), 건너뛴 테스트는 없다. [최종 로그](../../tests/dok/reports/dok-vd4iuo5j/dok.txt), [소스 해시](../../tests/dok/reports/dok-vd4iuo5j/snapshot.json), [선택적 의존성 복사 해시](../../tests/dok/reports/dok-optional-zy1e9krv/dependencies.json). 실행 명령은 Linux Python으로 `tests/dok/manual/theme_validation.py`를 호출하며 실제 테스트 탐색은 `tests/dok`에 한정한다.

ESC는 `popups.input.escape_seconds` 기본 0.05초를 팝업 포커스 중 각 파서·키 조합 대기에 적용한다. 고정 sleep으로 동작을 추정하지 않고 실제 입력부터 팝업이 닫힌 렌더 결과까지 별도 관측 파이프로 측정했다. 실제 Linux PTY에서 캘린더 **110.5ms**, 날씨 **109.7ms**, 입력 파이프에서 **104.6ms**였다. 자동 검증의 허용 기준은 500ms이며 이전 약 1.5초 기본 대기와 구분한다. Windows Terminal의 화면 표시 지연 자체를 측정한 값은 아니다. 분할 전달된 CSI 방향키·Alt 조합, 프롬프트 보존, 포커스 이탈·재설정·해제 시 원래 시간값 복원과 다른 플러그인의 후속 변경 보존도 통과했다.

초기 실행 245개 중 하루 종일 종료일을 Python 최대 날짜로 지정한 경계 테스트가 실패했다. 포함 종료일을 저장용 미포함 시각으로 변환할 때의 OverflowError를 EventDraft가 검증 오류로 돌려주도록 수정했다. 초기 로그는 `dok-eu5in95t/`에 보존하며, 수정 후 최종 실행은 모두 통과했다. [기본 JSONC와 현재 문서 예시 14개의 Owner/schema 검증](../../tests/dok/reports/dok-vd4iuo5j/docs-validation.json)도 통과했다.

확정된 화면 형태는 유지했다. ish 본체·llm·hub를 수정하지 않았으며 llm/hub 전체 회귀, 개인 WSL 설치본·설정 변경, 커밋·업로드는 수행하지 않았다.

## 날씨 실패 후 dock 재열기 재시도 (2026-11-07)

명시적인 숨김→열림 전환을 WeatherRuntime.begin_activation()에 전달한다. 날씨 Owner가 실패 여부·진행 중인 요청·재시도 의도·갱신 기한을 판단하며, 대기 중인 작업을 깨워 한 번 재시도한다. 성공한 캐시의 갱신 주기는 그대로다. 기본 크기로 다시 열어도 재시도 한 번을 완료하고, 재렌더링·크기 변경·이미 열린 dock의 show() 호출은 실패 재시도를 유발하지 않는다. 빠른 재열기는 합쳐지고 이전 요청의 취소 정리가 끝나기 전에 새로운 요청을 시작하지 않는다.

Linux Python 3.12.14의 해시 검증된 스냅샷에서 **dok 테스트 251개 전부 통과**(unittest 58.810초), 건너뛴 테스트는 없다. [실행 로그](../../tests/dok/reports/dok-tx2mqzsr/dok.txt), [소스 해시](../../tests/dok/reports/dok-tx2mqzsr/snapshot.json), [선택적 의존성 복사 해시](../../tests/dok/reports/dok-optional-r479bssq/dependencies.json). 새 검증은 같은 렌더 사이클의 빠른 재열기, 실패 후 기본 크기 재시도, 반복 실패 시 대기, 진행 중 요청 중복 방지, 취소 정리 직렬화, 실제 Ctrl+W 재열기와 성공 캐시·프롬프트 보존이다. 테스트의 날씨 조회는 모의 backend이며 개인 위치를 전송하지 않는다.

WSL 설치본의 관련 dok 파일 4개가 수정 전 소스와 일치함을 확인한 뒤 테스트한 파일만 반영했다. 원본 백업은 `/home/user/.cache/dok-weather-retry-20261107-v5fiiqx8/`이며 개인 `~/ish/dok/config.jsonc`의 바이트 일치를 확인했다. [배포 파일 해시](../../tests/dok/reports/dok-tx2mqzsr/deployment.json), [실제 ish Python의 설치본 확인](../../tests/dok/reports/dok-tx2mqzsr/installed-smoke.json). 실행 중인 ish는 재시작해야 새 코드가 적용된다. ish 본체·llm·hub 수정, llm/hub 전체 회귀, 커밋·업로드는 수행하지 않았다.

## UI 언어·선택 시간대 날짜·터미널 마우스 활성화 (2026-11-07)

Linux Python 3.12.14의 해시 검증된 Linux 파일시스템 스냅샷에서 **dok 테스트 266개 전부 통과**(unittest 67.792초). [실행 로그](../../tests/dok/reports/dok-h1prtx_q/dok.txt), [소스 해시](../../tests/dok/reports/dok-h1prtx_q/snapshot.json), [선택적 의존성 복사 해시](../../tests/dok/reports/dok-optional-383m25ak/dependencies.json). 영어·한국어 팩의 메시지/서식 변수 일치, 언어/schema/설정 왕복, 사용자 문구 보존, 초안·확인 상태를 유지하는 언어 전환, 선택 시간대의 자정 날짜 경계를 검증했다. 생성 JSONC도 현재 Owner 출력과 일치했다. 전체 실행 이후 이미 검증한 Language·Locale을 루트 `__all__`에 추가한 변경은 실제 설치 Python의 공개 내보내기 확인으로 보완했다.

실제 ish 프로세스에서 클릭 신호를 직접 넣으면 처리되지만 터미널에 SGR 활성화 신호가 나오지 않는 현상을 재현했다. Application의 필터만 교체하고 Renderer의 생성 시 필터는 그대로였던 것이 원인이다. DockMouse가 두 공개 필터를 각각 보존·연결·복원하도록 수정했다. PTY 자동 테스트는 활성/해제 제어 신호까지 확인하며, 서로 다른 기존 필터와 다른 플러그인의 후속 변경도 보존한다. 실제 PTY ESC 응답은 캘린더 113.4ms, 날씨 112.2ms, 입력 파이프는 109.8ms였다.

WSL의 실제 ish 1.2.1 실행기와 격리된 임시 홈으로 배포 전후를 검사했다. 수정 후 캘린더·날씨 각각 활성화/해제 신호와 SGR 클릭 처리를 확인했다. [설치본 PTY 결과](../../tests/dok/reports/mouse-renderer-fix/installed-after-deploy.json). Windows Terminal 창 자체에서 사람의 물리 클릭을 관측한 검증은 아니다. 실제 ish Python으로 EN/KO 팝업의 24가지 화면 크기와 공개 내보내기도 확인했다. [설치본 언어/폭 검사](../../tests/dok/reports/mouse-renderer-fix/locales-installed.json)

WSL dok 코드 31개 파일을 반영하고 98개 Python 파일의 해시를 검증했다. 개인 설정과 일정 데이터는 바이트 단위로 보존했다. 백업은 `/home/user/.cache/dok-locales-mouse-jixwcsdd/`이며 [배포 기록](../../tests/dok/reports/mouse-renderer-fix/deployment.json)에 해시를 남겼다. ish를 완전히 다시 시작해야 새 코드가 적용된다. ish 본체·llm·hub 수정, llm/hub 전체 회귀, 실제 날씨 조회, 커밋·업로드는 수행하지 않았다.

## 상태 문구 언어팩 전용화·CPU 초기 기준선 (2026-11-07)

TitleTexts·WeatherTexts와 대응 설정/schema 필드를 제거하고 재생 접두 문구·오디오/날씨 연결 실패 안내를 Locale API로만 읽는다. 기본 JSONC와 현재 문서 예시에서 해당 옵션을 제거했다. CPU 그래프의 첫 유효 측정 전 구간은 MetricFormat이 점 기준선으로 그리며 실제 측정값·수집 이력에는 값을 보충하지 않는다. 측정 후 누락 구간과 네트워크 그래프의 빈 구간은 유지한다.

Linux Python 3.12.14의 해시 검증된 스냅샷에서 **dok 테스트 268개 통과**(74.318초). [실행 로그](../../tests/dok/reports/dok-y5w5_0bq/dok.txt), [소스 해시](../../tests/dok/reports/dok-y5w5_0bq/snapshot.json). 상태 설정 제거·schema 거부·기본 JSONC, 두 언어의 독/날씨 상세 안내, 진단 우선순위, 시작/부분 이력/누락값/크기별 그래프를 확인했다. ish 본체·llm·hub 수정과 llm/hub 전체 회귀는 수행하지 않았다.

WSL 코드 6개 파일을 반영하고 개인 설정에서는 `widgets.title.texts`와 `widgets.weather.texts` 객체만 제거했다. 나머지 JSON 값·외부 주석·일정 데이터를 보존하고 원본을 `/home/user/.cache/dok-status-language-c4m2g9ur/`에 백업했다. [배포 기록](../../tests/dok/reports/dok-status-language-c4m2g9ur/deployment.json). 기존 파일의 일회성 편집이며 런타임 호환/이전 설정 변환은 추가하지 않았다. 실행 중 ish는 다시 시작해야 새 코드가 적용된다.

간헐적인 `[[[[[` 입력은 실제 장애 순간의 원인이 아직 확정되지 않았다. 정상 갱신/마우스 입력과 포커스 제어 신호 합성 입력을 비교했고, 일반 텍스트를 저장하지 않는 임시 진단 실행기를 마련했다. [조사 결과와 기록 방법](input-diagnostics.md).

## 설정 이름 간소화·하단 바 바깥 여백·CPU 8단계 표시 (2026-11-07)

설정 Owner의 필드명을 `width`, `value_width`, `pad_left`, `pad_right`, `gap`, `align`, `digits` 등으로 줄이고 소비 코드·schema·JSONC·현재 문서를 함께 정리했다. 시간 단위는 `_s`·`_min`으로 명시한다. 구 이름 별칭·런타임 자동 변환은 추가하지 않았다. 변경 전 runtime/initial 값과 새 이름으로 대응한 결과가 일치하는지 확인했고, Open-Meteo의 외부 요청 필드명은 유지했다. [이름 변경표](options.md#간소화한-설정-이름).

하단 바 `pad_left`·`pad_right`의 런타임 기본값은 각각 0, 생성 파일 값은 각각 1이다. 배경 런타임 기본값은 공통 테마를 유지하고 생성 파일은 하단 바 전용 `#1D2035`를 사용한다. ThemeBackground가 RGB·이름 해석을 소유하며 팔레트·공통 역할 기본값은 바꾸지 않았다. 여백은 무제한 비음수 정수로 받되 실제 할당은 화면 너비 안으로 제한한다. 오른쪽 시스템 영역은 오른쪽 여백 직전까지 정렬한다.

CPU는 기존 Braille 높이 4단계 대신 `▁▂▃▄▅▆▇█`의 8단계(12.5% 간격)를 MetricFormat에서 그린다. 한 셀에 한 측정값을 표시하고 0–100% 축을 유지한다. 시작 전 구간은 최저 블록으로 채우되 실제 이력은 변조하지 않으며 측정 중 누락은 빈칸이다. 메모리·네트워크 그래프 방식은 유지한다.

Linux Python 3.12.14의 해시 검증된 Linux 파일시스템 스냅샷에서 **dok 테스트 275개 전부 통과**(108.443초). [실행 로그](../../tests/dok/reports/dok-wss3c6re/dok.txt), [소스 해시](../../tests/dok/reports/dok-wss3c6re/snapshot.json), [선택적 의존성 복사 해시](../../tests/dok/reports/dok-optional-7t7f7_1y/dependencies.json). 구 설정 거부·Owner/schema 일치·생성 폼 일치, 매우 큰 여백·0칸 화면·배경 상속·재로드·프롬프트 보존, 8단계 경계·최근 이력·결측, 숫자 축약·부호·평균·말줄임을 검증했다. [현재 문서 설정 예제 16개](../../tests/dok/reports/dok-wss3c6re/docs-validation.json)도 Owner/schema를 통과했다. 기존 ESC 입력 파이프 검사는 117.5ms로 통과했다.

간소화 후 첫 실행에서 자릿수 제한 인자와 기존 로컬 문자열/카운터 이름의 충돌이 발견되어 로컬 변수명을 분리했다. 초기 실패 기록은 `dok-_u3cs7bh/`에 보존했고 위 최종 전체 실행에서 재검증했다. 이전 바깥 여백 작업의 중간 실패 기록 `dok-ll1kbrr4/`도 보존한다. 새 toolbar 테스트의 오래된 Window 참조 비교는 재로드 후 공개 callback 소유권 검사로 고쳤고, 동일 실행에서 실패한 기존 ESC 분할 입력 검사는 단독 실행과 최종 전체 실행에서 통과했다. ESC 런타임 동작은 이번 작업에서 변경하지 않았다.

ish 본체·llm·hub 코드는 수정하지 않았고 llm/hub 전체 회귀 테스트는 실행하지 않았다. 이번 변경은 작업 폴더에만 적용했으며 WSL 설치본·개인 설정·일정 데이터, 커밋·업로드는 변경하지 않았다.

## 간소화 설정의 WSL 적용 (2026-11-07)

사용자 요청으로 WSL 개인 설정을 새 이름으로 바꿨다. 사전 확인 당시 설치된 dok Python 파일 98개는 이미 `dok-wss3c6re` 검증 소스와 모두 일치했지만 개인 JSONC에는 이전 필드명이 남아 있었다. 코드 복사 없이 98개 파일 해시를 확인하고 `~/ish/dok/config.jsonc`의 키 41개만 바꿨다. 테마 참조 값 변경은 필요하지 않았다.

변경 전 설정의 의미는 해시가 일치하는 이전 검증 스냅샷 `dok-y5w5_0bq`의 Owner로 읽고, 새 Owner로 읽은 결과와 모든 유효 설정 값이 같은지 확인했다. JSONC의 키 토큰만 치환하여 기존 주석·서식·개인 값은 보존했다. 생성 폼의 새 여백 1칸·배경 `#1D2035`를 기존 파일에 추가하지 않았다. 나머지 개인 파일·일정 데이터의 해시도 동일했다.

원본 개인 폴더와 설치 플러그인을 `/home/user/.cache/dok-compact-settings-jpn5_wtu/`에 백업했다. 실제 ish 1.2.1의 Python으로 준비 파일과 적용 파일을 각각 읽고 CPU 8단계 표시, 좁은/넓은 4가지 toolbar 너비, 음악 버튼·팝업 설정 접근을 확인했다. [배포·설정 보존 기록](../../tests/dok/reports/dok-compact-settings-jpn5_wtu/deployment.json). 실행 중인 사용자 ish는 종료하지 않았으며 재시작하면 적용된다. ish 본체·llm·hub는 수정하지 않았고 전체 회귀 재실행이나 커밋·업로드는 하지 않았다.

## 커밋 전 계약 검토 (2026-11-07)

[검토 기록](contract-review-20261107.md)에 Owner·호스트·저장·작업 수명 경계를 정리했다. 연속 취소 중 캘린더 파일 작업의 잠금이 일찍 풀리는 문제를 재현한 뒤 `run_blocking()`으로 수정하고 시스템 수집도 같은 규칙을 사용하게 했다. 팝업 테마 역할은 공개 Enum을 참조하도록 정리했다.

커밋된 hub/llm 의존성과 Linux Python 3.12.14를 사용해 **dok 276개 테스트 전부 통과**(87.607초). [로그](../../tests/dok/reports/dok-qknemr6e/dok.txt), [소스 해시](../../tests/dok/reports/dok-qknemr6e/snapshot.json), [의존성 해시](../../tests/dok/reports/dok-committed-deps-6sfnvcq9/dependencies.json). 실제 Hub 등록·사용자 설정 보존도 통과했으며 llm/hub 전체 회귀 테스트는 수행하지 않았다. ESC 입력 파이프 검사는 108.7ms였다.

## 캘린더 제목·월 이동 버튼 UI 확정 (2026-11-07)

사용자가 [캘린더 미리보기](../../tests/dok/reports/calendar-header-preview-20261107/calendar.png)를 확정한 뒤 검증했다. 제목은 월 이동과 무관하게 `Calendar`로 유지하고 `<`·`>` 버튼은 `ThemeColor.WHITE`를 사용한다. 월·년도는 달력 영역의 탐색 행에 계속 표시한다.

첫 실행에서는 기존 계약 테스트가 이전 제목 `February 2024`를 기대해 1개 실패했다. [초기 로그](../../tests/dok/reports/dok-jbs97vdw/dok.txt)를 보존하고, 해당 검사를 고정 제목과 본문의 월·년도 표시로 갱신했다. 윤년 날짜·오늘 이동·연도 경계 이동 검사는 유지했다. UI 구현은 확정 이후 추가 변경하지 않았다.

Linux Python 3.12.14의 해시 검증된 Linux 파일시스템 스냅샷에서 **dok 테스트 276개 모두 통과**(80.726초). [최종 로그](../../tests/dok/reports/dok-mor9io8w/dok.txt), [소스 해시](../../tests/dok/reports/dok-mor9io8w/snapshot.json), [실행 결과](../../tests/dok/reports/dok-mor9io8w/results.json). 선택적 Hub/llm 의존성은 앞선 커밋 소스 복사본의 해시를 재확인한 뒤 사용했다. ESC 입력 파이프 검사는 114.4ms였다. ish 본체·llm·hub 수정, llm/hub 전체 회귀 테스트, WSL 설치본 배포, 커밋·업로드는 하지 않았다.
