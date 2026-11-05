# 컴포넌트 관리 팝업

프로젝트에서 선택한 tools·skills·mcp·rag·agents·workflows·prompts·memory만 명령과 자동완성에 나타납니다.
명령 이름은 단수 명사이며 `/tool`, `/skill`, `/mcp`, `/rag`, `/agent`, `/workflow`, `/prompt`, `/memory`를 사용합니다.
인자 없이 관리 창을 열고 도움말은 팝업 안의 도움말 버튼으로 봅니다. `config`, `help` 하위 명령과
`/agent ID` 초대 명령은 제공하지 않습니다. 초대는 대화 상대 목록에서 처리합니다.

관리 창은 보통 크기로 통일합니다. 가져오기 입력 위에는 컴포넌트와 파일/경로 종류만
표시하며, 별도 불러오기 버튼 없이 창을 열거나 데이터를 변경할 때 목록을 갱신합니다.
버튼은 테두리 안에 모으고 조작 안내는 그 안의 왼쪽 하단에 주석색으로 표시합니다.
진행 바와 작업 상태는 창 최하단의 한 줄을 공유합니다. Workflow·Agent 생성/편집은 큰 창입니다.

| 명령 | 가져오기 |
| --- | --- |
| `/tool` | Python Tool 패키지 디렉토리 |
| `/skill` | Markdown·텍스트 지침 또는 Skill JSON |
| `/mcp` | transport와 command 또는 url을 포함한 서버 JSON |
| `/rag` | UTF-8 텍스트·Markdown·PNG·JPEG |
| `/agent` | Agent JSON |
| `/memory` | 메모리 JSON 또는 팝업에서 직접 생성 |
| `/prompt` | messages 배열을 포함한 Prompt JSON |
| `/workflow` | schema_version 1 Workflow JSON |

경로 입력 후 Enter로 가져옵니다. 상대 경로는 ish의 현재 작업 디렉토리 기준입니다.
원본 파일을 변경하지 않고 기존 ID를 덮어쓰지 않습니다. 파일 이름에서 ID를 만들고
`SKILL.md`는 부모 폴더 이름을 사용합니다. 경로 자동완성과 가져오기는 UI 스레드를 막지 않습니다.

Tab으로 목록에 이동하고 방향키로 선택합니다. Tool은 Enter로 파일 탐색기를 엽니다.
Prompt·Agent·Workflow는 **생성 / 편집 / 삭제** 버튼을 제공합니다. 생성 시 고유 이름을
입력합니다. Prompt는 JSON 편집창에서 작성합니다. Workflow는
[그래프 편집기](workflow-editor.md)에서 노드·연결과 선택 노드의 설정을 편집하고,
고급 JSON에서 전체 정의를 수정합니다. 진입 노드는 ▶, 연결선에는 포트·분기 이름을 표시합니다.
노드 선택·배치 변경 시 그림을 갱신하며 중첩된 반복 본문도 따로 열어 편집합니다.
실행 없는 검증에서 백엔드의 구조·참조 진단을 확인하고 해당 노드로 이동합니다.
미완성 초안은 유지하고 저장은 기존 공개 API와 버전 충돌 검사를 사용합니다.
팝업 편집의 최대 1,048,576자 제한은 유지하며, 캔버스는 보이는 셀만 그립니다.

나머지 레코드는 일반 설정의 에디터에서 JSON을 수정합니다. 스키마와 읽어 온 버전을 검증하고,
실패한 초안은 팝업을 닫기 전까지 유지합니다. RAG 편집 필드는 title/content/metadata/description이며
공개 문서 API로 색인도 갱신합니다. MCP 정의 가져오기는 서버를 실행하거나 연결하지 않습니다.

MCP의 **연결·조회**는 서버에 접속해 도구 목록을 준비한 뒤 연결을 닫습니다.
이어지는 선택 창에서 에이전트와 도구를 골라 적용하며, 다음 Run부터 사용합니다.
목록 조회와 선택 적용은 도구 실행 승인과 별개입니다. 연결·변경 충돌·오류 처리 경계는
[MCP 연결·Memory 처리](mcp-memory-integration.md)를 따릅니다.

`d`는 삭제 확인 창을 엽니다. 참조 중인 항목 등 백엔드가 삭제를 금지한 항목은 유지하고
오류 이유를 표시합니다. 내장 Tool은 Space 활성/비활성 조작을 유지합니다. 수정 시각이 없으면
`—`를 표시합니다. 설정 버튼은 해당 컴포넌트 페이지로 이동하며 필터를 추가하지 않습니다.

공통 레이아웃은 `hub/widget/forms/component_manager.py`의 `ComponentManager`가 소유합니다.
`ToolManager`·`RecordManager`가 항목 활성화/편집을 확장하고, `DefinitionManager`는 정의 CRUD를
`DefinitionEditor`·`AgentEditor`·`WorkflowEditor`에 연결합니다. `RAGManager`는 등록 관찰과
재시도·취소·삭제·이미지 변환 선택을 추가합니다. Hub의 `RecordManagement`는 공개 ComponentData의
CRUD를, `DocumentManagement`는 RAG 문서 API를, `AgentManagement`는 snapshot/revise를 사용합니다.
컴포넌트 내부 파일을 직접 수정하거나 지우지 않습니다. 관리 요청은 프로젝트 핸들을 고정하며,
다른 세션의 스트리밍을 위한 Hub 화면 잠금을 점유하지 않습니다.

Agent는 `AgentEditor`가 엔진별 `agents.adescribe(engine=...)`의 공개 스키마를 조회해
기존 `SchemaForm`의 제목·설명·입력칸으로 편집합니다. 엔진을 바꾸면 해당 스키마만 표시하며
다른 엔진의 초안은 자동 변환하지 않습니다. 모델·API 키·주소는 Agent의 completion에 저장합니다.
Agent 편집은 홈/설정과 같은 패널 너비를 사용하고 `에이전트 • 현재 페이지`와
`에이전트 : 이름` 제목을 표시합니다. 설정 필드에서 ↑↓로 항목 이동, ←로 패널 복귀,
Enter로 직접 편집, d로 비우기, e로 외부 에디터 편집을 사용합니다.
Agent 편집의 진행·상태 한 줄은 좌측 패널을 제외한 메인 영역 맨 아래에 둡니다.
공통 페이지가 이 줄을 소유하고 팝업은 외부 상태 줄을 중복으로 만들지 않습니다.

`/agent`는 현재 Project의 Agent와 [다른 플러그인이 등록한 Agent](plugin-api.md)를 함께
표시합니다. 별도 Agent 종류나 관리 창을 만들지 않으며, 플러그인 이름으로 소속만 구분합니다.
같은 ID가 여러 Project에 있어도 편집·삭제는 선택한 항목의 원래 소유 Project와 revision을
유지합니다. 새로 생성하거나 가져온 Agent는 현재 Project에 저장합니다.
공개 스키마가 지원하는 엔진에서는 일반 페이지의 완료 검증 선택으로 상속·사용·사용 안 함을
지정할 수 있습니다. 기존 세부 검증 예산과 상위 정책의 제한은 그대로 유지합니다.

## Memory 관리

`/memory`는 프로젝트·세션 범위의 candidate/confirmed 메모리를 함께 표시합니다.
생성, JSON 본문 편집, 삭제를 공통 정의 편집 팝업에서 제공합니다. 새 메모리는 기본적으로
프로젝트 범위의 confirmed note이며 content에 내용을 입력합니다. session 범위를 사용할 때는
해당 session_id를 지정합니다. 수정·삭제는 마지막 조회의 generation/revision을 검증하며,
다른 작업이 변경한 메모리를 덮어쓰지 않습니다. 삭제는 백엔드의 복구 가능한 삭제를 사용합니다.
내부 생성 시각·출처·리비전과 같은 관리 필드는 편집 JSON에서 제외합니다.

## RAG 등록·진행·재개

`/rag`의 등록은 `aenqueue_document` → `arun_job`을 사용합니다. 완료 문서 외에
대기·실행·실패·취소 작업을 목록에서 볼 수 있습니다. 실패 항목도 유지하며 **재시도**로 같은 Job ID의
저장된 원문·완료 청크·추출 체크포인트를 재사용합니다. 원본 파일을 다시 읽거나 새로운 Job을 만들지
않습니다. 실패 항목과 같은 ID로 새로 등록하려면 기존 작업을 먼저 삭제합니다.

`ajob_progress`의 실제 단계·completed/total/unit으로 표시합니다. 임베딩은 청크, 관계 추출은
처리한 배치의 비율입니다. 전체 등록 비율이나 남은 시간을 추측하지 않으며 total=None인 색인·공개
단계는 불확정 진행 바로 표시합니다. best_effort 추출의 배치 완료가 관계 추출 성공을 뜻하지는 않습니다.

임베딩·추출 소요 시간은 백엔드 통계입니다. 별도의 `관찰 시간`은 현재 Hub에서 실제로 관찰한 단계들의
경과 시간이며, 재시작 전에 놓친 단계 시간이나 짧아서 관찰하지 못한 단계 시간을 만들어내지 않습니다.
상태는 0.25초 간격의 공개 API 조회와 진단 이벤트로 갱신하고, 경과 시간은 0.1초 간격으로 갱신합니다.
색인 공개는 백엔드 workspace 직렬화 경계이므로 조회가 공개 완료까지 기다릴 수 있습니다. 이때도
기존 상태의 경과 시간·진행 애니메이션은 유지합니다. 진행 조회 오류가 등록 자체를 중단시키지는 않습니다.

**작업 취소**는 대기/실행 중인 Job에 취소를 요청합니다. 현재 Hub가 실행하는 Job은 실행 coroutine도
취소하고 백엔드의 종료 처리를 기다립니다. 다른 실행자의 Job은 백엔드가 다음 취소 검사 지점에서
반영합니다. 취소된 Job은 재개하지 않습니다. 팝업을 닫거나 최소화하는 동작은 등록을 취소하지 않습니다.

**삭제** 또는 `d`는 실패·취소 Job의 저장 원문과 체크포인트 제거를 확인받습니다. 최신
`acheckpoint_plan`의 revision을 검증하고 필요한 경우 `aabandon_job` 후 `adelete_job`을 호출합니다.
실행 중인 worker는 백엔드 lease로 보호하며, 완료된 문서·활성 검색 색인·공유 벡터 캐시는 유지됩니다.
완료 문서의 삭제는 기존 문서 삭제 API를 사용합니다. 오래된 직접 등록에는 Job이 없으므로 재개할 수 없습니다.

팝업을 다시 열면 공개 Job 목록과 현재 Hub의 관찰 상태를 복원합니다. 열려 있는 팝업만 실행 중인
목록을 1초 간격으로 갱신합니다. 문서 목록은 본문·벡터가 없는 catalog를 페이지 단위로 읽습니다.
실패는 공통 ErrorInfo 계약과 UI 오류 안내를 사용하며 별도의 RAG 재시도 예산을 만들지 않습니다.

이미지 등록과 검색 우선순위의 설정·사용법은 [RAG 사용 안내](rag.md)를 참고하세요.

## 생성·수정 시각

`llm.core.record_times.RecordTimes`가 생성·수정 시각의 의미와 검증을 소유한다.
Hub는 공개 CRUD에 사용자 정의를 그대로 전달하고 관리 시각을 metadata에 삽입하지 않는다.
기록과 원자적 저장은 Backend가 담당하며, 목록의 날짜는 소유자의 공개 조회 결과로 표시한다.
[영속 기록 시각 계약](../llm/component-times.md)을 따른다.

| 자원 | 날짜 소유권과 연결 경로 |
|---|---|
| Agent | `project.agents.aget_times(id)`를 `RecordTimes.read()`로 읽음. 일반·플러그인 Agent 목록이 같은 경로를 사용 |
| Workflow·Prompt·MCP·Skill·Python Tool | Component의 `aget_times(id)`를 `RecordTimes.read()`로 읽음 |
| RAG 문서 | `alist_catalog()`의 문서 시각을 `RecordTimes.from_record()`로 읽음. 본문 추가 조회·완료 Job 날짜 대체 없음 |
| Memory | 공개 Memory 기록의 기존 `updated_at` 표시 유지 |
| Goal·Vision·Refinement·Data | 해당 Backend 저장 Owner가 시각을 기록. Hub 생성 도구·후처리에서 날짜를 중복 주입하지 않음 |
| 내장 Tool·RAG Job | 내장 Tool은 사용자 관리 자료의 수정 날짜가 없으며, Job은 기존 작업 진행 시각을 표시 |

기본 assistant·planner·Workflow 생성, 프리셋과 플러그인 Agent 등록, MCP의 Agent 도구 선택 변경,
모델의 자료 생성 도구도 이 경계를 따른다. Skill 가져오기는 `aimport_directory()` 한 번으로
완료하며 날짜를 넣기 위한 snapshot 조회·Markdown 재저장을 하지 않는다.

Hub는 원본 metadata나 Skill frontmatter에 이미 존재하는 사용자 날짜를 변경·삭제하지 않고,
관리 시각으로 해석하지도 않는다. 기존 필수 시각이 없는 저장 자료를 현재 시각이나 파일 mtime으로
보정하지 않는다. Backend 오류를 그대로 기존 오류 처리 경로로 전달한다.

동일 내용 저장·목록 조회는 수정 시각을 바꾸지 않으며, 실제 변경이 성공한 경우만 Owner가
갱신한다. 날짜는 revision/CAS를 대체하지 않는다. 편집에는 기존 snapshot의 version,
Agent revision, RAG generation/revision을 계속 전달한다. Run·Step 실행 시간과 Job 진행 시간,
Hub 자체 복구 백업 기록의 시각은 별도 의미이며 변경하지 않는다.

### 2026-11-05 전환 검토

- 선언·검증 Owner는 `RecordTimes` 하나다. Hub의 `ResourceTimestamps`와 날짜 전용
  Skill 재저장 helper를 제거했다. 새로운 날짜 필드·기본값·호환 Reader는 만들지 않았다.
- 관찰 시각은 목록의 `modified` 표시값으로만 전달한다. 저장 승인·CAS·재개 판단에는
  snapshot의 기존 version/revision을 사용하며 날짜를 사용하지 않는다.
- 자료 Writer·manifest·저장 경로는 Backend에 남아 있다. Hub의 관리 창·기본 자료·프리셋·
  플러그인 등록·모델 Tool·Goal 후처리는 원래 공개 CRUD를 호출한다.
- RAG는 catalog가 가진 시각을 사용해 본문을 추가 조회하지 않는다. 다른 관리 목록은 각
  Owner의 `aget_times()`를 호출하며, 검증을 생략하는 자체 캐시나 파일 읽기는 도입하지 않았다.
- Skill 가져오기는 Backend 내부에서 파일·권한 정보를 추가 확정할 수 있다. 따라서 가져오기
  완료 시의 생성·수정 시각을 동일하게 강제하지 않고 반환된 두 시각을 그대로 사용한다.
- Linux Python 3.12.14의 격리 소스에서 집중 83개를 실행했다. 81개 통과,
  가져오기 시각의 동일함을 가정한 두 검사 실패는 `focus01`에 그대로 보존했다.
  그 가정을 Owner 관찰값 기준으로 수정한 `focus02`는 관련 12개 모두 통과했다.
  Markdown 원문·사용자 metadata 보존, 동일 내용 저장의 무변경, 공개 API로 변경한
  시각 표시, 오래된 CAS 거부, RAG 본문 추가 조회 없음도 검사했다.
  원본 결과와 소스 해시는 `tests/hub/reports/backend-times-20261105/`에 기록한다.

- 전체 Hub 회귀 `full01`은 1,134개를 실행하여 1,132개 통과, RAG mock 응답의
  필수 시각 누락 2개 오류를 기록했다. 해당 모듈의 검사 자료를 현행 catalog 계약에
  맞춘 `focus03`은 10/10 통과했다. 제품 코드는 전체 실행 이후 바꾸지 않았다.
  변경된 모듈 전체의 재검과 나머지 동일 소스 결과를 합친 최종 확인 대상은
  1,134개이며 누락·Skip은 없다. 단일 전체 실행이 모두 통과했다고 표현하지 않는다.
  테스트 ID별 근거와 소스 대조는 `final-summary.json`에 보관한다.
- 기존 미검토 항목의 경계 점검과 남은 소유권 정리 내용은
  [미검토 경로 검토](pending-contract-review.md)에 따로 기록한다. 기능 회귀 통과와
  모든 리터럴·동적 경로의 전수 계약 감사 완료는 구분한다.
