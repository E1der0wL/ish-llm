# Hub 구조와 정리 내역

## 미사용 경로와 중복 처리 정리 (2026-10-16)

폐기된 입력 상태줄의 projection·표시 속성과 HistoryUI의 중복 팝업 참조를 제거했습니다.
일반/보조 버튼 탐색과 RadioList/CheckboxList의 키 연결을 공통화했고 언어 팩의 중복 정의를
정리했습니다. 현재 프로젝트 조작은 `ui/chat/projects.py`가 담당하며, 설정에는 탐색·초안·
JSON 편집만 남습니다. 그래프 실행과 공개 출력 확장 계약은 유지합니다.
[검토 범위와 유지한 코드](maintenance.md#미사용-경로-정리-2026-10-16).

## 엔진 출력 소유권 (2026-10-16)

`widget/output/engine.py`는 모든 홈 출력이 상속하는 공통 레이아웃·태그 바,
`widget/output/loop.py`는 대화 본문과 출력 객체를 공급하는 기본 Loop 출력을 정의합니다.
태그 바는 공통 출력 클래스의 자식이며 홈 전체의 별도 열로 렌더링하지 않습니다.
홈 공통 레이아웃은 출력 슬롯·입력창·패널을 조립합니다. 구체적인 출력 위젯의 컨트롤이나
태그 바 상태를 참조하지 않습니다. [확장 방법](maintenance.md#엔진별-출력-영역).

## 패널 UX 보강 (2026-10-15)

프로젝트 탐색과 CRUD는 `ui/chat/projects.py`로 이동했습니다. 설정의 navigation/drafts는
현재 프로젝트의 일반·엔진·컴포넌트 섹션을 표시하고 공유 초안을 관리합니다.
공통 프로필·패널은 `widget/profile.py`, `widget/sidebar_panel.py`에 있으며 실패 안내는
`backend/observability/failures.py`가 같은 Run의 기록을 분석합니다. [사용법](panel-ux.md).

## 현재 구조 보강 (2026-10-14)

최신 책임 분리와 확장 위치는 [UI 요청·설정·컴포넌트 구조](maintenance.md)에 정리합니다.
아래 2026-10-13 절은 당시 이동 내역과 검증 기록입니다.

- `ui/requests.py`: Future 결과, 승인 접수, 진행률의 UI 루프 전달.
- `ui/settings/navigation.py`, `drafts.py`, `project_actions.py`: 탐색/편집, 초안/저장, 프로젝트 작업.
- `components.py`: 관리 가능한 컴포넌트의 정식 이름·모델 도구 접두사·관리 계약.
- `backend/bootstrap.py`: 백엔드 생성과 어댑터 구성. Runtime의 공개 생성 진입점은 유지합니다.
- `ui/host.py`: 설정과 관리 팝업이 함께 쓰는 에디터·파일 탐색기 연동.
- `asset/prompts/`, `asset/skills/`: 기본 지침 본문. `asset/guides.py`는 목록과 로딩만 담당합니다.

## 책임별 구조 (2026-10-13)

이번 정리는 Hub 내부 파일 위치와 상태 소유권을 바꿉니다. llm public API, 승인 authority,
저장 형식, 키 입력, 화면 배치, 렌더링/캐시 수명은 유지합니다. 직전 변경인 질문·승인 선택지의
전체 너비 표시도 유지합니다. 변경 전 모든 Python 파일의 판정과 목적지는
[파일별 구조 감사](structure-audit.md)에 기록했습니다.

```text
hub/
├─ backend/
│  ├─ runtime.py, worker.py          # composition, 작업 순서, backend loop
│  ├─ execution/                    # context/completion/continuation, 실행 관찰
│  ├─ interactions/                 # 질문 대기와 승인 내용 표시
│  ├─ tools/                        # 내장 도구·패키지 관리·경로·프로세스·스킬
│  ├─ project/                      # 생성 템플릿·설정·명령·자동 제목
│  └─ observability/                # 읽기 전용 snapshot·진단·알림 stream
├─ widget/
│  ├─ transcript/                   # 대화 렌더/메시지 캐시/문자 스트림/찾기
│  ├─ output/                       # 읽기 팝업·viewport·표·태그 바·검색 색인
│  ├─ feedback/                     # 준비/응답/중단/재시도/진행/알림 표시
│  ├─ forms/                        # 설정·질문·선택지·세션·도구·실행 입력
│  └─ 공통 컨트롤, 크롬, output_inspector.py
├─ ui/
│  ├─ chat/, input/, layout/, output/, settings/
│  └─ live.py, view.py, presentation.py, snapshot_inbox.py 등
└─ config/, asset/, locales/, model.py, 설치·실행 진입점
```

backend 26개, widget 27개 모듈을 이동했습니다. `project/naming.py`의 TitleEngine과
SessionNamer는 자동 제목이라는 같은 개념을 구현하므로 함께 유지합니다. UI 컨트롤러와
렌더러를 `widget/` 안으로 다시 합치지 않습니다. 공통 `controls.py`, `dialog.py`,
sidebar/header/shortcut 위젯은 여러 category의 기반이므로 widget 루트에 둡니다.

## 객체의 소유권과 수명

| 객체 | 소유 상태 / 수명 | 소유하지 않는 책임 |
| --- | --- | --- |
| `HubRuntime` | backend 구성·세션 handle·선택·구독·작업 순서; backend loop 수명 | 알림 dedup, Tool 패키지 CRUD 구현 |
| `NotificationBuffer` | session 표시 identity, 관찰 Run 상태, 질문 identity, 64개 알림·128개 dedup 키·단조 sequence; Runtime 수명 | 번역·토스트 디자인·Run 상태 변경 |
| `ToolManagement` | 한 UI 명령의 Project/ToolData/ToolPaths와 root/builtin, 버전 검사 | 영속 Tool 권한·실행·승인 authority |
| `SnapshotReader` | 읽기 캐시·thinking/trace projection; backend loop, 프로젝트 전환 시 clear | 명령 실행·제목 생성 예약 |
| `QuestionBroker` | 기다리는 질문/Future; backend 종료 시 취소 | 영속 승인 결정·checkpoint 계약 |
| `SessionNamer` | 제목 생성 작업·오류; backend 종료 시 정리 | 일반 대화 Run 실행 |
| `LiveController` | UI intent → BackendWorker → UI 반영, 현재 loop/app, 화면 상태 복원 | snapshot mailbox 내부 lock/slot |
| `SnapshotInbox` | lock·최신 pending snapshot·한 번의 delivery 예약; Controller 수명 | event loop/thread 생성·callback 실행·Controller 참조 |
| `RenderWorker`, `MessageRenderCache`, `MessagePresentationCache` | 기존 작업 취소·최신 렌더·본문/헤더/trace 재사용 경계 그대로 | backend 변경·추가 thread pool |
| `ViewStateWriter` | 기존 비동기 읽기 위치 저장·load cache 수명 그대로 | snapshot publication |

알림은 프로젝트 전환에도 기존 세션 identity를 유지해 다른 프로젝트의 실행 완료를 전달합니다.
첫 snapshot의 과거 Run은 조용히 기준 상태로 관찰하며, 이후 이벤트와 snapshot reconciliation은
같은 dedup/sequence를 사용합니다. 이미 관찰한 paused 이벤트는 질문 알림 뒤에 반복하지 않습니다.
세션 삭제는 identity와 관찰 상태만 제거하고 이미 발행한 알림은 보존합니다.

ToolManagement는 명령마다 `for_project()`로 엽니다. 데이터/경로를 한 번 확인한 뒤
`list`, `import_package`, `delete`, `toggle_builtin`, `open_path`로 처리하며 `execute`는
문자열 action의 얇은 분기입니다. 명령 사이에 설정·enabled 값을 캐시하지 않습니다.
패키지 codec, lifecycle lock, optimistic version 검사와 최종 권한은 llm에 남습니다.

SnapshotInbox는 여러 backend publish를 최신 한 개로 합치고 UI callback 예약권을 한 번만
반환합니다. consume할 때 slot을 비우며, ish prompt loop가 바뀌면 예약만 해제하고 pending
데이터는 유지합니다. 스케줄 실행 가능 여부와 UI 반영은 LiveController가 결정합니다.

## 함수와 import 경계

- `review_lines/review_text/risk_label/approval_preset`, risk advice 변환,
  `split_followup`, component command parsing, 경로 변환은 상태·수명이 없어
  함수로 유지합니다. 새 Manager/registry/event bus/DI 기반 계층을 만들지 않습니다.
- 새로운 알림/Tool/mailbox 객체는 HubRuntime이나 LiveController concrete type을 import하거나
  back-reference로 받지 않습니다. 기존 SettingsService/SessionNamer의 조정 연결은 유지합니다.
- `hub.hub.install`, `HubInstallation`, `HubConfig/HubTheme/UserProfile` 노출과
  `hub.backend.runtime.HubConfig`, `hub.ui.output` 출력 확장 API는 그대로입니다.
- 문서화된 `hub.widget.output_inspector.ExpandedOutputDialog`도 기존 경로를 유지합니다.
  내부 파일 경로를 위한 재-export/compatibility 모듈은 만들지 않습니다. 내부 호출과 테스트는
  category 경로를 직접 사용하며 새 package `__init__.py`는 기능을 eager import하지 않습니다.
- 방향은 `ui → widget/backend adapter`, `backend/runtime → feature packages → llm API`입니다.
  관찰 계층은 llm 상태를 수정하지 않으며, 위젯은 서비스 handle 대신 데이터·콜백을 받습니다.

## 보류한 구조 후보

SessionCatalog는 세션 생성/삭제/복제와 실행 start/shutdown, 제목 생성, 선택·dirty 처리의
순서를 함께 조정해야 하므로 분리하면 Runtime을 다시 전달하는 wrapper가 됩니다. 이번에는
유지합니다. SettingsService/SessionNamer의 기존 Runtime 의존성도 저장/재시작·제목 예약의
계약을 건드리지 않고 유지합니다. ui의 현재 category, 렌더러, 순수 함수는 이미 경계가 명확해
추가 추상화나 파일 크기를 이유로 한 분할을 하지 않습니다. 테스트는 기능 중심 이름을 유지합니다.
정적 import 그래프의 기존 conversation/character_stream 상호 참조(지연 import)는 유지하며,
이동 전후 새 순환 의존성은 추가하지 않습니다.

## 이번 구조 정리 검증 (2026-10-13)

- Linux Python 3.12.14, Linux 파일시스템 스냅샷 `ish-provider-kkqhcsih` 사용.
- 집중 76개 통과(48.935초), Hub 전체 282개 통과(312.866초). 집중 검사는 전체의 일부입니다.
- 새 상태 소유 테스트 8개는 알림 baseline/dedup/용량/다른 프로젝트/세션 삭제와
  동시 snapshot publish/consume/prompt loop 교체를 검사합니다.
- 기존 렌더 파이프라인·responsiveness·snapshot·질문/승인·Tool 관리·설정·셸 통합·재시작 검사를
  유지했고, 배포 플러그인 로더 검사에서 공개 import 경로도 확인했습니다.
- 80×24 터미널, 100 KiB 응답, 100회 갱신 벤치마크: 이전 메시지 200개 모두 재사용,
  추가 본문 렌더 1회, prefix 구성 1회, 대기 surface 최고 1개. 기존 캐시/큐 상한을 유지합니다.
- Hub Python 135개 구문/import 검사: 누락된 내부 import 0개, 새 순환 의존성 0개.
  이동만 한 52개 모듈은 import를 제외한 AST가 이전 검증본과 동일합니다.
- 638개 스냅샷 파일이 현재 소스와 일치함을 확인했으며, 이후 변경은 이 결과 문서뿐입니다.
  llm와 llm 테스트 소스도 직전 전체 1,383개 통과본과 해시가 동일합니다. 이번에는 Hub를 재검사했습니다.
- 첫 집중 검사에서 발견한 ToolManagement의 `list` 메서드/타입 표기 충돌과 새 테스트의
  snapshot 필수 필드 누락을 수정한 뒤 위 최종 검사에서 모두 통과했습니다.
- 로컬 검증 기록: `tests/llm/reports/ish-provider-kkqhcsih/`,
  `tests/hub/reports/structure-review.json`, `tests/hub/reports/structure-benchmark.json`.
  보고서와 일회성 이동/검사 스크립트는 배포·커밋 대상에 포함하지 않습니다.

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
| 설정 키·설명·값 편집 | `widget/forms/settings.py`, `widget/forms/settings_viewport.py` | 설정 페이지 |
| 읽기 팝업 | `widget/output/reader.py`의 `ReadOnlyDialog` | 도움말·로그·컴포넌트 조회 |
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

오류 안내는 `backend/observability/failures.py`에서 실패 Step의 저장된 진단을 먼저 읽습니다.
유효한 Step 진단이 없으면 Run 오류 코드만 사용하고, 로그로 진단을 보완하지 않습니다.
로그 창의 `HubRuntime.project_activity()`는 실제 backend workspace를 사용하며,
프로젝트 활동과 진단을 Run ID로 연결하고 Step/call ID를 유지합니다. 보조 버튼의 로그는
활동·프로바이더 진단을 표시하며 모든 도메인의 `service.log`를 재귀적으로 합치는 파일 뷰어는 아닙니다.
물리적 로그 위치를 바꾸려면 llm의 저장 계약 변경이 필요하므로 이번 정리에서는
llm 로그 파일 이동이나 저장 계약 변경을 하지 않았습니다.

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
