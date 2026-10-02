# Architecture

## 저장소와 배포 경계

실행 플러그인 루트는 `llm/`과 향후 구현할 `hub/`다. 테스트는 `tests/<plugin>/`,
예제는 `examples/<plugin>/`, 아키텍처·작업 인계·검토 문서는 `docs/<plugin>/`에 둔다.
`ish.platform/`은 호스트 참고 소스로 유지하며 로더 계약 테스트에서만 참조한다.
개인 수동 스크립트와 검사 기록은 `tests/llm/manual/`, `tests/llm/reports/`에 둔다.
플러그인 실행 경로는 테스트와 예제를 import하지 않는다. 배포 동기화와 패키지 탐색에서도
tests/examples를 제외한다. 코드 옆 README는 공개 API 계약을 설명한다.
이 구분은 파일 배치만 바꾸며 Project → Session → Run → Step 소유 관계나 저장 형식에
영향을 주지 않는다. 테스트 실행 방법은 저장소 루트 README와 tests/README.md를 따른다.

## Project Tool runtime isolation과 Host observability

Project-owned Python source는 components/tools/_worker.py의 child interpreter에서만 실행한다.
prepare/resolve는 inspector의 JSON descriptor를 검증하고, backend에는 source function 대신
ProjectToolHandler를 등록한다. 승인·operation 예약 이후 fresh execution worker를 생성한다.
ToolExecutor의 승인/retry/timeout/Step/영수증 책임과 Project → Session → Run → Step 소유 관계는 유지한다.
source/requirements fingerprint를 child가 재검증하고, 승인/재개 binding에도 포함한다.
Host builtin과 RAG/Memory/MCP capability를 자동 subprocess로 바꾸지 않는다.
worker는 runtime isolation이며 OS filesystem/network sandbox가 아니다.

Host observability는 RunManager, ToolExecutor, provider boundary, EventSubscriptions,
Project worker에서만 fact를 수집한다. 누적 통계만 소유하고 runtime gauge는 기존 owner에서 읽는다.
privacy-safe bounded recent와 snapshot은 best-effort projection이며 실행 결정/복구 원본이 아니다.
상세 계약은 llm/services/infrastructure/OBSERVABILITY.md와 llm/components/tools/README.md에 있다.

Durable Tool 호출은 EngineContext.execute_tool 또는 BaseEngine.execute_tool을 통해
기존 checkpoint invocation key와 Interaction decision을 ToolExecutor에 연결한다.
ToolExecutor는 관측 여부와 무관하게 연결 누락/불일치를 실행 전에 검증한다.
key는 승인 증명이 아니며 별도 dedup 저장소나 저장 형식을 추가하지 않는다.
Graph pause_before와 Tool 승인 재개는 구분한다. 승인/receipt/retry/Step의 소유자는 그대로다.
GraphNodeContext.decision은 노드 확인용일 수 있으므로 ToolNode가 이를 승인으로
전달하지 않는다. 공통 Tool helper는 원본 waiting Tool interaction의 action/binding에
맞는 응답만 해석한다. confirmation 후에도 호스트 authorize를 거치며 ASK는 새 승인
대기로 이어진다. 알림·UI 조회 계약은 [interactions.md](interactions.md)를 따른다.

승인·재개 값의 순수 해석은 core.interactions.InteractionRequest의
decision_for/select_decision과 same_interaction_value를 공유한다. 서비스는 응답 수명·
지문·영수증, Engine은 binding/retry_nodes/전략별 형식, ToolExecutor는 실행 연결과
승인 전 효과 금지를 계속 담당한다. 중첩 checkpoint bridge는 부모/자식 요청의 연결을
검증하고 선택지 ID로 자식 값을 복원하며 새 승인을 생성하지 않는다. 갱신된 응답은
서비스에서 검증하므로 bridge는 옛 체크포인트 요청의 만료를 재검사하지 않는다.
저장 버전·형식과 Project → Session → Run → Step 소유 관계는 유지한다.
상세 API와 누락/거절/confirmation 구분은 docs/llm/interactions.md에 있다.
응답·취소·갱신 UI 알림은 기존 transaction after_commit에서 예약한다. rollback된 변경은
알리지 않으며 저장 transaction 문맥을 이벤트 루프로 넘기지 않는다. Engine/Run 알림은
기존 저장 경계를 유지하고, UI가 놓친 알림은 도메인 조회/출력 저널로 복원한다.

## RAG 추출 정책과 프롬프트

PromptComponent는 prompts/records 아래 messages 정의를 소유한다. RAG의 기본 지침과
검증 계약은 components/rag, 범용 프롬프트 CRUD는 components/prompts에 둔다.
ProjectConfig.component_configurations.rag.extraction에서 prompt_id, repair_attempts,
relation_types를 설정한다. RAGData는 선택된 registry의 PromptComponent에서 정의를 읽고
작업별 사본으로 바인딩한다. 프롬프트 내용도 준비/색인 작업의 설정 fingerprint에 포함된다.
삭제된 참조는 새 모델 호출을 막되 설정 편집·조회·실패 작업 정리를 막지 않는다.

TripleExtractor의 temperature와 few-shot은 명시한 모델 설정/프롬프트로 지정한다.
JSON/참조/인용 검증 실패의 수정 요청은 명시한 repair_attempts 안에서만 수행한다.
오류·직전 JSON·원본 chunks를 전달하며 연결 오류나 취소는 이 수정 루프의 대상이 아니다.
관찰·사용량 한도는 매 호출에 동일하게 적용한다. 기존 확장 Extractor는
extract(chunks)를 유지하며 정책 적용을 원하면 with_extraction(options, prompt)를 구현한다.

프로그램이 검증 관계에 document_id/extracted_at을 부여하고 metadata를 보존한다.
동일 트리플의 서로 다른 문서/청크 쌍을 weight로 세며 응답 중복·재시도는 가산하지 않는다.
문서 graph에는 문서 내부, Kuzu 검색에는 코퍼스 전체 가중치를 제공한다. 수정·삭제는
영향받은 관계의 가중치를 갱신한다. RAG corpus graph_schema_version=1는 새 Kuzu 속성
계약을 나타내며 이전 색인은 비파괴 거부한다. 새 Project에 원문 재등록이 필요하다.
Project/Session/Run/Step 저장 형식 및 실행/저장 책임은 변경하지 않는다.

## 영속 도메인과 확장 계약

영속 소유 관계는 **Project → Session → Run → Step**이다. Session은 재시작 후에도
다시 열 수 있는 독립 작업·대화 공간이며 Conversation은 Session에 속한다.
한 Session의 Run은 직렬 실행하고 서로 다른 Session은 동시에 실행할 수 있다.
Engine은 Run에 선택되는 실행 전략, Component는 Project가 선택하는 기능과 자료의
소유자다. Engine이 이벤트를 발생시키면 서비스가 Run과 Step을 저장한다.
Engine이나 Component를 핵심 소유 계층의 중간 단계로 삽입하지 않는다.

Session/SessionManager/SessionRepository/SessionPaths/SessionHandle을 사용한다.
공개 진입점은 project.sessions, EngineContext.session, Run.session_id다.
ProjectConfig.session_defaults와 session_config가 설정 기본값·덮어쓰기를 담당한다.
Memory의 세션 범위는 scope="session"과 session_id로 지정한다.

Project/Session/Run/Step 저장 형식은 storage_version=1다. Session은 Project 아래
sessions/<id>/session.json에 저장되며 대화와 runs/는 해당 Session 아래 유지된다.
출시 전 저장 버전 번호는 1로 통일한다. 이전에 생성한 version 2는 읽기/쓰기 전에
거부하며 자동 변환하지 않는다. Task 형식의 변환이나 호환 reader도 제공하지 않는다.
기존 데이터는 보존하고 새 테스트 workspace로 검증한다.
구형 공개 API의 별칭은 제공하지 않는다. asyncio.Task와 처리기 실행 세션은
영속 Session 도메인과 다른 개념이므로 기존 역할을 유지한다.

## Engine 패키지 경계

Engine 구현은 components와 같이 종류별 하위 패키지에 둔다. 공통 실행 계약·문맥·이벤트와
BaseEngine은 engines/base.py, 등록·조회·설정 사전 검증은 engines/registry.py가 담당한다.
Loop/Pipeline은 각각 engines/loop/engine.py, engines/pipeline/engine.py에 둔다.
Graph의 공개 엔진과 Run별 내부 실행은 engines/graph/engine.py에 함께 유지하며
Graph용 AgentNode, ToolNode, 중첩 체크포인트 연결은 같은 graph/ 폴더에 둔다.
공개 진입점은 각 패키지의 __init__.py다. 호환용 옛 모듈은 만들지 않는다.
등록 방식·설정 키·저장 형식·이벤트 계약은 유지한다. 상세 import와 디렉토리 구조는
[Engine 안내](../../llm/engines/README.md)를 참고한다.

## 메인 도메인 출력·조회 비용

RunOutputState와 RunRepository는 OutputProjection을 공유한다. 불변 EngineDelta/
EngineOutput 계약은 유지하고 런타임 문자열 버퍼로 델타를 누적한다. 출력 저널은
순차 소비하며 결과 조회에 이벤트 전체 목록을 만들지 않는다. 사용자 저장소의
output_events 또는 저널 read 재정의는 계속 사용한다.

Conversation도 도메인 Message와 별도로 본문 버퍼를 가진다. 공개 조회·상태 확정·
보관 시 본문을 문자열로 제공하고, 파일 JSONL 기록은 즉시 fsync하는 기존 계약을
유지한다. 메모리 트랜잭션 rollback은 원래 버퍼/길이를 복원하며 파일 rollback은
기존 캐시 무효화로 재투영한다. 버퍼가 저장 파일이나 도메인 모델에 들어가지는 않는다.

Graph는 실행별 Workflow 스냅샷을 한 번 탐색하여 등록 검증과 체크포인트 바인딩을
공유한다. 다음 Run/재개에서는 새로 검증하며 실행 간 캐시로 변경을 숨기지 않는다.
같은 graph.py 안에서 노드 계산과 승인·재개·Step 수명을 분리한다. RunManager의
이벤트 저장 함수는 기존 IO 트랜잭션 안에서만 호출하고, 알림은 저장 성공 후 전달한다.

역순 Query는 역순 순회가 가능한 컬렉션을 복사하지 않는다. 파일 목록은 변경/손상
검사를 위해 여전히 메타데이터를 확인한다. 이 최적화는 색인 DB 도입이나 검증 생략이 아니다.

## 공통 저장 트랜잭션

WorkspaceOwnership이 소유한 TransactionManager가 기존 서비스 저장 경계를 묶는다.
Run 시작·종료, 출력/Step/체크포인트, 승인/재개/Tool 원장, Project/Session 수명·보관·복구에
적용한다. JSONL은 추가 구간만 되돌리고 디렉토리 삭제는 격리 후 확정한다.
잠금 안에서 읽기 전에 미완료 저널을 복구하며 Engine/외부 효과를 재실행하지 않는다.
Engine 이벤트와 UI 알림은 저장 확정 후 전달한다. 메모리 저장의 휘발성은 유지한다.
아래 과거의 파일별 복구 설명보다 이 절의 확정 경계가 우선한다.
적용 목록·저널 형식·확장 계약과 한계는 [저장 트랜잭션](storage-transactions.md)을 참고한다.

## 설정 적용 계약

Engine의 기본값 → Project → Session → Agent → 명시적인 호스트 값 순서를
core/configuration.py에서 해석한다. Loop/Graph/Preparation/Pipeline의 configuration 계약은
실행과 UI 조회가 공유하며, 호스트 값의 출처와 가려진 설정을 표시한다. Pipeline은 이름 있는
단계별 설정을 제공하고 Graph는 중첩 실행/재개에서도 호출별 설정 사본을 사용한다.
RAG의 JSON 설정은 분할·모델 인자·배치·검색·DB 옵션을 소유하며 등록 객체를 변경하지 않는다.
ProjectConfig.component_configurations는 컴포넌트 설정의 유일한 저장 위치다.
Component는 기본값·검증·해석을 담당하고 ComponentData의 편의 수정도 ProjectConfig에만 저장한다.
컴포넌트별 설정 파일과 직접 쓰기 API는 제거했다. 복제에서 설정은 Project, 자료는 Component가 담당한다.
Session은 Project 컴포넌트 설정을 덮어쓰지 못한다.
policies.output은 Run 시작 시 복사하여 델타 저장 묶음 설정을 프로젝트별로 적용한다.
준비/검색 중 설정 변경은 충돌로 거부하고 영속 준비 결과도 설정 바인딩을 확인한다.
도메인·저장 경계와 호스트 실행 객체 주입은 유지한다. API와 기본값 변경은
[설정 일관성](settings-consistency.md)에 설명한다.

## 운영 계약과 UI 설정 스키마

`LargeLanguageModel.project_schema()`는 Component/Engine의 `configuration_schema()`와
ProjectConfig 정책 스키마를 합친다. Component 기록 스키마는 설정 스키마와 분리한다.
Facade의 `configuration()`은 원본 값·편집 버전·폼용 values/schema를 함께 제공한다.
폼의 `values.config.component_configurations`에는 컴포넌트 기본값을 병합하며,
`project.config`는 저장 원본을 유지한다. JSON Schema의 required/ref/배열 제약은 제거하지 않는다.
`ProjectHandle.validate_configuration(config)`와 비동기 API는 저장 경로와 같은 검증으로
전체 ProjectConfig 후보를 미리 확인한다. 반환 버전은 미리보기 당시 저장 원본의 버전이다.
백엔드는 EngineRegistry의 동기 설정 검증 계약을 ProjectManager/SessionManager에 주입한다.
Project 생성·저장·기본 Project 생성·백업 복원, session_defaults 및 Session 생성·저장 시
등록 Engine의 configuration을 검사하며 Engine 실행이나 모델/준비 함수는 호출하지 않는다.
설정 해석 계약이 없는 확장 Engine은 선언된 스키마로 전달값을 검사한다. 같은 설정 키를
공유하는 Engine의 UI 스키마는 allOf로 결합하여 모든 소비자의 조건을 만족해야 한다.
독립적으로 사용하는 ProjectManager에는 configuration_validator를 주입해야 Engine 검증이
연결된다. 미등록 Engine이나 계약을 선언하지 않은 확장의 임의 의미까지 추론하지 않는다.
자유 JSON 키는 열린 영역으로 유지하며 호스트 실행 객체는 Project 설정으로 가장하지 않는다.
Engine 설정은 입력 스키마와 최종 적용값을 차례로 검증한다. 호스트 고정값이 있어도
스키마가 선언된 잘못된 Project/Session 입력을 저장하지 않는다. 확장 Engine의 입력 스키마는
UI에 제공되는 것과 같으며, 상속 가능한 옵션은 입력 스키마에서 선택 필드로 선언한다.

RunOutputState는 runtime/output.py에서 출력 소유권·확정 상태·순서·대화 델타를 관리한다.
영속 저장과 Step 수명은 계속 RunManager/StepEventRecorder가 담당하고, 저장 성공 후에만
구독자에게 알린다. 확정 출력은 런타임 검증에 필요한 식별 정보만 남기며 큰 결과 원본은
Run/Step 저장소에서 조회한다. Tool/체크포인트 이벤트의 ACK 경계는 변경하지 않는다.

ToolContract와 ToolPolicy revision은 승인/격리/업무 키의 신뢰한 호스트 계약이며 재개
fingerprint에 포함된다. OperationRepository는 외부 확인 증거를 CAS로 반영한다.
ProjectRecovery와 RunManager 시작 복구는 같은 stale 상태 정리 함수를 사용한다.
HistoryRetention은 비활성 Session의 완료 Run을 명시적 저널 아래 정리하고, SessionManager는
미완료 정리 저널이 있는 Session의 실행 접속을 차단한다. Component는 history_references와
maintenance 훅으로 자신의 출처와 저장 수명을 관리한다.

비스트리밍 ModelClient는 저장을 알지 않는 provider observation 경계를 사용한다.
ComponentData에 주입한 ComponentUsage가 예약·한도·영수증을 담당하며 원본은 소유
컴포넌트 아래에 둔다. Run 호출 원본은 기존 Run Completion 기록이다. 프로젝트 한도는
둘을 합쳐 조회하며 별도 프로젝트 합계 파일이나 하위 Run을 만들지 않는다.
API/정확한 범위는 [운영 및 UI 설정](operations-and-ui-settings.md)을 참고한다.

## 장시간 실행과 복구

ProjectConfig.policies는 tool_retry/provider_retry/usage/retention도 소유한다. 기본값은
재시도 0회(또는 호스트 기본값), 사용량/보관 상한 없음이다. 실행 정책은 Run 시작 시 복사하고,
조건부 Tool 재시도의 안전성은 신뢰한 호스트 ToolPolicy/핸들러만 선언한다. 정상 None 반환은
완료로 저장하며 일반 예외·timeout·취소·저장 실패로 외부 효과를 자동 반복하지 않는다.

Loop는 회차와 Tool 영수증을 CHECKPOINT 이벤트로 전달하고 RunRepository가 저장한다.
재개는 새 Run을 생성하며 완료 효과를 재사용한다. 불확실한 Tool은 명시적 retry_nodes가
필요하다. 승인 대기와 Graph의 검토 상태 변경은 저장된 decisions를 통해서만 재개한다.
Graph 안의 Loop도 EngineCheckpointScope를 통해 호출 경로별 회차/Tool 체크포인트를 부모의
Graph 기록에 넣는다. Agent 정의는 데이터이며 별도 Run을 만들지 않는다. 승인 대기는 부모
Run을 PAUSED로 종료하고, 명시적 재개에서 정확한 경로의 결정만 자식에 전달한다. 누적 Agent
Tool 예산과 대기 중 예약도 복원한다. 완료 Tool의 None 결과도 재사용한다.
모든 Engine이 임의 SDK 호출을 하는 경우까지
가로채지 않으며 BaseEngine completion 경로가 사용량 예약/공급자 재시도를 지원한다.

HistoryRetention은 비활성 Session 또는 완료 Run 단위의 명시적 미리보기/삭제를 조율한다. 출력 저널과
Conversation의 부분 저장은 RunRepository/Conversation.reconcile로 본문만 복구한다.
영구 삭제 의도는 storage 계층이 기록하고 소유자가 복구한다. Project/Session/Run/Step 책임은
그대로이며 실제 LLM/Tool 작업을 복구 서비스에서 실행하지 않는다.

RAG의 영속 색인 작업·벡터/관계 증분 갱신은 components/rag가, 현재 작업의 오래된 Tool
교환 요약은 components/memory가 소유한다. 원본 Tool 결과 읽기는 주입한 history_reader로
현재 Session의 Step 저장소에 연결한다. 다른 컴포넌트도 같은 선택적 조회 계약을 사용할 수 있다.
Run/Step 목록 캐시는 작은 메타데이터만 보관하며 사용자 저장소와 Query 확장 계약은 유지한다.
Project 사용량 검사는 RunRepository의 변경 감지 투영과 컴포넌트 사용량 영수증을 합친다.
각 호출을 소유한 Run/Component가 원본을 보관하고 Project 합계 파일은 만들지 않는다.
Memory는 모델에 전달한 완전한 턴/Tool 교환만 요약으로 교체하며 입력 초과 구간은 남긴다.
RAG 작업은 OS lease, 임베딩/관계 추출 배치 영수증을 소유한다. 검색 캐시도 컴포넌트 인스턴스가
제한된 크기로 소유하며 불변 세대 키로 갱신한다. DB 복사는 Linux reflink 지원 시 블록을 공유하고,
미지원 시 일반 복사한다. 전역 저장 잠금과 corpus JSON 비용이 없어졌다는 의미는 아니다.
API, 재개 범위, 용량 측정 한계는 [장시간 실행 안내](long-running.md)를 참고한다.
Run/Step 파일 목록의 커서 없는 유한 페이지는 전체 변경 검사를 유지하면서 상위 항목만
정렬한다. 커서 조회는 기존 정렬/커서 유효성 검사를 유지한다. RAG도 전체 RRF 점수를
계산한 뒤 반환할 상위 항목만 정렬하며 동점 순서를 보존한다. corpus JSON과 색인 세대의
복사/원자적 공개 방식은 그대로다. examples/llm/operational_probe.py는 동시 Session·반복 대화의
RSS/FD/스레드/이벤트 루프 지연과 재시작 결과를 확인하는 Linux 실행 도구다.

## 프로젝트 실행 정책

ProjectConfig.policies의 context/completion/run 등 JSON은 Project가 소유한다. 기본값·스키마·형식
검증은 core/policies.py, 실행 객체 구성은 services/runtime/policies.py의 ProjectPolicyResolver가
담당한다. ProjectConfig.configure_policies는 후보 사본에 부분 변경을 병합·검증한 뒤 반영한다.
ProjectManager/Facade의 configure_policies는 잠금 아래 최신 설정에 이 메서드를 호출하고 저장하며
configuration은 UI용 policy_schema도 제공한다. 다른 열린 설정과 컴포넌트 소유권은 유지한다.
Run 시작 시 정책 사본을 metadata.policies에 저장하고 ContextPolicy/CompletionPolicy/RunLimits로
해석한다. 실행 중 변경은 다음 Run부터 적용한다. Session 설정에는 policies를 허용하지 않는다.
ServiceConfig는 토큰 계산기 registry/resolver와 runtime 구현·공유 자원 한도를 제공한다.
미등록 계산기는 큐 수락 전에 거부한다. Graph 재개는 기존 설정 fingerprint 검증을 유지한다.
[프로젝트 정책 API](project-policies.md)에 기본값·변경 시점·확장 계약을 설명한다.

## 프로젝트 장기 기억

`components/memory`의 MemoryComponent가 프로젝트의 `memory/` 설정·현재 기억·revision
이력을 소유한다. ComponentData의 잠금/수명 검사 및 StorageIO를 공유하며 CRUD 자체는 Run을
만들지 않는다. 대화의 휘발성 memory 저장 옵션과는 독립적인 영속 컴포넌트다.
선택 시 `tools` capability에 memory_search/get/create/update/delete를 제공한다.
ToolExecutor의 실행 문맥은 소유 Project/Session/Run/Message/Step 출처만 전달하며 엔진이나
RunManager에 기억 저장 책임을 추가하지 않는다. ToolPolicy와 Step 저장 경계를 유지한다.
현재 값·변경 이력은 단일 JSON 원자 교체, 수정은 expected_revision 비교, 삭제는 복원 가능한
형태가 기본이다. 모델 작성은 candidate, 기본 검색은 confirmed이며 프로젝트 설정으로 조절한다.
BaseEngine에 Memory 전용 로직을 추가하지 않는다. [Memory API](memory.md)를 참고한다.

Memory의 completion_processors export는 호출별 세션을 제공한다. Loop는 CompletionPipeline의
prepare/after_completion/finish 이벤트 스트림과 aclose 자원 정리 계약을 소비하며
RunManager/SessionManager는 변경하지 않는다. 처리기는 priority/name 순, 정리는 역순이다.
CompletionRequest는 공급자 메시지와 원본 ID를 분리하며 Memory 요약은 ID/내용을 확인해
원문만 교체한다. 관찰자는 예산 적용 요청·응답·원본 대화의 독립 사본을 받는다. 처리기
컬렉션이 비어 있어도 유효하다. 커스텀 CapabilityResolver도 요청받은 completion_processors를
빈 tuple 또는 제공자 tuple로 반환해야 한다. Graph Agent의 기존 capability 전달을 활용한다.
[공통 처리기 계약](completion-processing.md)에 조합·취소·Tool transcript 보호 규칙을 설명한다.
Memory가 Session 범위 기억, 증분 요약, 예산 내 검색 주입, Tool 결과 미리보기, 기억 후보 추출,
중복/만료/대체 제안 조회를 소유한다. 보조 모델 관찰도 기존 Run의 내부 Step으로 전달한다.
원본 Conversation/Tool 결과/Graph 체크포인트는 변경하지 않는다. 파생 요약은 memory/contexts
아래에서 원자 교체하고 처리 구간은 개수·해시·원본 ID 경계로 저장한다. 캐시 generation/revision과
컴포넌트 identity/설정을 검증한 뒤 저장한다. 자동 보조 모델 호출은 기본적으로 꺼져 있으며
[Memory 처리 정책](memory-processing.md)에서 설정·범위·실패 계약을 설명한다.

## 실행 자원·저장·백업 정책

ServiceConfig의 ProviderLimits/주입 ProviderCalls는 백엔드의 동기 completion 스트림
슬롯을 공유한다. 취소된 Run도 실제 공급자 호출과 close가 끝날 때까지 슬롯을 유지한다.
EngineContext나 영속 도메인에 실행 핸들을 추가하지 않으며 호출 문맥으로 전달한다.
독립 RAG의 사용량은 ComponentData의 관찰 계약으로 연결하며 provider 슬롯과는 별도다.
사용자 코드가 직접 SDK를 호출한 경우에는 명시적 관찰 계약이 필요하다.

OutputPolicy는 RunManager의 델타 저장 경계를 조절한다. 기본값은 매 이벤트 저장이다.
선택적인 묶음 처리는 Engine 생산자를 하나의 asyncio.Task에서 유지하고 TEXT_DELTA만 모은다.
다른 이벤트는 ACK 경계이며 승인/체크포인트를 저장하기 전에 효과를 진행하지 않는다.
RunRepository와 Conversation에 각각 append/fsync한 후 개별 알림을 전송한다.
두 파일 간 원자적 트랜잭션은 아니며, 실패 시 Run은 성공으로 보고하지 않는다.

ProjectManager는 런타임이 해제된 file Project의 백업을 조율한다. 저장소의 read_backup과
Component의 backup_scope/validate_backup에 자기 데이터 검증을 위임한다. 복사·해시·
스테이징 공개는 주입 가능한 DirectoryBackups 책임이다. 기존 ID를 덮어쓰지 않고
복원만으로 Run을 실행하지 않는다. RAG의 벡터/그래프는 같은 세대로 보존한다.
Project/Session/Run/Step JSON에는 storage_version=1를 명시한다. 누락/미지원 버전은
자동 추정하지 않으며 명시적인 백업 사본 변환 후 검증한다.
운영 설정, 예제, 남은 제한은 [실행 자원과 저장 정책](operational-storage.md)을 참고한다.

## 공통 출력 데이터와 조회

core/results.py의 EngineDelta와 EngineOutput은 기존 실행 도메인의 값 객체다.
새 출력 도메인/Manager를 만들지 않는다. Base·Loop·Graph·Agent·Pipeline·Tool이
EngineEvent.delta/output으로 전달하고 RunManager/StepEventRecorder가 저장한다.
CompletionResult는 모델 관찰값, ExecutionResult는 Run 조회 뷰로 유지한다.

BaseEngine.run은 str/EngineDelta/EngineOutput을 받아 이벤트로 변환한다. 델타 append/replace를
Step 안에서 합산하고 최종 결과를 생략하면 합산한 텍스트로 확정한다. delta_event/output_event/
step_completed_event는 저장 없이 소유 ID/표시 범위/순번 초기화를 적용하는 공통 메서드다.
Loop는 상속으로, Graph/Agent/Pipeline은 정적 호출로 사용하여 각자의 실행 방식을 유지한다.
내부 표시 범위는 사용자 표시 범위로 승격하지 않으며, 결과 검증도 Step 실패 처리 범위에 둔다.

output_id와 step_id로 병렬·중첩 출력을 분리한다. step_id=None인 최상위 결과는 Run에,
Agent와 중첩 Graph 및 Tool 결과는 소유 Step에 저장한다. Pipeline은 stage별 engine Step을
추가하고 마지막 stage 결과를 전달한다. Graph의 Workflow JSON 입출력 계약은 유지한다.

Run/state/outputs.jsonl은 append-only 출력 저널이다. RunManager가 Run 내 sequence를
부여하고 저장한 뒤 알림을 발행한다. 최종 스냅샷은 run.json/step.json의 metadata.output에
저장하며 공개 조회는 ExecutionResult.output과 Step.output을 사용한다. Facade의
aoutputs/aoutput_events(after, limit)는 주입 RunRepository를 공유하여 UI 복구를 제공한다.
커서 조회는 재생성 가능한 outputs.index.json 희소 위치 인덱스를 사용한다. Run 성공 여부와 출력 final을 구분한다.

대화 본문에는 user 델타만 반영하며 Loop 완료 시 마지막 답변으로 확정한다. 중간 출력은
저널에서 조회한다. 교체는 conversation JSONL의 message.delta operation=replace 이벤트이며
파일 전체를 다시 쓰지 않는다. memory 선택은 대화 저장에만 적용하고 Run/Step 출력 기록은
계속 영속화한다. 이전 event.text/raw Step 결과 형식의 별칭이나 자동 변환은 제공하지 않는다.
자세한 엔진/UI 계약은 [공통 출력 안내](engine-output.md)를 따른다.

## 중첩 Workflow 실행

`workflow` 노드는 같은 Project의 저장 정의를 참조한다. GraphEngine.for_agent도 제공하며
AgentNode에 등록한 GraphEngine은 GraphNodeContext.invoke_graph로 동일한 하위 실행 경로를
사용한다. `_WorkflowRuntime` 자식 인스턴스는 체크포인트 records를 공유하고 호출 경로에
workflow/ID를 추가한다. 상태/메시지/입출력은 호출별로 분리하고 최상위 Run만 생성한다.
자식 출력에는 Workflow 계약을, 부모에 병합하기 전에는 호출 노드 계약을 적용한다.

정적 capability를 읽은 뒤 additional_capabilities를 반복 해결하여 참조 전체를 탐색한다.
Workflow/Agent 참조의 순환과 깊이 초과, 누락된 정의/처리기/Tool은 실행 전에 거부한다.
max_nested_depth 기본값은 16(0~32)이다. 바인딩에는 자식 정의와 엔진 제한/revision도 포함한다.
직접 workflow 노드는 부모 handlers를 공유하며 Graph Agent는 별도 등록 엔진 handlers를 쓴다.

자식 노드 방문은 모든 조상의 max_steps에 합산한다. 실제 작업만 조상 순서대로 세마포어를
획득하며 조율 노드는 슬롯을 점유하지 않는다. 부모/자식 timeout과 Tool 권한/예산/원장은
같이 적용한다. Run 제한은 재개 시 새 Run의 한도이며 Graph Agent Tool 시도/성공 횟수는
agent_usage로 체크포인트에 저장/복원한다. Tool 승인 이후 효과 전에도 컨테이너 사용량을 저장한다.

자식 pause_before는 예외로 최상위 실행기에 전달하고 PAUSED는 루트만 발행한다. container=true인
조율 노드는 재개 승인을 요구하지 않으며 자식의 completed 출력만 재사용한다. started 상태의
실제 작업은 기존대로 정확한 retry_nodes 승인이 필요하다. 커스텀 graph_engine 선언만으로
container가 되지는 않으며 순수 조율 처리기의 resumable_container=True 명시가 필요하다.
외부 효과는 자식 작업 노드에서 수행해야 한다. 네이티브 LangGraph interrupt는 계속 거부한다.

subgraph Step은 parent_step_id/workflow_id/path를, 작업 Step은 node_checkpoint_key/node_path를
기록한다. Agent 연결은 agent_step_id를 유지한다. Engine은 이벤트만 발행하고 저장은 기존
RunRepository/CheckpointRepository/StepManager가 수행한다. UI와 저장 예제는
[중첩 Workflow 안내](nested-workflows.md)를 따른다.

## Linux 전용 실행 기반

실행 지원 OS는 Linux다. LargeLanguageModel, CLI, workspace 잠금과 프로세스 실행기의
진입점에서 공통 require_linux 검사를 수행하고 파일 변경/명령 실행 전에 거부한다.
모듈 import와 PLUGIN_META 정적 읽기는 실행과 분리한다. Windows Job/msvcrt/PowerShell/
junction 분기는 제거했다. 파일 잠금은 fcntl.flock, 메타데이터 디렉토리 동기화는 fsync,
기본 셸은 /bin/sh다. 경로에는 Linux 심볼릭 링크 검사를 유지한다.

기본 프로세스 Tool과 ProcessToolRunner는 infrastructure.processes의 그룹 종료를 공유한다.
자식 worker는 부모 사망 신호와 프로세스별 자원 한도를 설정한다. sandbox의 Bubblewrap
경계와 비격리 process 모드의 계약은 구분한다. Windows 전용이던 max_processes는 제거했고
cgroup 기반 전체 프로세스 수/합산 메모리 제한은 제공하지 않는다. 기존 Run/Step 이벤트와
작업 원장, JSON/JSONL 형식은 유지하며 OS API를 Engine이나 Core 모델에 추가하지 않는다.

## Tool 프로세스 실행과 작업 원장

ToolPolicy.runner에는 ProcessToolRunner를 주입할 수 있다. 등록된 argv의 JSON worker를
유한 프로세스로 실행하며 Linux sandbox는 Bubblewrap을 요구하고 실패 시 실행을 거부한다.
Linux 전용이다. process 모드는 그룹 종료와 부모 사망 신호를 사용하고, sandbox는
Bubblewrap을 사용한다. 기본 프로세스 Tool과 Runner는 공통 그룹 종료 함수를 사용한다.
Engine은 프로세스·경로·OS API를 알 필요가 없다. 기본 Tool 핸들러는 자동 이동하지 않는다.

ToolPolicy.operation_key는 동일 Session의 논리적 업무 키를 선택한다. ToolExecutionScope의
ToolOperations 서비스가 공유 RunRepository를 통해 Session.state/tool_operations에 예약/결과를
저장한다. OS workspace 잠금 안에서 먼저 started를 기록하고 완료 결과는 원자적으로 저장한다.
완료 결과는 재사용하고 started는 불확실하므로 재실행을 거부한다. 서로 다른 인자/Tool로 키를
재사용하면 충돌이다. 단순 인자 중복 제거/자동 재시도/새 실행 도메인은 추가하지 않는다.
명시적 외부 결과 확인은 비활성 Session에서만 허용한다. 외부 제공자의 exactly-once와는 다르다.
상세 설정/OS별 한계/저장 계약은 [process-isolation.md](process-isolation.md)를 따른다.

## 운영 정책과 UI 관찰

ProjectPolicyResolver는 프로젝트별 ContextPolicy/CompletionPolicy/RunLimits를 구성한다.
ServiceConfig는 ToolPolicy의 호스트 실행 계약과 파일 대화 캐시 크기를 조립한다.
RunManager는 Session 대기열 admission/취소와 Run 실행 시간 한도를 담당한다. EngineContext의
ToolExecutionScope는 중첩 Agent/Graph 분기에서 공유하는 Run 전용 런타임 객체이며 저장하지
않는다. LoopEngine/ToolNode는 공통 ToolExecutor를 통해 승인·호출 예산·선택적 실행 어댑터를
적용한다. STEP_UPDATED는 기존 Step의 진행 메타데이터를 갱신하고 승인 기록 이후에 실행한다.
CompletionPolicy은 모델별 counter를 주입받아 Loop 요청의 과거 턴만 선택한다.

EventSubscriptions의 queued/drop_oldest 전달은 UI 관찰용이다. 저장은 먼저 완료되고,
누락 통계를 보고 UI가 재조회한다. 실행에 필요한 요청/검증은 EventHandlers/ToolPolicy에 둔다.
SessionManager 대화 팩토리는 파일 모드의 증분 읽기 상태를 제한된 LRU로 공유하며, 메모리
대화와 주입된 저장소의 소유권/수명은 유지한다. 상세 계약은 [runtime-reliability.md](runtime-reliability.md).

## 현재 구현으로 통일

재사용 업무 정의는 agents, 그래프는 Workflow v1, 검색은 통합 rag를 사용한다.
ProjectConfig의 열린 키는 보존하지만 구형 모델 설정을 자동 변환하지 않는다.
ProjectManager의 initializers 인자는 제거했으며 Session 초기화와 선택된 Component.initialize로
초기화를 구성한다. 과거 구현을 위한 별칭·데이터 이전·누락 필드 보정은 제공하지 않는다.
SDK dict/object 처리, Tool 카탈로그 기본 정의와 프로젝트 재정의,
선택적인 컴포넌트 capability 등 현재 확장 계약은 유지한다.


## LangGraph 기반 GraphEngine

GraphEngine은 저장된 Workflow v1을 읽고 engines/graph/engine.py의 Run별 내부 실행 객체를
통해 LangGraph StateGraph로 컴파일한다. 일반 노드는 실제 LangGraph 노드, branch는
조건부 간선, parallel은 독립 상태를 갖는 하위 그래프와 합류 barrier, loop는 조건부
후방 간선으로 변환한다. 기존 수동 _walk 스케줄러는 제거했다. 외부 노드 ID와 Step 경로는
유지하고 LangGraph 예약 이름은 내부 ID로 분리한다. JSON 형식과 공개 핸들러 API는 같다.

LangGraph는 실행 순서를, GraphEngine은 정책/상태 바인딩/EngineEvent 연결을 담당한다.
AgentNode → LoopEngine → litellm.completion(stream=True) 경로를 그대로 사용하며
langchain-litellm은 추가하지 않는다. LLM/Tool 호출과 RAG는 기존 capability 경로를 쓴다.
Session/Run/Step/Conversation 파일은 기존 서비스만 기록한다. 상태 채널에는 JSON만 전달하고
이벤트 연결·예산·세마포어·EngineContext는 Run별 런타임 객체가 소유한다.

graph/engine.py의 _branch_port, _loop_continues, _plan_node는 JSON 입력만 받는
순수 판단 함수다. 각각 분기 port, 다음 반복 여부, 체크포인트 재사용/사전 대기/실행을
선택한다. 상태·시각·ID·이벤트를 생성/변경하지 않으며 _NodePlan은 즉시 소비하는 내부
결과다. 처리기 호출과 체크포인트/Step 이벤트, 상태 반영은 _WorkflowRuntime이 담당한다.
재개 승인/binding/retry_nodes 검증은 기존 진입 경계에 유지한다. 병렬 스케줄러나 별도
영속 계획 도메인을 추가하지 않으며 Workflow/체크포인트 저장 형식과 공개 API는 같다.

Step 시작 이벤트를 서비스가 소비·저장한 뒤 작업을 시작하는 ACK 순서와 backpressure를
유지한다. max_steps는 모든 하위 그래프에서 공유하며 max_parallelism은 사용자 처리기
수만 제한한다. 병렬 경로는 상태와 EngineContext.state를 분리하고 회차마다 초기화한다.
취소는 처리기에 한 번 전달한 뒤 비동기 정리를 기다려 LangGraph의 중복 취소로 finally가
중단되지 않게 한다. 노드 자체 CancelledError도 Run INTERRUPTED 계약을 유지한다.

LangGraph의 네이티브 checkpointer/interrupt()는 사용하지 않는다. GraphEngine은 대신
Workflow 노드 경계의 JSON 체크포인트를 EngineEvent로 전달하고 RunRepository의
CheckpointRepository가 Run/state/checkpoints/graph 아래에 기록한다. 초기 헤더와 상속
기록을 한 번 저장하며 이후에는 변경된 노드 파일만 원자적으로 교체한다. 노드 실행 전
started, 결과 검증 후 completed를 저장하고 ACK를 받은 뒤 다음 단계로 진행한다.

pause_before=true 노드에 도달하면 호출 전에 waiting을 저장하고 Run/Assistant를 PAUSED로
종료한다. Session은 다음 요청을 처리할 수 있다. session.run.resume(run_id, engine=...)은 같은
Session의 새 QUEUED 요청/Run을 생성하고 metadata.resume으로 원본을 연결한다. 원본은 수정하지
않고 한 Run에서 한 번만 재개 요청을 받는다. 병렬/중첩/반복의 실행 키는 JSON 배열로 구분한다.
LangGraph가 그래프를 다시 스케줄링하되 completed 노드는 저장된 출력을 반환하며 처리기나
Tool을 재호출하지 않는다. 미완료 제어 노드는 완료된 자식 결과를 복원하며 진행한다.

started 상태의 처리 노드는 부작용이 발생했을 수 있으므로 retry_nodes에 정확한 키를 넣어야
재개한다. 이것은 재실행 승인이지 exactly-once 보장이 아니다. Loop Agent는 호출 경로별
자식 체크포인트의 완료 Tool을 재사용하고, Graph Agent도 완료 자식 노드를 재사용한다.
컨테이너 전체를 다시 실행하도록 승인하는 대신 효과가 불확실한 자식 키를 지정한다.
네이티브 interrupt()는 계속 거부하며 사용자는
pause_before 경계를 사용한다. 임의 Python 프레임이나 EngineContext.state 핸들은 저장하지 않는다.

재개 admission과 실행 시 Workflow/Agent/Tool 정의·Project/Session 설정·Engine revision을 비교한다.
헤더에는 원래 입력 상태와 대화 ID 목록을 저장하며 Conversation 본문을 중복 저장하지 않는다.
파일 대화에서 원래 문맥을 복원하고 새 요청 ID에 연결한다. 메모리 모드도 같은 백엔드에서는
재개할 수 있지만 대화가 소실된 뒤에는 거부한다(노드 입력/출력의 영속화는 원래 계약대로 유지).
대기 중 재개는 파일 큐에서 복구하지만 stale RUNNING Run을 자동 재실행하지는 않는다.
사용법/저장 형식/제약은 [체크포인트 안내](graph-checkpoints.md)를 따른다.

LangGraph는 첫 Graph 실행 시 비동기 루프 밖에서 지연 import한다. Python 3.12.14의
검증 버전은 LangGraph 1.2.12다. 개발·검증 대상은 Linux Python 3.12.14 하나이며,
다른 인터프리터를 위한 의존성 분기나 버전 어댑터는 제공하지 않는다.

## Workflow 입출력과 범용 Agent 실행

components/workflows/bindings.py는 저장 가능한 JSON Pointer 매핑과 로컬 JSON Schema
계약을 소유한다. GraphEngine이 요청 prompt/message_id를 초기 상태에 연결하고 처리 노드의
inputs를 선택한 뒤 검증한다. 반환 dict는 output_schema 검증 및 outputs 매핑이 모두 성공한
후 상태에 병합한다. 업무 검증의 passed=false는 정상 데이터로서 제한된 loop에 피드백을
전달한다. 포인터/스키마 오류는 Run 실패이며 암묵적 재실행을 하지 않는다.
Workflow outputs는 root graph Step의 공개 output을 선택한다. 매핑을 생략한 사용자
처리기의 기존 state/반환 dict 병합 계약은 유지한다. 제어 노드/loop.body는 상태를 공유하며
바인딩은 처리 노드에만 지정한다. 실제 입력/반환/병합 결과는 노드 Step 이벤트에 남긴다.

AgentComponent는 purpose와 명시적인 engine, engine_options, completion, system_prompt,
tools, resources, policy와 입출력 schema를 저장하는 재사용 업무 정의다. 실행 상태는 소유하지
않는다. AgentNode(engines=...)가 선택된 엔진의 동기 for_agent(definition)으로 호출별 실행기를
구성한다. LoopEngine과 GraphEngine이 이 계약을 제공하고 커스텀 엔진도 구현할 수 있다.
Graph Agent는 부모가 제공하는 하위 실행기를 사용한다. 일반 Agent 엔진의 CHECKPOINT/PAUSED
발행은 거부하며 Graph 하위 실행기는 공유 체크포인트의 노드 기록만 전달한다.

RunManager는 static capability를 구성한 뒤 additional_capabilities(snapshot)으로 요청된
추가 기능만 해결한다. GraphEngine은 선택 Workflow 노드들의 요청을 모으며 AgentNode는
참조된 Skill/MCP/RAG와 선택 엔진의 capability를 선언한다. Component CRUD나 Engine이
직접 Run 저장을 수행하지 않는다. Skill 지침은 system prompt에 추가하고 resources.rag=true는
Project의 rag_search를 허용한다. MCPComponent(connector=...)는 서버 정의를 받아 ToolRegistry를
제공하는 async context manager를 주입받는다. Agent는 서버별 명시적 Tool 별칭만 연결하고
정상/실패/취소 모두 세션을 닫는다. SDK별 전송 구현은 호스트 책임이다.

Agent 입력은 선택된 node.inputs, 출력은 {text, data?}이며 둘 다 저장된 schema로 검증한다.
메시지와 임시 상태는 호출별로 분리한다. ToolExecutionScope.child는 Agent 호출 한도를 추가하며
부모 Run의 권한·승인·원장·실행기·총예산을 유지한다. require_tool은 완료 Tool이 없으면 실패하고
Loop의 첫 Tool 성공 전에는 tool_choice=required를 사용한다. 정책 실패는 자동 재시도하지 않는다.
timeout_seconds는 연결/LLM/Tool/검증 전체에 적용한다. 중간 텍스트 공개는 노드 emit_text 선택이다.
Agent Step에 원본 정의·SHA256 revision·리소스 스냅샷·입출력을 기록하고 자식 LLM/Tool Step은
agent_step_id로 연결한다. 모든 Step과 Completion은 부모 Run에 귀속된다.
Graph 체크포인트 binding은 Skill/MCP 정의와 어댑터 revision까지 비교한다. 호스트 엔진/함수의
코드나 실행 설정을 바꾸면 AgentNode/GraphEngine revision을 올려야 한다. RAG 검색 자료는 실행
당시 corpus에서 읽으며 corpus 전체의 고정 스냅샷을 제공하지 않는다.
AgentData.snapshot/revise(expected_revision)는 UI 동시 편집을 검증하며 진행 중 Run은 변경하지 않는다.
구조/코드 품질 검증과 파일 저장은 예제/호스트 처리기의 책임이다.

전체 사용 계약과 테스트 방법은 [GraphEngine 안내](graph-engine.md)를 참고한다.

## 조회 경로의 중복 작업 감소

RequestHandle.run/result는 같은 잠금 범위에서 Session/사용자 Message/Run 연결을 읽는다.
result가 RunHandle을 만든 뒤 같은 Run을 다시 읽지 않는다. RunHandle.response도 이미
읽은 Session을 응답 저장소 조회에 재사용한다. 조회 사이의 캐시는 두지 않으며 매 호출마다
현재 수명과 소유권을 검사하고 백엔드에서 주입한 저장소를 사용한다.

Query의 오름차순 선택은 입력 전체 복사/필터 목록 없이 필요한 페이지까지만 순회한다.
역순은 입력 순서를 뒤집기 위한 목록이 필요하다. 커서→상태→offset→limit 순서와
없는 커서 오류를 유지한다. RunResultQuery는 기본 Query를 Run에 먼저 적용한 뒤 선택된
항목만 ExecutionResult로 변환한다. 사용자 Query 하위 클래스에는 기존 ExecutionResult
입력을 유지한다. 파일 저장소의 정렬용 메타데이터 전체 스캔 자체는 여전히 필요하다.

RAG 검색은 불변 corpus.json을 한 번 읽고, 이후 수명·동시 변경 검사에는
corpus_version의 identity/generation 포인터만 사용한다. 재정렬 이후 검사는 검색을
재실행하지 않는다. component snapshot과 corpus_version은 같은 세대를 나타내야 하며,
별도 저장 구현에서 이를 재정의할 때도 이 계약을 유지해야 한다. 모델 await 전후의
선택/삭제/세대 검사는 그대로 수행한다. publish의 변경 검사는 포인터를 사용하지만
compact는 삭제 전 활성 코퍼스가 읽히는지 확인하여 손상 시 정리를 중단한다. 관계 출처 조회는 관련 문서의 문단 ID 맵을 한 번 구성하여 재사용한다.
전역 캐시나 변경 가능한 공유 코퍼스 사본은 추가하지 않는다.

## 프로젝트 설정 통합 조회와 기본 프로젝트

ProjectHandle.configuration/aconfiguration은 ProjectManager의 잠금 범위에서 프로젝트
설정과 선택된 Component들의 설정을 하나의 JSON 호환 스냅샷으로 모은다. 저장 원본은
project.json의 ProjectConfig.component_configurations이며 디렉토리 지식 집중을 피한다.
설정 수정은 기존 project.asave 또는 ComponentData.aconfigure에 위임한다. 이 뷰는 저장된
설정만 표현하며 런타임 생성자 객체를 재구성하지 않는다. YAML과 자동 UI 스키마는 미구현이다.

Projects.get_default/aget_default는 명시적으로 기본 프로젝트를 생성하거나 재사용한다.
첫 생성 시 default_engine="loop", 등록된 모든 Component, conversation_storage="file"을
적용한다. 재조회는 사용자 변경을 보존하며 일반 create나 생성자 부작용은 바꾸지 않는다.
실행의 engine 인자는 여전히 필수다. 프로젝트 모델에는 실행 책임이 추가되지 않는다.
projects/default-project.json과 Project 초기화를 같은 저장 트랜잭션으로 확정한다.
확정 전 실패는 포인터와 새 Project를 함께 되돌린다. 기존 pending 참조의 명시적 초기화
경로는 유지하지만 새 요청에서는 부분 생성 상태가 공개되지 않는다. 소프트 삭제는 복원 요청을
요구하고 영구 삭제 후에는 새 ID를 만든다. 복제는 기본 프로젝트 참조를 복사하지 않는다.
사용법과 설정 반영 범위: [프로젝트 설정 안내](project-settings.md).

## RAG / GraphRAG 컴포넌트의 문서·검색 책임

분할, 임베딩/rerank, 트리플 추출, Chroma/BM25/Kuzu 색인과 검색을 모두
components/rag가 소유한다. 공개 컴포넌트는 RAGComponent 하나이며 선택 이름/디렉토리는
rag다. 등록/수정에는 embedding과 extractor를 주입한다. 문서 한 번 등록으로 벡터와
근거 관계를 함께 준비하고 같은 세대로 공개한다. extractor는 SLM/LLM/사용자 구현 중
선택할 수 있고, 검색할 때 별도의 SLM 질의 전처리를 강제하지 않는다.
asearch는 documents/entities/relations/sources를 반환한다. 실제 검색된 문단 ID로
근거 관계를 찾고 양 끝 엔티티의 나가는 관계를 제한된 깊이/개수로 확장한다.
확장된 문서 전체를 그래프 seed로 삼지 않는다. 문서와 근거는 같은 세대에서 읽는다.
문서 목록만 필요한 호출자는 asearch_documents를 사용한다. agraph_search는 정확한
엔티티 이름으로 시작하는 보조 API다. llm/inference와 components/graphrag는 제거했다.

RAG 세대는 graph.entities/relations와 graph.kuzu를 반드시 포함한다. 누락된 데이터를
빈 관계로 대신 반환하지 않는다. 구형 graphrag 이전 함수와 벡터 전용 세대 읽기는 제거했다.
기존 사용자 데이터를 자동 이전하거나 수정하지 않는다.

기존 acreate/asave/adelete 정의 CRUD는 그대로이며 aadd/aupdate/adelete_document가
문서와 검색 색인을 함께 변경한다. 모델 객체와 인증은 Component 생성자에 주입하고
저장하지 않는다. 기본 등록만으로 모델 호출이나 DB 생성을 시작하지 않는다.

RAGData는 ComponentData를 상속하여 최신 Project 선택/삭제 상태, backend
수명, workspace 소유권과 StorageIO를 공유한다. 모델 호출은 잠금 밖에서 await하며
세대/문서 revision을 확인한 뒤 공개한다. 충돌은 RAGConflictError이고 자동 재시도하지 않는다.
새 원문/벡터와 DB를 generations/<id>에 완성하고 닫은 다음 active.json 하나를 원자적으로
교체한다. 준비/색인 실패 시 이전 세대가 유지된다. 조회·공개·복제는 같은 잠금으로 직렬화한다.
모델 준비 중에는 기존 검색이 가능하지만 DB 재구축 동안에는 다른 저장 작업이 기다린다.

문서 수정은 그 문서만 다시 임베딩/추출하고, DB는 보존한 다른 문서의 벡터/근거를 재사용해
전체 재구축한다. 삭제는 모델 호출 없이 남은 문서로 재구축한다. 관계마다 문서/문단/인용을
보관하므로 한 문서를 삭제해도 다른 문서의 근거는 남는다. 복제도 저장된 벡터/관계를 사용하여
DB를 재생성한다. 프로젝트/컴포넌트 영구 삭제는 기존 디렉토리 소유권 규칙을 따른다.
종료·취소 중 수락한 DB 공개 작업은 끝까지 drain한다. 모델 준비 단계의 취소는 공개하지
않으며, backend 종료 후 돌아온 모델 결과는 수명 검사로 거부한다.

직접 문서 API는 완료를 기다리는 비동기 호출이며 별도 작업 큐/진행률 이벤트/자동 재개를
만들지 않는다. UI는 자신의 작업 상태를 표시한다. Component는 Run/Step을 쓰지 않는다.
Engine/Tool 호출에서는 기존 이벤트 계약을 통해 실행을 기록한다. 커뮤니티 요약 기반
전역 GraphRAG와 대규모 증분 색인은 범위 밖이다. 자세한 API/저장 구조: [RAG 안내](rag-components.md).

## 검색 Tool capability와 실행 연결

RAG는 rag 정의 capability와 tools capability를 제공한다. 프로젝트에서
선택된 컴포넌트는 tools capability 요청 시 항상 검색 Tool을 제공한다. 별도 활성화 API나
비활성화 설정은 없고 과거 search_tools_enabled 값도 해석하지 않는다. ToolComponent와
직접 결합하지 않고 공통 Tool/ToolRegistry 계약을 사용한다. Tool 정의 구성만으로 모델이나
DB 검색을 실행하지는 않는다. 백엔드에만 등록된 미선택 컴포넌트는 노출하지 않는다.

ComponentRegistry.resolve는 선택적인 data_factory를 받아 resolve_runtime을 호출한다.
기본 Component.resolve_runtime은 기존 resolve에 위임한다. 레지스트리는 Tool의 내용이나
검색 구현을 알지 않는다. RunManager가 자신의 SessionManager ProjectAccess와 StorageIO를
공유하는 ComponentData 핸들을 공급하며 컴포넌트 어댑터가 이 핸들로 공개 검색 API를 호출한다.
별도 기본 저장소를 만들거나 전역 현재 프로젝트를 사용하지 않는다. 직접 저수준 resolve로
활성 검색 Tool을 요청하려면 data_factory를 제공해야 하며 생략 시 명시적으로 실패한다.

RAG는 문서·관계·출처를 함께 반환하는 rag_search 하나를 제공한다.
호스트의 외부 검색 연결은 BuiltinTools의 external_rag_search/external_graphrag_search로
구분한다. 이 어댑터의 corpus/query/options는 호스트 계약이며 내장 검색의 프로젝트 선택을
우회하지 않는다. 예제는 examples/llm/rag_components.py에서 공개 API만 조합하고 별도의 DB/
분할/검색 구현을 두지 않는다. 이전 markdown_rag.py 호환 진입점은 제거했다.
Tool 목록은 Run 시작 시 구성하고 프로젝트·
컴포넌트 선택/삭제 및 실행 서비스 수명은 각 호출에서 검사한다. 파일 검색은 공유 잠금과
StorageIO를 사용하고 모델 await는 잠금 밖에서 수행한다. ToolExecutor가 인자·결과·실패를
EngineEvent로 내보내며 StepManager가 저장한다. 검색 오류를 빈 결과로 숨기거나 자동 재시도하지
않는다. GraphRAG 관계 결과와 원문 출처 버전은 하나의 잠금에서 읽는다.

## 대화 저장소 선택

Conversation은 Session 소유를 유지하고 저장 방식만 선택한다. 기본 ConversationStore는
append-only JSONL/fsync이며 MemoryConversationStore는 동일한 Conversation 상태 API를
메모리에 구현한다. projects.create/acreate(conversation_storage="file"|"memory")로
프로젝트별로 선택하고 Project.conversation_storage를 project.json에 저장한다.
LargeLanguageModel(conversation_storage=...) 또는 ServiceConfig(conversations=...)는
새 프로젝트의 기본값이다. 저장된 선택이 우선하며, conversation_storage 필드가 없는
프로젝트는 오류다. 기존 프로젝트를 읽는 것만으로 설정/대화를 이전하지 않는다.

SessionManager의 ProjectConversations가 프로젝트의 최신 선택으로 라우팅한다. 메모리
팩토리는 백엔드마다 하나를 소유하며 Session/workspace 경로로 분리한다. 실행/Facade/문맥/
복제는 같은 라우터를 공유한다. Project 복제는 선택을 보존하며, 다른 Project로 Session을
복제하면 대상 저장소를 사용한다. 저장 방식 변경은 Session이 없는 프로젝트에서만 허용한다
(소프트 삭제된 Session도 검사). 사용자 팩토리를 주입하면 프로젝트 선택은 None이어야 하며,
명시된 file/memory와 충돌할 때는 오류로 알려 임의의 저장소 대체를 방지한다.

QUEUED 메시지를 선택한 저장소에 먼저 반영한 후 런타임 큐에 넣는다. 파일 모드는 내구성을
유지하고, 명시적 메모리 모드는 백엔드/프로세스 종료 시 메시지와 대기 요청을 잃는다.
session.run.shutdown()은 메모리를 지우지 않으며, backend.shutdown()은 자신이 생성한 메모리를
정리한다. 직접 주입된 팩토리 수명은 호출자가 소유한다. Session/Project 영구 삭제는 선택적
discard(session) 계약으로 메모리도 해제한다. 코어 모델에는 저장소 객체를 넣지 않는다.

Project/Session/Run/Step 파일과 로그는 유지한다. 메모리 모드는 기존 대화 파일을 읽거나
이전하지 않으며, 응답 Message가 사라진 Run은 상태/Step 조회만 가능하다. stale 실행은
기존대로 interrupted로 복구하며 재실행하지 않는다. 자세한 사용법은
[대화 저장 방식](conversation-storage.md)에 있다.

## 서비스 확장 계약

`ServiceConfig`에서 저장소, 대화 문맥, 이벤트 처리기, 로그 출력을 구성한다.
LargeLanguageModel의 실행과 Facade/Project/Session 조회는 같은 Run/Step 저장소를 사용한다.
Engine은 `required_capabilities` 튜플을 선언하며 기본값은 빈 튜플이다. LoopEngine만
기본적으로 tools를 요청하고, PipelineEngine은 단계별 요구를 합친다. 일반 capability는
EngineContext.capabilities에 들어가며 도메인 영속 모델에는 저장되지 않는다.

RunManager는 내장 이벤트를 명시적으로 분기하고 사용자 이벤트는 EventHandlers로 전달한다.
처리기 오류는 Run 실패로 이어지고 비동기 처리기를 중단할 수 있다. EventContext의
메타데이터 저장은 기존 RunRepository/StorageIO를 거친다. EventSubscriptions는 관찰용
다중 구독을 제공하며 구독자별 유한 큐, 순서, backpressure와 종료 시 배출을 보장한다.

ContextPolicy의 full/recent/completed/recent_completed/budget은 모델 입력 선택만 바꾼다.
Query는 커서, 상태, 오프셋, 개수, 역순 조건을 제공한다. 기존 파일 저장소의 전체 스캔과
JSON/JSONL 형식은 유지되며 인덱스는 아직 도입하지 않는다. 제목/메타데이터 변경은
SessionManager.update_details로 최신 상태에 적용하고 Run 상태 저장 시 이를 보존한다.
실행 설정 변경, 삭제, 복제는 기존 런타임 해제 규칙을 따른다.

DomainLogger는 workspace 잠금 범위의 ContextVar로 전달되어 백엔드별 출력을 격리한다.
기본 도메인별 파일 로그를 유지하며 설정/동기 sink를 교체할 수 있다. ComponentData의
런타임 연결은 bind_runtime 공개 계약을 사용한다.

자세한 예시와 제약은 [서비스 확장 안내](service-extensions.md)를 참고한다.

## 정의 컴포넌트와 Workflow 그래프

Skill/MCP/Agent/Workflow와 RAG의 records API는 Component를 상속하는 정의 저장소이며 새 실행
계층이 아니다. DefinitionComponent는 열린 JSON 검증과 configuration/records capability
스냅샷을 제공한다. 기본 백엔드는 Tools, RAG, Skill, MCP, Agent, Workflow를 등록하되 Project에서 선택한
디렉토리만 생성한다. 명시적인 backend components 목록은 기본 등록을 대체한다.

Agent는 엔진/정책/입출력 계약을 가진 업무 정의, Skill은 지침, MCP는 연결 설정을 관리한다. RAG는 열린 정의와
별도의 문서 API를 제공한다. 정의 CRUD가 모델 호출, 프로세스 실행, 네트워크 연결이나 인덱싱을
일으키지 않는다. 목적별 모델 정의는 AgentComponent 하나로 통일하며 별도 Subagent 저장소는 없다.

Workflow schema_version 1은 entry/nodes/edges로 구성한다. branch의 조건별 port,
parallel/join의 합류, loop.body와 양의 반복 상한을 검증한다. 일반 연결은 DAG이고
반복은 명시적 loop 노드에만 표현한다. WorkflowGraph는 조립과 검증을 수행하고
GraphEngine이 등록된 async 노드 처리기로 같은 Run 안에서 실행한다. 저장·조회·수정·복제도
버전 1 검증을 통과해야 하며 버전 없는 JSON을 위한 우회 경로는 없다. 데이터와 사용 예시는
[컴포넌트 정의 안내](component-definitions.md)에 있다.

GraphEngine은 분기/병렬/합류/제한된 반복을 실행하며 처리기 참조를 시작 전에 검사한다.
노드 시작 이벤트를 서비스가 저장한 뒤 작업을 진행하고, Step 완료 metadata로 출력과
선택한 경로를 저장한다. 병렬 경로의 상태는 분리되며 실패 시 형제 작업을 취소/정리한다.
전체 노드 수/실행 시간과 처리기 동시 실행 수를 제한한다. 코드 검증 예제와 개발자 계약은
[GraphEngine 안내](graph-engine.md)에 있다. GraphEngine은 영속 파일을 직접 쓰지 않는다.

## 기본 Tool과 공유 실행기

BuiltinTools는 명시적인 작업 루트에 파일 도구와 선택적 명령/검증/Git 기능을 연결한다.
외부 웹/브라우저/커널/검색/하네스 제안 Tool은 호스트 어댑터가 있을 때만 등록한다.
카탈로그 등록과 Project 활성화는 분리되며 자동으로 실행 권한을 부여하지 않는다.
자원 수명은 BuiltinTools async context가 담당하고, 자체 프로세스의 출력/시간을 제한한다.
명령 실행의 cwd는 OS 샌드박스를 의미하지 않는다.

LoopEngine과 GraphEngine의 ToolNode는 ToolExecutor를 공유한다. 실행 인자/결과는
기존 Step metadata에 저장하고 파일별 별도 도메인 이력을 만들지 않는다. Run/Step 저장은
여전히 RunManager/StepManager가 담당한다. AgentData의 프롬프트 변경은 Project 잠금과
revision 확인 뒤 수행하며 이후 Run에 적용한다. 사용법과 제약은
[기본 Tool 안내](builtin-tools.md)에 있다.

BaseEngine은 LiteLLM의 Tool 호출 ID를 불투명 문자열로 보존한다. Gemini 응답 서명이
포함되면 ID가 길어질 수 있으므로 고정 256자 제한 대신 max_argument_chars와 같은
유한 크기 상한을 적용하고, 다음 assistant/tool 메시지에도 동일한 ID를 전달한다.

## Library overview

The `llm` package is an AI execution library and plugin for the `ish.platform`
Linux shell host, designed for long-running and concurrent AI work.

A user creates a Project with model and runtime configuration. The Project becomes an independent workspace that can gradually accumulate memory, workflows, tools, sessions, and other project-specific state.

The application deliberately separates persistent domain state from runtime execution state.

## ish platform integration

`llm/llm.py` replaces the former `ish/demo.py` entry point. The platform selects
`<plugin-name>/<plugin-name>.py` before `__init__.py`, reads its literal
`PLUGIN_META` using AST, checks Python dependencies, then imports `llm.llm`.
Other plugin dependencies belong to `requirements`; Python distributions belong
to `dependencies`, with `distribution|import_name` aliases supported. The Python
package is `llm` and the distribution is `ish-llm` so neither replaces the host's
`ish`. No `ish` compatibility alias is installed.

The host registers `main` through `prompt.set_tool`. It serializes the callable
for a separate PTY worker, applies the shell cwd/environment before unpickling,
and passes parsed positional arguments as strings. `main(*argv)` therefore
parses those explicit arguments, owns its asyncio loop, and reports status via
SystemExit (the worker ignores return values). The entry point creates a new
Project/Session for each invocation and guarantees RunManager shutdown. The
Project → Session → Run → Step boundaries and persistence layout remain
unchanged, including the existing `.ish.lock` ownership protocol.

Plugin import does not start workers, create workspaces, or call models. The
current command is a single request adapter, not a persistent conversation UI.
Future UI integration should own long-lived Session-bound managers and translate
EngineEvent/RunEvent without moving storage into Engines. A background worker
or IPC interface has not been introduced. Reloading source requires a host
restart; the host's unload of the entry module does not clear every `llm.*` module.

ChromaDB, Kuzu and rank-bm25 are declared dependencies for planned retrieval
components, not implemented Engine strategies. They remain optional `rag`
extras for library installation, but the host checks all declared plugin
dependencies. The plugin targets Linux Python 3.12.14. Offline loader/pickle tests do not verify dependency
installation, Linux PTYs, or a Nuitka build.

## Backend facade and service handles

`LargeLanguageModel`, defined in `llm/llm.py`, is the public backend factory
available as `plugin.get("llm").LargeLanguageModel`. It owns one ProjectManager,
one EngineRegistry and a lazily created RunManager per (Project ID, Session ID).
ProjectManager creates SessionManager when omitted and accepts component instances
as an iterable, constructing ComponentRegistry internally; explicit injection
remains supported. The facade defaults to registering Tool/Skill/MCP/RAG/Agent/Workflow
components and LoopEngine,
without initializing unselected component directories.

`llm/services/api.py` contains navigable service handles: Projects -> ProjectHandle
-> Sessions -> SessionHandle -> Runs -> RunHandle -> Steps. Persistent core models are
unchanged and never hold these handles, schedulers or registries. `.data` reloads
detached domain state. Mutations delegate to existing lifecycle services; Run/Step
history access holds workspace ownership and checks the parent identities.
Engine execution still goes only through a Session-bound RunManager. `run.engine`
is its persisted strategy name, not an independent Engine execution interface.

`project.components.tools.create(definition)` persists a ComponentData definition,
independent of Python handlers. Generic named access does not import
Tool types. Runtime tool binding stays in ToolComponent/ToolRegistry. All component
operations still reload Project selection and enforce lifecycle/locking rules.

Components may declare `data_class`, a ComponentData subclass; None/omission uses
the generic handle. Registration validates the class, and ProjectManager.component
constructs it with the same access/registry/Project/name arguments. Generic named
access and registry never import Tool types. ToolComponent declares ToolData in
`components/tools/data.py`: enable/disable/set_enabled/enabled acquire workspace
ownership and reload Project selection before delegating to ToolComponent.
Selection read/modify/write stays within one lock and preserves unknown config
keys. Adding/removing a name is idempotent; replacement rejects duplicate names.
Definitions/handlers are not required to edit selection. The stored enabled list,
runtime binding and per-Run capability snapshots remain unchanged. Custom handle
methods must use the same lock and lifecycle check; there is no automatic method
forwarding to raw components.

Construction is synchronous with no filesystem writes or asyncio loop creation.
Async execution binds the backend to one process/event loop. Repeated Session loads
share one scheduler. Session shutdown detaches only that runtime; later submission
creates a new manager. Backend shutdown rejects new work, attempts every runtime
shutdown, drains cancellation, and is terminal. Storage failure is reported after
all shutdowns are attempted. The host owns input/event wiring and must await
shutdown; no implicit .ishrc shutdown hook or cross-process backend transport is
provided. The single-request CLI uses this same facade inside an async context.

The development and test target is Linux Python 3.12.14 only. `.python-version`
and `pyproject.toml` pin it. The Linux snapshot runner verifies the interpreter
before running focused and full suites; use `.venv-linux312/bin/python`.

### Request completion and async facade I/O

Facade Runs.submit returns RequestHandle bound to the durable user Message ID;
RunManager.submit retains its Message return contract. RequestHandle.wait delegates
to RunManager.wait_request and returns the matching terminal RunHandle. A request
can be reopened by ID using Runs.request/arequest. Runtime notifications are only
wakeups: the persisted Message.run_id and Run status are the source of truth.
Waiters capture the change signal before reading state to avoid lost wakeups.
Each waits for that signal or the worker's termination, without polling files or
waiting for the whole Session queue. Multiple/cancelled/timed-out waiters do not
cancel the Engine or each other. Worker/storage errors propagate; a stopped worker
leaves queued requests durable and reports RuntimeError. Reading terminal history
does not start recovery; waiting on pending input does. Stale Runs are never replayed.

ProjectHandle/SessionHandle.results expose RunResultQuery list/load and alist/aload.
RunHandle.result/aresult returns ExecutionResult, response/aresponse returns the
Assistant Message from ConversationStore. RequestHandle.result/aresult is None
until its Run exists. These are detached query views, not persisted result copies.

Storage facade methods retain synchronous variants and add a-prefixed async ones
through storage.async_method. Project/Session/Run/Request data properties have
aget_data(); Session has aconversation(); components.aget avoids a blocking initial
lookup. ComponentData has async config/CRUD methods and ToolData has async selection
methods. Component handles obtained through the facade use its lifecycle guard
and storage runner; direct ProjectManager handles use their own StorageIO lane.
Custom methods opt in explicitly; there is no arbitrary method forwarding.

LargeLanguageModel owns an additional StorageIO lane and tracks admitted facade
operations. Their complete read/modify/write transactions run in worker threads
under existing workspace ownership, including handle construction and selection
validation. Shutdown rejects new calls, drains admitted facade I/O, then stops all
RunManagers. A per-backend context variable permits already admitted synchronous
operations to finish while shutdown is pending. Cancellation drains writes before
propagating; no asyncio objects enter domain persistence. Sync APIs still block and
queries still scan full scopes. Async calls bind to the original process/event loop.
The later Query API adds conditional paging without a database or new persistence format; file scans remain.

## Current Implementation

The initial workspace contained documentation only. The `llm` package now
implements the domain and persistence/runtime foundation described below:

* `llm/core`: Project, Session, Message, Run, and Step dataclasses, persisted enums, and major paths (native slots)
* `llm/engines`: Engine protocol, context snapshots, event types, registry, BaseEngine event lifecycle and LiteLLM chunk handling, LiteLLM LoopEngine, and sequential PipelineEngine/PreparationStep
* `llm/components`: generic directory/JSON base and registry; Tool, Skill, MCP, RAG, Agent, Workflow definition CRUD; unified RAG document CRUD, embedding/triples, local indexing and retrieval; requested runtime capabilities with a separate Tool adapter; Workflow structural validation
* `llm/providers`: LiteLLM synchronous stream transport, bounded async bridge and request container copying
* `llm/services/lifecycle/components.py`: ComponentData lifecycle-checked, workspace-locked access to selected component data
* `llm/services/lifecycle/projects.py`: ProjectRepository and ProjectManager with delegated initialization and Session lifecycle coordination
* `llm/services/lifecycle/sessions.py`: SessionRepository metadata persistence, SessionManager lifecycle/conversation snapshot cloning, and runtime-only SessionRuntime definition
* `llm/services/history/conversation.py`: append-only JSONL conversation events
* `llm/services/history/context.py`: shared conversation ordering and Run/clone snapshots
* `llm/services/lifecycle/access.py`: authoritative Project loading and lifecycle/ownership checks
* `llm/services/runtime/runs.py`: RunRepository persistence, RunEventPublisher notifications, and RunManager ownership of SessionRuntime queues/orchestration/recovery
* `llm/services/lifecycle/steps.py`: StepRepository metadata persistence, StepManager lifecycle, and StepEventRecorder translation
* `llm/services/results.py`: RunResultQuery, read-only Project/Session views of Run observations
* `llm/services/infrastructure/logging.py`: domain-scoped rotating operational logging
* `llm/services/infrastructure/storage.py`: atomic JSON helpers, validated directory removal, StorageIO background execution, and cancellation draining
* `llm/services/infrastructure/locking.py`: workspace OS ownership, shared attachment guards, and scoped transaction locking
* `tests/llm/support/fake_engine.py`: deterministic test fixture, excluded from the product package
* `llm/llm.py`: a one-request streaming CLI example with an optional arithmetic tool

There is currently no TUI or dedicated SingleEngine. GraphEngine is implemented. Project
configuration holds extensible JSON completion, engines,
session_defaults and data sections. Session.config holds overrides. CompletionResult
is persisted in Run metadata; ExecutionResult is a computed query view in core/results.py. Portable file-Project backups, restore into an absent ID, and explicit backup-copy upgrades are implemented.
Online backup and general retention/cleanup policies remain outside that contract.

The supported development and test runtime is Linux Python 3.12.14. Domain models
use native dataclasses with slots, enum.StrEnum, asyncio.timeout and
contextlib.aclosing. These preserve persisted fields/string values, timeout
semantics and stream cleanup without a version compatibility layer or backports.
Type-hint resolution, frozen models, cancellation and path safety retain regression
coverage in tests/llm/test_python312.py. Symlink checks protect owned paths. Queue
ownership, Engine events, persistence responsibility and stored formats are unchanged.

The runtime uses one owning service container per workspace, in one process/event
loop, with one RunManager instance per Session. ProjectRepository creates WorkspaceOwnership, shared by ProjectManager and
SessionManager through ProjectAccess. Synchronous manager transactions acquire an
exclusive nonblocking OS lock on `<projects-root>/.ish.lock`. Each attached Session
retains ownership from before recovery through shutdown and storage drain, even
when idle. Competing repository instances/processes raise WorkspaceBusyError
before recovery or lifecycle mutation. Different workspaces are independent;
Sessions in the owning workspace still execute concurrently.

The lock uses Linux fcntl.flock, releases on handle close
or process exit, and never uses PID age to steal ownership. The permanent lock
file is outside Project trees and must never be deleted while processes might
access the workspace. This is cooperative local-filesystem locking, not a
distributed lease or protection against arbitrary filesystem writers. NFS/SMB and
Linux deployment behavior need platform verification. Create a fresh repository
after process fork; do not share inherited live service instances.

Within the owner, synchronous transactions use an instance-owned threading RLock.
Attached Session IDs are shared across SessionManagers bound to the same repository,
preventing duplicate attachment or lifecycle mutation even while idle. Shutdown
all affected runtimes before removal/cloning. No locks enter persisted models.

Public manager APIs supply ownership scopes automatically. ProjectRepository also
guards reads/writes. Direct lower-level Session/Run/Step repository, ConversationStore,
and component writes require the caller's `repository.ownership.scope()` because
they cannot infer an arbitrary injected workspace root. They remain trusted
storage APIs, not an authorization boundary.

ProjectManager binds its ProjectAccess to SessionManager. Mutation/execution entry
points reload Project state rather than trusting stale handles. Public save
edits existing active Project/Session configuration only; it cannot change managed
deleted/execution state or recreate missing metadata. Session runtime state writes
use RunManager's internal SessionManager path, requiring an attachment. Repositories
remain lower-level storage APIs and are not authorization boundaries.

ConversationContextBuilder owns turn ordering for both Run context and Session
cloning. RunManager and SessionManager receive a ConversationStore factory rather
than choosing storage paths at every call. Defaults retain the existing Session
JSONL layout. RunManager retains one store per attached Session and releases it on
shutdown. ConversationStore projects only newly appended complete events using
a byte offset and file identity/size/mtime. Replacement/truncation resets the
projection; malformed complete records repeatedly fail. Reads return deep copies.
A threading RLock protects each store. External in-place edits to append-only
records are unsupported.

StorageIO runs filesystem transactions with asyncio.to_thread, with one in-flight
operation per RunManager and no unbounded executor submission queue. Recovery,
QUEUED creation, Run begin/finalization, context reads, Step writes, and deltas
await this ordered lane. Engines/tools and UI callbacks stay on the event loop.
Injected synchronous storage factories, repositories, context builders, and
capability resolvers must support worker-thread calls: no required running event
loop or thread-affine connections.

Cancellation drains submitted disk operations before propagating. Accepted
submit/start/shutdown calls finish before forwarding cancellation: a cancelled
submit can still accept and schedule its request. Do not blindly resubmit.
Interrupt during Run preparation records intent and finalizes the claimed Run
as interrupted without executing its Engine. Shutdown holds ownership until
workers and disk work stop. An unresponsive filesystem can delay shutdown.
Synchronous Project/Session APIs remain available; async UI callers use the facade's
a-prefixed methods or, for direct manager calls,
`StorageIO(projects.ownership).run(projects.create, ...)`. Do not pass coroutine
APIs such as RunManager.submit to StorageIO.

Engine contexts are snapshots of committed turns through the current request.
Assistant answers are paired with user inputs by Run ID, since queued requests
may be appended before a preceding answer exists. Future queued inputs are
excluded. Engines return async iterators; RunManager closes streams that expose
`aclose`. Engines must propagate cancellation and put JSON-safe
values in event metadata. Execution error details are preserved as strings with
stable Run error codes; there is no exception-message masking.

Run creation, input commitment, assistant creation and Session activation share one
storage commit. Recovery still treats an already claimed queued input as committed
when inspecting independently written records. It interrupts stale pending/running Runs and
Steps and streaming Assistant messages without replaying claimed requests.
The worker owns finalization, including when cancellation arrives before the
Engine coroutine starts. Failed Engines do not discard subsequent queued input.

Shutdown stops new submissions, interrupts active executions, and stops workers
while retaining queued JSONL messages. `wait_idle` reports a stopped worker if
shutdown prevents its queue from draining. Acknowledged writes remain fsynced;
RunManager waits off the event loop. Mutable JSON uses atomic replacement and
POSIX directory fsync. Conversation appends fsync the file each time and sync the
directory on first creation; appending does not change the directory entry.
QUEUED is durable before queue insertion; streaming notifications follow durable
deltas. Per-delta operational logging is omitted because JSONL already records
the event; message lifecycle and domain operation logging remain. Conversation replay ignores a final unterminated record, and the
next append truncates only that incomplete tail. Malformed complete records
raise errors rather than silently discarding history.

`ProjectManager.delete` and `SessionManager.delete` mark metadata in place by
default. Their keyword-only `permanent=True` option removes the complete owned
tree after runtime, identity, path containment, and symlink checks.
Project deletion checks all Sessions, including soft-deleted Sessions, before removal.
Restore reloads metadata, so permanently deleted objects cannot be resurrected
by restore. Workspace ownership excludes cooperating writers during removal; arbitrary
filesystem mutation remains unsupported; recursive
deletion is not transactional and an I/O failure may leave a partial tree.
The old `soft_delete` API was removed. Session clones normalize turn
order and copy conversation/configuration with fresh Message and Session IDs,
clear Run links, and cancel copied queued requests. They do not copy Runs,
Steps, or artifacts. Project cloning delegates Session snapshots to SessionManager
and copies Project configuration. Selected components define their own clone
policy: the common Component base copies configuration and records for Tools,
Workflows and Agents. Record IDs remain stable within the new Project so graph
references still resolve. Other subsystem artifacts are not copied by default.

The test suite is `python -m unittest discover -s tests/llm -t . -v` after installing the
package dependencies. It needs no live API or credentials and includes an
actual subprocess crash/restart test plus an installed LiteLLM SDK test using
mock HTTP SSE and a local test tokenizer. The sections below describe the broader target architecture;
features beyond the scope above remain planned.

## Session-bound Run API and lifecycle notifications

Each request requires an explicit Engine: `submit(content, engine="loop")`.
Both the facade and RunManager use a required keyword-only string argument.
There is no Project/Session fallback or inference from available registrations.
An omitted argument fails before execution of submit; invalid empty/non-string
values use `engine_required`, and unavailable names use `engine_not_registered`.
Validation precedes queue admission. The chosen name is stored with the QUEUED
message and copied to Run.engine when execution begins.

ProjectConfig no longer inserts or treats default_engine as a reserved setting.
Its open JSON keys remain preserved. Session no longer has a default_engine field
or creation argument; SessionRepository rejects removed fields without rewriting metadata. Existing queued messages with an Engine keep their
choice on restart. Missing/malformed selections create a FAILED Run with an empty
engine name and `engine_required`, publish failure, and allow the worker to
continue. They never execute an inferred Engine. Stale Runs are still never replayed.

The one-request CLI likewise requires `--engine loop`; it currently registers
only LoopEngine. Other registered Engine names are selected through the backend
API. Default LoopEngine registration in LargeLanguageModel is availability only.

RunManager(sessions, engines, session=session) binds exactly one Session. Its public runtime
methods no longer take Project/Session: start(), submit(content, engine=...), wait_idle(),
interrupt(), shutdown(). Project ownership and current Session/configuration are
reloaded through SessionManager before use. One manager owns one SessionRuntime and one
conversation store. Different managers share the workspace service container and
OS ownership coordinator; their Sessions still run concurrently. shutdown releases
only the bound Session after draining its worker/storage; other Sessions continue.

New requests validate Engine and selected component registrations before admission.
RunRequestError exposes a stable code for registration errors without a new
message/Run or attachment. Accepted requests retain QUEUED -> COMMITTED durability.
Recovery still never replays stale Runs. Restored queued requests with unavailable
Engines fail with engine_not_registered and retain their Run/conversation history.

RunEvent/RunEventType are separate from EngineEvent and are emitted by RunManager,
not Engines. on_run_event receives STARTED/COMPLETED/FAILED/INTERRUPTED after durable
state transitions, on the event loop, with detached snapshots and callback-error
isolation. Recovery emits INTERRUPTED for each newly recovered stale Run. Run's
optional error_code field describes terminal failures; ExecutionResult
also exposes error/error_code. Exception details are preserved, not masked.

Codes distinguish engine_not_registered, component_not_registered (admission),
capability_failed, engine_failed, interrupted and process_restart. wait_idle still
means the queue drained; clients inspect terminal Run events/results for failures.
A storage error before durable finalization propagates through wait_idle/shutdown;
no terminal callback claims a terminal save that failed. Notifications are not a
persisted subscription log and a reconnect should query the Run repository.

ProjectConfig is an open dict subclass, not a dataclass. Arbitrary top-level keys
survive save/load/clone and are included in EngineContext.settings with Session
merging. Constructor kwargs, mapping access, existing attribute shortcuts and
JSON to_dict/serialize/deserialize are supported. Reserved execution sections
retain semantic validation; no field-name-specific secret policy remains.

Components declare capability names and implement resolve(project, capability).
ComponentRegistry invokes only matching components; the requested capability is
the only value built. Definition CRUD validates data without runtime handlers.
Tool handler binding occurs only while resolving tools for execution. Agent
and Workflow JSON CRUD likewise has no runtime registration dependency.

## Service Module Organization

서비스 루트에는 공개 Facade와 서비스 조립, 조회 API만 남긴다. 구현은 책임별로 묶되
Project → Session → Run → Step의 소유권과 저장 형식은 바꾸지 않는다.

| 경로 | 책임 |
| --- | --- |
| `api.py`, `configuration.py` | 공개 Facade와 서비스 의존성 조립 |
| `query.py`, `results.py` | 목록 필터와 Run 결과 조회 |
| `lifecycle/projects.py`, `sessions.py`, `steps.py` | 도메인 Repository/Manager, StepEventRecorder |
| `lifecycle/access.py`, `components.py` | Project 접근 검사와 ComponentData |
| `runtime/runs.py`, `events.py`, `tools.py` | Run 실행, 이벤트 처리/구독과 ToolExecutor |
| `history/conversation.py`, `context.py` | 파일/메모리 대화와 입력 문맥 정책 |
| `infrastructure/storage.py`, `locking.py`, `logging.py` | 파일 I/O, 잠금, 운영 로그 |

클래스는 필드/초기화 → 내부 구현 → 공개 API 순서로 정의한다. 공개 속성의 getter/setter와
`async_method`로 구성한 비동기 API는 정의 의존 순서를 유지한다.
`__call__`, `__getitem__` 같은 호출 프로토콜도 공개 API 구역에 둔다. 이 규칙은 services의
클래스에 적용하며 도메인 필드와 Enum 멤버의 선언 순서는 보존한다.

access는 Project/Session이 공유하고, locking은 파일 직렬화에 의존하지 않는다. 하위 패키지의
`__init__.py`는 구현 모듈을 미리 import하지 않아 Engine/Component와 순환 import가 생기지
않게 한다. RunEventPublisher는 runs.py, EventHandlers/EventSubscriptions는 events.py를
유지한다. 새 코드는 다음 경로를 사용한다.

```python
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.runtime.runs import RunManager
from llm.services.history.context import ConversationContextBuilder
from llm.services.infrastructure.storage import StorageIO
```

이전 평면 import 경로와 `_compat`는 제거했다. import/monkeypatch와 pickle 클래스
참조에는 책임별 패키지 경로를 사용한다. 패키지 탐색 경로 추가나 sys.modules 별칭은 없다.
정상 공개 패키지 재수출, Python 버전 대응 어댑터, 명시적 데이터 이전은 이름 별칭과
구분한다. async_method는 파일 I/O를 실행 루프 밖으로 보내는 정식 비동기 API다.

## Repository and Manager Responsibilities

Each mutable metadata domain has a repository and a manager in the same service
module:

| Module | Repository | Manager |
| --- | --- | --- |
| `projects.py` | ProjectRepository | ProjectManager |
| `sessions.py` | SessionRepository | SessionManager |
| `runs.py` | RunRepository | RunManager |
| `steps.py` | StepRepository | StepManager |

Repositories own metadata serialization, atomic writes, path resolution, loading,
listing, and ID/parent ownership checks. Managers own lifecycle policy and
delegate storage to their repositories. SessionRepository also initializes the
Session subsystem root and reloads persisted state for SessionManager's active-Run
guard. StepRepository handles the existence check used by StepManager to reject
duplicate Step IDs. ProjectManager continues to delegate subsystem initialization
and Session lifecycle work without knowing Session storage internals.

Managers expose their repository through `repository`. SessionManager and
StepManager can be constructed without arguments or with an injected repository.
RunManager accepts only `repository=RunRepository()` and exposes `.repository`;
the former `runs=` and `.runs` aliases are removed. RunManager retains its execution-oriented API while
ProjectManager and SessionManager retain workspace/session lifecycle APIs.

RunManager lives in `runs.py`; SessionRuntime is defined in `sessions.py` and imported
by RunManager, which still owns all runtime scheduling. Import RunManager from
`llm.services.runtime.runs`. The previous `run_manager.py` module has been removed.
This is module consolidation only: RunRepository and RunManager are still
separate classes, and persisted Session objects still contain no runtime objects.

Message persistence intentionally remains in ConversationStore, whose append-only
event log differs from mutable domain metadata. Engine remains an execution
protocol and does not acquire a persistence repository. JSON/JSONL formats,
enum values, and queue/recovery semantics remain unchanged. Project metadata
requires an explicit `components` list and `conversation_storage` field. Missing
selections are errors rather than inferred from existing directories or backend defaults.

## Flexible Configuration and Project Execution History

ProjectConfig owns completion, engines, session_defaults and data
JSON dictionaries. Configuration validation rejects runtime objects, non-string
keys and non-finite numbers at construction/save; arbitrary field names are allowed.
ProjectRepository preserves model/temperature/api_base as ordinary workspace keys.
Only completion configures model calls; no fields are relocated on load. Session JSON
requires config. There is no credential resolver.

SessionManager.create copies session_defaults then merges explicit config. Session save and
clone preserve config with independent containers. EngineContext.settings(name)
merges Project completion/engines[name]/data with Session overrides. Arrays/scalars
replace defaults; nested dicts merge. Engine constructor arguments override saved
values. Loop resolves its registered Run engine name unless settings_name is supplied;
within Pipeline this defaults to the Pipeline registration name. Its per-Run shallow
instance copy preserves subclass methods and shared SDK handles while replacing
Loop-owned configuration. All execution state stays local. BaseEngine authors
choose a section name and read settings explicitly. RunManager reloads the Project
before a Run, so later edits affect future Runs only.

COMPLETION EngineEvents carry CompletionResult snapshots: a stable call ID,
Step ID, model/response ID, finish reason, status, timestamps, duration, numeric
usage and usage_complete. BaseEngine.stream_completion yields these observations
alongside strings; BaseEngine.execute attaches its Step ID and forwards them.
Text consumers may opt out with include_events=False. Custom Engine-protocol code
must emit observations itself for detailed LLM accounting. Non-LLM/non-reporting
engines still receive a terminal Run summary with unknown usage.

The helper requests include_usage by default and retains only numeric usage data,
including nested token details. Observations update on identity, finish reason or
usage changes and completion/failure, not every text delta. RunManager persists
snapshots in Run metadata by call ID before notifying observers. Cancellation and
generator close never yield final events; the last durable snapshot is retained.
A provider may omit usage; unknown counts remain None, and interrupted streams do
not claim a complete aggregate. Observations are not billing guarantees.

Run metadata is the sole persisted source for completion observations. RunRepository
finalizes pending/running observations when saving terminal Runs, including crash
recovery. ExecutionResult.from_run builds a detached query view and normalizes old
terminal Run snapshots in memory without rewriting them on read. No Project-level
result files or persisted aggregates are created.

RunResultQuery resolves a Project or Session scope under workspace ownership and reads
RunRepository. ProjectManager, SessionManager and RunManager expose it as results;
RunManager supplies its configured Run repository. Custom query repositories can
be injected through the RunReader protocol. Session scope avoids a Project-wide scan.
List defaults to terminal Runs in active Sessions; include_deleted/include_running
make wider queries explicit. Direct loads allow soft-deleted history but verify
owner scope. Permanent Session deletion removes its Runs and results; clones contain
no Run history. Old Project state/executions JSON is ignored and never a fallback
for missing/deleted Runs. It is left untouched rather than deleting historical data
automatically. Existing Run observations already contain the source records.

## Reusable Model Inference

llm/components/rag owns the runtime model clients. EmbeddingModel.embed and
RerankModel.rerank can be called by Engines, Tools, preparation callbacks or
RAG components. They are not execution strategies and have no execute(), registry,
Run ownership or lifecycle persistence. The primary hierarchy is unchanged.

Each client snapshots open-ended LiteLLM kwargs and invokes aembedding/arerank.
Call arguments override constructor defaults; request containers are copied while
live client/callback identities remain intact. Shared defaults do not hold per-call
state. The shared private ModelClient supplies lazy off-thread SDK import and async
dispatch. Only providers.parameters is shared with BaseEngine; model-client imports load
neither Engines, services nor core domains. Model results and exceptions retain
SDK types; cancellation propagates, subject to provider cleanup behavior.

Model defaults can live in ProjectConfig.data.inference and Session data overrides,
read through context.settings(name). Callers own mapping configuration into clients,
inputs, output/index persistence and observability. Within a Run, self.step or
PreparationStep can record embedding/rerank lifecycle; Tool handlers may call the
same clients. No automatic model-call usage recording is introduced outside the
existing COMPLETION event contract. Inference responses include native metadata;
callers decide which numeric observations to emit. Rerank billing units must not be
silently summed as completion tokens. No vector database, RAG CRUD, model download,
embedding Engine or rerank Engine is added.

## Beginner Engine Authoring

engines/base.py defines BaseEngine alongside the existing Engine protocol,
context and events. EngineRegistry lives in engines/registry.py. BaseEngine is
re-exported from llm.engines. Override
run(context) or supply action=: return an async text iterator, yield strings from
an async generator, forward COMPLETION observations, or perform an async operation
returning None. The inherited
execute() wraps the operation in one Step and maps text to TEXT_DELTA. Engines
with several Steps override execute() and use self.step(context, action, name=...)
for each operation. Consumers forwarding these streams use compat.aclosing.

BaseEngine retains the optional whole-Step timeout, JSON-safe copied metadata,
failure events with exception details, and iterator cleanup. Cancellation/GeneratorExit propagate
without yielding while closing; RunManager finalizes interrupted active Steps.
Cleanup errors do not replace cancellation/close; normal cleanup failures fail the
Step. Execution/assembly state stays local, so one instance can serve several Runs.
Developer actions and lifecycle labels remain trusted code/data.

BaseEngine.stream_completion(request, response=None) calls the existing bounded
thread transport in providers/litellm.py, whose worker invokes litellm.completion.
The method enforces stream=True and one choice, reads dict/SDK chunks, yields text,
assembles indexed tool-call fragments in ordinary dictionaries, and validates
termination, duplicate IDs and size limits. An optional fresh response dictionary
receives a completion-format assistant message only after success; it remains
unchanged on failure/cancellation. The method emits COMPLETION observations alongside strings, but no Step
lifecycle events and no application deadline. Use run()/step() for lifecycle and deadline handling.
It handles text/tool completion streams and numeric usage observations;
reasoning/multimodal content is not surfaced. It executes no tools and performs no domain persistence.

copy_params() retains live SDK clients/callbacks while copying builtin containers.
Per-call transcripts are isolated even when a cancelled provider thread is still
finishing its network read. Base imports the lightweight transport only; LiteLLM
is lazily imported in the worker. Non-LLM actions do not invoke/import the SDK.
BaseEngine remains completion-specific. Reusable embedding/rerank operations
live in inference and do not inherit it.

LoopEngine is the only class in engines/loop/engine.py and inherits BaseEngine. Its
responsibility is Project/history/prompt configuration, batch validation before
side effects, tool execution and bounded iteration. Each completion/tool uses
self.step(), and every completion uses the inherited stream_completion().
PreparationStep also inherits BaseEngine; PipelineEngine composition is unchanged.
StepManager/StepEventRecorder still persist lifecycle events, and RunManager still
owns queues, Runs, streaming conversation writes, cancellation and recovery.

This is a public API rename: engines/step.py and engines/_completion.py were removed.
Use BaseEngine instead of StepEngine, and direct LoopEngine constructor limits
instead of LoopOptions/options=. LoopEngineError/_Turn/_ToolCall are removed;
validation uses ValueError/TypeError and wrapped execution retains contextual
RuntimeError. LLM Step errors stay 'LLM iteration failed' and tool errors stay
'Tool execution failed'. The Engine protocol remains usable without inheritance.
The offline examples/llm/custom_engine.py demonstrates minimal authoring/registration;
README also shows a minimal inherited LiteLLM completion engine.

## Developer Engine Composition and Completion Parameters

LoopEngine remains specifically coupled to LiteLLM completion and its chat/tool
stream format. No cross-library model abstraction, embedding, or rerank operation
is introduced. providers/litellm.py is the existing thread/stream transport; its
worker directly calls litellm.completion so synchronous network reads do not block
the event loop.

LoopEngine directly accepts max_iterations, request_timeout, tool_timeout,
buffer_size, max_tool_calls, max_argument_chars and max_output_chars. LiteLLM
options such as max_tokens belong in the open-ended completion_kwargs mapping. A synchronous context factory may supply that mapping.
Project config supplies defaults; explicit kwargs override them. Builtin option
containers are copied once per Loop execution and again per provider request,
while SDK clients/callbacks retain identity. These objects are runtime configuration,
not persisted JSON. Runtime api_key or SDK environment authentication is used.
No parameter allowlist attempts to mirror every LiteLLM release. Loop transcript
and tool-registry ownership remain reserved (messages/tools/functions/function_call),
and stream=True/n=1 are required. All other options are passed to LiteLLM; newer
response formats may still require Engine changes. Provider timeout is independent
of the application per-round deadline. system_prompt is a string or synchronous
context factory, prepended once to the in-memory provider transcript.

LoopEngine.settings_name defaults to None, selecting context.run.engine rather
than a hard-coded "loop" section. EngineContext.settings() shares that default;
an explicit section name overrides it. Pipeline stages therefore use the owning
pipeline's registered name unless configured with settings_name="stage-name".
Each Run-local Loop copy preserves that explicit choice. Registering a shared
LoopEngine under different names does not mutate the shared Engine instance.

EngineContext.state is a fresh dictionary per Run for preparation outputs and
runtime handles. RunManager's context construction creates it; neither core
models nor repositories acquire this field. Do not copy these outputs to event
metadata by default, because they may contain documents or runtime-only values.

engines/pipeline/engine.py supplies PipelineEngine(stages) and PreparationStep(name,
action, kind=..., timeout_seconds=...). PreparationStep emits lifecycle events
around an async action(context), with a 60-second default deadline (None disables
it). Pipeline passes one context through its sequential stages, including nested
pipelines. A failure, cancellation event, or unfinished Step prevents the next
stage. Iterator cleanup is propagated; RunManager still owns Run cancellation,
StepEventRecorder persistence, and queue/recovery semantics. Pipeline creates no
extra Run. Actual cancellation propagates so RunManager records INTERRUPTED;
non-success terminal events without cancellation fail the pipeline.

Preparation runs after Run creation, before the Loop. Document/RAG/environment
work delegates to developer callbacks and component services. Per-Run values use
context.state and can be consumed by Loop parameter/prompt factories. Callbacks
must cooperate with cancellation and offload blocking work; arbitrary side effects
cannot be rolled back. Never mutate process-global environment for one Session.
Use a Run-local env dictionary for subprocesses. Existing stale Runs, including
interrupted preparation, are never replayed automatically.

## LiteLLM Loop Execution

LoopEngine calls synchronous `litellm.completion(stream=True, ...)` through
`providers/litellm.py`. Each request has a dedicated daemon thread that
creates, reads, and closes its provider stream. A bounded queue bridges chunks
to the async event loop with backpressure. Cancellation stops delivery at once;
Python cannot forcibly interrupt an in-progress synchronous network read, so
the worker closes the stream after the read returns or its provider timeout
expires. These cleanup threads never execute tools or touch persistence.

The Engine resolves Project/Session configuration, emits an LLM Step,
and forwards each `delta.content` as TEXT_DELTA. Indexed `delta.tool_calls`
fragments are accumulated into complete call IDs, function names, and JSON
arguments. Only registered tools are callable. The entire batch is validated
against JSON Schema before any tool executes, and calls are executed serially
as individual tool Steps. Their observations are added to the next LLM request.

A normal `stop` response ends the Run. A `tool_calls` response starts another
round when budget remains. Missing/abnormal finish reasons, malformed tools,
tool failures, timeouts, and exhausted iteration budgets fail the Run. No
engine retries are performed. LiteLLM defaults to `num_retries=0`; developers may
override SDK request retries through completion_kwargs without enabling tool or
stale-Run replay. Calls on the
last permitted iteration are not executed without a follow-up LLM round.
Defaults are eight LLM rounds, 60 seconds per whole LLM round, and 30 seconds
per async tool. Tool handlers must propagate cancellation and avoid blocking.

The Engine still writes no files. RunManager and StepEventRecorder persist its
events through the existing services. RunManager's optional synchronous
`on_event(run, event)` observer receives snapshots after persistence, enabling
immediate display. RunEventPublisher isolates observer exceptions, recording a
operational `observer.failed` event without failing the Run. Callbacks must remain
synchronous and nonblocking. Engine/storage exceptions retain their execution
failure semantics; display errors are not execution errors.

User-visible deltas update the Assistant message while running. A final text
output replaces the message through a new JSONL event. Intermediate and internal
outputs remain in the Run output journal. Tool arguments are Step metadata and
Tool results are exposed as Step.output.data. Raw provider response objects are
not persisted. Stale Runs remain interrupted and are never automatically resumed.

Authentication is delegated to the SDK environment or runtime completion_kwargs.
There is no application credential service, reserved secret directory, key-name
blocking or error-message masking. Runtime objects are rejected by JSON validation.

Project components inherit `Component` from `llm/components/base.py`, declaring a
name and a direct-child directory. The base owns safe directory creation/removal,
open JSON codecs, configuration, definition CRUD and cloning. ComponentRegistry
validates identity and directory ownership; its declared-capability resolution mechanism
is generic and has no Tool dependency. Component paths are not in ProjectPaths.
Core Session initialization remains mandatory, independent of optional selection.

Component configuration is in ProjectConfig.component_configurations; owned records use `<project>/<directory>/records/<id>.json`.
All mutable JSON uses atomic replacement. Unknown keys survive round trips;
runtime objects, non-string keys and non-finite numbers are rejected.
Component-specific validation/codecs can evolve without a central fixed dataclass.
ToolComponent stores enabled names and optional native function-tool definitions;
AgentComponent stores purpose, explicit engine, open execution settings, resources and work contracts;
WorkflowComponent stores open graph data and validates version 1 node connections,
branch ports, parallel joins and bounded loop bodies. Node handlers, external resource
references and graph/agent execution belong to developer Engines, not this storage base.

ProjectManager.create/set_components coordinate idempotent initialization.
ComponentData returned by projects.component reloads Project state and selected
components under workspace ownership for each CRUD/configuration call. Updates
hold the lock through read/modify/write. Direct lower-level component access needs
an explicit ownership scope; async UIs use StorageIO for synchronous APIs.

Disabling retains data. ProjectManager.remove_component(permanent=True) requires
inactive/detached Sessions, publishes disabled selection, then delegates recursive
removal to the component. Failed removal stays disabled and may be retried. Linked
ancestors/entries and escaping paths are rejected. Core-owned sessions/logs/state/cache
and duplicate component directories cannot be claimed. Developer components are
trusted code, not a sandbox. Removal and initialization are not cross-file atomic.
Failed create/clone is soft-deleted; failed additions leave old selection intact
but may leave partial directories. Cloning copies JSON config/records, not artifacts.

ToolComponent declares tools in its capabilities tuple and resolve(project, "tools")
returns a ToolRegistry. Other capability values are not created by that call. The separate
ComponentToolResolver adapts those resolved values; RunManager wraps ComponentRegistry
passed as capabilities. Custom resolvers implement resolve(project, names);
Tool-only resolver fallback is removed. Only requested capabilities are constructed.
A tools request gets a fresh fixed snapshot, using persisted overrides or
registered catalog definitions with application-registered handlers. Unknown handlers
fail during runtime binding, not data CRUD/configuration/clone; saved names never
import code, and deleting an enabled override is
rejected. Provider extension keys survive into the completion tools argument.

Workflow records require schema_version 1. Legacy component.json files are rejected;
configuration is resolved from ProjectConfig and declared defaults. Re-enabling still performs
normal idempotent initialization. GraphEngine executes registered node handlers.
ToolExecutor records JSON arguments/results in Steps. RAG owns local indexes and
retrieval; MCP/Skill execution adapters require their own implementation.
See `llm/components/README.md` for authoring and CRUD examples.
Every submitted request explicitly selects an Engine. Registration defaults do not imply an execution choice.

## Domain Hierarchy

The primary execution hierarchy is:

Project
→ Session
→ Run
→ Engine
→ Step

Conversation belongs to Session rather than Run.

Conceptually:

Project
├── Configuration
├── Memory
├── Tools
├── Workflows
└── Sessions
└── Session
├── Conversation
└── Runs
└── Run
├── Engine
└── Steps

## Project

Project represents a persistent workspace.

It contains long-lived configuration such as:

* primary LLM
* API base
* temperature
* reasoning configuration
* embedding model
* reranker model
* SLM model
* Engine-specific settings
* metadata

ProjectPaths exposes major Project paths such as:

* root
* memory
* sessions
* state
* logs
* cache

ProjectPaths intentionally does not expose every nested subsystem path.

For example, ProjectPaths exposes `memory`, while MemoryComponent owns everything beneath that directory.

## ProjectManager

ProjectManager is a lifecycle orchestration service rather than a persistence implementation.

Its responsibilities include:

* Project creation
* loading and saving through ProjectRepository
* cloning
* archive import/export
* soft deletion
* restoration
* backup
* migration coordination
* subsystem initialization coordination
* Session cleanup coordination

Subsystem initialization is delegated through initializer objects.

ProjectManager does not know how Memory, Workflow, Tools, or Sessions internally structure their directories.

## Session

Session represents one long-lived independent AI session inside a Project.

A Session may continue across many user messages and many Runs.

A Session can be:

* created
* cloned
* soft deleted
* restored

Session stores persistent state such as:

* Session ID
* Project ID
* title
* status
* per-Session Engine settings
* current Run ID
* metadata

Runtime objects are intentionally excluded.

## Session Runtime

RunManager maintains runtime-only Session state.

Conceptually:

SessionRuntime
├── asyncio.Queue[message_id]
├── worker asyncio.Task
├── current execution asyncio.Task
└── closed state

These objects are not serialized.

The durable Message queue remains the source of truth.

## Conversation

ConversationStore owns conversation persistence.

Storage format is append-only JSONL.

Instead of rewriting complete Assistant messages during streaming, events are appended.

Example:

message.create
message.delta
message.delta
message.delta
message.status

Loading a conversation replays the event stream to reconstruct current Message objects.

This allows partial streaming output to survive unexpected process termination.

## Message Lifecycle

Typical user input:

QUEUED
→ COMMITTED

Typical Assistant response:

STREAMING
→ COMPLETED

Interrupted Assistant response:

STREAMING
→ INTERRUPTED

Failed response:

STREAMING
→ FAILED

Every user request is initially stored as QUEUED.

Only when RunManager actually starts a Run is the corresponding Message promoted to COMMITTED.

This prevents requests from disappearing if the process terminates before execution.

## Run

Run represents one Engine execution for one user request.

A Run contains:

* Run ID
* Session ID
* input Message ID
* Assistant Message ID
* Engine name
* status
* timestamps
* error
* metadata

A Session can contain many Runs over its lifetime.

Run is intentionally separate from Session so that interrupt, failure, retry policy, diagnostics, and execution history remain precise.

`core.models.validate_run_transition` validates lifecycle edges without performing
I/O: start permits PENDING → RUNNING; finish permits RUNNING → COMPLETED, FAILED,
INTERRUPTED, or PAUSED; restart recovery permits PENDING/RUNNING → INTERRUPTED.
Terminal attempts are not reopened. Explicit resume creates a new Run, while
queued-request cancellation remains a Message transition without creating a Run.

RunManager groups its final status/error/error_code in an internal immutable
`_RunOutcome`. Before any finalization mutations, it validates the transition and
the Session's current Run identity. Duplicate or stale finalization is rejected.
This adds no persistence owner or execution layer. Existing transaction boundaries,
Step/Assistant/Run/Session write order, cancellation handling and post-commit Run
notifications remain unchanged. Recovery shares only the pure validator, not the
normal finalization I/O sequence. The storage format remains version 1.

## Scheduling

Runs inside one Session execute serially.

Example:

Session A:
Run 1 → Run 2 → Run 3

Different Sessions have independent workers and may execute concurrently.

Example:

Session A: ──────────────
Session B: ────────
Session C: ───────────────────

This model preserves conversation ordering while supporting parallel AI work.

## Interrupt Semantics

Normal input during a Run does not interrupt that Run.

It is persisted as QUEUED and processed later.

Explicit interrupt:

1. stores the new request durably if one was supplied
2. cancels the current execution
3. marks the Run INTERRUPTED
4. marks partial Assistant output INTERRUPTED
5. preserves all queued user requests
6. lets the Session worker continue

## Engine

Engine defines the execution strategy.

Engine strategies (LoopEngine and GraphEngine are implemented; SingleEngine remains planned):

### SingleEngine

One direct completion-style request.

Typical use:

* short questions
* translation
* summarization
* simple generation

### LoopEngine

Agent-style iterative execution.

Typical pattern:

reason
→ action
→ observation
→ reason
→ action
→ observation

Typical use:

* coding
* debugging
* investigation
* multi-step tool use

### GraphEngine

Executes a predefined workflow graph.

Typical use:

* repeatable workflows
* retrieval pipelines
* structured research
* multi-stage processing

Engine implementations emit EngineEvent objects instead of directly mutating persistence layers.

## Step

Step is the smallest persistent observable execution unit inside a Run.

Possible Step kinds include:

* llm
* tool
* shell
* retrieval
* rerank
* graph_node

A Run may therefore look like:

Run
├── Step: llm / reason
├── Step: filesystem / read
├── Step: llm / reason
├── Step: shell / pytest
└── Step: llm / analyze

Step statuses:

* pending
* running
* completed
* failed
* interrupted
* cancelled

## StepManager

StepManager owns Step lifecycle and delegates metadata persistence to StepRepository.

It does not perform the actual work represented by a Step.

Engine emits events such as:

* STEP_STARTED
* STEP_COMPLETED
* STEP_FAILED
* STEP_INTERRUPTED
* STEP_CANCELLED

StepEventRecorder translates those events into StepManager operations.

This keeps Engine execution independent from filesystem persistence.

## Recovery

The application assumes that execution may stop unexpectedly.

On restart:

A stale Run that was PENDING or RUNNING becomes INTERRUPTED.

A stale Step that was PENDING or RUNNING becomes INTERRUPTED.

A stale Assistant Message that was STREAMING becomes INTERRUPTED.

QUEUED user messages are restored into the runtime scheduling queue.

Stale Runs are not automatically retried.

This is intentional because prior Steps may have executed side effects such as:

* modifying files
* running shell commands
* sending API requests
* writing external data
* creating commits

Automatic Run replay could execute those effects twice.

## Persistence Layout

A conceptual Project layout is:

projects/
└── <project-id>/
├── project.json
├── memory/
├── tools/
├── workflows/
├── state/
├── logs/
└── sessions/
├── .trash/
└── <session-id>/
├── session.json
├── conversation.jsonl
├── attachments/
├── state/
├── logs/
└── runs/
└── <run-id>/
├── run.json
├── state/
├── logs/
└── steps/
└── <step-id>/
├── step.json
├── state/
├── logs/
└── artifacts/

Subsystem implementations are free to evolve below their owned root directory.

## Logging

Conversation storage and application logging are separate concerns.

Conversation contains user-visible communication.

Logs contain operational diagnostics.

Recommended logging hierarchy:

application logs
+
Project logs
+
Session/Run/Step logs when necessary

Operational logs follow the lifecycle field schema.

The current implementation uses standard-library `logging.Logger` and
`RotatingFileHandler` to append structured JSON to each domain's
`logs/service.log` (1 MiB with three backups). Project/Session/Run/Step repositories
log persistence operations; managers log lifecycle and runtime operations.
Conversation mutations log event type and identity under Session, never text.
Completion observations use RunRepository and its existing Run logs.
Result queries create no separate persistence or result-log hierarchy.

Log fields are allowlisted: UTC time, event, IDs, status, count, permanent flag.
No raw exceptions, arbitrary metadata, prompts, answers, or provider objects are
logged. Domain directories are not recreated solely for logging. Handlers close
after each write to bound descriptor lifetime. Log write errors emit
a generic warning and do not normally prevent domain persistence. Logs are best
effort operational diagnostics, not a crash-durable or transactional audit trail.

Permanent deletion removes the deleted domain's own logs. The final Session
deletion record is written to Project logs; the final Project deletion record
goes to `<projects-root>/logs/service.log`. Default soft deletion retains all
domain logs. Linked log files/directories are rejected; all filesystem access
still assumes trusted paths and exclusive process ownership.

## UI Model

The UI should expose complexity gradually.

Primary concepts visible to ordinary users:

Project
→ Session

Run, Engine, and Step may be shown as execution details.

A Session view can show:

* conversation
* current Engine
* active Run
* current Step
* queued requests
* other active Sessions

A separate Session navigator should provide parallel-work visibility while allowing the current Session to use most terminal width.

## Future Direction

Major remaining components include:

* production LiteLLM SingleEngine
* broader LoopEngine provider compatibility and durable provider tool-message replay
* broader host browser/kernel integrations beyond the existing injected Tool adapters
* indexed long-term memory search and optional Engine-level memory selection policies
* additional GraphEngine node handlers and Workflow UI
* centralized log export and durable audit requirements if needed
* prompt-toolkit UI
* sustained live-provider and deployed Linux-host validation beyond the automated WSL suite


## 공통 사용자 요청/승인 계약

`core.interactions`의 InteractionRequest/InteractionOption/InteractionResponse는 UI와
Engine이 공유하는 데이터 계약이다. Project → Session → Run → Step 계층은 유지한다.
Loop Tool 승인, Graph pause_before/ToolNode 승인, 중첩 Agent 승인은 동일한 요청 형태로
체크포인트에 포함된다. EngineEvent.interaction은 영속 요청과 일치해야 하며 알림은 저장 후 전달한다.
RunRepository는 응답 저장을 InteractionRepository에 위임하고 Facade와 RunManager가
같은 저장소 및 소유권 잠금을 사용한다. Engine은 파일을 직접 쓰지 않는다.

RunHandle의 interactions/interaction_responses/respond 및 대응하는 비동기 API가
조회와 답변 저장을 제공한다. 응답 저장은 실행을 시작하지 않으며 기존 명시적 resume가
새 Run을 만든다. 응답은 요청 버전/지문과 결합되고 중복·상충 응답/만료를 검사한다.
재개 전 Tool/Engine/Workflow/호스트 정책 변경 검증과 불확실 효과 재시도 승인은 유지한다.
추천 선택지와 UI 우선순위는 자동 승인 권한이 아니다. Project approval 정책은 호스트가
명시적으로 허용한 카테고리/위험도 안에서 응답만 저장한다. InteractionView는 응답과 실행
상태의 공통 투영이다. 취소/갱신/불확실 효과 재시도도 Run 소유권 아래서 처리한다.

Graph의 제한 시간 이후 남은 취소 정리는 RunManager PendingWork가 추적한다. Session 소유권과
대화 저장소는 실제 종료까지 유지한다. Engine에는 파일 저장 책임을 추가하지 않는다.
Memory는 부분 요약 커서, 재검색, 레코드 캐시, 명시적 통합 저널을 직접 소유한다. 서비스의
공통 문맥 처리 계약에 Memory 전용 분기는 추가하지 않는다.
자세한 사용법과 한계는 [domain-hardening.md](domain-hardening.md), [interactions.md](interactions.md)를 참고한다.


## 공통 조회·진단 계약

core/contracts.py는 Diagnostic, ResourceRef, OperationProgress와 JSON 변환을 정의한다.
core/views.py의 SessionRuntimeView/RunView는 원본 도메인 기록을 조회한 사본이다.
core/plans.py는 ResumePlan/RecoveryPlan/RecoveryResult/RetentionPlan을 각각 정의한다.
소유 서비스가 이 타입을 반환하며 원본 버전·승인·효과 정책을 다시 검증한 뒤 실행한다.
예외와 실행 상태는 기존 도메인이 소유한다. Diagnostic은 설명이며 재시도 권한이 아니다.
EngineEvent의 공통 관찰 정보는 StepEventRecorder를 통해 소유 Step metadata로 저장된다.
RAG job_progress도 같은 표시 타입을 사용하지만 상태 전이·저장은 RAG 컴포넌트가 담당한다.
ProjectConfig/Component 정의와 업무별 데이터는 dict로 유지한다. 새 실행 계층이나 범용
Manager를 추가하지 않는다. API 변경과 예시는 [data-contracts.md](data-contracts.md)에 있다.
