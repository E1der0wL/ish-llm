# dok 폴더 구조와 책임

기존 기능을 책임별 패키지로 나눈 구조다. 팝업과 월간 캘린더를 포함하며 아직 구현하지 않은 외부 캘린더 연동·메일·스케줄러의 빈 폴더는 만들지 않는다. 아래 트리에서는 하위 패키지의 `__init__.py`를 생략했다.

```text
dok/
├── __init__.py                 # 공개 API
├── dok.py                      # ish 메타데이터, 설치·해제 진입점
├── app/
│   ├── controller.py           # 상태, 키, Float, 기능 작업 조율
│   └── lifecycle.py            # 날씨·시계 작업을 독립적으로 호스트에 연결
├── core/
│   ├── records.py              # Owner 필드 선언, 검증, schema, 직렬화
│   ├── colors.py               # 공통 색상과 RGB 규칙
│   ├── theme.py                # 공통 팔레트·역할·색상 참조 해석
│   ├── styles.py               # 전경색과 굵기 Owner
│   ├── elements.py             # 활성화와 모드별 여백 규칙
│   ├── state.py                # 표시·제출 상태 전이
│   └── tasks.py                # 작업 중복 방지, 스레드 작업 취소 완료 대기, 최종 종료
├── settings/
│   ├── config.py               # 루트 설정 조합, 모드 키와 너비
│   ├── popups.py               # 일반·알림·기능별 팝업 설정 조합
│   ├── files.py                # 파일명과 기본 경로
│   ├── store.py                # 조회, 원자적 저장, 누락 파일 생성
│   └── jsonc.py                # JSONC 해석과 Owner 설명 주석 생성
├── dashboard/
│   ├── config.py               # 전체 위젯 설정 조합
│   ├── widgets.py              # 기본 영역 위젯 계약과 배치 순서
│   ├── layout.py               # 행 수, 높이, 공간에 따른 모드 선택
│   └── view.py                 # 기능별 화면 조합과 가운데 정렬
├── features/
│   ├── calendar/
│   │   ├── config.py           # 월간 캘린더 설정
│   │   ├── models.py           # 일정 검증·반복·알림 시각 해석
│   │   ├── store.py            # 원자적 저장·수정 충돌·알림 처리 기록
│   │   ├── runtime.py          # 직렬 파일 작업·예약 알림 수명
│   │   ├── editor.py           # 팝업 내부 입력 버퍼·일정 초안
│   │   └── view.py             # 월간 달력·편집 폼·로컬 입력
│   ├── system/
│   │   ├── config.py           # 수집 주기·기록 한도·인터페이스 선택
│   │   ├── models.py           # 불변 측정값·구간 사용률과 속도 계산
│   │   └── runtime.py          # 단일 수집 작업·제한 길이 기록
│   ├── toolbar/
│   │   ├── config.py           # 상태 표시줄의 필드·기본값·표시 정책
│   │   ├── editor.py           # 입력 스냅샷·숫자 해석과 합계·평균
│   │   └── view.py             # 좌우 정렬·좁은 화면·그래프 표현
│   ├── audio/
│   │   ├── config.py           # 캡처·비주얼라이저 설정
│   │   ├── models.py           # 연결 상태와 측정 데이터
│   │   ├── runtime.py          # 캡처 작업 수명과 재시도 규칙
│   │   ├── spectrum.py         # PCM 분석과 주파수 대역
│   │   └── view.py             # 비주얼라이저 화면
│   ├── weather/
│   │   ├── config.py           # 위치·갱신·표시 설정
│   │   ├── models.py           # 예보 데이터와 날씨 종류
│   │   ├── popup.py            # 일별 상세 팝업 설정과 내용
│   │   ├── runtime.py          # 예보 갱신과 캐시
│   │   └── view.py             # 요일·아이콘·온도 표시
│   ├── clock/
│   │   ├── config.py           # 날짜·시간·요일 아트 설정과 해석
│   │   ├── runtime.py          # 필요한 시각에만 화면 갱신
│   │   └── view.py             # 시계 행과 FIGlet 아트 캐시
│   ├── media/
│   │   ├── config.py           # 제목·재생바·버튼 설정
│   │   ├── connection.py       # MPRIS 설정, typed player와 backend 계약
│   │   ├── runtime.py          # 플레이어 선택·조회·명령 작업 수명
│   │   ├── input.py            # 버튼과 재생바 드래그 상태
│   │   ├── models.py           # 재생 상태와 액션 계약
│   │   └── view.py             # 제목·재생바·버튼 화면
│   └── shortcuts/
│       ├── config.py           # 기본 항목, 슬롯·명령·구분선·간격 규칙
│       └── view.py             # 바로가기 표시와 클릭 연결
├── integrations/
│   ├── hub.py                  # 같은 프롬프트의 live Hub 감지와 dok Agent 템플릿 등록
│   ├── ish/
│   │   ├── language.py         # 호스트 선택 언어 조회
│   │   ├── toolbar.py          # toolbar 등록·원본 복원·날짜 시계 연결
│   │   ├── status.py           # 프롬프트의 공개 Document·선택·모드 관측
│   │   ├── host.py             # 호스트 크기와 UI 접근
│   │   ├── keys.py             # 키 해석과 등록·해제
│   │   ├── mouse.py            # 독 마우스 설정·범위·호스트 복원
│   │   ├── popups.py           # 팝업 포커스·입력 수명·알림 타이머 연결
│   │   └── submission.py       # 프롬프트 버퍼 제출과 복원
│   ├── audio/
│   │   └── pulse.py            # PulseAudio 조회·캡처 프로세스
│   ├── media/
│   │   └── mpris.py            # MPRIS D-Bus 연결·외부 필드 해석
│   ├── system/
│   │   └── psutil.py           # Linux 외부 카운터 의미·필드 해석
│   └── weather/
│       └── open_meteo.py       # HTTP 요청과 외부 응답 해석
├── popups/
│   ├── config.py               # 크기·여백·색상·맞춤 규칙
│   └── models.py               # 내용 계약·알림 대기열·가시 시간 전이
├── locales/
│   ├── __init__.py             # 언어 선택·영어 fallback·서식 해석
│   ├── messages.py             # 기본 영어 메시지와 식별자
│   └── ko.py                   # 한국어 언어팩
└── rendering/
    ├── toolbar.py             # 이미 너비에 맞춘 단일 행의 bounded 렌더링
    ├── overlay.py             # Float 배치와 투명 셀 렌더링
    ├── popups.py              # 팝업 프레임·스크롤·로컬 입력
    ├── interactive.py         # 팝업 입력 계약·셀 단위 조합 도구
    ├── presentation.py        # 행·부분 문자열·스타일 전달 계약
    └── text.py                # 터미널 표시 폭과 문자 경계 보존

docs/dok/                      # 설치·설정·설계·검증 문서
tests/dok/
├── core/                      # 공통 요소 규칙
├── settings/                  # 설정·JSONC·저장
├── features/                  # 오디오·표시 설정
├── dashboard/                 # 정보 영역·시계·날씨 작업
├── rendering/                 # Float와 화면 배치
├── integration/               # 호스트·수명주기·배포 로더·실제 PTY
├── support.py                 # 테스트용 호스트
├── terminal_fixture.py        # 실제 PTY 검증용 애플리케이션
├── run_linux.py               # dok 전용 Linux 스냅샷 검증
├── manual/                    # 개인 수동 스크립트, 배포·탐색 제외
└── reports/                   # 검증 기록, 배포·탐색 제외
```

## 공개 API와 호환성

설치·해제와 컨트롤러 실행 API는 유지한다. 설정 API는 `DockConfig(dock, audio, clock, widgets, media, system, toolbar, popups, theme, language)` 구조를 제공한다. `DockSettings`, `DockKeys`, `WidgetsConfig`, `ClockConfig`, `DateConfig`, `TimeConfig`, `ShortcutsConfig`, `MediaConfig`, `MouseConfig`, `SystemConfig`, `ToolbarConfig`, `PopupSettings`, `Theme`가 각 설정을 소유한다. 새 필드는 끝에 추가하여 기존 위치 인자 호출도 유지한다. 이전 설정 생성자·필드명·파일 구조의 별칭이나 변환 계층은 두지 않는다. 개인 설정 파일을 자동으로 다시 쓰지 않는다.

내부 구현은 위 경로에서 가져온다. 예를 들어 `DockOverlay`는 `dok.rendering.overlay`, `AudioRuntime`은 `dok.features.audio.runtime`에 있다. 이전의 평면 내부 모듈을 다시 만드는 호환용 파일은 두지 않는다. 외부 호출자는 패키지 루트의 공개 API를 사용한다.

배포에는 하위 패키지를 포함한 `dok/` 전체가 필요하다. 문서·테스트는 포함하지 않는다. 개인 설정의 이름을 바꿔야 하는 배포는 원본을 백업하고 명시적인 일회성 편집으로 처리한다. 런타임에 구 설정 변환 계층을 두지 않는다.

## 책임과 의존성

`dok.py` → `app` → 기능 작업·화면 조합·호스트 연동 순서로 실행을 연결한다. `dashboard`는 기능별 화면을 조합하고 `rendering`은 터미널 셀과 Float를 처리한다. 외부 서버 필드와 프로세스 명령은 해당 `integrations` 구현에서 해석한다.

`HubAgents`가 dok의 Hub 네임스페이스, Agent ID·역할 템플릿과 등록 상태를 소유한다. 활성 프롬프트에서 Hub 설치가 감지되면 host 이벤트 루프에서 공유 클라이언트의 `register()`를 호출한다. 이 작업은 각 입력 줄보다 오래 살아야 하므로 Enter로 프롬프트가 끝나도 계속되고, dok 해제 또는 Hub 설치 교체 시 취소된다. Project 생성·재사용, Agent 검증·저장과 사용자 설정 보존은 Hub의 등록 계약에 위임한다.

기능별 `config.py`의 Owner가 기본값·한도·검증·표시 해석을 제공한다. 루트 `DockConfig`와 `WidgetsConfig`는 Owner를 조합한다. `Record`가 그 선언에서 schema와 직렬화를 만들고, `Jsonc`가 같은 설명을 주석으로 출력한다. 저장 계층과 화면은 내부 딕셔너리를 다시 해석하지 않는다.

`body_widgets()`는 기본 영역의 순서와 기능별 View 연결을 한 번 선언한다. 높이 계산과 실제 렌더링은 모두 이 선언과 `ElementConfig.arrange()`를 사용한다. 정보 영역의 여러 행은 `TextRow`·`TextSpan`으로 전달해 정렬과 스타일 처리를 공유한다.

`AudioRuntime`은 캡처 상태와 실패 후 재시도 정책을 소유한다. `WeatherRuntime`은 예보·갱신 시점·캐시를 소유하고, `ClockRuntime`은 `ClockConfig.schedule()`이 제공하는 `ClockSchedule`로 다음 갱신 시각을 계산한다. `DashboardRuntime`은 날씨와 시계를 독립된 작업으로 조율한다. 오디오는 앱 객체 대신 주입된 작업 생성 함수를 사용하며, 컨트롤러와 `DashboardRuntime`이 호스트의 작업 생성·화면 갱신 콜백을 연결한다.

`core/tasks.py`의 `TaskSlot`은 작업 수명만 관리한다. 같은 실행 조건에서는 작업을 유지하고, 조건이 달라지면 한 번 취소한 뒤 정리가 끝나기 전까지 다음 작업을 시작하지 않는다. 완료 콜백은 재평가를 요청할 뿐 직접 작업을 재시작하지 않는다. 다음 호스트 렌더링에서 최신 활성화 상태와 설정으로 판단한다. `close()` 이후에는 다시 시작하지 않는다. 기능별 캐시·실패·재시도 정책은 이 공통 코드에 넣지 않는다.

같은 모듈의 `run_blocking()`은 취소로 중단할 수 없는 스레드 작업의 완료 대기를 소유한다. 취소가 반복돼도 실제 작업이 끝난 뒤에 호출자의 잠금·TaskSlot을 해제하며, 취소 도중 발생한 작업 오류가 취소 상태를 덮어쓰지 않는다. 시스템 수집과 캘린더 파일 작업이 이 API를 공유한다.

실행 조건도 해당 기능 Owner가 선언한다. 날씨의 `request_key`는 좌표·단위·예보 일수, `polling_key`는 여기에 갱신 주기를 포함한다. 지역의 참고용 `label`, 아이콘·색상·여백 변경은 조회를 재시작하지 않는다. 갱신 주기 변경은 날씨 작업만 교체하고 같은 조회 조건의 캐시는 유지한다. `ClockSchedule`은 시간대와 초/분 갱신 여부만 포함하므로 같은 주기의 날짜 형식이나 색상 변경은 시계를 재시작하지 않는다. 시간대 변경 중인 날씨 요청은 중단하지 않고 완료 시 최신 시계로 갱신 시각을 기록한다.

오디오는 dock 표시 중, 날씨·시계는 최대화 중이라는 활성화 조건을 유지한다. 팝업의 날씨와 날짜 갱신도 같은 날씨·시계 작업에 수요를 추가한다. 모든 표시 수요가 사라지거나 프롬프트 종료·플러그인 해제 시 표시용 작업을 정리한다. 시스템 측정과 toolbar 시계는 dock과 독립적으로 프롬프트의 toolbar 표시 조건을 따른다. 캘린더 예약 알림은 별도의 CalendarRuntime이 소유하며 프롬프트 사이에도 이벤트 루프가 유지되는 동안 실행한다. 기존 불변 스냅샷·조회 API와 화면 조합을 사용하며 범용 데이터 버스나 미래 기능용 등록 계층은 추가하지 않았다.

## 기능을 확장할 때

시스템 측정과 toolbar는 [상세 계약](toolbar.md)을 따른다. 루트 설정에는 기존 위치 인자를 유지하며 `system`, `toolbar`를 끝에 추가했다. `SystemConfig`는 수집 정책, `SystemSnapshot.between()`은 시간차·카운터 초기화·메모리 해석을 소유한다. 외부 psutil 필드는 어댑터만 읽는다. `NumberCalculator`가 숫자 문법·계산 한도·반올림을 소유하고 표시 계층은 결과만 사용한다. 날짜는 기존 `ClockConfig`를 공유한다. 설정 저장·schema·영어 주석은 같은 Owner 선언을 참조한다.

날짜 형식은 `ClockFormat.render()`가 해석하고 toolbar의 요일 문구도 이 API에 전달한다. `ClockSchedule.for_formats()`가 초/분 갱신 주기를 해석하며 기존 시계와 toolbar가 함께 사용한다. toolbar에서 날짜 토큰이나 `%S` 규칙을 따로 재구현하지 않는다.

`DockToolbar`는 공개 `bottom_toolbar` 등록 및 원본 복원을 소유한다. 짧은 애니메이션 문자열을 prompt-toolkit 전역 폭 캐시에 계속 쌓지 않도록, 공개 레이아웃 순회에서 ish의 `class:bottom-toolbar` Window를 찾아 내용 컨트롤을 한 줄 전용 어댑터로 감싼다. 소유 중에는 그 창의 `wrap_lines` Filter를 켜서 `UIContent`의 높이 API를 사용하며, 이미 너비에 맞춘 행의 높이는 1이다. 호스트 소스·스타일·배치와 전역 캐시는 수정하지 않는다. 레이아웃 재구성 시 다시 연결하고 해제 시 자신이 설치한 컨트롤과 Filter만 원복한다. 나중에 등록된 toolbar나 컨트롤은 덮어쓰지 않는다.

기능을 실제로 구현하는 시점에 `features/<name>/`을 추가하고 필요한 설정·데이터·화면·작업만 둔다. 외부 서비스가 필요하면 그 응답 해석과 연결 코드를 `integrations/<service>/`에 둔다. 루트 설정에는 기능 Owner를 연결하고, 화면 조합에는 해당 View를 등록한다.

팝업은 [팝업 계약](popups.md)을 따른다. `PopupPanel`이 배치와 스타일, `NotificationQueue`가 순서와 가시 시간 상태 전이, `PopupHost`가 포커스 복원과 호스트 수명주기를 소유한다. `OverlayLayers`가 기존 Float 하나에서 dock 뒤에 팝업을 그린다. 캘린더와 날씨 View는 팝업 내용과 기능별 이동만 제공한다. 날씨 상세와 dock은 기존 WeatherRuntime 하나를 공유하며 팝업이 열려 있는 것도 조회 활성 조건에 포함한다. 일반 팝업은 호스트 프롬프트 종료 시 닫고 알림은 입력 화면이 다시 활성화될 때까지 대기한다.

`Reminder`·`ReminderUnit`은 알림 단위·숫자·중복 기준과 표시 문구를 선언한다. `CalendarEvent`·`Recurrence`는 저장 필드·검증·반복 날짜·알림 시각을 제공한다. CalendarStore는 파일 잠금·원자적 저장·revision 충돌과 알림 claim·다음 시각 계획을 소유하고, CalendarRuntime은 I/O 직렬화·취소·기한 대기를 소유한다. EventDraft와 CalendarPage는 공개 API로만 저장·조회하며 저장 딕셔너리를 해석하지 않는다. 세부 시간·재시작·중복 방지 계약은 [캘린더](calendar.md)를 따른다.

CalendarSnapshot은 현재 월의 일별 조회 결과와 캐시 수명을 소유하며 CalendarEvent의 시간 해석 환경 선언을 사용한다. CalendarStore는 파일 내용 비교와 검증된 해석 결과 하나의 재사용을 소유한다. CalendarRuntime은 변경 없는 상태를 다시 게시하지 않는다. UI는 이 공개 조회를 사용하고 반복 해석·저장 검증·캐시 무효화 규칙을 재구현하지 않는다.

날짜·시각 선택은 calendar/selection.py의 DateSelection·TimePart·TimeSelection이 이동·범위·임시 값을 소유한다. picker_view.py는 해당 공개 API를 키·마우스와 공통 테마로 연결한다. CalendarPage는 기존 팝업 본문 안에 선택기를 배치하고 취소 시 초안을 유지하며, 적용할 때 EventDraft의 타입별 공개 API만 호출한다. 최종 시간대·시작/종료 관계 검증과 저장·알림 실행은 기존 CalendarEvent·Store·Runtime에 남는다.

calendar/recurrence.py의 Recurrence·RepeatEnd가 반복 종료 방식·총 횟수·종료날짜·끝난 시리즈의 마지막 회차 조회를 소유한다. 기존 models의 공개 Recurrence·RepeatUnit import는 유지한다. EventTiming은 새 일정 생성 시 한 번 적용할 시작 정각과 종료 간격을 선언한다. EventDraft의 이후 날짜·시각 선택은 해당 필드만 바꾸며 종료 자동 연동 상태를 두지 않는다. CalendarStore는 명령 회차의 원자적 선기록과 미래 시작 감시, CalendarRuntime은 기한 대기·제출 결과 안내를 담당한다. CommandOccurrence는 기존 회차 계산을 재사용하고 별도 반복 규칙을 만들지 않는다. ish 어댑터는 ShortcutSubmission의 동일한 검증·수락·문서 복원 경로로 예약 명령을 제출하며 빈 기본 프롬프트 이외에는 건너뛴다. 호스트 소스와 llm/hub 실행 계층은 바꾸지 않는다.

일반 팝업이 포커스를 가진 동안 controller는 dock의 audio·media 수집 요구를 끄고 필요한 팝업 시계·날씨 요구만 유지한다. 알림 실행기는 독립 수명으로 계속 실행한다. SystemConfig는 활성·유휴 측정 간격을 제공하며 SystemRuntime은 같은 작업과 카운터 기준값·이력을 유지한 채 간격만 바꾼다. 월 달력 행과 날씨 그래프는 해당 View가 마지막 렌더링 입력 한 건에 한정해 캐시한다. 저장·외부 조회·시간 해석은 각 기능 Owner의 공개 API를 사용한다.

InteractivePopup은 로컬 키 처리·닫기 확인·본문 렌더링·해제 계약이다. PopupHost는 기존 단일 modal 컨트롤의 포커스를 유지하며 PopupSurface가 키와 제한된 화면 크기를 위임한다. 편집 Buffer는 캘린더 초안만 소유한다. 일반 PopupContent API와 비대화형 알림 표시는 그대로 유지한다. 현재·일별·시간별 날씨는 OpenMeteo 어댑터가 한 응답을 WeatherSnapshot으로 해석하고 기존 WeatherRuntime이 같은 요청 키와 캐시·실패 전이를 사용한다.

`InteractivePopup.scroll_at()`은 본문 셀 좌표의 휠 이벤트를 선택적으로 처리한다. PopupSurface가 prompt-toolkit 문자 좌표를 터미널 셀로 바꾸고 테두리·제목·여백을 제외한 본문 좌표를 제공한다. CalendarPage는 그린 알림 목록의 사각형과 3행 스크롤 상태를 소유한다. 목록 밖이거나 API가 처리하지 않은 휠은 기존 페이지 스크롤로 전달한다.

외부 인증·계정 동기화처럼 현재 없는 책임은 해당 기능 구현 시 명시적으로 설계한다. 빈 패키지나 범용 서비스 프레임워크를 미리 추가하지 않는다.

## 검증

Linux Python 3.12.14에서 `python tests/dok/run_linux.py`로 dok 테스트만 실행한다. 러너는 하위 테스트 패키지를 재귀 탐색하고 Linux 파일시스템에 해시 검증된 스냅샷을 만든다. `manual/`, `reports/`, 캐시는 스냅샷과 자동 탐색에서 제외한다. 실제 실행 결과는 [검증 기록](validation.md)에 남긴다.

## 공통 Theme 소유권

ThemeColors/ThemeRoles가 RGB·역할·이름·기본값을 선언한다. ThemeReference와 Theme API가 검증·해석을 소유하며 schema와 JSONC는 같은 Record 선언을 사용한다. 독, 툴바, 팝업에는 불변 Theme를 명시적으로 전달하고 재로드 시 팝업 캐시도 교체한다. 호스트 스타일과 전역 활성 테마는 변경하지 않는다. 바로가기와 비주얼라이저의 직접 RGB는 각 기능 Owner에 유지한다. 자세한 설정은 [공통 테마](theme.md)를 따른다.

## UI 언어 소유권

Language와 Locale이 언어 선택·영어 폴백·사용자 문구 우선순위를 소유한다. Message의 영문 선언과 한국어 팩을 View에 명시적으로 주입하며, 호스트 선택은 읽기 전용 HostLanguage 어댑터로 얻는다. 기능별 필드·날씨 종류·반복 단위 Owner가 표시 이름을 제공한다. 설정의 language와 nullable 사용자 문구는 기존 Record 선언에서 검증·schema·JSONC를 함께 생성한다. 전역 언어 변경이나 저장 문자열 자동 변환은 하지 않는다. [UI 언어 계약](localization.md)
