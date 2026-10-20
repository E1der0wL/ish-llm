# 전체 문서 검토 목록

기준 main `94229cb865374d99f20ec48c5e48269488041a4e`, 검토·문서 갱신 2026-10-20.
기존 문서 **118개**(`docs/` 79, `llm/` 29, `hub/` 10), 21,910행의 전문을 읽고
코드 블록을 제외한 870개 절, Python 예제, Schema·호출·Reader/Writer·테스트와 대조했다.
모든 문장을 실행 검증했다는 의미는 아니다. [구체적 근거와 미해결 항목](llm/documentation-review.md),
[새 단계 계획](llm/clean-refactor.md)을 함께 본다. 아래는 검토 범위의 고정 목록이며 모두 수정했다는 뜻은 아니다.

현행 계약 안에 있는 날짜별 시험·작업 이력은 해당 날짜의 기록이다. 과거 기록은 새 호환성 의무를 만들지 않는다.
제품 프롬프트/스킬 9개는 동작에 영향을 주는 자산으로 검토만 했으며 이번 변경에서 보존했다.

## docs 전체

| 문서 | 분류 | 판단·유효 계약 |
|---|---|---|
| [docs/README.md](README.md) | 현행 문서 진입점 | 계약 우선순위·현재/목표/과거 구분과 전체 목록. |
| [docs/hub/README.md](hub/README.md) | 현행 설계 | 책임·분류/승인 경계와 전용 문서 목록. 이전 본문은 history.md로 보존. |
| [docs/llm/naming-conventions.md](llm/naming-conventions.md) | 현행 명명 계약 | 의미 규칙 보존. B/C와 충돌하는 구 API·지문 보존 예외 제거; 앞선 Naming 변경 기록 표시. |
| [docs/llm/architecture.md](llm/architecture.md) | 현행 설계 | 실제 main 구조·소유권·원자 저장/외부 효과를 분리. 목표와 이전 본문 링크. |
| [docs/llm/project-authority.md](llm/project-authority.md) | 현행 기능 계약/사용법 | Project 권한·Host 기술 deny·Engine/Component 상속·명시 의존성. 직접 helper는 trusted 호출자 책임. |
| [docs/llm/schema-ownership.md](llm/schema-ownership.md) | 현행 기능 계약/사용법 | 닫힌 정의와 owner 있는 open object, handler/client 검증 위임. Agent 현행 필드는 B/C 변경 대상. |
| [docs/hub/backend-contract-handoff.md](hub/backend-contract-handoff.md) | 과거 요구·작업·검증 기록 | 당시 범위·완료/실패·파일 manifest 보존. 현재 계약은 연결된 전용 문서/소스/시험으로 판단; 과거 pass 수 승계 안 함. |
| [docs/hub/coding-workflow.md](hub/coding-workflow.md) | 현행 설명 + 날짜별 검증 기록 | 제품 스킬·실제 저장/검증 안내. 모델 지침은 Tool 권한이나 수행 보증 아님. |
| [docs/hub/completion-display.md](hub/completion-display.md) | 현행 설명 + 날짜별 검증 기록 | 공개 생각 요약과 phase/elapsed/헤더 projection. 새 Provider model/usage로 직접 이관. |
| [docs/hub/component-managers.md](hub/component-managers.md) | 현행 기능 계약/사용법 | 선택 component·정규 이름·CRUD/CAS·RAG plan/lease. B Agent 관리 직접 전환. |
| [docs/hub/error-handling.md](hub/error-handling.md) | 현행 설명 + 날짜별 검증 기록 | 원래 Diagnostic 보존·단일 backend classifier·Tool callback admission·분류와 복구 권한 분리. |
| [docs/hub/home-navigation.md](hub/home-navigation.md) | 현행 설명 + 날짜별 검증 기록 | 현재 홈/중단/문맥 선택 계약. 오래된 README 키 설명보다 현행 전용 UX 기준. |
| [docs/hub/maintenance.md](hub/maintenance.md) | 현행 설명 + 날짜별 검증 기록 | UIRequests·settings·observability·동시 callback 소유. 날짜별 회귀 기록은 과거. |
| [docs/hub/mouse.md](hub/mouse.md) | 현행 기능 계약/사용법 | Hub-owned UI 동작·X11 실제 roundtrip. 제품 실행 권한과 별도; 과거 Skip은 현재 baseline과 분리. |
| [docs/hub/operation-recovery.md](hub/operation-recovery.md) | 현행 기능 계약/사용법 | fresh plan/revision으로만 resume/restart. 효과 불명 시 새 submit 우회 금지. |
| [docs/hub/output-objects.md](hub/output-objects.md) | 현행 기능 계약/사용법 | 출력 객체와 renderer registry·표 크기·UI 제한은 UI owner. 외부 효과 승인과 무관. |
| [docs/hub/panel-ux.md](hub/panel-ux.md) | 현행 설명 + 날짜별 검증 기록 | 현재 panel/settings draft·partial save·프로젝트 선택. 실행 API 변화와 독립인 UX 보존. |
| [docs/hub/presets-and-resume.md](hub/presets-and-resume.md) | 현행 기능 계약/사용법 | preset은 앱 생성값, explicit resume/restart 승인·미저장 draft 보호. fallback submit 금지. |
| [docs/hub/provider-recovery.md](hub/provider-recovery.md) | 현행 설명 + 날짜별 검증 기록 | backend 예산 내 editable body만 축약, 자체 retry loop 없음. 앱 수치 선택과 구 옵션 제거를 구분. |
| [docs/hub/questions-tools.md](hub/questions-tools.md) | 현행 기능 계약/사용법 | enabled 유지·승인/질문·sandbox Tool·입력과 Run 재개 분리. 위험 설명은 허가가 아님. |
| [docs/hub/rag.md](hub/rag.md) | 현행 기능 계약/사용법 | 현재 Job 진단·재개 설정 fingerprint·Vision·catalog. 옛 generation 자동 변환 없음. |
| [docs/hub/rendering-performance.md](hub/rendering-performance.md) | 현행 설명 + 날짜별 검증 기록 | worker/UI thread 분리, bounded snapshot·원본 journal 재조회. 병렬화 시 immutable projection 유지. |
| [docs/hub/request-history.md](hub/request-history.md) | 현행 기능 계약/사용법 | 논리 턴 위치·부분 출력 유지, backend 새 Run과 UI projection 구분. D 1:N 직접 이관. |
| [docs/hub/resource-authoring.md](hub/resource-authoring.md) | 현행 기능 계약/사용법 | 모델 authoring은 현재 Project·공개 Schema·Refinement/승인 사용. 저장≠실행. |
| [docs/hub/response-reliability.md](hub/response-reliability.md) | 현행 설명 + 날짜별 검증 기록 | WSL 당시 진단/설정 변경과 현재 명시 retry 안전성 구분. 이전 수치 무조건 복원 금지. |
| [docs/hub/shell-environment.md](hub/shell-environment.md) | 현행 기능 계약/사용법 | 요청 cwd/env·승인 지문·path adapter 보존. 옛 환경 없는 Run fallback 제거 계획(D09). |
| [docs/hub/structure-audit.md](hub/structure-audit.md) | 과거 요구·작업·검증 기록 | 당시 범위·완료/실패·파일 manifest 보존. 현재 계약은 연결된 전용 문서/소스/시험으로 판단; 과거 pass 수 승계 안 함. |
| [docs/hub/structure.md](hub/structure.md) | 현행/과거 설명 혼재 | 소유 구조·미사용 제거 기록. 코드 위치는 현재 구조와 대조, 과거 manifest는 역사 자료. |
| [docs/hub/tool-retry.md](hub/tool-retry.md) | 현행 기능 계약/사용법 | backend no-effect/admission·재승인 유지, 보조 모델 no-tools. 앱 선택값 owner-local 선언. |
| [docs/hub/usage-and-system-messages.md](hub/usage-and-system-messages.md) | 현행 기능 계약/사용법 | 일별 usage·system message는 저장 원본의 UI projection. |
| [docs/llm/agents.md](llm/agents.md) | 현행 기능 계약/사용법 | 현재 재사용 정의/Graph vs behavioral Agent. 독립 Project Agent는 B에서 새 계약. |
| [docs/llm/architecture-boundaries.md](llm/architecture-boundaries.md) | 현행 기능 계약/사용법 | 계약/불변식/실행 정책/제품 정책 구분. 기술 sizing·프로토콜 값을 숨은 정책으로 오판하지 않음. |
| [docs/llm/backend-api.md](llm/backend-api.md) | 현행 공개 API | 현재 root Engine 요청과 핸들·설정·공개 승인/Resume 예제. 새 Agent API를 구현된 것처럼 안내하지 않음. |
| [docs/llm/backend-recovery.md](llm/backend-recovery.md) | 현행 기능 계약/사용법 | Diagnostic·CompletionRepair·Run/RAG plan API. App safe/retryable 플래그는 권한 증거가 아님. |
| [docs/llm/boundary-evolution-review.md](llm/boundary-evolution-review.md) | 과거 요구·작업·검증 기록 | 당시 범위·완료/실패·파일 manifest 보존. 현재 계약은 연결된 전용 문서/소스/시험으로 판단; 과거 pass 수 승계 안 함. |
| [docs/llm/builtin-tools.md](llm/builtin-tools.md) | 현행 기능 계약/사용법 | 명시 shell/env·파일 경로·승인·부분 프로세스 보장. Tool 사용 가능과 승인 구분. |
| [docs/llm/checkpoints.md](llm/checkpoints.md) | 현행 기능 계약/사용법 | plan/CAS/abandon/delete/restart 증거. valid state만으로 실행 허용 아님. source 손상 명시 거부. |
| [docs/llm/code-simplicity-review.md](llm/code-simplicity-review.md) | 과거 요구·작업·검증 기록 | 당시 범위·완료/실패·파일 manifest 보존. 현재 계약은 연결된 전용 문서/소스/시험으로 판단; 과거 pass 수 승계 안 함. |
| [docs/llm/completion-processing.md](llm/completion-processing.md) | 현행 기능 계약/사용법 | prepare/after_completion/finish/close 순서·출처 보호. F는 관찰 hook 변조가 아닌 새 verdict 계약. |
| [docs/llm/component-definitions.md](llm/component-definitions.md) | 현행 기능 계약/사용법 | 정의 CRUD와 실행 분리·명시 선택·참조 검증. Agent 저장 책임만 B/C로 이동. |
| [docs/llm/configuration-audit.md](llm/configuration-audit.md) | 과거 요구·작업·검증 기록 | 당시 범위·완료/실패·파일 manifest 보존. 현재 계약은 연결된 전용 문서/소스/시험으로 판단; 과거 pass 수 승계 안 함. |
| [docs/llm/conversation-storage.md](llm/conversation-storage.md) | 현행 기능 계약/사용법 | file/memory 선택과 수명·restart 한계. file 초기 형식은 CRUD 계약이며 숨은 모델 실행 정책과 다름. |
| [docs/llm/data-contracts.md](llm/data-contracts.md) | 현행 기능 계약/사용법 | ResourceRef/Diagnostic/plan은 데이터 사본. ID/분류/계획 조회가 실행·삭제 권한을 생성하지 않음. |
| [docs/llm/data-lifecycle.md](llm/data-lifecycle.md) | 필수 설계 계약 + 미해결 감사 요약 | owner의 무결성·수명주기, Application 보존/선정과 Backend 안전한 삭제의 분리. 손상 원본 보존·조건부 격리/삭제·증거 초기화 금지. 구현 완료 아님. |
| [docs/llm/lifecycle-verification.md](llm/lifecycle-verification.md) | 단계별 보강·실행 기록 | H1 이후 실제 실패·수정·검증 범위와 미완료 항목. |
| [docs/llm/domain-hardening.md](llm/domain-hardening.md) | 현행 기능 계약/사용법 | 승인 영속화·취소 정리·기억 generation/CAS. 문서 코드 예제는 자동 승인의 일반 허가가 아님. |
| [docs/llm/engine-output.md](llm/engine-output.md) | 현행 기능 계약/사용법 | append/replace/final 저널·저장 후 알림·Step 소유. F의 final 후보 표현과 같이 검증. |
| [docs/llm/graph-checkpoints.md](llm/graph-checkpoints.md) | 현행 기능 계약/사용법 | uncertain key 명시 재시도·binding 검증·UI resume. 현재 공통 nested bridge 문서와 함께 해석. |
| [docs/llm/graph-engine.md](llm/graph-engine.md) | 현행 기능 계약/사용법 | LangGraph 스케줄링·Workflow 원본·handler 확장. 예제 검증 기록은 과거 결과. |
| [docs/llm/handoff.md](llm/handoff.md) | 과거 요구·작업·검증 기록 | 당시 범위·완료/실패·파일 manifest 보존. 현재 계약은 연결된 전용 문서/소스/시험으로 판단; 과거 pass 수 승계 안 함. |
| [docs/llm/interactions.md](llm/interactions.md) | 현행 기능 계약/사용법 | 질문/승인/confirmation 의미, 응답 저장≠실행, renewal 지문·policy 자동 응답 조건. |
| [docs/llm/ishrc-loop-test.md](llm/ishrc-loop-test.md) | 현행 기능 계약/사용법 | 실제 모델/인증 명시 실행 안내. 문서 존재가 이번 live 모델 실행 증거는 아님. |
| [docs/llm/long-running-capabilities.md](llm/long-running-capabilities.md) | 현행 기능 계약/사용법 | 명시 compaction·부분 진행·큰 Tool 결과 참조. 현재 입력·Tool pair 보존, 미완료 요약 미적용. |
| [docs/llm/long-running-components.md](llm/long-running-components.md) | 과거 요구·작업·검증 기록 | 당시 범위·완료/실패·파일 manifest 보존. 현재 계약은 연결된 전용 문서/소스/시험으로 판단; 과거 pass 수 승계 안 함. |
| [docs/llm/long-running.md](llm/long-running.md) | 현행 사용법 | 공개 승인/plan, policy.provider, 필수 Memory/retention 설정, 현재 RAG 청크·캐시 계약으로 정정. |
| [docs/llm/markdown-rag-test.md](llm/markdown-rag-test.md) | 현행 기능 계약/사용법 | 현재 examples 진입점과 과거 실모델 기록 분리. 예제 기본값은 backend 정책과 별도. |
| [docs/llm/memory-processing.md](llm/memory-processing.md) | 현행 기능 계약/사용법 | 명시 recall/extract/summary·원본 hash/CAS·Run-local processor. 선택 기능을 자동 활성화하지 않음. |
| [docs/llm/memory.md](llm/memory.md) | 현행 사용법 | 닫힌 content, 명시 검색·Tool 쓰기 정책; config.ui 및 암묵 기본값 설명 제거. |
| [docs/llm/nested-workflows.md](llm/nested-workflows.md) | 현행 기능 계약/사용법 | 같은 환경 workflow/다른 환경 Graph Agent, 부모 Tool scope, 중첩 깊이 명시·PendingWork. |
| [docs/llm/operational-storage.md](llm/operational-storage.md) | 현행 사용법 + 제거 대상 | 현재 backup/restore와 transaction 유지. upgrade는 실제 잔존 API지만 제거 대상임을 명시. |
| [docs/llm/operations-and-ui-settings.md](llm/operations-and-ui-settings.md) | 현행 기능 계약/사용법 | UI describe와 실행 값 분리, operation probe/reconcile은 명령 재실행 아님. retention/usage 소유. |
| [docs/llm/process-isolation.md](llm/process-isolation.md) | 현행 기능 계약/사용법 | sandbox 필수 capability·명시 network·실패 시 비격리 강등 금지. CI 완료 gate(D24). |
| [docs/llm/production-readiness.md](llm/production-readiness.md) | 현행 검증 상태 | 동일 main 실제 기준선의 실패·Skip·미검증과 운영 한계. 과거 본문 별도 보존. |
| [docs/llm/project-authority-review.md](llm/project-authority-review.md) | 과거 요구·작업·검증 기록 | 당시 범위·완료/실패·파일 manifest 보존. 현재 계약은 연결된 전용 문서/소스/시험으로 판단; 과거 pass 수 승계 안 함. |
| [docs/llm/project-policies.md](llm/project-policies.md) | 현행 기능 계약/사용법 | 공통 정책과 구현 config/policy 구분. 기본값 복원·옛 평면 설정 alias 금지. |
| [docs/llm/project-settings.md](llm/project-settings.md) | 현행 기능 계약/사용법 | ProjectConfig가 설정 원본. 전문 configure와 Project 저장은 같은 잠금/CAS. |
| [docs/llm/provider-validation.md](llm/provider-validation.md) | 현행/과거 설명 혼재 | 현행 단일 청크/벡터 검증·retry guard 계약과 특정 시점 실행 결과를 구분. |
| [docs/llm/rag-components.md](llm/rag-components.md) | 현행 기능 계약/사용법 | 문서 generation/revision·검색/원본 소유·metadata-only CRUD와 document API 구분. |
| [docs/llm/retry.md](llm/retry.md) | 현행 기능 계약/사용법 | backend eligibility·같은 예산/기한·인자 재검증/재승인. Tool business key 임의 변경 금지. |
| [docs/llm/revisions.md](llm/revisions.md) | 현행 기능 계약/사용법 | 변경 token·generation ABA 방지·소비자 opaque 비교. 구 기록 generation 자동 생성 없음. |
| [docs/llm/runtime-reliability.md](llm/runtime-reliability.md) | 현행 사용법 + 제거 대상 | ToolRuntime와 Project 정책 분리. 현재 구현 기능과 미검증 운영 영역 구분, legacy callback 제거 계획. |
| [docs/llm/schema-ownership-review.md](llm/schema-ownership-review.md) | 과거 요구·작업·검증 기록 | 당시 범위·완료/실패·파일 manifest 보존. 현재 계약은 연결된 전용 문서/소스/시험으로 판단; 과거 pass 수 승계 안 함. |
| [docs/llm/service-extensions.md](llm/service-extensions.md) | 현행 기능 계약/사용법 | BackendServices 등록·capability·EventHandlers/Subscriptions. metadata 보호와 관찰/권한 구분. |
| [docs/llm/settings-consistency.md](llm/settings-consistency.md) | 현행 기능 계약/사용법 | Component는 Session override 대상 아님. 같은 모델 인자 이름으로 다른 owner에 방송하지 않음. |
| [docs/llm/shared-boundary-review.md](llm/shared-boundary-review.md) | 과거 요구·작업·검증 기록 | 당시 범위·완료/실패·파일 manifest 보존. 현재 계약은 연결된 전용 문서/소스/시험으로 판단; 과거 pass 수 승계 안 함. |
| [docs/llm/shared-contracts.md](llm/shared-contracts.md) | 현행 기능 계약/사용법 | 선택된 자식 binding·STEP_COMPLETED+checkpoint 원자 ACK·모호한 승인 거부·owner 조회 계약 보존. |
| [docs/llm/steering.md](llm/steering.md) | 현행 기능 계약/사용법 | 선택 대상·scope·적용 ACK·일회성 예약·삭제/재개 원본 보호. 적용은 모델 준수 보증 아님. |
| [docs/llm/storage-transactions.md](llm/storage-transactions.md) | 현행 저장 계약 | 기본 Project 표현 정정, 첨부 Step/checkpoint receipt의 원자 저장과 별도 이벤트 구분. |
| [docs/llm/test-workspaces.md](llm/test-workspaces.md) | 현행 기능 계약/사용법 | Project 공간 record/ready/보존·명시 삭제, 기존 ToolExecutor 승인·receipt. 실제 sandbox 테스트 유지. |
| [docs/llm/tool-constraints.md](llm/tool-constraints.md) | 현행 기능 계약/사용법 | fixed만 명시 값 보충, bounded/selectable 입력 필수, child 교집합·원본 Schema 재검증. |
| [docs/llm/tool-validation.md](llm/tool-validation.md) | 과거 요구·작업·검증 기록 | 당시 범위·완료/실패·파일 manifest 보존. 현재 계약은 연결된 전용 문서/소스/시험으로 판단; 과거 pass 수 승계 안 함. |

## LLM 내부 문서

| 문서 | 분류 | 판단·유효 계약 |
|---|---|---|
| [llm/CONFIGURATION.md](../llm/CONFIGURATION.md) | 현행 설정 계약 | Missing/Null/Value·명시 정책·nullable leaf와 envelope 구분. tools 목록 보완; 미수정 코드 차이 연결. |
| [llm/README.md](../llm/README.md) | 현행 기능 계약/사용법 | 현재 사용자가 보는 root API/ownership. A/B/C/D 새 계약과 동시에 예제 직접 이관. |
| [llm/components/README.md](../llm/components/README.md) | 현행 기능 계약/사용법 | 등록≠활성화≠실행, 명시 dependencies·closed record·configure 전체 교체. Agent 책임 이동 외 보존. |
| [llm/components/agents/README.md](../llm/components/agents/README.md) | 현행 기능 계약/사용법 | 현재 purpose/engine schema·records 저장. B/C에 독립 Agent로 직접 대체. |
| [llm/components/data/README.md](../llm/components/data/README.md) | 현행 기능 계약/사용법 | 파일 manifest/hash/revision·경로 안전·명시 Component. Memory 원문 참조 저장소 역할만. |
| [llm/components/goals/README.md](../llm/components/goals/README.md) | 현행 기능 계약/사용법 | 목표 리소스·명시 연결·작업상태 summary. Run 소유 계층에 Goal 삽입 안 함. |
| [llm/components/mcp/README.md](../llm/components/mcp/README.md) | 현행 기능 계약/사용법 | 실제 MCP connector·Tool alias·schema revision·부모 Tool scope 유지. 호환 Adapter 아님. |
| [llm/components/memory/README.md](../llm/components/memory/README.md) | 현행 기능 계약/사용법 | 명시 처리 정책·출처·summary/data 원본·generation/CAS. doc memory.md 낡은 설명의 대조 근거. |
| [llm/components/prompts/README.md](../llm/components/prompts/README.md) | 현행 사용법 | config/policy extraction 경로·metadata 정정. null은 추가 Prompt 없음; 고정 추출 프로토콜과 의미 지침 샘플 구분. |
| [llm/components/rag/README.md](../llm/components/rag/README.md) | 현행 기능 계약/사용법 | 단일 청크·증분 signature·Job·catalog·벡터·graph 형식. cache miss 재계산과 legacy Reader 구분. |
| [llm/components/refinement/README.md](../llm/components/refinement/README.md) | 현행 기능 계약/사용법 | proposal/evaluation/approve/apply 별개·원자 CAS·참조 보호. Agent target만 B/C 이관. |
| [llm/components/skills/README.md](../llm/components/skills/README.md) | 현행 기능 계약/사용법 | 지침은 권한 아님·Data/Tool 참조·lineage·현재 snapshot. 신규 지침 자동실행/자동이행 없음. |
| [llm/components/tools/README.md](../llm/components/tools/README.md) | 현행 기능 계약/사용법 | child inspector/worker·Python 명시 default·준비 설치와 실행 분리. 승인·retry·receipt는 ToolExecutor. |
| [llm/components/tools/builtin/README.md](../llm/components/tools/builtin/README.md) | 현행 기능 계약/사용법 | 명시 Host 도구·작업환경 binding·shell argv. 예전 architecture 기본 shell 설명과 구분. |
| [llm/components/vision/README.md](../llm/components/vision/README.md) | 현행 기능 계약/사용법 | 명시 VLM/OCR·immutable bytes/hash/tombstone. A의 ContentPart 변환 대상, 자동 이미지 첨부 없음. |
| [llm/components/workflows/README.md](../llm/components/workflows/README.md) | 현행 기능 계약/사용법 | 정의 저장·bounded loop·handler 실행 분리. schema version owner 유지. |
| [llm/core/README.md](../llm/core/README.md) | 현행 기능 계약/사용법 | 공통 도메인/계약. ParameterLayout은 값 생성/호환 이행 아님. 새 Agent/Message 형식 owner 추가. |
| [llm/engines/README.md](../llm/engines/README.md) | 현행 기능 계약/사용법 | Engine public extension·Tool durable helper·오류·binding·checkpoint. root Agent 전환에도 보존. |
| [llm/engines/graph/README.md](../llm/engines/graph/README.md) | 현행 기능 계약/사용법 | Graph 조율·공통 checkpoint bridge·부모 scope·선택 handler. 별도 승인/저장 시스템 없음. |
| [llm/engines/loop/README.md](../llm/engines/loop/README.md) | 현행 기능 계약/사용법 | 반복·Tool·steering·Resume 원본. A typed 모델/C binding/F review 접점. |
| [llm/policies/README.md](../llm/policies/README.md) | 현행 기능 계약/사용법 | 재사용 알고리즘과 실제 설정/실행 owner 분리. 전역 product defaults 생성 금지. |
| [llm/providers/README.md](../llm/providers/README.md) | 현행 기능 계약/사용법 | 실제 Adapter·사용량·retry guard·SDK 계약. A에서 common model 경계로 확대하되 불변식 유지. |
| [llm/services/README.md](../llm/services/README.md) | 현행 기능 계약/사용법 | Facade·도메인 수명·종료 전이. 일반 Facade를 구형 호환 wrapper로 오판하지 않음. |
| [llm/services/history/README.md](../llm/services/history/README.md) | 현행 기능 계약/사용법 | Conversation 원본·memory 수명·turn 삭제/retention·checkpoint 참조 보호. D 1:N 영향. |
| [llm/services/infrastructure/ACTIVITY.md](../llm/services/infrastructure/ACTIVITY.md) | 현행 기능 계약/사용법 | 파생 index·원본 Run/Step·limit None/0 의미·append fsync. 실행 권한/원본 저장소 아님. |
| [llm/services/infrastructure/OBSERVABILITY.md](../llm/services/infrastructure/OBSERVABILITY.md) | 현행 기능 계약/사용법 | owner fact·counter·no-op instrumentation·진단과 실행 권한 분리. |
| [llm/services/infrastructure/README.md](../llm/services/infrastructure/README.md) | 현행 기능 계약/사용법 | 저장/잠금/트랜잭션·외부 효과 경계·로그 사후 관찰. 단일 선언은 각 구현 owner에. |
| [llm/services/lifecycle/README.md](../llm/services/lifecycle/README.md) | 현행 기능 계약/사용법 | CRUD/CAS·삭제·접근·Component handle 수명. B Agent service와 D 참조 보호 확장. |
| [llm/services/runtime/README.md](../llm/services/runtime/README.md) | 현행 기능 계약/사용법 | Run lifecycle·Tool 공통 경계·구독 해제·저장 ACK. C/D 실행 단일 경로 전환. |

## Hub 내부 문서와 제품 자산

| 문서 | 분류 | 판단·유효 계약 |
|---|---|---|
| [hub/README.md](../hub/README.md) | 현행 사용법 + 날짜별 기록 | 현재 사용법과 과거 회귀 수 구분. 구 profile 언어 fallback은 잔존 제거 대상으로 표시. |
| [hub/asset/prompts/system.md](../hub/asset/prompts/system.md) | 제품 프롬프트/스킬 (감사 대상 데이터) | 모델에 전달하는 제품 가이드. 실제 tool schema·Project 권한·승인·저장/행동 검증 구분 유지. 감사 에이전트 지시로 실행하지 않음. |
| [hub/asset/prompts/work-context.md](../hub/asset/prompts/work-context.md) | 제품 프롬프트/스킬 (감사 대상 데이터) | 모델에 전달하는 제품 가이드. 실제 tool schema·Project 권한·승인·저장/행동 검증 구분 유지. 감사 에이전트 지시로 실행하지 않음. |
| [hub/asset/skills/coding.md](../hub/asset/skills/coding.md) | 제품 프롬프트/스킬 (감사 대상 데이터) | 모델에 전달하는 제품 가이드. 실제 tool schema·Project 권한·승인·저장/행동 검증 구분 유지. 감사 에이전트 지시로 실행하지 않음. |
| [hub/asset/skills/create-agent.md](../hub/asset/skills/create-agent.md) | 제품 프롬프트/스킬 (감사 대상 데이터) | 모델에 전달하는 제품 가이드. 실제 tool schema·Project 권한·승인·저장/행동 검증 구분 유지. 감사 에이전트 지시로 실행하지 않음. |
| [hub/asset/skills/create-rag.md](../hub/asset/skills/create-rag.md) | 제품 프롬프트/스킬 (감사 대상 데이터) | 모델에 전달하는 제품 가이드. 실제 tool schema·Project 권한·승인·저장/행동 검증 구분 유지. 감사 에이전트 지시로 실행하지 않음. |
| [hub/asset/skills/create-skill.md](../hub/asset/skills/create-skill.md) | 제품 프롬프트/스킬 (감사 대상 데이터) | 모델에 전달하는 제품 가이드. 실제 tool schema·Project 권한·승인·저장/행동 검증 구분 유지. 감사 에이전트 지시로 실행하지 않음. |
| [hub/asset/skills/create-workflow.md](../hub/asset/skills/create-workflow.md) | 제품 프롬프트/스킬 (감사 대상 데이터) | 모델에 전달하는 제품 가이드. 실제 tool schema·Project 권한·승인·저장/행동 검증 구분 유지. 감사 에이전트 지시로 실행하지 않음. |
| [hub/asset/skills/diagnose.md](../hub/asset/skills/diagnose.md) | 제품 프롬프트/스킬 (감사 대상 데이터) | 모델에 전달하는 제품 가이드. 실제 tool schema·Project 권한·승인·저장/행동 검증 구분 유지. 감사 에이전트 지시로 실행하지 않음. |
| [hub/asset/skills/document.md](../hub/asset/skills/document.md) | 제품 프롬프트/스킬 (감사 대상 데이터) | 모델에 전달하는 제품 가이드. 실제 tool schema·Project 권한·승인·저장/행동 검증 구분 유지. 감사 에이전트 지시로 실행하지 않음. |

## 이번에 추가한 문서

| 문서 | 역할 |
|---|---|
| [문서 검토 결과](llm/documentation-review.md) | 근거·충돌 해결·실제 검증·미완료 항목 |
| [클린 리팩터링 설계](llm/clean-refactor.md) | 미구현 목표·선언 소유권·단계·안전성/CI gate |
| [이전 아키텍처](llm/architecture-history.md) | 갱신 전 원문 보존 |
| [이전 API](llm/backend-api-history.md) | 갱신 전 원문 보존 |
| [이전 운영 준비 기록](llm/production-readiness-history.md) | 갱신 전 원문 보존 |
| [Hub 이력](hub/history.md) | 갱신 전 README 원문 보존 |
| 이 목록 | 기존 118개 및 추가 문서의 상태·참조 경로 |
