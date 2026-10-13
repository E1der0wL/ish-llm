# 컴포넌트 관리 팝업

프로젝트에 등록된 컴포넌트만 명령과 자동완성에 나타납니다.

| 명령 | 가져오기 |
| --- | --- |
| `/tools config` | 기존 Python Tool 패키지 디렉토리 |
| `/skill config` 또는 `/skills config` | Markdown·텍스트 지침 또는 Skill JSON 파일 |
| `/rag config` | UTF-8 텍스트 문서, Markdown 포함 |
| `/workflow config` 또는 `/workflows config` | schema_version 1 Workflow JSON 파일 |

위쪽 경로에 파일/디렉토리를 입력하고 Enter를 누르면 가져옵니다. 상대 경로는
ish의 현재 작업 디렉토리를 기준으로 합니다. 원본 파일은 변경하지 않으며 동일한 ID가
이미 있으면 덮어쓰지 않습니다. 파일 이름에서 ID를 만들고, `SKILL.md`는 부모 폴더 이름을
사용합니다. 경로 자동완성과 가져오기는 UI 스레드를 막지 않습니다.

Tab으로 목록에 이동하고 방향키로 선택합니다. Tool은 Enter로 기존처럼 파일 탐색기를
엽니다. Skill·Workflow·RAG는 Enter로 일반 설정의 에디터에서 JSON을 수정합니다.
저장 시 컴포넌트의 스키마와 읽어 온 버전을 검증합니다. 실패한 수정 초안은 팝업을
닫기 전까지 유지합니다. RAG는 title/content/metadata만 편집하며 문서와 검색 색인을
함께 갱신합니다. RAG 등록·수정에는 프로젝트의 임베딩·추출 모델 설정이 필요합니다.

`d`는 삭제 확인 창을 엽니다. 참조 중인 Skill 등 백엔드에서 삭제를 금지한 항목은
오류 알림으로 이유를 표시하고 유지합니다. 내장 Tool은 Space 활성/비활성 조작을 유지합니다.
항목이 수정 시각을 제공하지 않으면 날짜 칸에 `—`를 표시합니다.

공통 구조는 `hub/widget/forms/component_manager.py`의 `ComponentManager`가 소유합니다.
`ToolManager`와 `RecordManager`는 항목 활성화/편집만 확장합니다.
Hub의 `RecordManagement`는 공개 ComponentData의 CRUD를, `DocumentManagement`는
RAG의 문서 전용 API를 사용합니다. 컴포넌트 내부 저장 파일을 직접 수정하지 않습니다.
관리 작업은 프로젝트 핸들을 고정해 실행하며 다른 세션의 스트리밍을 잠그지 않습니다.
