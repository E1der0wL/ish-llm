# Closed contracts / semantic ownership 작업 결과

## 기준과 범위

1. **Base commit:** `f25a84de6759b9d723baf402f79ed451de1e84e8`. 시작 시 origin/main을 fetch하여 확인했다.
2. **Final commit:** 이 보고서를 포함하는 게시 commit이다. 사용자의 결정에 따라 Hub 수정은 보류하고 검증된 llm 변경만 게시한다. 정확한 commit ID는 Git 이력과 업로드 결과에서 확인한다.

## Inventory와 구현 경계

3. **Open-schema inventory:** [schema-ownership.md](schema-ownership.md)의 열린 경계 표를 따른다. Production Python 161개에서 schema 후보 128개를 수집했다. 원문 후보는 배포 제외 `tests/llm/reports/schema_ownership_audit.json`에 있다.
4. **Semantic-owner inventory:** 서비스는 Project policies, Engine은 실행 전략 설정, Component는 도메인 설정, handler는 action 설정, 모델 클라이언트/adapter는 모델 요청, Application은 metadata/data를 소유한다. 구체적인 필드 목록은 같은 문서의 닫힌 계약 표에 있다.
5. **Actual substitution boundaries:** Engine registry/for_agent, Graph handler dispatch, 주입 RAG embedding/reranker/extractor, OCR backend, MCP/브라우저/외부 RAG host adapter, token counter, plugin Component.
6. **Ownerless open objects found:** 내장 definition 최상위, Skill resources, Project/Session 임의 최상위 키, parameters/policies 임의 section, Graph control node와 상위 Workflow 확장, 기본 Component 설정, owner 표시가 없는 provider/adapter 객체.
7. **Ownerless objects removed:** 내장 계약을 닫고 metadata/data 및 선택 구현체/adapter 경계를 명시했다. JSON Schema 문서, Workflow 입력/비교 데이터, evaluator 결과처럼 실행 설정이 아닌 JSON도 작성자/소비자를 명시했다.
8. **Backend-owned contracts closed:** Agent, Skill, Prompt, Workflow/control node, Goal, MCP, Refinement lifecycle, Memory content/envelope, Vision record, RAG generic record, Project/Session config/policies, 기본 구현 설정.
9. **Explicit metadata:** 정의의 metadata, Project/Session.config.data. Memory 삭제 대상을 선택하던 metadata.replaces는 명시적인 replaces 계약으로 분리했다. 내부 Run/checkpoint/summary 영수증은 Application metadata와 다른 신뢰 경계다.
10. **Pass-through:** provider kwargs, 선택 Engine/모델 인자, host adapter, selected Tool arguments, evaluator 결과를 소유자가 검증한다. 새 ownerless options/extra wrapper는 없다.
11. **Parent/child dependencies:** RAG가 모델 schema와 identity/vector/cache 공개 메서드를 사용한다. Graph는 선택 handler의 공개 configuration_schema/validate를 사용한다.
12. **Parent schema duplication removed:** Completion schema를 providers/schema.py로 옮겼고 RAG model params와 document/query kwargs는 모델이 공개한 schema를 사용한다.
13. **Private-option branching removed:** RAG의 params.model/dimensions 및 _call_fn/__dict__ 해석을 제거했다. Loop의 model/api_base 유효성 판단은 provider validator로 이동했다. RAG corpus의 model identity/dimension 무결성 검사는 그대로다.
14. **Child validation:** GraphEngine.for_agent의 SettingsLayout schema, handler.configuration_schema/validate, ModelClient.configuration_schema/validate_configuration/configured, provider.validate_model_params를 사용한다. 값 coercion이나 default materialization은 없다.
15. **Abstraction review:** 새 registry/interface/factory 계층은 만들지 않았다. 추가 production 모듈은 provider schema 한 개다. Workflow action의 기존 flat 형식을 유지했다.

## 도메인별 계약

16. **Agent schema:** description/input_schema/output_schema를 소비 코드와 일치하게 명시하고 metadata를 추가했다. legacy loop/skills/rag/mcp와 unknown top-level은 거부한다.
17. **Graph Agent allowed:** purpose, description, engine, engine_options, metadata.
18. **Graph Agent rejected:** completion/system_prompt/tools/resources/policy/input_schema/output_schema/output_format은 빈 값도 거부한다. foo/system_promt/instructions/permissions/resources2/future_option/ui도 거부한다. AgentComponent는 Graph 이름 분기를 하지 않는다.
19. **Graph engine_options:** workflow; config.buffer_size/cleanup_timeout; policy.max_steps/max_parallelism/timeout_seconds/max_nested_depth. unknown, revision/settings_name 우회는 거부한다.
20. **Workflow schema:** 알려진 top-level, edges, control nodes만 허용한다. initial_state와 JSON Schema는 작성자 소유 데이터다. version 1과 기존 DAG/loop/join 검증은 유지했다.
21. **Handler options:** type으로 handler를 선택하고 공통 필드를 제외한 node 사본을 handler schema로 검증한다. 기존 validate(node, context)도 유지한다. schema/validator 없는 함수는 공통 필드만 허용한다. AgentNode/ToolNode의 private 필드는 각 handler가 선언한다.
22. **RAG:** 분할·검색·generation publish 및 corpus 무결성은 RAG 책임이다. 설정에 제품 기본값/자원 상한을 추가하지 않았다. Generic record CRUD는 metadata를 저장하고 문서는 document API로 등록한다.
23. **Embedding:** model identity, 요청 dimensions, SDK no-cache는 EmbeddingModel 책임이다. chunk ordering/concurrency/cache/unchanged reuse/checkpoint는 기존 RAG 책임을 유지한다.
24. **Reranker:** schema/forwarding은 RerankModel 및 provider adapter가 소유한다. results index/score 검증과 재정렬은 기존 rerank 계약을 유지한다.
25. **Provider/SDK:** 알려진 JSON 옵션과 미래 kwargs를 adapter가 소유한다. opaque 인자와 runtime client identity를 보존한다. DEFAULT_MAX_RETRIES=0, no-cache, stream/n 프로토콜 및 retry 중첩 방지는 변경하지 않았다.
26. **Skill:** instructions/description/title/tags/resources/lineage/metadata. resources는 uri/description/metadata만 허용하고 lineage 불변성과 참조 보호는 유지한다.
27. **Prompt:** messages/description/metadata. message의 role/content 계약 유지.
28. **Goal:** 기존 알려진 목표·진행·scope/run_refs + metadata. lifecycle/승인 경계 유지.
29. **MCP:** transport/command/args/env/url/headers/metadata. 새 generic connector options를 미리 만들지 않았다.
30. **Refinement:** lifecycle/history/source/evaluation/receipt 필드를 명시했다. create allow-list와 target별 EDITABLE은 유지하며 metadata를 자동 변경 대상으로 확대하지 않는다. Memory 제안은 새 replaces 경로를 사용한다.

## 호환성과 회귀

31. **Backward compatibility:** 기존 unknown top-level definition/config는 저장·읽기·준비 시 거부된다. 이전 approval/checkpoint가 definition 지문 변경을 무시하지 않는다. 공개 completion_schema import 위치가 provider로 이동했다.
32. **Manual changes:** ui→metadata.ui, Project 사용자 키→data, Skill resource 설명→description/metadata, Memory metadata.replaces→replaces, completion_schema→llm.providers.schema. [예제](schema-ownership.md#수동-변경-예시).
33. **Automatic migrations:** 없음. 저장 버전 변경, alias, 자동 cascade/변환도 없다.
34. **Tool constraints:** fixed/bounded/selectable, 교집합 schema, 실행 직전 검증, 자식 narrowing, approval/checkpoint binding을 변경하지 않았다. 최종 suite 결과는 아래에 기록한다.
35. **Memory:** extraction/retrieval 분리와 명시 prompt를 유지했다. metadata 무권한 및 replaces/CAS/원자 consolidation 경로를 검증한다.
36. **Skill evolution:** Agent/child 참조 보호, 불변 lineage 및 FORK/rollback 검사를 유지한다.
37. **Approval/Refinement:** 공통 approval/ToolExecutor와 평가·CAS·rollback 소유자를 유지한다. 새 승인 엔진은 없다.

## 검증 기록

38. **Files added:** production 1개, 회귀 테스트 1개, 문서/예제 3개. 아래 전체 목록을 참고한다.
39. **Files modified:** 아래 전체 목록을 참고한다. 구현 변경과 함께 기존 fixture의 소유 경계를 수정했다.
40. **Tests added:** tests.llm.test_schema_ownership의 정의/Graph 옵션/Skill resources/Project/Workflow closure, schema helper, 공개 schema inventory/AST, custom handler/embedding, opaque provider forwarding, Memory metadata 무권한 검사 13개. 기존 fixture는 임의 키를 metadata/data로 바꾸거나 자체 schema를 선언했다.
41. **Focused tests:** Linux Python 3.12.14, 175 passed / 0 failed / 0 skipped, 72.457초. schema_ownership, definition_components, graph_agent_contract, domain_hardening, operational_storage, operations_schema, rag_components, refinement, memory_processing, tool_constraints.
42. **Full LLM:** Linux Python 3.12.14, 1,308 passed / 0 failed / 0 skipped, 466.383초. snapshot `ish-provider-nobi1r8k`. llm/tests/llm/examples/llm의 Python 276개가 현재 소스 해시와 일치한다. 집중 검사는 전체와 중복이다.
43. **Hub:** 사용자의 명시적 요청으로 수정을 보류했으며 원본 Hub는 변경하지 않았다. 최소 연결 수정 후보를 적용한 Linux snapshot `ish-hub-contract-candidate-fp6chokr`에서 110 passed / 0 failed / 0 skipped, 105.592초. 후보는 hub/backend/defaults.py, hub/backend/naming.py, hub/widget/execution.py와 tests/hub/test_live.py, tests/hub/test_execution.py의 계약 위치/import만 수정한다. 원본 Hub 기준 이전 검사는 110개 중 44 errors였으므로 후보 결과를 현재 원본의 통과 결과로 해석하면 안 된다.
44. **CI:** 새 ownership/definition 모듈을 CI 집중 목록에 추가했다. 게시 후 실행 결과는 GitHub Actions에서 별도로 확인해야 하며, 아래 로컬 통과 결과를 CI 통과로 간주하지 않는다.
45. **External model/endpoint:** 호출하지 않았다. Fake/injected model, 로컬 provider fixture, 실제 로컬 저장·프로세스·Graph 실행으로 검증한다. 사내 endpoint와 장시간 soak 결과는 주장하지 않는다.
46. **Known limitations:** API 계약 변경은 Application의 명시적 수정이 필요하다. Custom handler.validate와 custom model schema의 정확성은 해당 구현체의 책임이다. SDK 미래 kwargs의 실제 지원 여부는 SDK/endpoint가 판단한다. Hub 후보 중간 검사에서 읽기 위치 UI 테스트의 대기 timeout이 한 번 발생했고 최종 전체 검사에서는 재현되지 않았다. timeout 완화나 해당 UX 수정은 하지 않았으며 간헐적 UI 타이밍 문제의 해결을 주장하지 않는다.
47. **Remaining ownership ambiguities:** 내부 영수증 metadata와 Application metadata를 구분해야 한다. 전자는 기존 backend-managed 저장 계약이며 이번 작업에서 사용자 권한으로 공개하지 않는다. 정적 schema inventory는 임의 외부 plugin 코드를 증명하는 검증기가 아니다.

## 변경 파일 목록

새 파일:

- `docs/llm/schema-ownership-review.md`
- `docs/llm/schema-ownership.md`
- `examples/llm/owned_handler.md`
- `llm/providers/schema.py`
- `tests/llm/test_schema_ownership.py`

수정 파일:

- `.github/workflows/tests.yml`
- `docs/llm/agents.md`
- `docs/llm/architecture-boundaries.md`
- `docs/llm/architecture.md`
- `docs/llm/component-definitions.md`
- `docs/llm/domain-hardening.md`
- `docs/llm/handoff.md`
- `docs/llm/memory-processing.md`
- `docs/llm/memory.md`
- `llm/components/agents/component.py`
- `llm/components/agents/README.md`
- `llm/components/base.py`
- `llm/components/definitions.py`
- `llm/components/goals/component.py`
- `llm/components/goals/README.md`
- `llm/components/mcp/component.py`
- `llm/components/memory/component.py`
- `llm/components/memory/consolidation.py`
- `llm/components/memory/data.py`
- `llm/components/memory/processing.py`
- `llm/components/memory/tools.py`
- `llm/components/prompts/component.py`
- `llm/components/prompts/README.md`
- `llm/components/rag/_client.py`
- `llm/components/rag/component.py`
- `llm/components/rag/embedding.py`
- `llm/components/rag/extraction.py`
- `llm/components/rag/ingestion.py`
- `llm/components/rag/README.md`
- `llm/components/rag/rerank.py`
- `llm/components/README.md`
- `llm/components/refinement/component.py`
- `llm/components/refinement/data.py`
- `llm/components/refinement/README.md`
- `llm/components/refinement/tools.py`
- `llm/components/skills/component.py`
- `llm/components/skills/README.md`
- `llm/components/tools/builtin/catalog.py`
- `llm/components/vision/component.py`
- `llm/components/workflows/component.py`
- `llm/components/workflows/graph.py`
- `llm/components/workflows/README.md`
- `llm/CONFIGURATION.md`
- `llm/core/models.py`
- `llm/core/policies.py`
- `llm/core/README.md`
- `llm/core/schema.py`
- `llm/engines/graph/agent.py`
- `llm/engines/graph/engine.py`
- `llm/engines/graph/tool.py`
- `llm/engines/loop/engine.py`
- `llm/providers/README.md`
- `llm/README.md`
- `llm/services/schema.py`
- `tests/llm/test_agent_workflow.py`
- `tests/llm/test_architecture_boundaries.py`
- `tests/llm/test_backend.py`
- `tests/llm/test_builtin_tools.py`
- `tests/llm/test_components.py`
- `tests/llm/test_configuration_results.py`
- `tests/llm/test_configuration_validation.py`
- `tests/llm/test_definition_components.py`
- `tests/llm/test_domain_hardening.py`
- `tests/llm/test_explicit_configuration.py`
- `tests/llm/test_facade_requests.py`
- `tests/llm/test_graph_checkpoints.py`
- `tests/llm/test_memory_component.py`
- `tests/llm/test_operational_storage.py`
- `tests/llm/test_operations_schema.py`
- `tests/llm/test_persistence.py`
- `tests/llm/test_project_component_settings.py`
- `tests/llm/test_project_conversation_storage.py`
- `tests/llm/test_project_creation.py`
- `tests/llm/test_project_policies.py`
- `tests/llm/test_python312.py`
- `tests/llm/test_rag_components.py`
- `tests/llm/test_rag_extraction_repair.py`
- `tests/llm/test_refinement.py`
- `tests/llm/test_run_api.py`
- `tests/llm/test_search_tools.py`
- `tests/llm/test_settings_consistency.py`
- `tests/llm/test_settings_contract.py`
- `tests/llm/test_tool_data.py`
