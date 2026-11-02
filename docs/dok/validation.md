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
