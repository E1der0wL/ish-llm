# dok 폴더 구조와 책임

기존 기능을 책임별 패키지로 나눈 구조다. 아직 구현하지 않은 캘린더 연동, 메일, 팝업 등의 폴더는 만들지 않는다. 아래 트리에서는 하위 패키지의 `__init__.py`를 생략했다.

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
│   ├── styles.py               # 전경색과 굵기 Owner
│   ├── elements.py             # 활성화와 모드별 여백 규칙
│   ├── state.py                # 표시·제출 상태 전이
│   └── tasks.py                # 작업 중복 방지, 취소 완료 대기, 최종 종료
├── settings/
│   ├── config.py               # 루트 설정 조합, 모드 키와 너비
│   ├── files.py                # 파일명과 기본 경로
│   ├── store.py                # 조회, 원자적 저장, 누락 파일 생성
│   └── jsonc.py                # JSONC 해석과 Owner 설명 주석 생성
├── dashboard/
│   ├── config.py               # 전체 위젯 설정 조합
│   ├── widgets.py              # 기본 영역 위젯 계약과 배치 순서
│   ├── layout.py               # 행 수, 높이, 공간에 따른 모드 선택
│   └── view.py                 # 기능별 화면 조합과 가운데 정렬
├── features/
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
│   ├── ish/
│   │   ├── toolbar.py          # toolbar 등록·원본 복원·날짜 시계 연결
│   │   ├── status.py           # 프롬프트의 공개 Document·선택·모드 관측
│   │   ├── host.py             # 호스트 크기와 UI 접근
│   │   ├── keys.py             # 키 해석과 등록·해제
│   │   ├── mouse.py            # 독 마우스 설정·범위·호스트 복원
│   │   └── submission.py       # 프롬프트 버퍼 제출과 복원
│   ├── audio/
│   │   └── pulse.py            # PulseAudio 조회·캡처 프로세스
│   ├── media/
│   │   └── mpris.py            # MPRIS D-Bus 연결·외부 필드 해석
│   ├── system/
│   │   └── psutil.py           # Linux 외부 카운터 의미·필드 해석
│   └── weather/
│       └── open_meteo.py       # HTTP 요청과 외부 응답 해석
└── rendering/
    ├── toolbar.py             # 이미 너비에 맞춘 단일 행의 bounded 렌더링
    ├── overlay.py             # Float 배치와 투명 셀 렌더링
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

설치·해제와 컨트롤러 실행 API는 유지한다. 설정 API는 `DockConfig(dock, audio, clock, widgets, media, system, toolbar)` 구조를 제공한다. `DockSettings`, `DockKeys`, `WidgetsConfig`, `ClockConfig`, `DateConfig`, `TimeConfig`, `ShortcutsConfig`, `MediaConfig`, `MouseConfig`, `SystemConfig`, `ToolbarConfig`가 각 설정을 소유한다. 새 필드는 끝에 추가하여 기존 위치 인자 호출도 유지한다. 이전 설정 생성자·필드명·파일 구조의 별칭이나 변환 계층은 두지 않는다. 개인 설정 파일을 자동으로 다시 쓰지 않는다.

내부 구현은 위 경로에서 가져온다. 예를 들어 `DockOverlay`는 `dok.rendering.overlay`, `AudioRuntime`은 `dok.features.audio.runtime`에 있다. 이전의 평면 내부 모듈을 다시 만드는 호환용 파일은 두지 않는다. 외부 호출자는 패키지 루트의 공개 API를 사용한다.

배포에는 하위 패키지를 포함한 `dok/` 전체가 필요하다. 문서·테스트는 포함하지 않는다. 이번 폴더 정리는 저장소에만 적용하며 배포된 플러그인이나 개인 설정을 교체하지 않는다.

## 책임과 의존성

`dok.py` → `app` → 기능 작업·화면 조합·호스트 연동 순서로 실행을 연결한다. `dashboard`는 기능별 화면을 조합하고 `rendering`은 터미널 셀과 Float를 처리한다. 외부 서버 필드와 프로세스 명령은 해당 `integrations` 구현에서 해석한다.

기능별 `config.py`의 Owner가 기본값·한도·검증·표시 해석을 제공한다. 루트 `DockConfig`와 `WidgetsConfig`는 Owner를 조합한다. `Record`가 그 선언에서 schema와 직렬화를 만들고, `Jsonc`가 같은 설명을 주석으로 출력한다. 저장 계층과 화면은 내부 딕셔너리를 다시 해석하지 않는다.

`body_widgets()`는 기본 영역의 순서와 기능별 View 연결을 한 번 선언한다. 높이 계산과 실제 렌더링은 모두 이 선언과 `ElementConfig.arrange()`를 사용한다. 정보 영역의 여러 행은 `TextRow`·`TextSpan`으로 전달해 정렬과 스타일 처리를 공유한다.

`AudioRuntime`은 캡처 상태와 실패 후 재시도 정책을 소유한다. `WeatherRuntime`은 예보·갱신 시점·캐시를 소유하고, `ClockRuntime`은 `ClockConfig.schedule()`이 제공하는 `ClockSchedule`로 다음 갱신 시각을 계산한다. `DashboardRuntime`은 날씨와 시계를 독립된 작업으로 조율한다. 오디오는 앱 객체 대신 주입된 작업 생성 함수를 사용하며, 컨트롤러와 `DashboardRuntime`이 호스트의 작업 생성·화면 갱신 콜백을 연결한다.

`core/tasks.py`의 `TaskSlot`은 작업 수명만 관리한다. 같은 실행 조건에서는 작업을 유지하고, 조건이 달라지면 한 번 취소한 뒤 정리가 끝나기 전까지 다음 작업을 시작하지 않는다. 완료 콜백은 재평가를 요청할 뿐 직접 작업을 재시작하지 않는다. 다음 호스트 렌더링에서 최신 활성화 상태와 설정으로 판단한다. `close()` 이후에는 다시 시작하지 않는다. 기능별 캐시·실패·재시도 정책은 이 공통 코드에 넣지 않는다.

실행 조건도 해당 기능 Owner가 선언한다. 날씨의 `request_key`는 좌표·단위·예보 일수, `polling_key`는 여기에 갱신 주기를 포함한다. 지역의 참고용 `label`, 아이콘·색상·여백 변경은 조회를 재시작하지 않는다. 갱신 주기 변경은 날씨 작업만 교체하고 같은 조회 조건의 캐시는 유지한다. `ClockSchedule`은 시간대와 초/분 갱신 여부만 포함하므로 같은 주기의 날짜 형식이나 색상 변경은 시계를 재시작하지 않는다. 시간대 변경 중인 날씨 요청은 중단하지 않고 완료 시 최신 시계로 갱신 시각을 기록한다.

표시 중인 오디오와 최대화 중인 날씨·시계라는 기존 활성화 조건은 유지한다. 해당 dock 기능은 숨김·공간 부족·프롬프트 종료·플러그인 해제 시 정리한다. 시스템 측정과 toolbar 시계는 dock과 독립적으로 프롬프트의 toolbar 표시 조건을 따른다. 명령 실행 중 상시 작업이나 예약 작업은 아직 없다. 기존 불변 스냅샷·조회 API와 화면 조합을 사용하며 범용 데이터 버스나 미래 기능용 등록 계층은 추가하지 않았다.

## 기능을 확장할 때

시스템 측정과 toolbar는 [상세 계약](toolbar.md)을 따른다. 루트 설정에는 기존 위치 인자를 유지하며 `system`, `toolbar`를 끝에 추가했다. `SystemConfig`는 수집 정책, `SystemSnapshot.between()`은 시간차·카운터 초기화·메모리 해석을 소유한다. 외부 psutil 필드는 어댑터만 읽는다. `NumberCalculator`가 숫자 문법·계산 한도·반올림을 소유하고 표시 계층은 결과만 사용한다. 날짜는 기존 `ClockConfig`를 공유한다. 설정 저장·schema·영어 주석은 같은 Owner 선언을 참조한다.

날짜 형식은 `ClockFormat.render()`가 해석하고 toolbar의 요일 문구도 이 API에 전달한다. `ClockSchedule.for_formats()`가 초/분 갱신 주기를 해석하며 기존 시계와 toolbar가 함께 사용한다. toolbar에서 날짜 토큰이나 `%S` 규칙을 따로 재구현하지 않는다.

`DockToolbar`는 공개 `bottom_toolbar` 등록 및 원본 복원을 소유한다. 짧은 애니메이션 문자열을 prompt-toolkit 전역 폭 캐시에 계속 쌓지 않도록, 공개 레이아웃 순회에서 ish의 `class:bottom-toolbar` Window를 찾아 내용 컨트롤을 한 줄 전용 어댑터로 감싼다. 소유 중에는 그 창의 `wrap_lines` Filter를 켜서 `UIContent`의 높이 API를 사용하며, 이미 너비에 맞춘 행의 높이는 1이다. 호스트 소스·스타일·배치와 전역 캐시는 수정하지 않는다. 레이아웃 재구성 시 다시 연결하고 해제 시 자신이 설치한 컨트롤과 Filter만 원복한다. 나중에 등록된 toolbar나 컨트롤은 덮어쓰지 않는다.

기능을 실제로 구현하는 시점에 `features/<name>/`을 추가하고 필요한 설정·데이터·화면·작업만 둔다. 외부 서비스가 필요하면 그 응답 해석과 연결 코드를 `integrations/<service>/`에 둔다. 루트 설정에는 기능 Owner를 연결하고, 화면 조합에는 해당 View를 등록한다.

인증·개인 데이터 저장·팝업 포커스처럼 현재 없는 책임은 해당 기능 구현 시 명시적으로 설계한다. 빈 패키지나 범용 서비스 프레임워크를 미리 추가하지 않는다.

## 검증

Linux Python 3.12.14에서 `python tests/dok/run_linux.py`로 dok 테스트만 실행한다. 러너는 하위 테스트 패키지를 재귀 탐색하고 Linux 파일시스템에 해시 검증된 스냅샷을 만든다. `manual/`, `reports/`, 캐시는 스냅샷과 자동 탐색에서 제외한다. 실제 실행 결과는 [검증 기록](validation.md)에 남긴다.
