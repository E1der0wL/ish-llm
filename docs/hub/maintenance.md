# Hub UI 요청·설정·컴포넌트 구조

초기 책임 분리는 화면 동작과 저장 형식을 유지했습니다. 이후 패널·설정 UX 변경은
[패널과 설정 UX](panel-ux.md)에 정리합니다. llm 구현은 변경하지 않습니다.
홈 카테고리 이동, 공통 프로필 버튼 및 중단·문맥 정책의 후속 정리는 [홈 탐색](home-navigation.md)을 참고하세요.
영속 설정과 실행 권한은 기존 llm 공개 API가 계속 담당하며 Hub 객체는 UI 수명과
어플리케이션 구성을 담당합니다.

## 비동기 요청

`ui/requests.py`의 UIRequests가 BackendWorker의 Future를 RequestResult로 변환합니다.
성공한 None과 예외를 구분하고, 승인 접수·진행률·최종 결과를 같은 UI dispatch 경로로 전달합니다.
UI가 닫힌 뒤 큐에 남아 있던 callback은 실행하지 않습니다. Hub 최소화는 종료가 아니므로
진행 중인 백엔드 작업이나 알림 전달을 취소하지 않습니다.

LiveController의 `_call`과 `_settings_request`는 화면별 오류 정책을 연결하는 얇은 진입점입니다.
일반 요청은 공통 알림, 설정 요청은 설정 하단 메시지로 실패를 전달합니다. 설정 catalog만
연결 전 조회를 허용하며 Project 활성화 snapshot은 완료 callback보다 먼저 적용합니다.
준비·중단·승인 접수의 즉시 피드백과 영속 완료 시점은 합치지 않습니다.

## 설정 화면

버튼의 방향키 처리와 하단 안내는 `widget/actions.py`의 `bind_button_navigation`을 공유합니다.
일반 버튼과 보조 버튼은 좌우로 이동하며 끝에서 멈춥니다. 상하 키는 동작하지 않습니다. 선택 위젯은
`widget/controls.py`의 공통 연결을 사용합니다. CheckboxList의 키 확인·마우스 확인은
`_handle_enter`에서, 방향키 이동은 키 처리 후 한 번만 선택 변경을 알립니다.

| 객체 | 소유 상태와 책임 |
| --- | --- |
| SettingsScreen | 레이아웃, 현재 표시 페이지, 카탈로그, 요청 중 상태, 페이지 조정 |
| SettingsNavigation | 좌측 선택 키, 메인 영역/항목 위치, 포커스 이동, 인라인·외부 편집 |
| SettingsDrafts | 페이지 초안, 저장된 전역 값, 시작 시 적용값, 선택 프로젝트, 저장·취소·불러오기·재시작 안내 |
| SettingsJsonEditor | 외부 JSON 편집, 스키마·고정값 검증, 복구용 편집 내용, 초안 교체; 저장은 SettingsDrafts에 위임 |
| ChatProjects (`ui/chat/projects.py`) | 홈 패널의 프로젝트 선택과 생성·복제·이름 변경·삭제 |
| Page | 설정 폼·필터·버튼과 초안 상태; 각 페이지는 저장 요청 생성과 결과 적용만 구현 |

Screen은 키 바인딩과 위젯에서 사용하는 기존 행동 메서드를 명시적으로 위임합니다.
상태 접근은 `screen.navigation`과 `screen.drafts`로 구분합니다. 동적 속성 프록시나
하나의 거대한 기반 클래스로 되돌리지 않습니다. 저장 배치가 일부만 성공하면 성공한
초안만 반영하고 실패한 페이지와 나머지 초안을 유지합니다. 패널 너비는 독립 저장을 유지합니다.
저장 버튼 → `SettingsScreen.save()` → `SettingsDrafts.save()`가 유일한 UI 저장 경로입니다.
전역 설정과 현재 프로젝트의 변경을 함께 검증하고 저장하며, 재시작 안내도 여기서 처리합니다.
개별 페이지의 `submit/after_submit` 경로는 제거했고 기존 테스트는 실제 저장 버튼의 handler를
호출하거나 Enter 키로 버튼을 실행합니다.

패널 하단 프로필과 설정/홈 이동·최소화는 `widget/profile.py`를 공유합니다. 설정 화면은 현재
프로젝트만 편집하며 섹션별 폼이 하나의 ProjectPage 초안을 공유합니다. 저장·취소는
설정 화면에 머뭅니다. 자세한 동작은 [패널과 설정 UX](panel-ux.md)를 참고하세요.

## 엔진별 출력 영역

홈 출력은 `widget/output/engine.py`의 `EngineOutput` 공통 클래스를 상속합니다.
공통 클래스는 오른쪽 1칸 여백, 제목, 본문 오른쪽의 태그 바와 팝업 수명을 관리합니다.
설정 메인 영역도 같은 `ContentInset`을 사용하며 홈 출력과 시작 위치·너비·오른쪽 1칸 여백이 같습니다.
`LoopOutput`은 대화 본문·환영 화면·찾기와 코드·표 등의 확대 화면을 제공합니다.
태그 바를 열어도 입력창·보조 버튼·진행 영역의 너비나 높이는 유지합니다.

각 출력 클래스는 `tag_entries()`에서 `OutputTag`(ID·제목·위치·아이콘·원문·여백)를
반환하고 `select_tag()`와 필요할 경우 `expand_tag()`를 구현합니다. `tag_position()`은
현재 스크롤 위치이며, 엔진이 자체적으로 스크롤을 변경할 때 `position_changed()`를 호출합니다.
태그 바를 열 때는 현재 위치에 가장 가까운 항목만 선택하고 스크롤은 옮기지 않습니다.
목록 조작·원문 복사·제목·접기 버튼은 모든 엔진에서 공통으로 동작합니다.

홈의 공통 입력 처리·마우스 범위·실시간 갱신은 transcript나 TagBar 내부를 조회하지 않습니다.
스크롤·메시지 갱신·읽기 위치·테마 변경은 출력 계약에 위임하고, 엔진의 추가 단축키는
현재 출력의 ShortcutRegistry에서 읽습니다. 팝업 표시 자체는 공통 DialogController를 사용합니다.
RenderWorker는 질문·로그 팝업도 사용하는 홈 공용 자원이며 출력 교체 시 종료하지 않습니다.

새 출력 구현은 `HubView(output_factory=...)`로 생성하거나 UI 루프에서
`view.set_output(NewOutput(view))`으로 교체합니다. 교체 시 기존 출력의 검색·확대·태그 포커스와
진행 중 렌더를 정리하고 현재 메시지·테마·작업 경로를 새 출력에 전달합니다.
공통 `close()`가 `deactivate()`와 엔진별 `release()`를 호출하므로 엔진은 자신이 소유한
렌더 작업·캐시만 `release()`에서 정리합니다.
입력 초안·좌측 패널·보조 버튼·하단 진행 영역은 유지합니다. Graph 전용 화면이나
엔진별 자동 전환 규칙은 아직 추가하지 않았으며 현재 기본 화면은 LoopOutput입니다.

경로 입력은 `widget/forms/path_input.py`의 `PathInput`을 공유합니다. 자동완성 목록은
홈의 루트 FloatContainer 마지막에 한 번만 배치합니다. 팝업 내부에 cursor Float를
중첩하면 PTK의 지연 그리기 순서상 입력창보다 먼저 좌표를 읽을 수 있으므로 추가하지 않습니다.

## Provider 진단과 재시도 표시

`GeneralSettings`의 Provider 진단 필드는 전역 preferences에만 저장하며 재시작이 필요합니다.
`backend/bootstrap.py`는 원래 workspace의 preferences를 읽고 `BackendServices.provider_diagnostics`에
연결합니다. 프로젝트의 provider 정책이나 이미 생성된 RunManager를 저장 시 변경하지 않습니다.
사용자 지정 backend_factory는 자신의 BackendServices 구성을 계속 소유합니다.

`RetryObservationMixin`은 completion iterator를 진행하는 동안만 `diagnostic_scope`를 연결합니다.
Session worker 생성 시점이나 UI 호출 Task에 의존하지 않으며, yield 이후 관찰 문맥을 남기지 않습니다.
`RetryMonitor`는 `provider_retry`의 call_id·attempt·delay_seconds로 표시 상태를 만들고,
Retry-After/jitter를 재계산하지 않습니다. 취소·종료 시 호출별 상태를 정리합니다.

`FailureDiagnostics`는 public Run/Step 조회에서 실패 Step의 `diagnostic`을 우선 사용합니다.
진단의 소유권·Run 오류 코드를 확인하며, 유효한 Step 진단이 없으면 Run 오류 코드만 안내합니다.
오류 안내를 위해 로그 파일이나 활동 기록을 조회하지 않습니다. 원본 로그 조회는 로그 창이 담당합니다.
UI는 한국어·영어 안내와 finish_reason을 투영하며 실행·재시도를 결정하지 않습니다.

## 컴포넌트 관리

기본 `/` 명령은 `ui/input/commands.py`의 불변 `CommandSpec` 목록이 이름·설명 키·실행 함수·
인자 후보 종류를 소유합니다. 실행과 `HubCompleter`가 같은 목록을 사용하며, 컴포넌트 명령은
아래 목록과 `config/help` 계약을 따릅니다. 등록되지 않은 `/` 입력은 일반 요청으로 전달합니다.
`/engine`, 요청별 `engine_options`, Workflow 조회와 Graph 실행 경로는 유지합니다.
Graph 전용 페이지가 추가되기 전에는 Workflow를 백엔드 실행 옵션으로 지정합니다.

`hub/components.py`의 불변 ComponentSpec 목록이 정식 이름·모델 도구 접두사·후보 순서·관리 방식·
가져오기 형식·백엔드 데이터 계약을 정의합니다. 이 모듈을 UI에서 읽어도 llm/PTK를 로드하지
않습니다. 고정된 데이터 클래스 경로는 백엔드에서만 해석하며 사용자 텍스트를 import하지 않습니다.

- 명령 파서는 이 목록과 현재 Project의 선택 컴포넌트 교집합만 노출합니다.
- ComponentCommands는 관리 방식에 따라 기존 ToolManager/RecordManager를 선택합니다.
- RecordManagement는 같은 정의로 데이터 타입을 검사하고 일반 레코드·Agent·RAG 어댑터를 선택합니다.
- 슬래시 명령은 정식 이름만 사용하고, 공통 authoring_tools는 별도의 도구 접두사를 사용합니다.

이 목록은 실행 컴포넌트를 활성화하거나 권한을 부여하지 않습니다. 실제 엔진·컴포넌트 구성은
`backend/bootstrap.py`, 사용자 생성 기본값은 `backend/project/defaults.py`에 남습니다.
RAG의 색인 변경과 Agent의 revise/version 계약은 서로 다른 어댑터에서 처리합니다.

## 호스트와 기본 자산

`ui/host.py`가 터미널을 빌려 외부 에디터를 실행하고 데스크톱 파일 탐색기를 엽니다.
관리 위젯에는 콜백을 전달하여 설정 화면 모듈에 의존하거나 직접 프로세스를 실행하지 않습니다.
에디터의 오류 처리·종료·임시 파일 정리와 적용된 에디터 설정 사용은 유지합니다.

`widget/text_editing.py`는 쓰기 가능한 Hub 입력의 전체 선택·선택 교체·잘라내기·실행 취소와
이동 키를 공유합니다. 입력 드래그는 `widget/mouse.py`의 공통 캡처와 PTK 좌표 매핑을 사용합니다.
셸·읽기 전용 위젯·설정의 탐색 모드는 해당 편집 키의 적용 대상에서 제외합니다.

`asset/guides.py`는 StarterSkill 목록과 텍스트 로딩만 담당합니다. 시스템 프롬프트는
`asset/prompts/system.md`, 모든 기본 Skill 본문은 `asset/skills/`에 있습니다.
리소스 ID·지침 내용·새 프로젝트에서만 복사하는 정책은 유지합니다. 저장된 언어 팩이
깨지지 않도록 과거 번역 키는 보존하되, 제거된 명령 사용법은 현 관리 창 안내로 고칩니다.

## 검증 범위

### 미사용 경로 정리 (2026-10-16)

- 제거된 입력창 위 상태줄의 `Presentation.activity` 계산과 View의 보관 필드를 제거했습니다.
  실행 상태·재시도는 응답 헤더에, 질문 대기·오류 안내는 기존 하단 영역에 계속 표시합니다.
- 사용처가 없는 `execution_label` 표시 속성을 제거했습니다. `/engine`, 요청별 엔진 옵션과
  Graph Workflow 실행 경로는 유지합니다.
- 요청 목록·로그 팝업은 DialogController만 소유합니다. HistoryUI의 중복 참조를 제거해
  닫힌 팝업과 출력 데이터를 다음 요청까지 붙잡지 않게 했습니다.
- 한·영 언어 팩의 중복 키는 실제로 적용되던 마지막 값을 유지하면서 하나로 정리했습니다.
  사용자 JSON 언어 팩의 검증 대상 키와 플러그인 공개 진입점, 성능 관찰 카운터는 유지합니다.
- 설정↔홈 전환은 PTK의 부모 컨테이너 연결을 즉시 갱신합니다. 다음 렌더 이전에 들어온
  프로필 이동 등의 공통 단축키가 이전 페이지의 연결 때문에 누락되지 않게 합니다.
- 정적 참조 검사에서 런타임의 미사용 파일은 추가로 발견되지 않았습니다. PTK/Rich에서 호출하는
  override와 타입 검사 전용 import는 일반 호출이 보이지 않아도 필요한 코드입니다.

상태 표시 검사는 폐기된 상태줄 데이터 대신 실제 응답 헤더·질문 안내를 확인합니다.
`test_choice_controls.py`는 선택 키 입력당 통지 횟수와 RadioList 동작을 검사하며,
기존 보조 버튼·팝업·설정·그래프 실행 회귀 검사도 유지합니다.

UIRequests의 스레드 전달 순서·오류·종료 후 callback 억제를 직접 검사합니다. 기존 PTK
설정 탐색·편집·초안·부분 저장 실패·프로젝트 전환·재시작, 컴포넌트 가져오기·편집·삭제,
승인·중단·진행 표시와 렌더링 회귀 검사는 유지합니다. 전체 검증은 WSL Linux Python 3.12.14와
Linux 파일시스템의 소스 스냅샷으로 실행합니다. 과거 보고서와 구조 감사 스냅샷은 고치지 않습니다.

### 2026-10-15 검증 결과

- Linux Python 3.12.14: 백엔드 전체 1,405개, Hub 전체 365개 통과.
- 백엔드 결과: `tests/llm/reports/ish-provider-6o0vwrar/suite.txt`.
- 최종 Hub 결과: `tests/llm/reports/ish-provider-h_queukj/hub.txt`.
- 최초 Hub 검사에서 발견한 기존 아이콘의 고정 기대값과 세션 렌더 완료 전 이동키를
  보내던 테스트 대기 조건을 수정하고 전체 Hub를 재실행했습니다. 실행 코드는 바꾸지 않았습니다.
- `snapshot.json` 해시로 최종 구현·테스트와 검증 스냅샷이 동일한 것을 확인했습니다.
  검증 후 변경은 안내 문서뿐입니다. 정리 전 스냅샷과 비교한 llm 소스·문서 194개와
  기본 시스템 프롬프트·Skill 본문도 동일합니다.

`tests/hub/run_linux.py`는 Python뿐 아니라 Markdown·JSON 자산도 복사합니다.
더미 터미널 테스트에서는 실제 데스크톱 클립보드 연결 환경을 제외합니다.
