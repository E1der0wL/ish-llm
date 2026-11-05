# 기존 미검토 경로 검토 — 2026-11-05

Backend 시각 계약 전환과 함께 기존 감사의 미검토 항목을 점검했다. 기준은
[단일 소유](../llm/contract-ownership.md), [명명](../llm/naming-conventions.md),
[검증](../llm/verification-policy.md) 계약이다. `llm/`은 읽기만 했다.

## 범위와 판정의 한계

기존 목록의 미검토 109개 중 날짜만을 위해 Skill을 재저장하던
`hub/backend/project/skill_registration.py`를 제거하여 현재 대상은 108개다.
이 108개 전체의 import·저장/실행 호출·동적 접근·고정 dict 구조를 정적으로 추출하고,
아래의 위험 경계는 실제 소비자와 구현을 따라 읽었다. 경계 점검과 모든 리터럴의
정규 Owner 분류는 서로 다른 작업이다. 이번 점검을 후자의 완료로 기록하지 않는다.

기존 감사의 `violations=0`은 **등록된 위반 판단이 0개**라는 뜻이지 코드에 위반이
없다는 증거가 아니다. 이번에 확인한 아래 미정리 항목도 존재한다. 파일 전체를
예외 처리하거나 미분류 리터럴을 일괄 승인하지 않았으며 `complete=false`를 유지한다.
새로 확인한 항목의 동작 변경은 이번 시각 전환에 섞지 않았다.

## 발견 사항과 권장 순서

### 1. 실행 중 Memory 누락 설정을 Hub 생성 프리셋으로 보충하는 경로

`WorkingContext.compact_prefix()`는 `compact_active`가 없으면 꺼진 것으로 처리하지
않으며, `active_keep_iterations`가 없으면 `HubDefaults.MEMORY_SUMMARY`를 읽는다.
`ToolReferences.prepare()`도 `tool_result_chars`가 없을 때 파일 읽기 프리셋의 상한을
대신 사용한다. 이는 신규 Project 생성 기본값과 실행 시 Memory 정책 해석이 섞인
지점이다. 명시 설정이 없는 기존 Project에서도 영향을 받을 수 있다.

원본 설정과 Missing/Null/Value를 보존하면서 Memory의 정규 해석 결과를 사용하거나,
별개의 Hub 표시/입력 정책이라면 별도 명시 설정 Owner로 구분할 필요가 있다.
생성 프리셋으로 조용히 보충하는 현재 동작을 제거할 때는 기존 대용량 처리 의미를
먼저 확정하고 `test_working_context`, `test_memory_setup` 등의
누락·명시 비활성·요약 실패 사례를 함께 검증해야 한다.

이는 저장된 Tool 결과를 지운다는 의미는 아니다. 현재 참조는 Backend가 준
`result_ref`를 사용하고, Tool 실행·완료 영수증을 재작성하지 않는다.

### 2. 플러그인·질문·승인·MCP의 고정 요청/응답 구조가 중복됨

| 경계 | 근거 | 정리할 Owner |
|---|---|---|
| 플러그인 | `hub/api.py`, `plugins/requests.py`, `plugins/stream.py`, `HubRuntime.plugin_call()`이 작업명과 요청 참조/응답 필드를 나누어 직접 사용 | Hub 플러그인 프로토콜의 작업·요청 참조·응답 Projection |
| 질문 | `HubRuntime.question_action()`과 `QuestionInbox`가 `run_id`, `targets`, `response`, `delivery` 등을 직접 조립/조회 | 질문 UI 요청/응답 Projection. 질문·답변의 원본은 native 계약 유지 |
| 승인 | `ApprovalManagement`, `ApprovalManager`, `ApprovalSelectionDialog`의 payload와 조건 표시가 고정 dict 필드를 반복 사용 | Hub 승인 조작 요청 및 native 승인 관측 타입 |
| MCP | `MCPManagement`와 `MCPToolSelection`이 서버/도구 목록·Agent 선택·revision envelope를 따로 조립 | MCP 관리 조회/변경 Projection |

문자열 상수 목록만 추가하는 것으로는 충분하지 않다. 경계에서 검증된 타입을 만들고
소비자가 그 속성/API를 쓰도록 해야 한다. 공개 JSON 형식은 유지해야 한다.
현재 이 경로들은 공개 서비스 저장과 revision/CAS를 통과하므로, 이 중복 자체를
승인 우회나 중복 Tool 실행의 재현 증거로 해석하지 않는다.

### 3. 지시 분류와 관측 프로토콜의 선언 위치

`InstructionRouting`의 모델 분류 결과(`action`, `reason`, `steer`, `redirect`)와
`backend/terminal.py`와 `TerminalReview`의 대상·페이지 envelope가 실행부에 남아 있다.
분류 모델 결과는 사용자 승인이나 Tool 재실행 허가가 아니다. native 대상 확인,
명시 redirect, 공개 페이지 조회 경계는 유지하면서 Hub 요청/관측 타입으로 모아야 한다.

### 4. 음성·이미지·위젯의 고정 설정과 인터페이스 선언

`SpeechConfig`와 장치/세션 소비자의 interaction 키, PCM 표현·진단 이름,
`KittyGraphics`의 환경변수·프로토콜 template, Workflow 탐색의 키와 일부 표시 크기,
Dialog의 동적 suspend/resume hook 등이 해당 Owner의 선언 영역과 소비 경로로
정리되어야 한다. `MouseBinding`에는 메서드 뒤에 선언된 값도 있다.

이들은 대부분 Hub가 소유할 수 있는 계약이다. 모든 값을 전역 상수 파일에 모으거나
Backend로 옮기는 것이 해결책은 아니다. 반대로 PTK나 외부 프로토콜이라는 이유로
실행부 재선언을 전부 비계약으로 면제해서도 안 된다.

## 확인한 책임 경계

| 영역 | 실제 확인한 경계 |
|---|---|
| 자료 날짜 | `RecordTimes`를 사용하며 Hub의 metadata 날짜 삽입·mtime/Job 대체·날짜 전용 Skill 재저장을 제거. 날짜를 CAS로 사용하지 않음 |
| 승인/거절/재개 | `ApprovalFlow`가 공개 승인 관측·resume plan·불확실 효과를 확인한 뒤 명시 재개. 조건 UI가 저장/실행 검증을 대체하지 않음 |
| 명령 범위 승인 | 파이프라인 구성과 연산자·작업 경로·환경 조건을 유지. 제안된 prefix는 사용자가 선택하는 범위이며 그 자체로 승인되지 않음 |
| 질문 | Backend 질문·답변 저장 사용, 답변 저장과 전달 분리. 종료된 원래 대상에 늦은 UI callback으로 자동 새 요청을 만들지 않음 |
| 플러그인 | Project/Session 소유자 검증, 명시 제출·응답·재개, 공통 출력 stream. Home 선택을 실행 대상의 원본으로 사용하지 않음 |
| Worker/출력 | dirty 신호·프레임 제한·이벤트 유실 후 저장 상태 재조회. UI 캐시를 실행·승인 권한으로 사용하지 않음 |
| Workflow 편집 | draft/undo는 로컬 편집 상태. 공개 handler/schema·inspection과 저장 CAS를 유지하고 편집기에서 그래프 실행하지 않음 |
| 초기화 복구 | Hub 소유 preference/preset 경로만 백업·초기화. symlink/동시 변경을 확인하고 Backend 내부 자료를 직접 삭제하지 않음 |
| 미디어 | 음성 장치 task/queue와 provider stream 수명은 명시 종료. 이미지는 제한된 로컬/등록 artifact를 읽고 모델이 만든 터미널 escape를 실행하지 않음 |
| 테마/언어/기본 자료 | UI 선언과 생성 지침이며 승인·저장 권한이 아님. 모델명·키를 기본값으로 채우거나 사용자 정의를 날짜 때문에 덮어쓰지 않음 |

이 검토와 관련 Hub 회귀에서는 새로운 승인 우회·체크포인트 재구현·Backend 파일 직접
변경의 재현 사례를 찾지 못했다. 위의 선언/설정 해석 문제와 미분류 후보까지 없다는 뜻은 아니다.

## 근거와 후속 감사

`tests/hub/reports/backend-times-20261105/`에 다음 근거를 남겼다.

- `coverage-before.json`: 108 미검토, 6 오래된 해시, 9,151 미분류 후보의 원본 목록.
- `pending-scan.json`: 108개와 이번에 바뀐 6개의 파일별 해시·호출 경계·동적 접근 목록.
- `audit_scan.py`: 읽기 전용 추출기. 자동 승인/판정 기능은 없음.
- `full01`, `focus01`~`focus03`: 실패를 포함한 원본 테스트 결과와 고정 소스 해시.

시각 전환으로 해시가 바뀐 기존 6개 기록은 변경 내용을 검토한 후 현재 근거로 갱신한다.
기존 미검토 파일을 이번 경계 점검만으로 전수 감사 완료로 바꾸지 않는다. 다음 정리 작업은
위 1 → 2 → 3·4 순서가 적합하며, 각 계약의 선언·Reader·소비자·Writer를 연결한 뒤
리터럴별 판단과 동적 경로 증거를 갱신해야 한다.

갱신 후 계약 인덱스 검사는 8/8 통과했다. 오래된 해시와 인덱스 구조 오류는 0개이며,
전수 분류는 108개 미검토 파일·9,037개 미분류 후보를 유지한다. 문서 검사도 239개 문서,
188개 Python 예제, 상대 링크 1,522개에서 오류 없이 통과했다. 결과는 `audit01/`에 있다.
`llm/` 412개 파일은 작업 시작의 원본 바이트와 동일하다.
