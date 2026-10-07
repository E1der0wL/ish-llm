# Project authority / risk / Agent delegation 작업 보고

2026-10-07. 정책·소유권의 현재 계약은 [project-authority.md](project-authority.md),
영속·실행 관계는 [architecture.md](architecture.md)에 있다.
아래 번호는 작업 지침서의 최종 보고 79개 항목과 대응한다.

## 기준과 설정 소유권 (1–14)

| 번호 | 항목 | 실제 변경 / 판정 |
| --- | --- | --- |
| 1 | Base commit | `865c738b85ff354ce6160a99c7289ab9251c6e96`; 원격 main 재확인 후 같은 기준에서 작업 |
| 2 | Final commit | 자기 자신의 해시는 보고서 내용에 넣을 수 없으므로 최종 응답에 원격 main의 정확한 SHA를 기록 |
| 3 | Commits created | `7666779` 구현·문서, 후속 CI fixture/dependency 수정 commit(최종 응답 참조). 코드·회귀 fixture·문서가 같은 계약으로 배포됨 |
| 4 | Ownership inventory | [설정 inventory](project-authority.md#설정-inventory). 서비스, Engine, Tool, Component, Provider, 저장/출력, worker를 PROJECT/HOST/INVARIANT로 구분 |
| 5 | PROJECT | 서비스 policies, Engine config/policy, Component parameters, Tool 선택·호출/시간/출력 예산·인자 제약·재시도·승인 threshold |
| 6 | HOST | repositories/factories, handlers, completion 함수, connectors, classifier/authorize/runner/operation adapters, 기술 revision, token counter, logger/observability, 공유 Provider/worker 자원 |
| 7 | INVARIANT | protocol/storage/schema/CAS/path/checkpoint/승인 binding, uncertain effect 보호, single-stream choice, SDK retry/cost-map isolation, embedding cache 차단 |
| 8 | 경계 판단 | Provider admission은 Backend 전체 물리 자원, OutputPolicy는 flush tuning으로 유지. 호출 결과를 자르는 Tool output limit과 실행 timeout은 Project로 이동 |
| 9 | ServiceConfig | product policy를 추가하지 않음. `configuration()` 및 `backend.host_configuration()`으로 공유 자원/구현 identity의 읽기 전용 투영 제공. callable/credential은 반환하지 않음 |
| 10 | ToolPolicy | allowed_tools/max_calls/argument_constraints → policies.tools; max_retries/retry_delay → policies.tool_retry; auto_approve_categories 삭제. authorize/runner/operation_key/operation_probe/retry_safe_tools/revision 유지, classify 추가 |
| 11 | Engine Host override | Loop/Graph/Preparation 실행 scalar 생성자 제거. Project→Session→Agent 해석. BaseEngine도 전역 timeout/출력 제한 생성자를 없애고 호출별 집행 인자를 사용. Host는 구현 함수/handler/identity만 등록 |
| 12 | ProviderLimits | max_active/max_waiting/wait_seconds는 공유 admission 자원. 모두 None이면 추가 제한 없음. Project provider retry/timeout과 별개 |
| 13 | OutputPolicy | batch_size/max_delay/max_chars는 저장 flush 기준; 출력 삭제·절단하지 않음. Project output policy와 기존 batching owner 유지 |
| 14 | BuiltinTools | root/shell/checks/adapters/기능 가용성은 Host. max_file_bytes/timeout_seconds/max_output_bytes는 Tool 인자와 Project argument_constraints로 제어. 프로세스 기록의 임의 개수 eviction 제거 |

설정은 missing을 상속하고, nullable 일반값의 null은 명시값이다. 부모의 유한한 실행 한계는
Session/Agent에서 더 크게 하거나 null로 해제하지 못한다. Project에서 직접 정책을 편집하는
행위와 child override는 구분한다. RAG client의 명시 params는 Project보다 낮은 baseline이다.

## 정의와 RAG (15–20)

| 번호 | 항목 | 결과 |
| --- | --- | --- |
| 15 | 기존 DefinitionComponent | bare subclass가 open record를 암묵적으로 허용 |
| 16 | 최종 DefinitionComponent | 기본 `object_schema()`는 closed; unknown record field를 저장 전 거부 |
| 17 | Explicit open plugin | `open_schema(owner, category="implementation")`를 명시한 plugin만 open. 일반 Component generic 저장 계약은 그대로 유지 |
| 18 | 기존 RAG child 검증 | 주입 client는 의미 검증했지만 default factory 선택에서는 일부 의미 검증이 실행 전까지 지연 |
| 19 | 최종 default child 검증 | 주입 client 또는 EmbeddingModel/TripleExtractor/RerankModel default class의 `validate_configuration()`을 configuration 저장 경계에서 사용. RAG가 SDK private field를 해석하지 않음 |
| 20 | Fail-fast 검사 | 세 default child 모두 공백 model 및 file:// api_base 거부; custom child의 알려지지 않은 옵션 opaque 전달/child-owned validation 회귀 유지 |

## 승인과 위험 분류 (21–35)

| 번호 | 항목 | 결과 |
| --- | --- | --- |
| 21 | 이전 승인 계층 | Host auto_approve_categories가 Project rule 위에 별도 제한으로 작동 |
| 22 | 최종 승인 계층 | Project approval rules만 제품 임계값 소유. Host의 technical deny와 ToolContract 요구는 승인으로 해제 불가 |
| 23 | 이전 risk | Backend 문자열 low/medium/high 분류 |
| 24 | 최종 risk | `int >= 0` 또는 null. bool/float/숫자문자열/음수 거부 |
| 25 | Scheme | 수치에는 비어 있지 않은 risk_scheme 필요. Project scheme과 정확히 같을 때만 threshold 비교 |
| 26 | Unknown | risk=null이면 자동 승인 없음. 의미를 추정하거나 숫자로 변환하지 않음 |
| 27 | Label 소유권 | Hub `hub-risk-v1`의 label/preset helper에만 low/medium/high 구간 존재. Backend priority(표시 중요도)와 risk는 별개 |
| 28 | Dynamic classifier | `ToolPolicy(classify=callable, revision=...)`; sync/async callback → ToolClassification. 취소 전파 |
| 29 | Static fallback | `Tool.classification`; 동적 classifier가 있으면 그 결과 사용. 둘 다 없으면 unknown |
| 30 | Timing | constrained_arguments/schema 검사 뒤, 승인과 효과 앞 |
| 31 | 최종 인자 | fixed/bounded/selectable 적용 사본을 classifier에 전달. 원본 모델 인자로 분류하지 않음 |
| 32 | Spoofing 방지 | arguments의 risk/category와 InteractionRequest 표시값을 authority로 사용하지 않음. 요청 category/risk는 trusted classification으로 채움 |
| 33 | Durable binding | Tool identity, 최종 인자, argument_constraints, ToolContract/revision, classification, scope budget/retry, Host adapter revision을 기존 checkpoint/interaction에 결합 |
| 34 | 변경 감지 | classification/contract/policy/adapter 변경 시 기존 승인 재사용 거부. 코드 의미 변경은 Host revision 갱신 책임. 저장 approve 이후에도 technical authorize 재검사 |
| 35 | retry_uncertain | 숫자 threshold로 자동 승인하지 않음. 기존 explicit retry_nodes/원장 reconciliation 계약 유지; response만 저장하고 자동 resume하지 않음 |

동일 Backend·동일 Tool을 두 Project에서 실행해 한쪽만 actor=policy 응답을 저장하는 검사를
추가했다. 자동 응답을 저장해도 실제 효과는 explicit resume 이후에만 실행된다.

## Agent 위임 (36–46)

| 번호 | 항목 | 결과 |
| --- | --- | --- |
| 36 | 이전 경로 | Graph AgentNode가 업무 실행·resource/binding/schema 해석을 소유 |
| 37 | 공용 경계 | `engines/agents.py::AgentExecution`으로 기존 실행 계약 이동. 별도 실행 Manager/저장 원본을 만들지 않음 |
| 38 | AgentNode | 공통 실행기의 Graph handler로 유지. GraphEngine orchestration 책임 유지 |
| 39 | agent_run | `{agent_id: 저장 ID, input: object}`만 입력. inline definition/engine/model/authority override 금지. selected Agent input_schema가 의미 검증 |
| 40 | Same Run | AgentComponent tools capability → ToolExecutor → AgentDelegation → AgentExecution. 별도 Session/Run 생성 없음 |
| 41 | Step | 외부 agent_run Tool Step → Agent Step → LLM/Tool/Graph child Step. parent_step_id/delegation_step_id/agent binding 유지 |
| 42 | 권한 축소 | 부모 allow-list/argument_constraints/호출 예산/usage/취소 공유. child는 선택 Tool을 확대하지 못함. 실행 한계는 더 좁은 값 사용 |
| 43 | 중첩 승인 | waiting checkpoint 저장 후 paused. 새 Run으로 명시적 재개. 완료 completion/Tool/Graph node 재사용; effect 재실행 방지. invocation key/runtime_tool_usage를 기존 checkpoint에 저장 |
| 44 | 취소/시간 | 부모 취소가 child 및 Tool에 전파. Agent timeout과 Tool output/call budget 집행. 기존 Run/Provider 제한 경계 사용 |
| 45 | 결과 검증 | 공통 Agent input/output schema, JSON format, require_tool 검증 이후 최종 출력. 검증 실패를 성공으로 기록하지 않음 |
| 46 | 검사 | same-Run/Step, 중첩 approval/resume, 완료 effect 비재실행, Graph-backed pause/restart, output schema, 호출 한도, child timeout, 권한 확대 거부, fixed 인자, recursion, cancellation |

동적 agent_run에는 부모 Workflow의 사전 steering target을 연결하지 않는다. 부모 Loop의
추가 지시는 다음 부모 completion 경계에서 소비한다. 직접 AgentNode의 steering 기능은
그대로다. durable agent_run 부모는 현재 Loop/Graph checkpoint 계약을 사용하며 임의 custom
Engine의 checkpoint 프로토콜을 자동 추정하지 않는다. container Tool에 business operation_key를
부여하지 않고 실제 effect Tool의 receipt를 사용한다.

## Component 의존성 (47–54)

| 번호 | 항목 | 결과 |
| --- | --- | --- |
| 47 | Public contract | ProjectComponent/Component.required_components: tuple[str, ...] = () |
| 48 | 등록 | tuple, 유효 exact ID, 중복 없음, 자기 참조 금지. 의존 구현을 먼저 등록할 필요 없음 |
| 49 | Project fail-fast | create/save/select/backup/restore의 선택 집합 검증. 누락은 ComponentDependencyError.missing/unavailable/code 제공 |
| 50 | 제거 | 다른 선택 Component가 요구하면 soft/permanent remove를 파일 변경 전에 거부. cascade 없음 |
| 51 | Discovery | project_schema().x-components에 required_components, capabilities 표시. 별도 Manager 없음 |
| 52 | exact vs capability | persistent ID 선택 의존성과 대체 가능한 runtime capability 요구를 별개로 유지. Agent Tool에 특정 tools Component ID 강제하지 않음 |
| 53 | 자동 해결 | 없음. 자동 설치/선택/설정 생성/초기화 순서 재배열 없음 |
| 54 | 검사 | 기본값/명시 선언, 잘못된 tuple/ID/중복/self, 등록 순서, 양방향 cycle, missing/unregistered, create/save/select/remove, backup/restore, discovery |

## 문서와 Hub (55–61)

| 번호 | 항목 | 결과 |
| --- | --- | --- |
| 55 | 문서 | architecture, configuration, backend API, interactions, Tool constraints, Graph, 운영/저장/정책/설정 가이드, Component/Engine README, handoff 갱신. 전체 경로는 아래 manifest |
| 56 | 낡은 설명 | Host product ceiling, 문자열 risk, auto_approve_categories, Base/Loop/Graph constructor limit, Memory extract_prompt 설명 교체. 과거 검증 기록은 수정하지 않고 현행 문서 링크로 구분 |
| 57 | Hub 파일 | backend/defaults/naming/runtime/settings_service/approval, settings/pages, widget/execution, locales ko/en, 관련 fixture/examples/docs |
| 58 | Metadata | Skill hub_template_version → metadata.hub_template_version; Workflow description → metadata.description. 저장된 옛 파일 자동 변환 없음 |
| 59 | Project 정책 | Engine factory는 구현만 생성. Hub의 명시 모델/prompt/실행값은 Project config로 작성. Host 자원은 읽기 전용 catalog |
| 60 | Risk 표시 | Hub 전용 scheme/label/approval_preset helper, classifier 주입 및 schema scheme 설명. 자동 classifier/preset 선택 없음. 기존 paused 안내를 유지하며 새 승인 대화상자를 만들지 않음 |
| 61 | Dependency UX | Component 설정 페이지에 요구 ID와 현재 미선택 ID 표시. 사용자가 전체 선택 집합을 명시 저장; Backend fail-fast 재검증 |

## 검증 (62–66)

최종 검증 snapshot은 `ish-provider-hbfe1l0s`다. 외부 endpoint 호출 없이 scripted provider,
실제 파일 저장소, subprocess/재시작, Graph checkpoint/Tool approval 및 Hub 서비스 경계를 검사한다.

| 번호 | 항목 | 결과 |
| --- | --- | --- |
| 62 | Focused | 36 passed / 0 failed / 0 skipped, 16.944초 |
| 63 | Full LLM | 1,331 passed / 0 failed / 0 skipped, 582.798초 |
| 64 | Full Hub | 112 passed / 0 failed / 0 skipped, 109.079초 |
| 65 | Integration | 위 focused/full에 포함. 별도 사내 endpoint/실제 모델 실험 아님 |
| 66 | Remote CI | 최초 실행 핵심 검사는 성공, Hub는 pyfiglet/ascii-magic 및 비배포 참조 Host 부재로 실패. PLUGIN_META 의존성 설치와 참조 Host 전용 검사 skip을 보강. 후속 workflow 실제 결과는 최종 응답 참조 |

명령은 Linux Python 3.12.14로 실행했다. `tests/llm/run_linux.py`가 Linux 파일시스템에
hash inventory를 갖는 source snapshot을 생성한다. reports/manual은 배포·자동 discovery에서 제외된다.
GitHub checkout에는 참조 ish.platform이 없으므로 실제 Host를 사용하는 검사만 명시적으로
skip한다. 일반 Hub 검사나 production 오류는 skip하지 않는다. 로컬 전체 검사는 참조 Host를
포함하며 skip 없이 통과했다. Hub dependency는 literal PLUGIN_META에서 읽어 설치한다.
CI fixture 보완 후 `ish-provider-2k885rdr`에서 focused 5개 및 Hub 전체 112개도
통과했다(실패/skip 0). 참조 Host가 없는 게시 checkout에서 별도 실행한 Host 검사 5개는
예상대로 skip됐다. 이 후속 변경은 테스트/CI/문서에만 있으며 LLM·Hub production 코드는 동일하다.

```sh
python tests/llm/run_linux.py --source /mnt/d/WorkSpace/ish \
  --modules tests.llm.test_project_authority tests.llm.test_component_dependencies \
  tests.llm.test_agent_delegation tests.llm.test_schema_ownership --full --hub
```

정적 감사: 제거한 Host product 필드/문자열 risk/Host override, Engine constructor, 설정 schema
ownership/narrowing, Component dependency call sites를 검색하고 검토했다. 기존 open-schema inventory
검사도 유지한다. `core/schema.py::mark_host_overrides`는 custom adapter의 읽기 전용 메타데이터
helper로만 남으며 내장 Engine product policy에는 쓰지 않는다. 새로운 registry/factory 계층은 없다.

## 파일 / 호환성 / 잔여 항목 (67–79)

| 번호 | 항목 | 결과 |
| --- | --- | --- |
| 67 | Added | project-authority.md, 이 보고서, hub/backend/approval.py, llm/components/agents/tools.py, llm/engines/agents.py, tests/hub/test_project_authority.py, tests/llm/test_project_authority.py, test_component_dependencies.py, test_agent_delegation.py |
| 68 | Modified | 아래 manifest. 대부분 기존 tests 변경은 생성자/Host policy fixture를 명시 Project 설정으로 이동한 것 |
| 69 | Deleted | 없음. AgentNode public import 유지 |
| 70 | Automatic migration | 없음 |
| 71 | Storage/schema | version 1 유지. risk/생성자/설정 계약은 출시 전 breaking change; 잘못된 옛 값/승인 binding을 자동 복원하지 않음 |
| 72 | External endpoint | 이번 작업에서는 실행하지 않음. 기존 API 키/사내 endpoint를 사용하지 않음 |
| 73 | Host/Product 판단 | 공유 Provider capacity/worker IPC·memory 한계는 Host 물리 자원, invocation 시간/Tool 인자·결과 한계는 Project. 구분 근거를 inventory에 기록 |
| 74 | Semantic ownership | 새 ownerless open record/second policy source 없음. Application metadata는 authority로 해석하지 않음. runtime callback 구현의 안전성과 revision 갱신은 Host 책임 |
| 75 | Agent 제한 | 동적 위임 steering 경로/임의 custom checkpoint 자동 지원 없음. Loop/Graph와 직접 AgentNode의 지원 계약은 구분. container는 효과 Tool의 원장을 대체하지 않음 |
| 76 | Dependency 제한 | exact ID 선택 검사만 제공. 설치/버전 constraint/topological initialization/자동 선택 없음. 선언 변경으로 기존 Project가 무효해지면 명시적 설정 수정 필요 |
| 77 | 운영 제한 | 프로세스 실행은 runner 선택에 따른 runtime isolation이며 자동 filesystem/network sandbox가 아님. 신뢰한 sync callback은 event loop를 block하지 않도록 Host가 구현해야 함. 실제 사내 endpoint/장시간 부하 검증은 별도 |
| 78 | Freeze criteria | 정의 closed/child 검증, Project 정책/Host 자원 구분, 수치 risk/신뢰 분류/binding, 공통 Agent 위임, 정확한 Component 의존성, Hub/문서 반영 및 전체 회귀 통과. 새 저장 원본 없음 |
| 79 | Architecture status | **FROZEN** — 이번 소유권·실행 계약의 구조를 고정. 위 운영 제한이나 실환경 성능까지 검증했다는 뜻은 아니며, 실제 결함 증거가 생기면 재검토 |

기존 persisted 문자열 risk나 옛 constructor를 조용히 지원하지 않는다. 수동 수정 예는
[계약 문서](project-authority.md#수동-변경-안내)에 있으며 자동 migration/호환 alias는 추가하지 않았다.

### Modified file manifest

변경된 기존 파일 128개다. AGENTS.md, ish.platform/, reports/, manual/은 업로드 대상이 아니다.

- .github/workflows/tests.yml
- docs/hub/README.md
- docs/llm/agents.md
- docs/llm/architecture-boundaries.md
- docs/llm/architecture.md
- docs/llm/backend-api.md
- docs/llm/component-definitions.md
- docs/llm/configuration-audit.md
- docs/llm/conversation-storage.md
- docs/llm/domain-hardening.md
- docs/llm/graph-engine.md
- docs/llm/handoff.md
- docs/llm/interactions.md
- docs/llm/nested-workflows.md
- docs/llm/operational-storage.md
- docs/llm/operations-and-ui-settings.md
- docs/llm/project-policies.md
- docs/llm/rag-components.md
- docs/llm/runtime-reliability.md
- docs/llm/settings-consistency.md
- docs/llm/tool-constraints.md
- examples/hub/ishrc.live.example.py
- examples/llm/bleach_research.py
- examples/llm/configuration_workflow.py
- examples/llm/developer_assistant.config.example.json
- examples/llm/ish_loop.py
- examples/llm/rag_components.py
- examples/llm/recovery_probe.py
- hub/backend/defaults.py
- hub/backend/naming.py
- hub/backend/runtime.py
- hub/backend/settings_service.py
- hub/locales/en.py
- hub/locales/ko.py
- hub/ui/settings/pages.py
- hub/widget/execution.py
- llm/CONFIGURATION.md
- llm/components/README.md
- llm/components/agents/README.md
- llm/components/agents/component.py
- llm/components/base.py
- llm/components/definitions.py
- llm/components/memory/README.md
- llm/components/memory/component.py
- llm/components/memory/data.py
- llm/components/memory/processing.py
- llm/components/rag/component.py
- llm/components/registry.py
- llm/components/tools/README.md
- llm/components/tools/__init__.py
- llm/components/tools/builtin/README.md
- llm/components/tools/builtin/catalog.py
- llm/components/tools/builtin/component.py
- llm/components/tools/builtin/files.py
- llm/components/tools/builtin/processes.py
- llm/components/tools/constraints.py
- llm/components/tools/registry.py
- llm/core/configuration.py
- llm/core/interactions.py
- llm/core/policies.py
- llm/engines/README.md
- llm/engines/base.py
- llm/engines/graph/README.md
- llm/engines/graph/agent.py
- llm/engines/graph/checkpoints.py
- llm/engines/graph/engine.py
- llm/engines/graph/tool.py
- llm/engines/loop/README.md
- llm/engines/loop/engine.py
- llm/engines/pipeline/engine.py
- llm/llm.py
- llm/policies/completion.py
- llm/providers/requests.py
- llm/services/configuration.py
- llm/services/lifecycle/projects.py
- llm/services/runtime/interactions.py
- llm/services/runtime/processes.py
- llm/services/runtime/runs.py
- llm/services/runtime/tools.py
- llm/services/schema.py
- tests/hub/test_bootstrap.py
- tests/hub/test_execution.py
- tests/hub/test_global_preferences.py
- tests/hub/test_ish_integration.py
- tests/hub/test_live.py
- tests/hub/test_plugin.py
- tests/hub/test_settings.py
- tests/llm/test_architecture_boundaries.py
- tests/llm/test_base_engine.py
- tests/llm/test_builtin_tools.py
- tests/llm/test_completion_processing.py
- tests/llm/test_configuration_results.py
- tests/llm/test_configuration_validation.py
- tests/llm/test_domain_hardening.py
- tests/llm/test_engine_steering.py
- tests/llm/test_explicit_configuration.py
- tests/llm/test_goals.py
- tests/llm/test_graph_agent_contract.py
- tests/llm/test_graph_checkpoints.py
- tests/llm/test_graph_engine.py
- tests/llm/test_interactions.py
- tests/llm/test_isolated_tools.py
- tests/llm/test_langgraph_engine.py
- tests/llm/test_long_running.py
- tests/llm/test_loop.py
- tests/llm/test_loop_steering.py
- tests/llm/test_memory_component.py
- tests/llm/test_memory_conversation.py
- tests/llm/test_memory_processing.py
- tests/llm/test_nested_graph.py
- tests/llm/test_observability.py
- tests/llm/test_operational_storage.py
- tests/llm/test_operations_schema.py
- tests/llm/test_pipeline.py
- tests/llm/test_policy_ownership.py
- tests/llm/test_project_creation.py
- tests/llm/test_project_policies.py
- tests/llm/test_reasoning_observations.py
- tests/llm/test_refinement.py
- tests/llm/test_runtime_reliability.py
- tests/llm/test_schema_ownership.py
- tests/llm/test_search_tools.py
- tests/llm/test_settings_consistency.py
- tests/llm/test_settings_contract.py
- tests/llm/test_skill_tools.py
- tests/llm/test_tool_constraints.py
- tests/llm/test_tool_packages.py
- tests/llm/test_vision.py
