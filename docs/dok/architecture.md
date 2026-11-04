# dok 폴더 구조와 책임

기존 기능을 책임별 패키지로 나눈 구조다. 아직 구현하지 않은 캘린더 연동, 시스템 모니터링, 메일, 팝업 등의 폴더는 만들지 않는다. 아래 트리에서는 하위 패키지의 `__init__.py`를 생략했다.

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
│   ├── store.py                # 조회, 원자적 저장, 최초 생성
│   └── jsonc.py                # JSONC 해석과 Owner 설명 주석 생성
├── dashboard/
│   ├── config.py               # 전체 위젯 설정 조합
│   ├── widgets.py              # 기본 영역 위젯 계약과 배치 순서
│   ├── layout.py               # 행 수, 높이, 공간에 따른 모드 선택
│   └── view.py                 # 기능별 화면 조합과 가운데 정렬
├── features/
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
│   │   ├── models.py           # 재생 상태와 액션 계약
│   │   └── view.py             # 제목·재생바·버튼 화면
│   └── shortcuts/
│       ├── config.py           # 기본 항목, 슬롯·명령·구분선·간격 규칙
│       └── view.py             # 바로가기 표시와 클릭 연결
├── integrations/
│   ├── ish/
│   │   ├── host.py             # 호스트 크기와 UI 접근
│   │   ├── keys.py             # 키 해석과 등록·해제
│   │   └── submission.py       # 프롬프트 버퍼 제출과 복원
│   ├── audio/
│   │   └── pulse.py            # PulseAudio 조회·캡처 프로세스
│   └── weather/
│       └── open_meteo.py       # HTTP 요청과 외부 응답 해석
└── rendering/
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

설치·해제와 컨트롤러 실행 API는 유지한다. 설정 API는 현재 `DockConfig(dock, audio, clock, widgets)` 구조만 제공한다. `DockSettings`, `DockKeys`, `WidgetsConfig`, `ClockConfig`, `DateConfig`, `TimeConfig`, `ShortcutsConfig`가 각 설정을 소유한다. 이전 설정 생성자·필드명·파일 구조의 별칭이나 변환 계층은 두지 않는다. 기본 표시값은 유지하며 개인 설정 파일을 자동으로 다시 쓰지 않는다.

내부 구현은 위 경로에서 가져온다. 예를 들어 `DockOverlay`는 `dok.rendering.overlay`, `AudioRuntime`은 `dok.features.audio.runtime`에 있다. 이전의 평면 내부 모듈을 다시 만드는 호환용 파일은 두지 않는다. 외부 호출자는 패키지 루트의 공개 API를 사용한다.

배포에는 하위 패키지를 포함한 `dok/` 전체가 필요하다. 문서·테스트는 포함하지 않는다. 이번 폴더 정리는 저장소에만 적용하며 배포된 플러그인이나 개인 설정을 교체하지 않는다.

## 책임과 의존성

`dok.py` → `app` → 기능 작업·화면 조합·호스트 연동 순서로 실행을 연결한다. `dashboard`는 기능별 화면을 조합하고 `rendering`은 터미널 셀과 Float를 처리한다. 외부 서버 필드와 프로세스 명령은 해당 `integrations` 구현에서 해석한다.

기능별 `config.py`의 Owner가 기본값·한도·검증·표시 해석을 제공한다. 루트 `DockConfig`와 `WidgetsConfig`는 Owner를 조합한다. `Record`가 그 선언에서 schema와 직렬화를 만들고, `Jsonc`가 같은 설명을 주석으로 출력한다. 저장 계층과 화면은 내부 딕셔너리를 다시 해석하지 않는다.

`body_widgets()`는 기본 영역의 순서와 기능별 View 연결을 한 번 선언한다. 높이 계산과 실제 렌더링은 모두 이 선언과 `ElementConfig.arrange()`를 사용한다. 정보 영역의 여러 행은 `TextRow`·`TextSpan`으로 전달해 정렬과 스타일 처리를 공유한다.

`AudioRuntime`은 캡처 상태와 실패 후 재시도 정책을 소유한다. `WeatherRuntime`은 예보·갱신 시점·캐시를 소유하고, `ClockRuntime`은 `ClockConfig.schedule()`이 제공하는 `ClockSchedule`로 다음 갱신 시각을 계산한다. `DashboardRuntime`은 날씨와 시계를 독립된 작업으로 조율한다. 오디오는 앱 객체 대신 주입된 작업 생성 함수를 사용하며, 컨트롤러와 `DashboardRuntime`이 호스트의 작업 생성·화면 갱신 콜백을 연결한다.

`core/tasks.py`의 `TaskSlot`은 작업 수명만 관리한다. 같은 실행 조건에서는 작업을 유지하고, 조건이 달라지면 한 번 취소한 뒤 정리가 끝나기 전까지 다음 작업을 시작하지 않는다. 완료 콜백은 재평가를 요청할 뿐 직접 작업을 재시작하지 않는다. 다음 호스트 렌더링에서 최신 활성화 상태와 설정으로 판단한다. `close()` 이후에는 다시 시작하지 않는다. 기능별 캐시·실패·재시도 정책은 이 공통 코드에 넣지 않는다.

실행 조건도 해당 기능 Owner가 선언한다. 날씨의 `request_key`는 좌표·단위·예보 일수, `polling_key`는 여기에 갱신 주기를 포함한다. 지역의 참고용 `label`, 아이콘·색상·여백 변경은 조회를 재시작하지 않는다. 갱신 주기 변경은 날씨 작업만 교체하고 같은 조회 조건의 캐시는 유지한다. `ClockSchedule`은 시간대와 초/분 갱신 여부만 포함하므로 같은 주기의 날짜 형식이나 색상 변경은 시계를 재시작하지 않는다. 시간대 변경 중인 날씨 요청은 중단하지 않고 완료 시 최신 시계로 갱신 시각을 기록한다.

표시 중인 오디오와 최대화 중인 날씨·시계라는 기존 활성화 조건은 유지한다. 숨김·공간 부족·프롬프트 종료·플러그인 해제 시 작업을 정리하며, 상시 실행이나 예약 작업은 아직 구현하지 않았다. 기존 불변 스냅샷·조회 API와 화면 조합을 그대로 사용하고 범용 데이터 버스나 미래 기능용 등록 계층은 추가하지 않았다.

## 기능을 확장할 때

기능을 실제로 구현하는 시점에 `features/<name>/`을 추가하고 필요한 설정·데이터·화면·작업만 둔다. 외부 서비스가 필요하면 그 응답 해석과 연결 코드를 `integrations/<service>/`에 둔다. 루트 설정에는 기능 Owner를 연결하고, 화면 조합에는 해당 View를 등록한다.

인증·개인 데이터 저장·팝업 포커스처럼 현재 없는 책임은 해당 기능 구현 시 명시적으로 설계한다. 이번 정리에 빈 패키지, 범용 서비스 프레임워크, 실제 플레이어 제어를 추가하지 않는다.

## 검증

Linux Python 3.12.14에서 `python tests/dok/run_linux.py`로 dok 테스트만 실행한다. 러너는 하위 테스트 패키지를 재귀 탐색하고 Linux 파일시스템에 해시 검증된 스냅샷을 만든다. `manual/`, `reports/`, 캐시는 스냅샷과 자동 탐색에서 제외한다. 실제 실행 결과는 [검증 기록](validation.md)에 남긴다.
