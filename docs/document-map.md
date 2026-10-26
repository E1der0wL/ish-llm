# 문서 색인 — 2026-10-26

현재 파일의 탐색 목록입니다. [LLM 안내](llm/README.md), [Hub 안내](hub/README.md),
[최신 작업 인계](llm/handoff.md)에서 시작하세요. 파일 분류는 과거 문장 전체의 현재 유효성을 보증하지 않습니다.
이전 고정 감사표는 [보관 목록](llm/documentation-history.md)에 있습니다.

단계별 계획·검증 기록은 해당 소스 SHA의 증거로 읽습니다. 현재 실행 계약과 과거 진행 상태를
구분하며 이전 pass 수를 합산하지 않습니다. 코드 옆 README는 [LLM](../llm/README.md)과
[Hub](../hub/README.md)에서 연결됩니다. 제품 prompt/skill 자산은 이 색인의 설명 문서와 구분합니다.

## 현행 안내와 계약

| 파일 | 제목 |
|---|---|
| [README.md](README.md) | 개발 문서 |
| [hub/README.md](hub/README.md) | Hub 설계 문서 |
| [hub/application-defaults.md](hub/application-defaults.md) | Hub application creation profile and Agent APIs |
| [hub/backend-progress.md](hub/backend-progress.md) | 초기 연결과 백엔드 단계 표시 |
| [hub/coding-workflow.md](hub/coding-workflow.md) | 코딩 스킬과 응답 UX |
| [hub/completion-display.md](hub/completion-display.md) | Completion 상태와 스트리밍 진단 |
| [hub/component-managers.md](hub/component-managers.md) | 컴포넌트 관리 팝업 |
| [hub/error-handling.md](hub/error-handling.md) | Hub error contract and recovery policy |
| [hub/home-navigation.md](hub/home-navigation.md) | 홈 탐색과 요청 문맥 |
| [hub/maintenance.md](hub/maintenance.md) | Hub UI 요청·설정·컴포넌트 구조 |
| [hub/mouse.md](hub/mouse.md) | Hub 마우스 동작 |
| [hub/operation-recovery.md](hub/operation-recovery.md) | Hub operation recovery and guidance |
| [hub/output-objects.md](hub/output-objects.md) | 출력 객체와 컴포넌트 관리 |
| [hub/panel-ux.md](hub/panel-ux.md) | 패널·설정 탐색과 오류 안내 |
| [hub/participants.md](hub/participants.md) | Agent 초대와 대화 상대 |
| [hub/presets-and-resume.md](hub/presets-and-resume.md) | 설정 프리셋과 중단된 응답 재개 |
| [hub/provider-recovery.md](hub/provider-recovery.md) | Hub provider error analysis and input recovery |
| [hub/questions-tools.md](hub/questions-tools.md) | 입력창 질문과 내장 도구 |
| [hub/rag.md](hub/rag.md) | Hub RAG 사용 안내 |
| [hub/rendering-performance.md](hub/rendering-performance.md) | Hub rendering pipeline |
| [hub/resource-authoring.md](hub/resource-authoring.md) | 작업 중 컴포넌트 사용과 리소스 생성 |
| [hub/response-reliability.md](hub/response-reliability.md) | 응답 안정성과 마우스 스크롤 |
| [hub/shell-environment.md](hub/shell-environment.md) | ish 작업 환경 연결 |
| [hub/structure.md](hub/structure.md) | Hub 구조와 정리 내역 |
| [hub/tool-retry.md](hub/tool-retry.md) | Tool 인자 수정 재시도 |
| [hub/ui-consistency.md](hub/ui-consistency.md) | 공통 UI 규격 |
| [hub/usage-and-system-messages.md](hub/usage-and-system-messages.md) | 일별 사용량과 시스템 메시지 |
| [llm/README.md](llm/README.md) | LLM 개발 문서 안내 |
| [llm/agent-skills.md](llm/agent-skills.md) | 표준 Agent Skills와 Skills 운영 기록 |
| [llm/agents.md](llm/agents.md) | Agent: 재사용 가능한 업무 정의 |
| [llm/architecture-boundaries.md](llm/architecture-boundaries.md) | Backend와 Application의 경계 |
| [llm/architecture.md](llm/architecture.md) | 현행 LLM 아키텍처 |
| [llm/backend-api.md](llm/backend-api.md) | 현행 Backend API |
| [llm/backend-recovery.md](llm/backend-recovery.md) | 오류·복구 공개 API와 애플리케이션 연결 |
| [llm/builtin-observations.md](llm/builtin-observations.md) | 내장 Tool의 입력·조회 계약 |
| [llm/builtin-tools.md](llm/builtin-tools.md) | 기본 Tool 사용법 |
| [llm/capability-owners.md](llm/capability-owners.md) | 내장 capability의 선언과 Agent 정의 투영 |
| [llm/checkpoints.md](llm/checkpoints.md) | 체크포인트 수명 계약 |
| [llm/completion-processing.md](llm/completion-processing.md) | 컴포넌트의 공통 모델 처리기 |
| [llm/component-definitions.md](llm/component-definitions.md) | Skill · Data · MCP · RAG · Agent · Workflow · Memory |
| [llm/contract-closure.md](llm/contract-closure.md) | 전수 계약 소유권 최종 교차 점검 |
| [llm/contract-ownership.md](llm/contract-ownership.md) | 계약 선언과 의미 해석의 단일 소유 |
| [llm/conversation-storage.md](llm/conversation-storage.md) | 대화 저장 방식 선택 |
| [llm/data-contracts.md](llm/data-contracts.md) | 공통 데이터 계약 |
| [llm/data-lifecycle.md](llm/data-lifecycle.md) | 영속 데이터 무결성과 수명주기 계약 |
| [llm/documentation-review.md](llm/documentation-review.md) | 현재 문서 검토 — 2026-10-26 |
| [llm/domain-hardening.md](llm/domain-hardening.md) | 승인, Graph·Agent, Memory·장기 문맥 보강 |
| [llm/editable-snapshots.md](llm/editable-snapshots.md) | 편집 조회 snapshot의 소유권 |
| [llm/engine-output.md](llm/engine-output.md) | 공통 Engine 출력 |
| [llm/error-observations.md](llm/error-observations.md) | 오류 관측과 분류의 소유 |
| [llm/execution-contexts.md](llm/execution-contexts.md) | Tool 호출 문맥과 사용량 문맥의 수명 Owner |
| [llm/graph-checkpoints.md](llm/graph-checkpoints.md) | Graph 체크포인트와 UI Workflow 저장 |
| [llm/graph-engine.md](llm/graph-engine.md) | GraphEngine과 코드 수정 Workflow 실행 |
| [llm/handoff.md](llm/handoff.md) | 최신 작업 인계 — 2026-10-26 |
| [llm/interactions.md](llm/interactions.md) | 공통 사용자 요청과 승인 |
| [llm/ishrc-loop-test.md](llm/ishrc-loop-test.md) | ish에서 명시적으로 Project를 선택하여 LoopEngine 테스트 |
| [llm/json-data.md](llm/json-data.md) | JSON 객체 검증의 책임 |
| [llm/long-running-capabilities.md](llm/long-running-capabilities.md) | 장기 실행 API: 연결, 문서 진행, 명시적 압축과 재개 |
| [llm/long-running.md](llm/long-running.md) | 장시간 실행·복구·보관 API |
| [llm/markdown-rag-test.md](llm/markdown-rag-test.md) | 마크다운 RAG / GraphRAG 예제 |
| [llm/memory-processing.md](llm/memory-processing.md) | Memory에서 장기 대화·작업 최적화 |
| [llm/memory.md](llm/memory.md) | 프로젝트 장기 기억 |
| [llm/model-protocol.md](llm/model-protocol.md) | 공통 모델 호출 값 |
| [llm/model-usage-contract.md](llm/model-usage-contract.md) | 모델 사용량의 선언·예약·영수증 |
| [llm/naming-conventions.md](llm/naming-conventions.md) | Naming conventions and semantic vocabulary |
| [llm/nested-workflows.md](llm/nested-workflows.md) | 중첩 Workflow와 Graph Agent |
| [llm/operational-storage.md](llm/operational-storage.md) | 실행 자원과 저장 정책 |
| [llm/operations-and-ui-settings.md](llm/operations-and-ui-settings.md) | 운영 보강과 UI 설정 API |
| [llm/package-boundaries.md](llm/package-boundaries.md) | Tool 패키지 저장·공용 dependency 경계 |
| [llm/process-isolation.md](llm/process-isolation.md) | Tool 프로세스 격리와 외부 작업 중복 방지 |
| [llm/production-readiness.md](llm/production-readiness.md) | 운영 준비 상태와 검증 한계 |
| [llm/project-authority.md](llm/project-authority.md) | Project 정책, Host 자원, 실행 불변식 |
| [llm/project-policies.md](llm/project-policies.md) | Project 공통 정책과 구현체 설정 |
| [llm/project-policy-values.md](llm/project-policy-values.md) | Project 정책의 선언·실행 관측 |
| [llm/project-settings.md](llm/project-settings.md) | Project 설정과 UI 연결 |
| [llm/provider-validation.md](llm/provider-validation.md) | Provider 검증과 명시적 설정 fixture |
| [llm/rag-components.md](llm/rag-components.md) | 통합 RAG 컴포넌트 |
| [llm/resource-references.md](llm/resource-references.md) | 보존된 정의의 공통 참조 관찰 |
| [llm/retry.md](llm/retry.md) | 재시도와 수정 인자 |
| [llm/revisions.md](llm/revisions.md) | Revision 계약 |
| [llm/runtime-reliability.md](llm/runtime-reliability.md) | 실행 안정성과 UI 연결 |
| [llm/schema-ownership.md](llm/schema-ownership.md) | Schema와 설정의 소유권 |
| [llm/service-extensions.md](llm/service-extensions.md) | 서비스 확장 API |
| [llm/session-transfers.md](llm/session-transfers.md) | Session 간 요청 전송 |
| [llm/settings-consistency.md](llm/settings-consistency.md) | 설정 일관성 |
| [llm/shared-contracts.md](llm/shared-contracts.md) | 공통 경계와 지원 범위 |
| [llm/steering.md](llm/steering.md) | 실행 중 추가 지시 |
| [llm/storage-transactions.md](llm/storage-transactions.md) | 도메인 저장 트랜잭션 |
| [llm/test-workspaces.md](llm/test-workspaces.md) | 테스트 공간과 OS 샌드박스 |
| [llm/tool-constraints.md](llm/tool-constraints.md) | Project-owned Tool 인자 제약 |
| [llm/tool-declaration-owner.md](llm/tool-declaration-owner.md) | Tool 실행 선언의 단일 Owner |
| [llm/tool-function-contract.md](llm/tool-function-contract.md) | Python 함수의 Tool 선언과 인자 Schema |
| [llm/tool-package-records.md](llm/tool-package-records.md) | Python Tool 패키지 정의와 manifest의 단일 Owner |
| [llm/tool-registration-bindings.md](llm/tool-registration-bindings.md) | Tool 등록 정보와 실행 binding 관측 |
| [llm/tool-selection-evidence.md](llm/tool-selection-evidence.md) | Tool 선택 기록과 공통 사용량 증거 |
| [llm/tool-selection.md](llm/tool-selection.md) | Tool 활성화 선택의 단일 Owner |
| [llm/tool-worker-boundary.md](llm/tool-worker-boundary.md) | Project Tool worker 시작·전송 경계 |
| [llm/tool-worker-protocol.md](llm/tool-worker-protocol.md) | Tool interpreter와 프로세스 시작 프로토콜 Owner |

## 단계별 설계·검증 기록

| 파일 | 제목 |
|---|---|
| [hub/backend-contract-handoff.md](hub/backend-contract-handoff.md) | llm 오류 처리와 체크포인트 공통 계약 작업 지침서 |
| [hub/history.md](hub/history.md) | > **과거 문서 보존본 — main `94229cb865374d99f20ec48c5e48269488041a4e`.** |
| [hub/structure-audit.md](hub/structure-audit.md) | Hub 구조 감사 — 2026-10-13 |
| [hub/observation-contract-review.md](hub/observation-contract-review.md) | 초대·관측 캐시·진행 단계 계약 보강 |
| [llm/agent-refactor-verification.md](llm/agent-refactor-verification.md) | B/C Agent 전환 검증 기록 |
| [llm/agent-skills-verification.md](llm/agent-skills-verification.md) | Agent Skills 전환 검증 기록 |
| [llm/architecture-reviews.md](llm/architecture-reviews.md) | 개별 정적 후보의 분류와 무효화 |
| [llm/boundary-evolution-review.md](llm/boundary-evolution-review.md) | Graph / Tool policy / Skill boundary 검토 결과 |
| [llm/ci-execution-budget.md](llm/ci-execution-budget.md) | 전체 CI 실행 시간 |
| [llm/clean-refactor.md](llm/clean-refactor.md) | 클린 리팩터링 설계와 구현 게이트 |
| [llm/completion-review-verification.md](llm/completion-review-verification.md) | F 최종 후보 검토 검증 |
| [llm/completion-review.md](llm/completion-review.md) | 명시적 최종 후보 검토 |
| [llm/configuration-audit.md](llm/configuration-audit.md) | 설정 정적 감사 기록 |
| [llm/declaration-coverage-verification.md](llm/declaration-coverage-verification.md) | 선언 위치·전수 완료 기준과 실행 Owner 보강 |
| [llm/lifecycle-verification.md](llm/lifecycle-verification.md) | 영속 데이터 수명주기 보강 검증 |
| [llm/long-running-components.md](llm/long-running-components.md) | Goal · 작업 상태 요약 · Refinement 구현 기록 |
| [llm/multi-agent-verification.md](llm/multi-agent-verification.md) | D 요청 1:N·병렬 실행 검증 |
| [llm/ownership-refactor.md](llm/ownership-refactor.md) | 계약 소유권과 책임 경계의 후속 정리 |
| [llm/ownership-verification.md](llm/ownership-verification.md) | Owner 정리의 구현·검증 기록 |
| [llm/p0-sdk-transition.md](llm/p0-sdk-transition.md) | P0 SDK/MCP 전환 |
| [llm/p0-verification.md](llm/p0-verification.md) | P0 검증 도구와 실행 기록 |
| [llm/p1-verification.md](llm/p1-verification.md) | P1 구현·검증 기록 |
| [llm/project-authority-review.md](llm/project-authority-review.md) | Project authority / risk / Agent delegation 작업 보고 |
| [llm/provider-refactor-verification.md](llm/provider-refactor-verification.md) | A Provider 전환 검증 기록 |
| [llm/schema-ownership-review.md](llm/schema-ownership-review.md) | Closed contracts / semantic ownership 작업 결과 |
| [llm/session-transfer-verification.md](llm/session-transfer-verification.md) | E Session 전송 검증 |
| [llm/shared-boundary-review.md](llm/shared-boundary-review.md) | 공통 실행 경계 검토 |
| [llm/tool-validation.md](llm/tool-validation.md) | Project Tool worker / observability 검증 |
| [llm/typed-contracts-verification.md](llm/typed-contracts-verification.md) | 고정 구조 데이터 Owner 보강 검증 |
| [llm/verification-policy.md](llm/verification-policy.md) | 검증 계층과 전체 회귀 실행 정책 |
| [llm/whole-ownership-verification.md](llm/whole-ownership-verification.md) | 계약 소유권 전수 적용 실행 기록 |

## 보관 안내

| 파일 | 제목 |
|---|---|
| [hub/request-history.md](hub/request-history.md) | 요청 목록 |
| [llm/architecture-history.md](llm/architecture-history.md) | 이전 아키텍처 기록 — 보관 안내 |
| [llm/backend-api-history.md](llm/backend-api-history.md) | 이전 Backend API 기록 — 보관 안내 |
| [llm/code-simplicity-review.md](llm/code-simplicity-review.md) | 2026-09-28 코드 단순성 검토 — 보관 안내 |
| [llm/documentation-history.md](llm/documentation-history.md) | 문서 보관 목록 — 2026-10-26 |
| [llm/production-readiness-history.md](llm/production-readiness-history.md) | 이전 운영 검증 기록 — 보관 안내 |
