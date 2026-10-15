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

| 객체 | 소유 상태와 책임 |
| --- | --- |
| SettingsScreen | 레이아웃, 현재 표시 페이지, 카탈로그, 요청 중 상태, 페이지 조정 |
| SettingsNavigation | 좌측 선택 키, 메인 영역/항목 위치, 포커스 이동, 인라인·외부 편집 |
| SettingsDrafts | 페이지 초안, 저장된 전역 값, 시작 시 적용값, 선택 프로젝트, 저장·취소·불러오기·재시작 안내 |
| ChatProjects (`ui/chat/projects.py`) | 홈 패널의 프로젝트 선택과 생성·복제·이름 변경·삭제 |
| Page | 공통 검증·저장 요청·결과 적용 흐름; 각 페이지는 저장 데이터와 적용 방식만 구현 |

Screen은 키 바인딩과 위젯에서 사용하는 기존 행동 메서드를 명시적으로 위임합니다.
상태 접근은 `screen.navigation`과 `screen.drafts`로 구분합니다. 동적 속성 프록시나
하나의 거대한 기반 클래스로 되돌리지 않습니다. 저장 배치가 일부만 성공하면 성공한
초안만 반영하고 실패한 페이지와 나머지 초안을 유지합니다. 패널 너비는 독립 저장을 유지합니다.

패널 하단 프로필과 설정/홈 이동·최소화는 `widget/profile.py`를 공유합니다. 설정 화면은 현재
프로젝트만 편집하며 섹션별 폼이 하나의 ProjectPage 초안을 공유합니다. 저장·취소는
설정 화면에 머뭅니다. 자세한 동작은 [패널과 설정 UX](panel-ux.md)를 참고하세요.

## 컴포넌트 관리

`hub/components.py`의 불변 ComponentSpec 목록이 정식 이름·단수 별칭·후보 순서·관리 방식·
가져오기 형식·백엔드 데이터 계약을 정의합니다. 이 모듈을 UI에서 읽어도 llm/PTK를 로드하지
않습니다. 고정된 데이터 클래스 경로는 백엔드에서만 해석하며 사용자 텍스트를 import하지 않습니다.

- 명령 파서는 이 목록과 현재 Project의 선택 컴포넌트 교집합만 노출합니다.
- ComponentCommands는 관리 방식에 따라 기존 ToolManager/RecordManager를 선택합니다.
- RecordManagement는 같은 정의로 데이터 타입을 검사하고 일반 레코드·Agent·RAG 어댑터를 선택합니다.
- 공통 authoring_tools는 같은 정식/단수 이름을 사용합니다.

이 목록은 실행 컴포넌트를 활성화하거나 권한을 부여하지 않습니다. 실제 엔진·컴포넌트 구성은
`backend/bootstrap.py`, 사용자 생성 기본값은 `backend/project/defaults.py`에 남습니다.
RAG의 색인 변경과 Agent의 revise/version 계약은 서로 다른 어댑터에서 처리합니다.

## 호스트와 기본 자산

`ui/host.py`가 터미널을 빌려 외부 에디터를 실행하고 데스크톱 파일 탐색기를 엽니다.
관리 위젯에는 콜백을 전달하여 설정 화면 모듈에 의존하거나 직접 프로세스를 실행하지 않습니다.
에디터의 오류 처리·종료·임시 파일 정리와 적용된 에디터 설정 사용은 유지합니다.

`asset/guides.py`는 StarterSkill 목록과 텍스트 로딩만 담당합니다. 시스템 프롬프트는
`asset/prompts/system.md`, 모든 기본 Skill 본문은 `asset/skills/`에 있습니다.
리소스 ID·지침 내용·새 프로젝트에서만 복사하는 정책은 유지합니다. 저장된 언어 팩이
깨지지 않도록 과거 번역 키는 보존하되, 제거된 명령 사용법은 현 관리 창 안내로 고칩니다.

## 검증 범위

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
