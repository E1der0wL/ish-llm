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
