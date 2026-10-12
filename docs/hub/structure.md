# Hub 구조와 정리 내역

## 실행 코드와 예제의 경계

- `hub/`: 배포 플러그인. `examples/`, `tests/`에 의존하지 않습니다.
- `examples/hub/sample_data.py`: 미리보기용 대화 데이터.
- `examples/hub/preview.py`: 독립 실행 미리보기 및 실제 ish에 샘플을 주입하는 개발 도우미.
- `tests/hub/support.py`: PTK 화면 크기, 비동기 대기, 최소화 검사 도우미.
- `tests/hub/test_preview.py`: 샘플을 사용한 화면·키 입력 회귀 검사.
- `tests/hub/manual/`: 수동 렌더 캡처와 성능 측정. 자동 discovery·스냅샷·배포에서 제외합니다.
- `tests/hub/reports/`: 실행 결과와 과거 일회성 진단 자료. 배포하지 않습니다.

실제 `LiveHubView`는 `HubView`를 직접 상속합니다. 기존 `ui/mockup.py`와
`SampleSession`은 제거했고 공통 화면 데이터는 `model.SessionView`로 관리합니다.
`install(prompt)`는 실제 백엔드를 연결합니다. `install(prompt, preview=True)`는
백엔드 없는 빈 UI를 만듭니다. 샘플 대화를 보려면 저장소에서
`python -m examples.hub.preview`를 실행합니다.

## 공통화 책임

| 책임 | 공통 구현 | 사용처 |
| --- | --- | --- |
| 페이지 배치·좌측 패널 이동 | `ui/layout/page.py`의 `TwoPanelPage` | 대화·설정 |
| 버튼·입력·선택·스크롤 모양 | `widget/controls.py` | 설정·팝업·대화 |
| 좌측 목록·상단·하단 | `SidebarList`, `HeaderBar`, `ShortcutBar` | 대화·설정 |
| 설정 키·설명·값 편집 | `widget/settings.py`, `settings_viewport.py` | 설정 페이지 |
| 읽기 팝업 | `widget/reader.py`의 `ReadOnlyDialog` | 도움말·로그·컴포넌트 조회 |
| 팝업 입력 차단·단축키 수신 | `widget/dialog.py`의 `PopupInput` | 읽기 팝업·출력 확대 |
| 출력 확대·검색·원문 | `ExpandedOutputDialog` | `OutputInspector`, `TableInspector`가 상속 |
| 텍스트 가상 스크롤 | `TextViewport` | 읽기 팝업·`OutputViewport` 계열 |
| Hub JSON 저장 | `config/store.py`의 `WorkspaceJSONStore` | `PreferencesStore`, `ViewStateStore`가 상속 |

동일한 데이터 계약과 수명을 갖는 구현은 상속하고, 페이지 배치·키 수신처럼
여러 객체를 묶는 책임은 조합합니다. 서로 다른 화면 제어기를 하나의 큰 기반
클래스로 합치지 않습니다. 설정 검증은 각 Store에 남기고 임시 파일 작성,
flush/fsync, 원자적 교체, 실패 시 임시 파일 정리만 공통 Store가 담당합니다.

## 로그 저장과 조회

로그는 **한 디렉터리에 모두 저장되는 구조가 아닙니다.** `llm`이 소유 도메인과
호출 범위별로 저장합니다. Hub는 별도 로그 복사본을 만들지 않습니다.

| 데이터 | 경로 (`workspace` 기준) | 소유자·조회 방식 |
| --- | --- | --- |
| 프로젝트 실행 활동 | `projects/<project-id>/logs/activity.jsonl` | llm 소유, Hub는 `project.aactivity()` 사용 |
| 서비스 수명 로그 | Project·Session·Run·Step 각각의 `logs/service.log` | llm의 도메인별 경로 |
| Run 프로바이더 진단 | `projects/logs/providers-*.log` | llm RunManager의 로깅 범위 |
| 직접 프로바이더 호출·초기화 진단 | `logs/providers-*.log` | llm workspace 로깅 범위 |
| Hub 전역 설정 | `.hub/preferences.json` | `PreferencesStore` (로그 아님) |
| 즉시 저장하는 패널 너비 | `.hub/sidebar-width.json` | `PreferencesStore` (다른 초안과 독립) |
| Hub 읽기 위치·마지막 선택 | `.hub/view-state.json` | `ViewStateStore` (로그 아님) |

일반 설정의 `workspace_root`를 변경하면 다음 Hub 시작부터 프로젝트·세션·Run·로그,
읽기 위치 및 테스트 작업 공간은 선택한 데이터 루트를 사용합니다. 자동 데이터 이전은
하지 않습니다. 프로필·모양·일반 설정과 패널 너비는 시작 시 지정한 원래 workspace의
`.hub/`에 남아, 데이터 루트를 여러 번 바꿔도 시작 경로와 전역 설정을 찾을 수 있습니다.
`config/preferences.py`가 시작 시 한 번만 경로를 확정하고 `HubConfig._workspace_anchor`로
시작 설정 위치를 보존합니다. 설정 저장 중에는 활성 backend나 `ViewStateStore`를 교체하지 않습니다.

전역 설정 dataclass 필드의 `metadata={"requires_restart": True}`를 `config/restart.py`에서
조회해 폼 설명과 저장 후 재시작 판단에 공유합니다. 메타데이터는 사용자 저장값에 섞지 않습니다.
UI는 마지막 저장값이 아니라 이번 시작 때 적용한 값과 비교하므로 재시작을 미룬 상태도 유지합니다.
`ui/restart.py`가 확인 후 비동기 종료를 조정하고, BackendWorker는 모든 프로젝트의 실행·예약 및
처리 중인 명령을 확인한 뒤 새 명령 진입을 막습니다. ish에서는 `HubInstallation`만 재설치해 셸과
셸 입력을 유지하며, 독립 실행에서는 앱 종료 결과를 받아 `__main__`에서 새 앱을 엽니다.

명시적 로깅 범위가 없는 독립 프로바이더 사용은 llm의
`~/.ish/llm-provider-logs` fallback을 사용합니다. 이 전역 폴더는 프로젝트를
구별할 수 없는 다른 실행의 기록도 포함할 수 있어 Hub가 임의로 합치지 않습니다.

Hub의 진단 파일 조회는 `backend/provider_logs.py` 한 곳에서 두 workspace 경로를
읽습니다. `HubRuntime.project_activity()`는 실제 backend workspace를 사용하며,
프로젝트 활동과 진단을 Run ID로 연결합니다. Ctrl+L은 활동·프로바이더 진단을
표시하며 모든 도메인의 `service.log`를 재귀적으로 합치는 파일 뷰어는 아닙니다.
물리적 로그 위치를 바꾸려면 llm의 저장 계약 변경이 필요하므로 이번 정리에서는
파일 이동이나 백엔드 변경을 하지 않았습니다.

## 제거·보관한 잔재

- 실제 화면 경로의 목업 상속, 샘플 데이터 초기화, 사용되지 않는 Run 상세 패널
  문자열 조립과 관련 표시 필드·문구를 제거했습니다.
- 사용처 없는 `Checkbox` 래퍼와 읽기 팝업의 옛 전체 화면 분기를 제거했습니다.
  전체 화면 객체 조작은 `ExpandedOutputDialog`가 담당합니다.
- 샘플 테스트와 공통 PTK 도우미를 분리했습니다.
- 중복 일회성 검사기 `check_popup_updates.py`와 특정 과거 실패의 진단기
  `debug_settings.py`는 `tests/hub/reports/history/cleanup-20261011/`에 보관했습니다.
- 수동 캡처 도구의 제거된 F키·상세 패널 참조, 구형 위치 인자 기반 snapshot
  예제를 현재 태그 바·Ctrl키·명명 인자 방식으로 갱신했습니다.
- Hub·Hub 테스트·예제 아래 오래된 `__pycache__` 15개 디렉터리를 삭제했습니다.
  기존 보고서·스냅샷의 내용은 변경하지 않았습니다.

`llm/`과 참조 호스트 `ish.platform/`은 이번 정리의 수정 대상이 아닙니다.
llm의 Python 모듈 163개를 읽기 전용으로 검사했으며 `DefinitionComponent`,
`ComponentData`, `AsyncFacade`, `ModelClient` 등의 공통 기반 클래스 사용을 확인했습니다.
llm 배포 루트 안에 중첩된 테스트·예제 디렉터리는 없습니다.

## 검증 (2026-10-11)

Linux Python 3.12.14와 Linux 파일시스템 소스 스냅샷에서 검증했습니다.

- llm 전체: 1,357개 통과. `tests/llm/reports/ish-provider-69pv7bdv/suite.txt`.
- 최종 Hub 전체: 184개 통과. `tests/llm/reports/ish-provider-zo4p51sz/hub.txt`.
- 수정한 테스트 집중 재검사: 12개 통과. 같은 디렉터리의 `focused.txt`.
- 첫 검사에서 발견한 테스트 도우미 import 누락과 옛 상세 문자열 검증은 수정하고
  최종 Hub 전체 검사로 재검증했습니다.
- 저장 실패 검사는 flush·교체 실패 시 이전 값 보존과 임시 파일 정리를 확인합니다.
- 최종 스냅샷과 현재 Python 소스가 일치하며, llm·참조 호스트 소스 234개는
  최초 스냅샷 대비 해시 변경이 없습니다.
- 수동 PNG 캡처 도구는 정적 구문 검사를 했으며 이번 검증에서 실행하지 않았습니다.
