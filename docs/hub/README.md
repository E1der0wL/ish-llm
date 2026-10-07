# hub 설계 문서

## Project 정책과 risk/dependency 표시

Hub의 모델·prompt·실행 한계는 새 Project의 명시 설정으로 작성합니다. Engine factory는
구현 주입만 담당하며 저장값을 덮는 Host ceiling을 만들지 않습니다. template 버전과 화면용
description은 definition.metadata에 있습니다. 이전 레코드를 자동 변환하지 않습니다.

`backend/approval.py`는 hub-risk-v1의 숫자→label 표시와 명시 threshold를 Project 승인
규칙으로 만드는 함수를 제공합니다. 낮음/중간/높음 구간은 Hub 소유이고 Backend는 모릅니다.
미분류 위험은 unknown이며 기본 classifier/자동 승인은 만들지 않습니다. 기존 Hub는 paused
Run의 승인·재개를 llm API에 안내하며 별도 승인 대화상자는 이번 범위에 추가하지 않았습니다.
정책 JSON은 Project 설정 폼에서 편집할 수 있습니다.

Component 설정 화면은 schema의 required_components와 선택 집합의 누락을 표시합니다.
사용자가 전체 선택 집합을 저장하면 Backend가 검증합니다. 자동 추가나 cascade 삭제는 없습니다.
공유 Host 자원은 읽기 전용 catalog이며 Project 설정에 복제하지 않습니다.

GitHub CI는 Hub의 literal PLUGIN_META에서 UI 의존성을 설치합니다. 참조 `ish.platform/`이
없는 checkout에서는 실제 Host가 필요한 검사만 사유와 함께 skip합니다. 로컬 전체 검사에는
참조 Host를 포함하며, 일반 Hub 동작 검사나 실행 오류를 skip으로 숨기지 않습니다.

실제 대화와 백엔드 없는 미리보기를 지원합니다. [사용법](../../hub/README.md)과
[독립 미리보기](../../examples/hub/preview.py)를 참고하세요.

## 2026-10-05 입력·팝업·스냅샷 책임 분리

`ui/view.py`는 화면 구성과 상태 연결을 맡습니다. `ui/input/context.py`가 실제 포커스를 읽고,
`registry.py`의 키·조건·동작·문구 정의로 PTK 바인딩과 하단 안내를 함께 생성합니다.
`bindings.py`는 페이지 동작, `commands.py`는 기본 `/` 명령을 연결합니다. 설정 필드·버튼·선택 목록·
읽기 팝업의 로컬 조작도 같은 등록부를 사용합니다. 안내는 ESC → Ctrl → Alt → 단독 키 순,
각 그룹에서는 키 이름 순으로 정렬합니다. 스크롤 키 집합은 여덟 이동키로 한 곳에서 정의합니다.

`ui/dialogs.py`가 팝업 열기·검증·닫기·포커스 복원을 소유하고 `ui/chat/session_actions.py`는
생성·복제·이름 변경 흐름을 담당합니다. 늦게 도착한 복제 경계 조회는 팝업과 프로젝트/세션 식별을
확인한 뒤 반영합니다. 실제 생성 폼은 `widget/session_form.py`에 있습니다.

`widget/header.py`의 HeaderBar로 설정/대화 상단 스타일을 통일하고 표식을 `•`로 바꿨습니다.
`widget/reader.py`의 ReadOnlyDialog는 도움말·로그·컴포넌트 조회 결과에 재사용합니다.
출력에는 포커스가 없고 공통 receiver에서 Alt + 이동키 전체를 처리하며 ESC·Enter·Ctrl+L로 닫습니다.
도움말은 한국어/영어 언어팩의 Markdown 본문을 Rich로 렌더링하고, 너비·테마 변경 시 다시 그립니다.

셸에는 Ctrl+Q 진입 바인딩만 설치하고 Ctrl+S는 수정하지 않습니다. Hub에서는 ESC로 패널에
이동하고, 패널에서 ESC로 최소화합니다. 설정의 Alt 스크롤 차단, Ctrl+X 중단, 직접/외부 편집,
셸 alternate screen 복원은 유지합니다.

`backend/snapshot.py`는 공개 llm 조회 API만 호출하며 원시 상태·예약 수·Run/Step 요약·ISO 시각을
`model.py`의 불변 스냅샷으로 만듭니다. UI 컨트롤이나 서비스 핸들은 UI 스레드에 전달하지 않습니다.
자동 제목 예약과 누락 알림 보정은 `HubRuntime`에 남기고 같은 조회 결과를 재사용합니다.
완료된 Run 표시 자료는 최대 1,024건 캐시하며 프로젝트 전환과 대화 삭제 시 비웁니다.
`ui/presentation.py`에서 현재 언어/아이콘/지역 시각에 맞춰 변환하므로 테마 변경에 백엔드 재조회나
캐시 무효화가 필요하지 않습니다. llm/ 및 ish 참조 소스는 수정하지 않았습니다.

검증: Linux Python 3.12.14에서 최종 집중 4개·Hub 전체 110개 통과.
최종 Hub 소스/테스트/예제 Python 파일 117개가 검증 스냅샷과 일치합니다.
기록: `tests/llm/reports/ish-provider-jchex4wl/` (앞선 집중 16개·Hub 110개: `ish-provider-r291fq_5/`).
백엔드 집중 191개·전체 1,242개 통과: `tests/llm/reports/ish-provider-ut6dwwur/`.
llm/ 및 ish 참조 Python 소스 190개의 해시도 일치합니다.

## 2026-10-05 위젯 통합·포커스 안내·아이콘 스타일

Hub 전용 UI 위젯을 `hub/widget/`으로 통합했습니다. 기본 컨트롤·버튼·버튼 행·설정 필드·
설정 스크롤·좌측 목록·섹션·대화 출력·진행 표시·검색·로그·실행 선택·알림·안내 화면을 포함합니다.
`ui/settings/`와 `ui/chat/`은 초안과 작업 흐름을 관리하고, 위젯에 데이터와 콜백을 전달합니다.
페이지 배치는 기존 `ui/layout/`, 출력 파싱과 렌더러는 `ui/output/`에 유지합니다.
`model.py`로 메시지 표시 데이터를 분리해 백엔드가 UI 위젯을 import하지 않도록 했습니다.
이전 내부 위젯 경로는 제거했으며 테스트와 예제의 참조도 갱신했습니다.

설정 본문의 중복 제목과 좌측 패널 하단의 안내 행을 없앴습니다. 입력창 내부 안내는 유지합니다.
`ui/shortcuts.py`는 현재 control·편집 상태·팝업 종류로 표시할 단축키를 고르고,
`widget/shortcuts.py`는 터미널 셀 너비 안에 한 줄로 표시합니다. 페이지 전환·포커스 이동 시
자동 갱신되며, 설정 화면에 대화용 Alt 스크롤 안내가 표시되지 않습니다.

`HubTheme.icon_style`은 `nerd`(기본) 또는 `unicode`입니다. 모양 설정의 선택 필드는
Enter로 선택 모드 진입 → 방향키 변경 → Enter 완료로 조작하며 저장 시 적용됩니다.
`asset/icon.py`의 불변 IconSet을 Language/화면마다 적용하므로 전역 상태를 변경하지 않습니다.
백엔드의 상태 문구와 렌더 캐시, 이미 떠 있는 알림도 새 스타일을 반영합니다.
아이콘 선택이 없는 이전 환경 설정은 Nerd Font 기본값을 사용합니다.

추가 정리 후보는 `ui/view.py`의 키 바인딩·대화상자 동작 분리와
`backend/runtime.py`의 스냅샷/표시 데이터 조립 분리입니다. 이번에는 UI 위젯 경계에 한정했습니다.
아래 과거 작업 기록의 경로는 당시 스냅샷을 나타내므로 그대로 유지합니다.

검증: Linux Python 3.12.14에서 집중 23개 및 최종 위치 정리 후 집중 7개·Hub 전체 108개 통과.
최종 Hub 소스/테스트/예제 105개가 검증 스냅샷과 일치합니다.
기록: `tests/llm/reports/ish-provider-n4uys9jb/`(이전 집중 기록 `ish-provider-qipdro26/`).
백엔드 집중 191개·전체 1,242개 통과: `tests/llm/reports/ish-provider-b627m1dv/`.
llm/ 및 ish 참조 Python 소스 190개의 해시도 일치하며 두 영역은 수정하지 않았습니다.

## 2026-10-05 Hub 왕복 시 셸 화면 보존

일반 PromptSession의 입력 영역에 터미널 높이의 Float를 직접 그리면 셸의 일반 화면과
같은 버퍼를 덮고, 숨길 때 렌더러가 Hub 영역을 지웁니다. `ui/terminal_screen.py`가 ish
설치의 표시 수명에 맞춰 PTK full_screen 모드를 소유하도록 연결했습니다. Hub를 그리기 전에
현재 프롬프트만 지우고 터미널의 alternate screen에 진입합니다. 숨김 시 alternate screen을
빠져나온 후 원래 모드와 커서 기준을 복원해 셸 출력 위에 프롬프트를 다시 그립니다.

기존 전체 화면 호스트의 모드는 소유하지 않으며, 플러그인 제거·호스트 종료에도 원래 모드를
복원합니다. `in_terminal()`의 배경 셸 출력과 외부 편집은 PTK의 기존 버퍼 전환을 사용합니다.
ish 참조 소스와 llm은 변경하지 않았습니다. `set_float`·`set_key` 계약도 유지합니다.

Linux Python 3.12.14 집중 11개 및 Hub 전체 106개 통과.
실제 ish/PTK 렌더러의 출력 연산을 셀 버퍼에 적용하여 기존 셸 출력과 초안 보존,
설정 왕복, background output, 제거·호스트 종료, 기존 전체 화면 호스트의 소유권을 검증했습니다.
기존 외부 에디터의 실제 프로세스 실행/복귀 검사도 통과했습니다.
기록: `tests/llm/reports/ish-provider-pq3i1wu4/`. Hub Python 소스 스냅샷 해시 일치 확인.
백엔드 집중 191개 및 전체 1,242개도 통과했습니다.
기록: `tests/llm/reports/ish-provider-fk20x4xw/`. llm/ish 참조 Python 소스 190개의 해시 일치 확인.

## 2026-10-05 직접 편집과 외부 에디터 병행

설정 항목의 Enter 직접 편집/완료를 복구했습니다. 선택 모드의 `e`/`E`는 외부 에디터를
열지만 직접 편집 중에는 일반 문자로 입력됩니다. Ctrl+Space·Alt+Enter 줄바꿈, 방향키 커서
이동, ESC로 편집을 마치고 좌측 패널 이동도 유지합니다. 편집 칸의 최대 3행 높이,
저장값·적용값 표시 제거, 동일한 하단 버튼 너비와 호스트 고정값 보호는 그대로입니다.

Linux Python 3.12.14에서 집중 9개 및 Hub 전체 103개 통과.
실제 ish 호스트에 설치해 Enter 편집/완료, e/E 문자 입력과 외부 에디터 호출의 구분,
두 줄바꿈 조합, 커서/패널 이동, 외부 프로세스 종료 후 입력 복귀를 검증했습니다.
기록: `tests/llm/reports/ish-provider-h5lyqe6z/`. Hub 소스 스냅샷 해시 일치 확인.
백엔드 전체 1,242개도 통과(`tests/llm/reports/ish-provider-k6fmqmmn/suite.txt`).
llm 파일 175개는 이 스냅샷과 변경 없이 일치합니다. 초기 에디터 테스트의 모형 화면 크기 문제를
실제 ish 호스트 설치로 교정한 뒤 위 최종 Hub 검사를 수행했습니다. 백엔드 실행기에서 뒤이어
시작된 이전 스냅샷의 중복 Hub 검사는 최종 Hub 통과 확인 후 중단했습니다.

## 2026-10-05 설정 외부 편집과 간결한 값 표시

설정 필드의 저장값·현재 적용값/출처 표시 행을 제거했습니다. effective 조회는 확장 키와
호스트 고정 경로 판정에만 사용하고 초안에 복사하지 않습니다. 고정 필드는 계속 읽기 전용이며
부분 고정 JSON의 저장 검증도 유지합니다.

설정 입력칸의 `e`는 `ui/settings/editor.py`를 통해 현재 적용된 `GeneralSettings.editor`를
실행합니다. PTK `in_terminal()`로 호스트 터미널을 잠시 넘기고, 셸 해석 없이 argv로 실행합니다.
UTF-8 임시 파일을 사용하며 에디터 저장·종료 후 초안에 반영합니다. 실패 시 원래 초안을 유지하고,
종료 시 임시 파일을 정리합니다. 설정 저장은 별도 버튼 또는 Ctrl+S로 수행합니다.
에디터 명령은 VISUAL → EDITOR → vi 기본 선택을 유지합니다. 마지막 자동 개행은 원래 값에
없던 경우 한 개 제거합니다. 필드 Enter 편집과 설정용 Ctrl+Space/Alt+Enter 개행은 제거했습니다.

필드 높이는 현재 내용의 줄 수에 맞춰 1~3행으로 정하며 값이 변경되면 viewport 측정도 갱신됩니다.
프로젝트 하단의 저장·불러오기·취소·열기는 모두 너비 10을 사용합니다.

Linux Python 3.12.14에서 외부 편집 집중 4개와 Hub 전체 103개 통과.
실제 외부 프로세스/PTK 터미널 복귀와 키 입력 재개, 실패 시 초안 보존, 호스트 고정 필드,
저장 전 초안 유지, 1~3행 렌더링 및 숨긴 적용값이 화면에 없는 것을 검증했습니다.
기록: `tests/llm/reports/ish-provider-btmdgz_l/`. 해당 Hub 소스 스냅샷 해시 일치 확인.
llm 집중 191개 및 전체 1,242개도 통과했습니다. 기록은
`tests/llm/reports/ish-provider-ap8ja8m3/`이며 llm 파일 175개의 스냅샷 해시가 모두 일치합니다.

## 2026-10-05 로그 높이·자동 제목·기본 지침

Ctrl+L 활동 창의 본문은 30행을 기본으로 확보하고 터미널 높이에서 여백을 뺀 공간이
부족할 때만 줄입니다. 열린 창의 크기 변경에도 즉시 반영하며 출력은 포커스를 받지 않습니다.

자동 제목은 완료된 Run의 엔진과 Session 설정에서 모델·접속 인자를 가져옵니다.
대화용 JSON 응답 형식, 종료 문자열, 출력 토큰 제한은 제외하고 `_hub_title.completion`의
명시적 설정을 마지막에 적용합니다. 시도 식별자는 Run과 설정의 해시이므로 같은 실패를
반복 호출하지 않으면서 설정 수정 후 다시 시도합니다. 별도 Session/Run으로 실행하므로
원래 대화 기록은 변경하지 않습니다. 복제는 원본 엔진 이름만 Hub 메타데이터에 보관합니다.
호스트 런타임 객체만으로 구성한 모델은 제목 전용 JSON 설정을 따로 지정해야 합니다.

`backend/defaults.py`가 최초 실행과 설정 화면의 신규 프로젝트 생성을 함께 처리합니다.
`asset/guides.py`의 Loop 시스템 프롬프트와 코드 수정·오류 진단·문서 작성 Skill을 복사하며,
명시한 프롬프트와 기존 프로젝트는 유지합니다. Skill은 일반 컴포넌트 데이터여서 수정·삭제가
가능하고 재실행 시 복원하지 않습니다. 실행 도구를 추가하거나 실행 정책을 변경하지 않습니다.
llm의 공개 구성·컴포넌트 CRUD·Run API만 사용하며 llm 소스는 수정하지 않았습니다.

Linux Python 3.12.14에서 Hub 94개 검증 통과: `/tmp/hub-ui-7p3rh61s`.
실제 PTK 렌더러의 로그 크기 변경, 제목 설정 상속·명시적 덮어쓰기·실패 후 설정 수정,
원본 대화 보존, Skill 목록/본문 Tool 호출, 수정·삭제 후 재실행 보존을 확인했습니다.
llm 집중 191개와 전체 1,214개도 통과했습니다.
기록: `tests/llm/reports/ish-provider-qjl4j9a3/`. 현재 Hub와 llm 소스의 스냅샷 해시 일치 확인.
외부 유료 모델은 호출하지 않았습니다.

## 2026-10-05 ESC 패널 이동과 상태 행 여백

공통 페이지 상태 행은 좌우에만 1칸을 확보하고 높이는 1행으로 유지합니다.
페이지의 ESC는 좌측 패널로 이동하며, 설정 편집 중에는 편집을 마치고 이동합니다.
대화 중단은 Ctrl+X입니다. 팝업의 ESC 닫기 동작은 유지합니다.
설정의 Alt+방향키/PageUp/PageDown/Home/End 조합은 명시적으로 소비해
ESC 처리나 대화 스크롤로 넘어가지 않습니다.

Hub 표시 중 PTK ttimeoutlen/timeoutlen은 각각 20ms로 설정하고 숨김 시 원래 값을 복원합니다.
ESC의 eager 처리는 Alt 시퀀스를 깨뜨리므로 사용하지 않습니다.
Linux Python 3.12.14 Hub 89개 검증 통과, 스냅샷 `/tmp/hub-ui-4kzim_7l`.
단독 ESC, Alt 조합, Ctrl+X, 설정 편집/선택 상태, 호스트 타임아웃 복원,
상태 행 크기와 좌우 여백을 실제 PTK 입력·렌더러로 검증했습니다.

## 2026-10-05 공통 페이지와 팔레트

`ui/layout/TwoPanelPage`가 대화·설정의 상단 바, 좌측 패널, 메인 영역,
고정 1행 상태 표시와 하단 바를 구성합니다. `TaskProgress`는 작업 중 Rich 진행 표시를,
작업이 없으면 페이지의 알림을 같은 행에 렌더링합니다. 표시할 내용이 없어도 행을 유지합니다.
Ctrl+W는 좌측 패널 진입만 수행하고, 패널의 Tab·Space·Enter는 메인 영역으로 이동합니다.
메인 설정의 Ctrl+S는 현재 페이지 저장 성공 콜백에서 대화로 복귀하며 오류는 화면에 남깁니다.

`HubTheme`의 저장 가능한 색상은 background, foreground, accent1~3, comment입니다.
위젯의 의미별 색은 팔레트에서 계산하는 속성으로 제공하며 설정 폼에 별도로 노출하지 않습니다.
기존 Hub 환경설정은 주요 색만 팔레트로 읽습니다. sidebar_width는 별도 설정으로 유지합니다.

Linux Python 3.12.14에서 Hub 88개 검증 통과. 스냅샷 `/tmp/hub-ui-8fpi86ys`.
132×40 및 80×24 화면에서 상태 행 높이, 키 이동, 저장 실패/성공, 기존 팔레트 읽기를 확인했습니다.
수동 화면 캡처는 `tests/hub/manual/render_pages.py`, 결과는 `tests/hub/reports/page-*.png`입니다.
Hub 스냅샷 실행기는 백엔드 테스트 보조 코드 참조를 위해 tests/llm도 복사합니다.

## 소스 구성

루트에는 플러그인 진입점 `hub.py`와 독립 실행 진입점 `__main__.py`를 둡니다.
내부 모듈은 역할별 패키지에서 직접 가져오며 이전 루트 경로의 별칭 파일은 두지 않습니다.
`.ishrc.py`의 `hub_plugin.install`, `HubConfig`, `HubTheme`, `UserProfile`은 그대로 제공합니다.

```text
hub/
├── hub.py, __main__.py, __init__.py
├── backend/       # LLM facade, worker, 제목 생성, 명령·설정 서비스
├── config/        # 일반·프로필·테마, 환경설정·읽기 위치 저장
├── ui/            # Application, 화면·컨트롤러, 공통 위젯
│   ├── chat/      # 대화 렌더링, 자동완성, 검색·알림·실행 선택
│   └── settings/  # 설정 탐색, 페이지 초안, 스키마 입력 폼
├── asset/         # 공통 아이콘
└── locales/       # 언어팩과 번역 조회
```

테스트와 예제는 각각 `tests/hub/`, `examples/hub/`에 유지합니다.
이 분류는 모듈 위치와 import만 변경하며 실행·저장 책임은 유지합니다.

현재 선택 표시는 `▶`, 스크롤바 화살표는 `△`·`▽`, Markdown Rule은 U+2500(`─`)입니다.
`ui/widgets.py`의 Hub 전용 위젯이 대화·설정·선택창의 스크롤바 기호를 통일하며,
호스트와 PTK 전역 기본값은 수정하지 않습니다. 아이콘은 hub/asset/icon.py에서, 문구는 언어팩에서 관리합니다.

`ui/chat/search.py`는 Ctrl+F 팝업과 결과 이동을 제공하고 ConversationControl이 렌더링된 텍스트의
일치 위치와 강조를 관리합니다. 검색 중 새 출력이 도착해도 자동으로 마지막 줄로 이동하지 않습니다.
`ui/chat/notifications.py`는 UI 루프의 비포커스 Float를 사용합니다. HubRuntime이 Run lifecycle 이벤트를
최대 64개 알림 스냅샷으로 전달하고, UI는 순번으로 중복을 제거하며 현재 세션은 알리지 않습니다.
현재 프로젝트의 최신 Run 조회로 누락된 관찰 이벤트를 보정하고, 과거 이력은 최초 조회 시 기준으로만
기억합니다. 시작·종료가 매우 빠르거나 알림이 밀리면 중간 상태 대신 최신 상태만 표시할 수 있습니다.
알림은 도메인 저장소와 실행 상태를 변경하지 않습니다.

상단은 프로젝트 이름, 입력창 제목은 메인 모델을 표시합니다. Ctrl+E는 실행 방식 선택,
실행 중 Enter는 steering/후속 지시 선택, ESC는 중단입니다. 세션 목록의 c/d/e/r은 생성/소프트 삭제/이름 변경/복제입니다.
`backend/component_commands.py`는 명령 문법과 데이터 API 어댑터, `ui/chat/component_ui.py`는 결과·삭제 확인·설정 이동을
담당합니다. 스냅샷의 프로젝트 컴포넌트 선택으로 자동완성을 구성하고, 실행 시 공개 Project API로
선택 상태를 다시 확인합니다. 일반 CRUD는 ComponentData, RAG 문서는 RAGData를 사용합니다.
설정 저장은 기존 SettingsService를 통해 ProjectConfig에 반영합니다.

설정 화면은 Ctrl+W로 좌측/메인을 전환하고, 메인의 설정/버튼 영역은 Tab으로 순환합니다.
추가 기능 체크 영역은 없으며 새 프로젝트에 모든 내장 컴포넌트를 등록합니다. 중앙과 하단 프레임은
좌측 패널과 구분선을 제외한 PTY 너비를 명시적으로 사용합니다. 하단에는 제목을 붙이지 않으며
저장/다시 읽기/취소와 기존 프로젝트의 대화 열기를 표시하고, 좁은 화면에서는 버튼을 줄바꿈합니다.
좌측 전역 설정과 프로젝트 목록 모두 좌우 방향키로 공통 너비를 조절할 수 있습니다.
`ui/settings/pages.py`가 페이지별 초안을 소유하며 `ui/settings/form.py`의 필드는 Enter로
탐색/편집 모드를 전환합니다. 저장 버튼의 별표는 원본 입력값과의 비교로 결정됩니다.
컴포넌트 선택과 config는 ProjectHandle.asave의 components/expected_components 인자를 통해 한 번에 저장합니다.
Loop와 Graph를 기본 제공하며 등록된 공개 엔진은 모두 선택할 수 있습니다.
HubTheme.sidebar_width는 설정 및 대화 좌측 패널의 공통 너비이며, 미리보기 변경도 모양 초안에 포함됩니다.

`hub.hub.install(prompt)`은 ish Application에 하나의 Float를 등록합니다.
`HubMockup.container`는 `ConditionalContainer`이며 내부 모달 영역에 목록·대화·입력창을
배치합니다. 표시 전환은 포커스만 바꾸고 Application이나 이벤트 루프를 새로 실행하지 않습니다.
좁은 화면에서는 목록을 숨기고, 실행 상세는 사용자가 F6으로 켠 경우에만 표시합니다.
ish 입력 컨테이너의 높이는 한 줄일 수 있으므로 Float 높이는 렌더링마다 호스트
output의 현재 PTY 행 수를 읽습니다. 너비는 좌우 앵커로 채우며, PTK의 resize 처리로
크기 변경을 반영합니다.

config 없이 설치해도 HubRuntime이 LargeLanguageModel 공개 Facade를 사용합니다.
`preview=True`로 설치하면 샘플 데이터와 Session별 초안은 UI 인스턴스에만 존재합니다.
도메인 파일과 Run/Step 상태는 llm만 소유하며 Hub는 별도 이력 파일을 만들지 않습니다.

ish의 prompt_async는 명령마다 끝나므로 백엔드를 Application background task에
종속시키지 않습니다. BackendWorker가 전용 스레드·이벤트 루프 안에서 백엔드를
생성·사용·종료하고, UI에는 불변 스냅샷만 call_soon_threadsafe로 전달합니다.
UI 위젯은 UI 스레드에서만 수정합니다. 명령과 조회는 백엔드 잠금으로 직렬화하되
각 Session의 Run 실행은 llm worker가 독립적으로 수행합니다.

queued 관찰 구독은 dirty 표시만 합니다. 100ms 단위로 변경을 모아 공개 API에서
대화·Run·Step 상태를 재조회합니다. 1초 주기의 보정 조회가 dropped 알림이나 출력
배치 flush 이후 상태도 반영합니다. 텍스트 델타를 중복 transcript로 저장하지 않습니다.
읽고 있는 위치는 유지하고 대화 끝을 보고 있을 때만 새 출력으로 따라갑니다.

참조 CLI는 .ishrc를 읽은 후 prompt.run()을 호출합니다. 이 경계를 한 번만 감싸서
finally에서 현재 설치를 닫습니다. backend.shutdown()을 완료한 뒤 worker를 join하며,
프롬프트 단위 종료나 Ctrl+Q는 백엔드 종료로 취급하지 않습니다. reload/close는 이전
백엔드를 먼저 정리합니다. 직접 임베딩한 호스트는 close를 자신의 종료 경계에 연결해야 합니다.
ish.platform 참조 소스는 수정하지 않습니다. llm의 CompletionResult에는 공급자가 공개한
reasoning_content를 추가하여 COMPLETION 이벤트의 기존 저장 경로로 기록합니다.

기본 연결은 LoopEngine/ToolComponent이며 HubConfig의 factory로 추가 엔진/Component를
등록합니다. 엔진은 요청 제출 시 명시하고 Ctrl+E 선택은 아직 보내지 않은 요청에 적용합니다.
`ui/chat/execution.py`는 엔진 선택과 Graph 전용 Workflow 패널을 구성합니다. `HubRuntime.execution_catalog`
가 공개 EngineRegistry와 프로젝트 ComponentData에서 실행 대상 목록을 읽고, UI에는 JSON 데이터만
전달합니다. 별칭과 관계없이 GraphEngine 인스턴스를 식별합니다. Ctrl+E는 매번 목록을 새로 읽으며,
조회 중 닫힌 창이나 다른 세션으로 전환한 뒤 도착한 결과는 무시합니다.
Workflow ID는 전송 시 `engine_options`로 복사해 전달하고 llm이 QUEUED 메시지부터 영속화합니다.
공유 엔진이나 프로젝트 설정을 수정하지 않습니다. UI 선택은 프로젝트·세션·엔진별 메모리 상태이며,
요청 옵션의 재시작 복구는 백엔드 책임입니다. Workflow 정의는 백엔드의 실행 시작 시 조회 계약을 따릅니다.
Ctrl+D는 활성 Run/수신 대상 조회 후 steer()를 호출합니다. 여러 대상이면 사용자가 선택합니다.
종료 경쟁은 서비스가 검증하며 실패 시 입력을 보존합니다. 승인 응답·재개 UI와
Project 설정 편집 화면은 후속 범위입니다. 모델 설정은 Project에 저장하며 재접속 시
기존 선택을 덮어쓰지 않습니다. 파일 대화가 기본이고 persisted memory 선택도 유지합니다.

대화는 `ChatMessage`로 역할·본문·표시 시간을 구분합니다. 사용자 메시지는 셀 너비를
기준으로 오른쪽 정렬하고 양쪽 본문과 입력 미리보기는 Rich Markdown → Segment → PTK fragment로
변환합니다. Console의 직접 출력이나 ANSI 스트림 삽입은 하지 않습니다.
전용 UIControl이 폭별 렌더 캐시와 스크롤 위치를 관리합니다.
Alt+이동키는 viewport의 첫 줄을 직접 이동하며 입력 커서 위치와 독립적으로 스크롤합니다.
ConversationControl은 포커스 불가이고 마우스 클릭·휠도 포커스를 바꾸지 않습니다.

한국어/영어 UI 문구는 locales 아래에 두고 Language가 조회합니다. 사용자/모델 콘텐츠와
서비스 원문 오류는 번역하지 않습니다. 자동완성은 별도 스레드에서 명령, 엔진 이름과
file_root 하위 파일명을 조회합니다. 파일 내용을 읽거나 모델 컨텍스트에 자동 첨부하지 않습니다.
입력 중 자동완성을 시작하고 composer에 포커스가 있을 때만 드롭다운을 표시합니다.
방향키 ↑↓는 완성 후보를 선택하고 Tab은 선택한 후보(미선택 시 첫 후보)를 확정합니다.
목록이 닫혀 있으면 Tab으로 다시 엽니다. Ctrl+Space는 미확정 후보를 취소하고 개행합니다.
Ctrl+W는 세션 목록과 입력창 사이를
이동하며 선택 중인 완성을 취소해 입력 원문을 유지합니다. 좁은 화면에서 세션 포커스를
요청하면 숨겨진 목록을 임시로 표시합니다. 출력·미리보기·상세는 포커스 순환에서 제외합니다.
대화상자에서는 기존 PTK의 컨트롤 간 Tab 이동을 사용합니다.
ish 설치에서는 같은 navigation_keys를 Application에도 등록하여 개별 영역의 modal
키 탐색 경계에 막히지 않게 합니다. Hub가 숨겨졌거나 대화상자가 열리면 이 바인딩은
비활성화되며, 해제 시 원래 Application 바인딩을 복원합니다. 포커스 영역 제목은
`▸`와 theme accent로 표시합니다.

이름 없는 Session은 hub_auto_title 메타데이터를 저장합니다. 응답 후 별도 내부 Session의
_hub_title Engine Run으로 이름을 만들고 SessionHandle.asave()로 저장합니다. 요청/출력은
기존 llm 저장소에 남고 사용자 대화에 섞이지 않습니다. 작업 Session은 목록에서 제외하고
종료 후 소프트 삭제합니다. 취소와 실패는 호출 자원 정리 후 표시하며 동일 응답을 재시도하지
않습니다. 복제는 source.run.shutdown() 후 SessionHandle.aclone()을 사용하고 원본 실행기를
다시 엽니다. 활성 Run/대기 요청이 있으면 복제를 거절합니다.

`HubTheme`는 UI 전용 설정이며 llm의 ProjectConfig에 저장하지 않습니다.
기본 전경·배경은 terminal default이고, PTK DynamicStyle과 Rich SyntaxTheme이 같은
색상 설정을 사용합니다. 설치 인자 또는 `set_theme()`로 적용하며 설정 저장은 후속 범위입니다.

사용자 메시지는 Rich의 오른쪽 채움 공백을 제거한 뒤 내용 폭의 배경 블록으로 우측에
배치합니다. 사용자 블록만 HubTheme.user_background를 사용합니다. ChatMessage는
id/status/time/author를 분리하며, 시간은 원본 Message.created_at입니다. UserProfile의
기본 display_name은 OS 계정명이며 HubConfig에서 교체합니다.
입력 Markdown 미리보기는 F5로 여는 고정 높이 패널이며 초기 상태는 접힘입니다.

읽기 위치는 Hub 소유 ViewStateStore가 workspace/.hub/view-state.json에 원자적으로
저장합니다. Project와 Session별 메시지 ID, 메시지 내 줄 오프셋 및 fallback 줄 위치만
저장하며 대화 본문이나 런타임 객체는 넣지 않습니다. 스크롤 저장은 UI 루프에서 250ms
묶음 처리하며 전환/숨김/종료 시 flush합니다. 스냅샷이 도착하기 전에는 복원 위치를
pending으로 보존하여 빈 캐시나 연결 초기화가 위치를 0으로 덮어쓰지 않게 합니다.
숨긴 동안의 스트리밍은 저장 위치를 강제로 최신 응답 끝으로 옮기지 않습니다.

## 2026-10-03 UI 확장 검증

- Linux Python 3.12.14의 Hub 24개 검사 통과. 스냅샷 `/tmp/hub-ui-19rybsd5`.
- llm 집중 48개와 전체 1,151개 검사 통과. 집중 검사는 전체에 포함됩니다.
  기록은 `tests/llm/reports/ish-provider-e64bs0e4/`에 있으며 현재 llm/test Python 파일과
  스냅샷 해시가 일치함을 확인했습니다.
- 실제 PTK 입력 파이프와 renderer, 참조 ish Prompt 및 로컬 HTTP SSE로 검증했습니다.
  외부 모델 API는 호출하지 않았습니다. 자동 제목과 reasoning 검사는 결정적인 모델 fixture입니다.
- `tests/hub/manual/render_features.py`로 한국어/영어, 생성/엔진 대화상자, 자동완성 메뉴를
  렌더링했습니다. `tests/hub/reports/features-*.png`는 모델 호출 없는 화면 확인용 데이터입니다.

## 2026-10-03 키 이동·자동완성 변경 검증

- 추가 지시를 Alt+I로 이동하고 Tab/Shift+Tab을 다음/이전 영역 이동에 사용합니다.
- 명령·엔진·경로 후보를 입력 중 자동 표시하며, 영역 이동은 후보 선택을 취소합니다.
- Linux Python 3.12.14에서 Hub 25개 검사 통과. 스냅샷 `/tmp/hub-ui-xgt_amic`.
- llm 집중 48개 및 전체 1,151개 검사 통과. 기록: `tests/llm/reports/ish-provider-ectgepmq/`.
- 실제 PTK renderer의 자동 드롭다운 화면: `tests/hub/reports/navigation-automatic-completion.png`.

## 2026-10-03 포커스 이동 보강 검증

- 참조 ish의 기본 영역 순환은 기존 코드에서도 통과했으므로 사용자 환경의 원인을
  단정하지 않았습니다. Application에도 navigation_keys를 등록하고 활성 영역 제목에
  포커스 표시를 추가했습니다.
- 영역에 별도 modal 키 탐색 경계를 둔 회귀 사례에서 기존 부모 전용 바인딩은 목록에
  멈추고, Application 바인딩 추가 후에는 목록 탈출·스크롤·입력 복귀가 통과했습니다.
- Linux Python 3.12.14에서 Hub 26개 검사 통과. 스냅샷 `/tmp/hub-ui-0o0alynm`.
- llm 집중 48개 및 전체 1,151개 검사 통과. 기록: `tests/llm/reports/ish-provider-_7cmvge_/`.
- 현재 Python 소스와 검증 스냅샷 해시 일치 확인. 포커스 화면은
  `tests/hub/reports/focus-navigation-transcript.png`에 기록했습니다.

## 2026-10-03 메시지 블록·읽기 위치 검증

- Linux Python 3.12.14에서 Hub 31개 검사 통과. 스냅샷 `/tmp/hub-ui-ypt5og8b`.
- 짧은 사용자 Markdown의 오른쪽 실제 내용 위치, 배경색, 사용자 프로필/계정명,
  원본 생성 시각과 상태를 검증했습니다. 읽기 위치는 자동 저장, 세션 전환, 숨김 중
  추가 응답, 마지막 선택이 다른 프로세스 재생성, 폭 변경/메시지 삽입까지 확인했습니다.
- 132×40, 80×24와 미리보기 열린 화면을 실제 PTK로 렌더링했습니다.
  `tests/hub/reports/message-layout-*.png`는 모델 호출 없는 fixture 화면입니다.
- 최초 백엔드 전체 실행 `ish-provider-71z707zi`에서 기존 RAG 동시성 peak 검사 하나가
  `1 != 2`로 실패했습니다. 백엔드 소스 변경 없이 해당 모듈 17개 및 전체 1,151개
  재실행은 통과했습니다. 기록: `tests/llm/reports/ish-provider-fi1u6kot/`.
- 현재 Python 소스와 Hub/llm 검증 스냅샷 해시 일치를 확인했습니다.

## 설정 화면 구조

Ctrl+S는 기존 ish Application에 등록하며 새 Application을 만들지 않습니다.
Hub 루트의 DynamicContainer가 대화와 SettingsScreen을 교체합니다. 설정 화면은
왼쪽 전역/프로젝트 탐색과 오른쪽 ScrollablePane으로 나누며, 컴포넌트 폼은
ConditionalContainer로 활성화 상태에 따라 표시합니다. 동적 페이지와 확인창의
Tab/Shift+Tab은 Application 수준 navigation_keys에서 처리합니다.

SettingsService는 기존 BackendWorker의 직렬 명령 경로에서 실행합니다.
project_schema/aconfiguration으로 입력 폼을 만들고 공개 validate/save API로
설정 버전을 검증해 저장합니다. 선택된 컴포넌트는 components.aselect로 별도 적용합니다.
폼은 빈 값/명시적 null/기존 알 수 없는 키를 보존하며 스키마 기본값을 합성하지 않습니다.
스키마의 복합 분기는 JSON textarea로 편집합니다. 실행 정책이나 컴포넌트 데이터를
UI에서 직접 파일로 쓰지 않습니다.

PreferencesStore만 UI 소유 프로필/테마를 workspace/.hub/preferences.json에 저장합니다.
프로젝트 전환 시 읽기 위치 캐시를 먼저 저장한 뒤 대상 Project의 위치를 불러옵니다.
전환은 다른 Project의 Run을 중단하지 않습니다. 제목 생성은 원본 Session의 Project에
묶어 두며, 복제/삭제는 실행·대기 요청을 검사하고 열린 유휴 worker를 정리합니다.
삭제는 소프트 삭제이고 현재 대화 Project는 다른 Project로 전환한 뒤 삭제합니다.

## 2026-10-03 설정 화면 검증

- Linux Python 3.12.14에서 Hub 36개 검사 통과. 스냅샷 `/tmp/hub-ui-3_vn6pqg`.
- llm 설정 집중 14개 및 전체 1,151개 검사 통과.
  기록: `tests/llm/reports/ish-provider-_algvi_o/`. 백엔드 Python 소스 해시 일치 확인.
- 실제 PTK 입력으로 첫 Ctrl+S 연결, 프로필·테마 저장/재실행, 타입 오류, 컴포넌트
  선택과 미저장 입력 보존, 프로젝트 생성·복제·삭제·대화 전환, 채팅 초안 보존을 확인했습니다.
  참조 ish에서 Ctrl+S 설치·해제 및 기존 바인딩 복원도 확인했습니다.
- 실제 renderer의 132×40 및 80×24 화면은 `tests/hub/reports/settings-*.png`입니다.
  `tests/hub/manual/render_settings.py`의 테스트 컴포넌트로 렌더링했으며 외부 모델을 호출하지 않았습니다.

## 2026-10-04 ish 팝업 렌더링 수정

참조 ish Prompt에서 자동완성은 활성화되고 엔진 선택 키도 동작하지만 최종 renderer 셀에는
팝업이 사라지는 문제를 재현했습니다. PTK의 Float 지연 렌더링이 컨테이너 내 순번으로
절대 그리기 우선순위를 정하므로, 호스트의 네 번째 Float인 Hub 본문이 내부 팝업보다
나중에 그려졌습니다. 독립 실행처럼 Hub가 첫 Float일 때는 드러나지 않던 문제입니다.

Hub 내부 FloatContainer에 z_index=0을 명시하여 본문을 먼저 그리고 팝업을 얹도록 했습니다.
자동완성의 attach_to_window는 입력창으로 고정했습니다. Float의 z_index 숫자만 높이는
방식으로는 PTK의 지연 렌더링 우선순위를 바꾸지 못합니다. 호스트 코드는 변경하지 않았습니다.

`tests/hub/test_overlays.py`는 실제 ish에서 설정 왕복, 132×40 → 80×24 크기 변경,
자동 드롭다운과 엔진 선택창의 최종 셀 가시성 및 엔진 선택을 검사합니다.
수정 전 메뉴 가시성 검사 실패, 수정 후 Hub 전체 37개 통과를 확인했습니다.
스냅샷: `/tmp/hub-ui-1nqqv69x`. 실제 renderer 캡처는 `tests/hub/reports/overlays-ish-*.png`,
수정 전 재현 화면은 `*-before.png`, 재현 스크립트는 `tests/hub/manual/render_overlays.py`입니다.
Linux Python 3.12.14에서 llm 집중 14개와 전체 1,151개도 통과했습니다.
백엔드 보고서: `tests/llm/reports/ish-provider-7av13rjr/`. Hub 및 백엔드 Python 소스와
검증 스냅샷의 해시 일치를 확인했습니다.

## 2026-10-04 예약 상태·어시스턴트 소요 시간

한국어 QUEUED 표시를 예약으로 바꾸고 IDLE은 유휴, PENDING은 실행 전으로 구분합니다.
예약은 실행 순서를 기다리는 요청이며 시각 기반 스케줄이 아닙니다. 상태 코드와 저장 형식은
변경하지 않고 언어팩 및 UI만 변경합니다.

ChatMessage.elapsed_seconds는 연결된 Run.started_at/ended_at에서 계산합니다.
RUNNING은 현재 시각까지 갱신하며 종료 상태는 저장된 종료 시각으로 고정합니다.
예약 대기 시간은 제외하며, 종료된 Run의 reasoning과 소요 시간은 함께 UI 캐시합니다.
사용자 메시지의 생성 시각은 유지하고 Assistant 헤더만 소요 시간으로 렌더링합니다.
Run이 없는 복제 메시지나 retention으로 제거된 실행 기록은 소요 시간 알 수 없음으로 표시합니다.

Hub 40개 검사 통과: `/tmp/hub-ui-ynsmf9m_`. 스트리밍 증가, 종료 후 고정, 예약 시간 제외,
중단·실패·취소·일시 정지, 기록 누락 및 실제 저장소 재실행 복원을 확인했습니다.
실제 ish renderer 캡처: `tests/hub/reports/message-duration.png` (샘플 데이터).
Linux Python 3.12.14에서 llm 집중 17개 및 전체 1,151개도 통과했습니다.
기록: `tests/llm/reports/ish-provider-s5fjqu5j/`. Hub/백엔드 Python 스냅샷 해시 일치 확인.

## 2026-10-04 바 배경색·입력 영역 테두리

HubTheme에 header_background/footer_background/bar_foreground를 추가했습니다.
기존 preferences.json은 누락된 필드에 기본값을 적용하며 모양 설정 폼에도 자동 노출합니다.
상단의 프로젝트/모델 fragment는 hub.header 하위 스타일로 두어 배경 초기화를 방지합니다.

입력창과 Markdown 미리보기를 각각 PTK Frame으로 감쌌습니다. 미리보기 높이 제한과
24줄 미만에서 숨기는 동작, 입력창 포커스·자동완성 앵커는 유지합니다.
HubMarkdown은 인스턴스별 hr 렌더러만 교체해 U+2013(–) Rule을 그립니다.
코드 블록·일반 문자열 및 다른 Rich 사용자의 전역 요소 매핑은 변경하지 않습니다.

Linux Python 3.12.14에서 Hub 42개 검사 통과. 스냅샷 `/tmp/hub-ui-je001x1j`.
실제 ish renderer에서 바 전체 셀의 배경색, 박스 테두리, resize 후 초안 보존을 검증했습니다.
132×40/80×24/60×14 및 밝은 터미널 배경의 화면: `tests/hub/reports/chrome-*.png`.
재현 스크립트: `tests/hub/manual/render_chrome.py`.
llm 집중 14개 및 전체 1,151개 검사도 통과했습니다.
기록: `tests/llm/reports/ish-provider-z5tuqrcp/`. Hub/백엔드 Python 스냅샷 해시 일치 확인.
