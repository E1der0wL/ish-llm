# 출시 전 호환 계층 제거 감사

## R0 기준과 판정 규칙

기준 HEAD 및 원격 `refactor/llm-backend`, PR #1 head:
`fafa8ce7f5333da279488c1ac4cac6b7d060a95c`. 시작 작업 트리는 clean이며
Codex 로컬 `work`를 사용한다. 원격 main은
`94229cb865374d99f20ec48c5e48269488041a4e`; 수정·병합·강제 Push하지 않는다.
이번 사용자 지침은 이전 B~F의 좁은 범위를 대체한다. 필요한 `hub/`, `tests/`,
`examples/`, `docs/`, `.github/` 호출자를 직접 이관한다. 새 UI 기능은 만들지 않는다.

호스트 `ish.platform`은 `d833790959a76ac457fdeea3f21a6bea1eecfe12`의 읽기 전용
참조 소스이며 제품 커밋 대상이 아니다. 사용자 프로젝트 자료는 변환·삭제하지 않는다.
기존 A~F 보고서는 당시 소스에 대한 역사적 증거로 보존하며 현재 API 안내로 사용하지 않는다.

초기 검색은 지침의 네 패턴 그룹으로 각각 555/142/983/633행을 찾았다.
문자열은 판정 근거가 아닌 탐색 입력이다. AST는 관련 이름의 정의 58개를 찾았으며,
정규 unittest discovery는 LLM+Hub 테스트 메서드 2,281개를 열거했다.
원시 검색, AST 목록, 전체 테스트 ID, Git 상태, 환경 진단 및 실제 회귀 로그는
`/workspace/compat-removal-evidence/r0-*`에 보존한다. 실제 전체 회귀 결과는
완료 후 기록하며 이 발견 개수를 통과 개수로 취급하지 않는다.

SDK wire Codec, 현재 schema 버전, 불완전 usage, 승인 거부, 영수증, CAS, digest,
트랜잭션·재시작 검증은 유지한다. 삭제 대상은 **과거 계약도 성공 처리하는 분기**다.
sync/async facade는 현재 사용 방식으로 유지하지만 과거 이름 alias는 제거한다.

## 호출 사슬별 감사표

행 번호는 변동하므로 아래 경로와 실제 심볼을 식별자로 사용한다. `대기`는 구현·검증
완료를 뜻하지 않는다. 각 변경 시 결과와 대체 테스트를 갱신한다.

| 경로:심볼 | 과거 지원 목적 | 현행 사용처 | 판정 | 정규 규약 | 변경 내용 | 기존 테스트 → 대체 테스트 | 확인 결과 |
|---|---|---|---|---|---|---|---|
| `providers/model_codec.py:message_from_legacy/message_to_legacy` | dict/common 이중 수용, v1 판독 | LiteLLM, Processor, Memory, 정책, 테스트 | 대체 | Provider 전용 typed↔SDK Codec | SDK encode/decode만 유지; 공용 호출자 typed 이관 | `test_model_protocol` wire opaque·부분 인자·multimodal 보존 + dict 공개 입력 거부 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| 같은 파일 `definition_*_legacy` | Tool v1 function dict 투영 | Registry, Worker, Graph binding | 대체 | `ToolDefinition` 및 현재 JSON descriptor | Provider만 SDK function 형식 생성 | `test_tool_workers`, `test_isolated_tools`, Graph binding 무결성 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| 같은 파일 `request_*_legacy/response_from_legacy/restore_response` | 구 kwargs 및 old Loop 응답 판독 | Base/Loop, 정책, 관찰 | 삭제/대체 | `ModelRequest`, `ModelResponse.from_dict` | old response 판독 제거; 저장은 현재 포맷만 | `test_common_provider`, 현재 Loop/Graph 재개, 구 포맷 거부 | R2 typed 이관 통과; R5 최종 fixture 감사 대기 |
| 같은 파일 `_LegacyMutation/_LegacyDict/_LegacyList/LegacyMessageView` | mutable dict를 typed owner로 역동기화 | `CompletionMessage.value` | 삭제 | `.message` + 명시적 typed 변경 | 변경 추적 wrapper 및 `.value` 삭제 | mutable Processor 권한/opaque 보존을 typed 방식으로 재검증 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| 같은 파일 `LegacyRequestSnapshot/stream_request/adapter_request/counter_input` | 표시 유무로 구 Engine/Adapter/Counter 자동 변환 | Base/Loop, Memory, token 정책 | 삭제 | 요청·Counter 인자 항상 `ModelRequest` | protocol marker 감지·왕복 변환 삭제 | `LegacyLoop` fake → typed override, dict 입력 부정 테스트 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| 같은 파일 `response_projection/validate_default_processing` | old response dict 및 SDK 기본 권한 fallback | BaseEngine/Pipeline | 삭제/대체 | typed result와 명시적 Adapter 검증 | Provider 권한 검증을 Adapter 계약에 귀속 | Processor 권한 상승 거부 테스트 보존 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `providers/model_codec.py:changed_request` | typed/dict retry 병행 | BaseEngine retry | 대체 | `ModelRequest.parameters` 수정만 | retry field·동일 입력 소유권 검사는 유지 | retry/부분 응답 뒤 재시도 금지 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `providers/litellm.py:LiteLLMCompletionAdapter` | `model_protocol`, `accepts_legacy_requests`, dict prepare | Base/Loop·테스트 Adapter | 대체 | 단일 CompletionAdapter | typed만 수용; SDK 함수 주입은 이 실제 Adapter 내부에 한정 | HTTP/SSE·retry·stream close + typed 거부/수용 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `providers/litellm.py:stream_completion/LiteLLMCompletionDecoder` | 실제 SDK 연결 (과거 API 아님) | LiteLLM Adapter | 유지/정리 | SDK raw stream→공통 update/response | opaque 서명·call ID·부분 인자·usage 보존 | `test_provider_runtime`, `test_model_protocol`, `test_common_provider` | 현재 기능으로 확인 |
| `providers/streaming.py:CompletionAdapter/CompletionDecoder` | marker로 명시 계약 외 옛 구현 허용 | Engine·등록 Adapter | 대체 | typed Protocol 하나 | marker 제거; 구현이 필수 메서드를 제공 | 독립 CommonProvider와 잘못된 Adapter 거부 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `engines/base.py:__init__/stream_completion/_completion_attempt` | completion_fn/decoder 별도 진입, response dict/list 이중형 | Loop, Hub 엔진, 예제, fake | 대체 | `completion_adapter=`, `ModelRequest`, `list[ModelResponse]` capture | SDK 함수 별도 Engine 인자와 dict response 제거 | BaseEngine streaming/취소/예산/진단 테스트 직접 이관 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `engines/loop/engine.py:__init__/_configure/execute` | SDK callable 주입, 구 override/응답 복원 | HubLoop, fake Loop, nested Agent | 대체 | Adapter 하나·현재 응답 checkpoint | `stream_request` 제거; 승인/Tool/검토 경계 보존 | Loop/Graph/Steering/Approval/Review 전체 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `components/processing.py:CompletionMessage` | dict 생성자·`.value` alias | Memory/Goal/Hub/테스트 | 대체 | `CompletionMessage(ModelMessage(...))` | 타입 검사, 독립 사본·source/result_ref 유지 | `test_completion_processing`, `test_model_protocol` typed 권한 검증 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| 같은 파일 `CompletionRequest.to_kwargs/to_model` | parameters 안 messages/tools 및 kwargs 재노출 | 정책/Memory/Hub | 삭제/대체 | `.to_model()`만 | messages/tools 분리 소유·옵션 덮어쓰기 거부 | Tool 쌍/원본 User 불변 테스트 강화 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| 같은 파일 `CompletionObservation/CompletionRecovery/Pipeline` | request/response/original_messages dict 프로젝션 | Memory/Goal/Hub recovery | 대체 | invocation/answer/messages 공통 타입 | 반환 사본 및 repair editable 권한 검사 보존 | Provider recovery·repair 범위·검토 통합 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `policies/completion.py:prepare_turn`, `providers/tokens.py` | dict/typed Counter 병행 | Run 정책, retention, Memory | 대체 | Counter(ModelRequest)→int | old counter_input 제거, 예산 fail-closed 보존 | 예산·unknown usage·병렬 quota/Resume 한도 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `components/memory/{processing,compaction}.py` | v1 wire summary fingerprint와 old input 투영 | 요약 checkpoint·복원 | 대체 | 현재 ModelMessage JSON summary | 버전/digest 하나, Tool 결과 원본 및 출처 보존 | memory processing/compaction/summary/Goal 현재 재기동 | R2 typed 이관 통과; R5 최종 fixture 감사 대기 |
| `components/goals/processing.py`, `services/history/retention.py` | 공통 전환 전 호출 방식 | Processor·토큰 retention | 이관/유지 | typed ModelRequest/Message | 독립 정책/보관 기준은 그대로 | Goal/retention/공통 Provider 통합 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `components/tools/registry.py:Tool/definitions/model_definitions/prepare` | wire definition·중복 조회·JSON 문자열 args | Worker/Graph/Loop/ToolExecutor | 대체 | typed `.definition`, `.definitions()`, dict args | alias 제거; schema/contract/선택 binding 검증 유지 | Tool arguments·schema·constraint·approval·binding | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `components/tools/{function,process,_worker}.py` | worker function dict descriptor | Python subprocess Tool loader | 대체 | `ToolDefinition.to_dict()` 단일 descriptor | 정확한 schema/identity 검증과 subprocess 격리는 유지 | 실제 worker·host·sandbox·오염 descriptor 거부 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `engines/graph/{engine,tool}.py`, `engines/agents.py:binding` | v1 Tool definition 기반 fingerprint | 중첩 Step·approval/Resume | 대체 | 현재 typed definition 직렬화 binding | 승인 무효화·selected binding 검사는 그대로 | Graph Tool/Agent 재개와 외부 효과 영수증 | R2 typed binding 통과; R3 Agent 경로 대기 |
| `providers/operations.py:normalized_rerank/embed_raw/invoke` | 구 RAG Client·SDK 반환을 공용으로 노출 | RAG ModelClient/Embedding/Rerank | 삭제/대체 | `OperationAdapter.complete/embed/rerank` | raw 호출은 실제 Provider 내부 private 함수만 | 독립 operation Adapter·결과 형식/범위/usage | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `components/rag/_client.py:ModelClient` | `_call_fn/_invoke`와 Adapter 이중 경로 | extraction/embedding/rerank | 대체 | 선택 OperationAdapter 하나 | 각 client는 typed 결과만 소비 | `test_rag_models`, `test_common_operations` | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `components/rag/{embedding,rerank,extraction}.py` | embedding_fn/rerank_fn/completion_fn·raw 결과 | RAG ingestion/index/retrieval | 대체 | embed→EmbeddingResult, rerank→RerankResult | embed_result/rank 중복 이름을 한 API로 통일 | 실제 RAG storage/corpus 경계·잘못된 결과 거부 | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `components/rag/{data,component}.py` | old injected client `rank/rerank` 감지 | RAG search·설정 | 대체 | typed client 계약 직접 호출 | schema/identity/caching/ownership 그대로 | RAG 통합·재시작·비 OpenAI Fake | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `components/vision/{backends,component}.py`, `providers/operations.py:legacy_image_part` | SDK image part와 callable/adapter 병행 | Vision Tool/OCR/모델 입력 | 대체 | ContentPart 및 OperationAdapter | raw image URL wire는 Provider 내부에만 | Vision/OCR/멀티모달·독립 Adapter | R2 완료: LLM 1751/Hub 534/host 44 통과, Skip 0 |
| `components/agents/{component,data,__init__}.py` | 구 Component facade와 purpose 필수 쓰기 | Hub/테스트/예제, component forms | 삭제 | `project.agents` | 클래스/export·Component 등록 삭제; 실제 정의는 독립 도메인 | Agent CRUD/CAS/수명/화면 현재 API 검증 | R3/R4 대기 |
| `components/agents/tools.py`, `components/registry.py` | Component 선택을 통한 agents/Tool 공급 | Graph agent node, agent_run | 대체 | Backend의 명시적 Agent capability와 Tool 제공 | 실제 agent_run 기능·권한·중첩 Step 소유는 유지 | `test_agent_delegation`, nested Graph·승인 | R3 대기 |
| `services/lifecycle/agents.py:AgentRepository.load` | envelope 없는 raw JSON 판독 | ProjectAgents/Graph/backup | 삭제 | 현행 envelope·id·revision 엄격 검증 | raw input 성공 분기 제거, 읽기 쓰기 없음 | old raw 거부 + 현행 reopen/CAS/backup | R3/R5 대기 |
| 같은 파일 `ProjectAgents` | 구 offline Component 쓰기 예외·중복 쓰기 방식 | 공개 Agent 관리자/Tool | 정리 | 명시적인 Engine 선택·revision CAS | 과거 편집 예외 제거; sync/async 제품 방식 유지 | 미등록 Engine·참조/삭제·CAS 실패 보존 | R3 대기 |
| `services/runtime/agents.py:RunAgentResolver.legacy/adapter/source` | 자동 legacy_<hash> 및 Agent 없는 Run | engine= submit/resume/history | 삭제 | 명시 Agent ID와 frozen definition/revision | 자동 Agent 생성·old source fallback 삭제 | explicit Agent dispatch·변경된 binding 거부·old Run 거부 | R3 대기 |
| `engines/registry.py:resolve_request/resolve_agent` | engine-request-v1/for_request fallback | AgentInvocation/Graph/runtime | 대체 | `Engine.for_agent(definition)` | 명시 factory 하나, 무효과·steering·checkpoint 계약 검증 | custom Engine factory/변경 후 Resume 거부 | R3 대기 |
| `engines/agents.py:AgentInvocation.legacy` | 옛 Run에서 Agent 정책·출력·리소스 생략 | 모든 최상위 Run | 삭제 | 동일 Agent→Engine 파이프라인 | 리소스/Tool/Processor 좁히기·output schema 항상 적용 | Agent policy·중첩 scope·검토 C12 | R3 대기 |
| `services/api.py`, `runtime/runs.py:submit/resume/restart/resume_plan` | engine=·engine_options=·Engine 재지정 | Hub/CLI/예제/대부분 과거 테스트 | 삭제/이관 | 명시 recipient Agent, 저장 binding 기반 Resume | 새 요청만 Agent 선택; Resume는 저장 identity 검사 | 요청·승인·취소·재개·stale revision 모든 호출자 이관 | R3/R4 대기 |
| `runtime/runs.py:_begin/_begin_group/_select_binding` | old queued User·단일 Engine begin shim | Dispatcher/실패 주입 | 대체 | admission→원자적 형제 그룹→Agent별 실행 | old 복원 분기 삭제, 그룹 COMMITTED 경계 유지 | F01~F12 주입 위치를 실제 정규 경계로 이관 | R3 대기 |
| `core/models.py:Session/Run`, `core/views.py:SessionRuntimeView` | current_run_id/active_run_id·Agent 없는 Run 기본값 | 서비스·Hub·낮은 수준 테스트 | 대체 | agent_ids/current_run_ids/active_run_ids; Run.agent_id 필수 | 단수 투영 삭제, 현재 버전 기본값 및 엄격 판독 | 1:N/활성 집합/FIFO/실제 병렬/취소·복구 | R3/R4 대기 |
| `core/models.py:Message`, `runtime/requests.py:read/runs` | User.run_id 및 admission 없는 옛 요청 | Conversation/History/Hub snapshot | 대체 | Run.input_message_id; Assistant만 run_id | User singular 귀속 제거; 일반 비실행/Steering 구별은 유지 | shared User 1:N·잘못된 출처·접수 이전 crash | R3 대기 |
| `services/api.py:RequestHandle.run/result/aget_run/aresult` | 단수 projection 및 임의 결과 선택 | Hub/테스트/예제 | 삭제 | `runs/aruns/wait_all`, 명시 single `wait` | wait는 정확히 한 recipient일 때만 허용 | 다중 요청 모호성 거부·일관된 결과 조회 | R3/R4 대기 |
| `services/lifecycle/sessions.py`, `runtime/runs.py:RunRepository` | Session/Run v1+v2 판독 | load/reopen/backup/recovery | 대체 | 한 현재 저장 버전 | unsupported/extra/missing fields 명확히 거부 | 현행 재시작·clone/backup + 구 포맷 부정 테스트 | R3/R5 대기 |
| `services/infrastructure/storage.py:record/read_domain_record/atomic_domain_json` | v1 필드 생략·복수 판독 | 모든 domain 저장·digest | 정리 | 각 domain 현재 포맷만 | 현재 header·path·atomic write·stale writer 거부 보존 | fsync/replace/부분 JSON·digest/F11 | R3 대기 |
| `services/history/{conversation,recovery,context,turns,retention}.py` | singular Message·구 자료 분기 | 요청 문맥·삭제·복구·보관 | 이관 | 현행 admission/Run 참조 | 그룹 단위 삭제/보관과 미확정 효과 재실행 금지 유지 | C09/C10/F01~F12·retention references | R3 대기 |
| `services/lifecycle/projects.py`, `runtime/transfers.py`, `core/paths.py` | 백업/clone의 다중 저장 수용 여부 | 프로젝트 수명·E 전송 | 감사/이관 | 정규 Agent/Session 경로·전송 영수증 | symlink/path traversal·인가/idempotency/원자성 유지 | F13/C11·same Project·deadline·clone inert | R3 대기 |
| `hub/backend/bootstrap.py`, `tools/resources.py:HubAgentComponent` | AgentComponent 설치·상속 | Hub 자원 Tool/구성 | 대체 | 실제 Project Agent 서비스 | 정규 Agent Tool 직접 제공, 기존 승인·소유 검사 유지 | Hub resource Tool·agent_run·승인 회귀 | R4 대기 |
| `hub/components.py`, `backend/project/record_management.py` | AgentData를 Component 화면 데이터로 요구 | 기존 Agent 정의 관리 화면 | 대체 | project.agents schema/snapshot/revise | 관리자를 독립 도메인으로 연결, CAS 그대로 | 정의 생성/편집/삭제·충돌·settings UI | R4 대기 |
| `hub/backend/runtime.py:submit/resume_latest` | engine= submit/재지정 | 기존 단일 대화·Graph workflow | 대체 | 실제 default/선택 Agent 생성·attach·agent_id submit | Hub 선택을 실체 있는 정의로 표현; 은닉 compat factory 금지 | 단일 대화·Graph·설정·중단/재개 | R4 대기 |
| `hub/backend/execution/environment.py`, `execution/turns.py`, Run 접수 | engine_options 안의 요청별 shell/turn snapshot | 기존 Hub 환경·승인 Resume·queued Graph | 현재 기능 이관 | Agent 정의와 분리된 명시적 요청 metadata | application JSON을 접수 시 복사·동결; reserved admission/transfer/binding 덮어쓰기 불가 | `test_shell_environment`의 큐별 환경·비밀값·승인 재개 동일성 유지 | R3/R4 대기 |
| `hub/backend/{runtime,history}.py`, `observability/snapshot.py` | singular active/run 및 User.run_id 연결 | busy/삭제/Steering/대화 표시 | 대체 | active_run_ids + 명시 선택 Run + input_message_id | 다중 UI 신설 없이 기존 기능의 모호성 검사 | request history·streaming·approval·observability | R4 대기 |
| `hub/backend/execution/{completion,rag_catalog,provider_recovery,context,retries,recovery_model}.py` | old Processor/Engine request·kwargs | Hub 오류 복구/추가 입력/추론 | 직접 이관 | typed request/message/observation | SDK dict 해석/역변환 제거 | Hub recovery·repair 권한/취소·budget·Tool 안전성 | R2 typed Hub 호출 통과; R4 Agent 이관 대기 |
| `hub/ui`, `hub/widget`, `hub/locales`, `hub/asset/skills` | 옛 관리 계약 설명·필드 참조 | 기존 화면·내장 Skill | 필요 위치 이관 | 현행 Agent 및 복수 Run 의미 | 신규 UI/업무 Reviewer 미구현 | 기존 PTK/질문/승인/X11 실제 테스트 | R4/R5 대기 |
| `tests/llm/fixtures/{provider_v1,agent_v1,agent_run_v2}` | 이전 소스 데이터 성공 판독 | legacy fixture 테스트 | 교체 | 현재 schema fixture·생성 SHA/manifest | old fixture 제거 전 동등한 실제 crash/Tool/approval/Graph 증거 확보 | 현재 생성→프로세스 종료→reopen→Resume/effect 1 + old 거부 | R5 대기 |
| `tests/llm/test_{provider_legacy,model_protocol,common_provider,agent_domain,agent_runs}` | legacy fake·view·자동 Agent 성공 assertion | 이전 호환 보장 | 교체 | 단일 계약·구 API 거부 | 안전성 assertion은 typed 현재 경계로 직접 이관 | 모든 교체 ID와 불변식 대응표 작성 | R2/R3/R5 대기 |
| `tests/llm/support`, `tests/hub`, `examples/llm`, `docs/llm`, 모듈 docstring | old constructor·counter·submit·예제 | 실제 개발자/Hub 소비자 | 직접 이관 | 하나의 정규 API 사용 예제 | 전역 문자열 치환 없이 호출 유형별 변경 | 예제 smoke·공개 API/아키텍처 금지 경계 테스트 | R2~R5 대기 |
| `.github/workflows/tests.yml` | smoke 실패로 모든 회귀 미실행 | GitHub Actions | 수정 | portable/host-ui/sandbox 독립 결과 | capability 진단·정확한 Python/host/X11·누락 없는 테스트 포함표 | 새 PR HEAD Actions 실실행·각 Job exit 확인 | R6 대기 |

## 유지하는 현재 기능과 오탐

| 경로/심볼 | 유지 이유 | 확인/검증 |
|---|---|---|
| `core/model_protocol.py`의 현재 `version`·typed JSON | 현재 한 포맷의 무결성/미지원 차단; 버전 숫자 자체는 호환 계층이 아님 | strict round-trip/unknown version/extra shape 검증 |
| ContentPart/Enum/InteractionOption/UI/ctypes의 `.value`, Processor/Decoder `finish()` | 각 현재 도메인의 실제 값/수명 훅; CompletionMessage의 dict alias와 무관 | 타입·호출자별로 구분하며 일괄 이름 치환하지 않음 |
| Provider opaque·raw termination·unknown usage | 실제 SDK 서명/출처/부분 응답/비용 불확실성 | 공통 소비자는 stop enum과 공통 의미만 사용; wire는 Provider 내부 |
| `core/checkpoints.py`, `runtime/tools.py`, ToolOperation ledger | 승인·효과 귀속·재개·중복 방지의 현재 제품 계약 | F07~F11 및 실제 프로세스 종료/영수증 재사용 유지 |
| `engines/loop/review.py:completion-review-v1` | 현재 유일 검토 영수증 헤더 | F14/C12·attempt/decision commit 경계 유지 |
| Graph binding/version과 Tool contract.revision | 현재 스냅샷 무결성·승인 scope | 새 단일 encoding으로 생성·검증; 구 binding 자동 판독 금지 |
| `components/files.py`의 unsupported legacy directory 오류 | 구 형식을 **거부**하는 안전 검사 | 경로 침범/자동 변환 없음 검증 |
| `components/tools/builtin/workspaces.py`, `services/runtime/processes.py`의 fallback 변수 | 명시 경로 결정·실패 상태 처리; old API 수용과 무관 | 실제 sandbox/프로세스 정리 테스트; 비격리 강등 금지 |
| Hub retry fallback_engine·render fallback·OCR/color 호환 용어 | 현재 오류 처리/표시·이미지 제약 | 호출 사슬 유지; Agent 대신 Engine 우회 실행이면 R4에서 정규 경로로 이관 |
| MCP/SDK protocol revision, OpenAI-compatible endpoint | 외부 현재 프로토콜/endpoint 의미 | 실제 Provider Adapter·MCP/host 테스트 유지 |
| `llm/REFACTOR_*.md`, `providers/REFACTOR_A.md` | 기존 작업의 역사적 성공·실패 증거 | 현행 API 문서는 새 보고서를 사용; 과거 결과를 소급 수정하지 않음 |

## 검증 경계와 단계 의존성

R0 감사·기준선 → R1 정규 계약 → R2 Provider/Processor/Tool → R3 Agent/저장 →
R4 Hub → R5 fixture/문서 최종 정리 → R6 Actions → R7 전체 회귀 순서다.
삭제한 API를 쓰는 호출자는 해당 삭제 단계에서 함께 고쳐 실행 가능한 커밋을 유지한다.
따라서 R2에 필요한 Hub typed 호출 수정은 R4까지 실패 상태로 미루지 않는다.
R3에서 필요한 최소 Hub 명시 Agent 전환도 같은 원칙이며 R4에서 기존 UI/관리 계약을
전체 확인한다. 안전성 회귀가 해결되지 않으면 다음 단계로 넘어가지 않는다.

GitHub REST API는 환경 egress proxy가 CONNECT 403으로 거부한다. Git 및 GitHub 웹
읽기는 가능하며 PR #1의 Draft와 head를 확인했다. 기존 allowlist를 보존하고
`api.github.com`을 추가한 환경 설정 초안을 저장했다(현재 runtime 반영/Publish 증거는 없음).
Cloud Bubblewrap 0.12.0의 `--unshare-all` 및 unprivileged user+network namespace
스모크는 exit 0이었다. GitHub runner의 `RTM_NEWADDR` 실패가 해결됐다는 증거는 아니다.

기존 CI 실제 실행: <https://github.com/E1der0wL/ish-llm/actions/runs/39288390903>,
job `118182322629`, PR head `fafa8ce`. GitHub 웹은 run failure 및
`Install Linux sandbox runtime` 이후 두 테스트 step의 미실행을 보여 준다.
해당 step log URL은 비인증 웹 접근에서 HTTP 404이며 REST는 CONNECT 403이다.
따라서 `RTM_NEWADDR` 원문은 사용자 제공 기준 증거와 구분하며 조회했다고 주장하지 않는다.
Runner 화면에는 ubuntu-latest의 Ubuntu 26 이전 공지가 있어 R6에서는 실제 OS/kernel/
AppArmor/user/network namespace 진단과 재현 가능한 runner 선택을 확인한다.

R2 호출자 감사: AST가 SDK callable keyword를 전달하는 호출 311개를 식별했다
(`r2-injection-call-sites.json`). 실제 Provider 내부 SDK 주입은 유지하고, Engine/Component
호출자는 명시 Adapter 생성으로 직접 이관한다. helper/예제 함수의 자체 인자는 소유자를
따로 추적하며, 모든 `completion_fn` 문자열을 같은 방식으로 치환하지 않는다.

## R0 변경 전 실제 회귀 기준선

소스 `fafa8ce7f5333da279488c1ac4cac6b7d060a95c`, Python 3.12.14,
호스트 `d833790959a76ac457fdeea3f21a6bea1eecfe12`에서 다음을 실행했다.
`TERM=xterm-256color`, 실제 로컬 Xvfb/xclip 경로, LiteLLM production/local cost map을
사용했다. Bubblewrap 실행을 허용한 Cloud 실행 환경에서 격리 설정은 유지했다.

| 명령 (`.venv-linux312/bin/python`) | 실행 | 통과 | 실패/오류 | Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|---:|
| `-m unittest discover -s tests/llm -t . -v` | 1747 | 1747 | 0 | 0 | 418.481s | 0 |
| `-m unittest discover -s tests/hub -t . -v` | 534 | 534 | 0 | 0 | 552.000s | 0 |
| `-m unittest -v tests.llm.test_plugin tests.llm.test_tool_packages tests.hub.test_ish_integration` | 44 | 44 | 0 | 0 | 18.568s | 0 |

총 고유 테스트는 2,281개이며 마지막 44개는 전체 집합과 중복인 명시 재검증이다.
X11 관련 9개도 실행·통과했다. 전후 Python 파일 집합·SHA256 비교로 실행 도중 소스가
변하지 않았음을 확인했다. 원본 명령·로그·exit·파일 hash·테스트 ID 목록은 작업 환경의
`/workspace/compat-removal-evidence/r0-*`에 보존했다. 실제 인증 모델 호출과 커널
short-write 장애 주입은 이번 기준선에서도 미검증이다. Actions 실패는 별도 미해결이다.

## R2 테스트 이관 추적

R0 전체 2,281개에서 R2 발견 목록은 2,285개다. 아래 10개 ID를 같은 경계의 현재
계약 테스트로 교체했고, raw 반환형·제거 API·잘못된 ranking·저장 형식 거부 4개를
추가했다. 발견 수는 실행 성공을 의미하지 않는다. 실행 결과는 단계 보고서에 기록한다.
전체 ID 차이는 `r2-test-id-diff.json`에 보존한다.

| 이전 테스트 (`tests.llm.` 아래) | 현재 테스트 | 보존·강화한 검증 |
|---|---|---|
| `test_model_protocol.*.test_legacy_processor_mutations_preserve_common_owner_and_opaque` | `test_typed_processor_edits_preserve_opaque_and_reject_dict_projection` | typed 편집·opaque·출처 및 dict 거부 |
| `test_model_protocol.*.test_legacy_signatures_arguments_and_multimodal_round_trip` | `test_sdk_signatures_arguments_and_multimodal_round_trip` | 실제 SDK 서명·Tool 인자 원문·멀티모달 wire 왕복 |
| `test_model_protocol.*.test_legacy_stream_override_preserves_foreign_state_and_runtime_options` | `test_unmarked_typed_stream_override_preserves_state_and_rejects_dict` | marker 없는 typed override·opaque·runtime client identity·old dict 거부 |
| `test_model_protocol.*.test_legacy_view_aliases_and_copies_preserve_multimodal_state` | `test_typed_copies_and_multimodal_edits_preserve_detached_state` | 멀티모달 편집과 독립 사본 |
| `test_model_protocol.*.test_worker_and_graph_v1_definition_encoding_is_stable` | `test_sdk_tool_definition_preserves_wire_extensions` | SDK 확장 필드 보존; worker/Graph는 아래 현재 fixture에서 검증 |
| `test_provider_legacy.*.test_baseline_graph_binding_and_worker_descriptor_are_stable` | `test_provider_current.*.test_current_graph_binding_and_worker_descriptor_are_stable` | typed worker descriptor hash·Graph 승인/Step 재사용·기존 checkpoint 불변 |
| `test_provider_legacy.*.test_baseline_omitted_and_null_definition_fields_keep_binding_bytes` | `test_provider_current.*.test_current_definition_fields_bind_exactly_and_old_wire_is_rejected` | 현재 strict=None/False/True 구별·old SDK 정의/문자열 인자 거부 |
| `test_provider_legacy.*.test_real_v1_completed_receipt_and_pending_approval_survive_reopen` | `test_provider_current.*.test_current_completed_receipt_and_pending_approval_survive_reopen` | 완료 응답 재개방·승인 전 효과 0·승인 후 1회·실패 후 기존 효과 재사용 |
| `test_provider_runtime.*.test_sdk_defaults_and_plain_model_client_are_preserved` | `test_sdk_defaults_and_transport_options_are_preserved` | 실제 SDK 기본 retry·transport options는 Provider에서 보존 |
| `test_rag_models.*.test_embedding_passes_open_kwargs_and_preserves_native_response` | `test_embedding_passes_open_kwargs_and_normalizes_vector_and_usage` | SDK kwargs와 공통 vector·usage 소비 |

`provider_current` manifest는 생성 당시 base commit과 수정 소스 여부, 각 파일 SHA256,
archive 및 내부 파일 hash를 함께 기록한다. 수정된 소스에서 생성한 자료를 clean commit
산출물로 표시하지 않는다. R5에서 최종 저장 계약 구현 SHA의 fixture를 다시 생성한다.
