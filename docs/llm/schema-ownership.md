# Schema와 설정의 소유권

기준 main: `f25a84de6759b9d723baf402f79ed451de1e84e8`.
Backend는 계약·검증·집행을 제공하고 Application은 프리셋·한계값·검색 전략·UX를 선택한다.
이번 변경은 값의 기본값, 저장 버전, 실행 소유 계층을 바꾸지 않는다.

## 원칙

- Backend-owned contract는 closed by default다. Unknown field는 저장 또는 실행 준비에서 거부한다.
- 설정의 semantic owner는 하나다. Parent는 child의 stable public interface를 사용하며 private 옵션은 해석하지 않는다.
- 교체 가능한 Engine, handler, 모델 클라이언트, host adapter가 자신의 설정을 검증한다.
- Application 확장은 definition.metadata, Project/Session.config.data에 둔다. metadata는 실행 권한이 아니다.
- Open schema는 소유자가 명시된 경계만 허용한다. 열린 dict를 늘리는 것이 목표가 아니다.
- 기존 저장값을 자동 이동하거나 호환 별칭을 추가하지 않는다.

## Inventory: 닫힌 계약

| 범위 | 허용 필드 / 의미 소유자 |
| --- | --- |
| ProjectConfig | policies, parameters, data. parameters는 engines/components 대상 map. 정책은 서비스, 구현 인자는 선택 구현체 소유 |
| Session config | parameters, data. engine override만 허용 |
| Agent | purpose, description, engine, system_prompt, completion, engine_options, tools, resources, policy, input_schema, output_schema, output_format, metadata |
| Graph-backed Agent | purpose, description, engine, engine_options, metadata만 허용. GraphEngine.for_agent가 선택 후 검증 |
| Graph engine_options | workflow + config(buffer_size, cleanup_timeout) + policy(max_steps, max_parallelism, timeout_seconds, max_nested_depth). SettingsLayout schema 사용 |
| Skill | instructions, description, title, tags, resources, lineage, metadata. resources 원소는 uri/description/metadata; lineage는 parent/parent_revision |
| Prompt | messages, description, metadata. message는 role/content |
| MCP | transport, command, args, env, url, headers, metadata. transport protocol은 MCPComponent, 연결 구현은 주입 connector |
| Goal | title, objective, scope, status, success_criteria, progress, next_actions, run_refs, metadata |
| Refinement | target, operation, expected_version, parent, reason, evidence, patch, status, before, metadata, source, history, evaluations, applied_target, skill_versions |
| Memory content | content, kind, scope, session_id, status, tags, expires_at, metadata, replaces. 생명주기 필드는 Component만 작성 |
| RAG generic record | metadata만. 문서 색인/등록은 별도의 document API 소유 |
| Workflow | schema_version, entry, nodes, edges, initial_state, inputs, outputs, input_schema, output_schema, metadata |

Agent common schema는 Graph/Loop를 분기하지 않는다. Graph-backed Agent 검증은 Engine 해석
직후, Prompt/Skill/RAG/MCP 조회 전에 수행한다. completion/system_prompt/tools/resources/policy/
input_schema/output_schema/output_format은 값이 빈 객체·배열·null이어도 Graph Agent에서 거부한다.
foo/system_promt/instructions/permissions/resources2/future_option/ui도 거부한다.

## Inventory: 명시적으로 열린 경계

| 경계 | 소유자·검증·소비 위치 |
| --- | --- |
| definition.metadata, config.data | Application. JSON-safe 검증; Backend는 Tool/Engine/timeout/권한으로 읽지 않음 |
| completion, model client params | 선택된 provider adapter/SDK. providers/schema.py의 알려진 JSON 필드 + provider 미래 kwargs; 실제 forwarding은 provider/model client |
| Agent.engine_options | 선택 Engine.for_agent + 해당 configuration_schema. AgentComponent가 Engine별 schema를 복제하지 않음 |
| RAG embedding/extraction/rerank_params | 주입된 client.configuration_schema/validate_configuration/configured. 기본 클라이언트는 LiteLLM schema를 공개 |
| document_kwargs/query_kwargs | 선택 Embedding client의 요청 계약. RAG는 객체를 그대로 전달 |
| custom Workflow action fields | type으로 선택한 handler. configuration_schema 또는 validate(node, context)에서 검증 |
| host browser action / external RAG options | 명시적 host adapter. 자체 인터페이스에서 검증/소비 |
| retention.counter_params | 선택된 host token counter. 서비스가 소유한 messages override는 금지 |
| Refinement.patch / before | EDITABLE allow-list와 선택 target validator / 실제 target snapshot. lifecycle authority로 쓰지 않음 |
| evaluation.results/regressions/improvements | 외부 evaluator의 결과 payload. envelope와 evidence 참조는 Backend 검증 |
| input/output/resume JSON Schema | Workflow/Agent contract author. JSON Schema 검증 및 local reference만 허용 |
| initial_state, condition.value | Workflow가 명시적으로 선언한 입력/비교 데이터. Backend 설정 확장이 아니며 실제 의미는 bindings/condition contract가 부여 |
| Component serializer / DefinitionComponent 확장 | 선택 plugin Component가 자신의 record를 검증. 일반 serializer는 실행 설정 해석기가 아님 |

`x-schema-owner`, `x-open-kind`는 schema 설명이며 runtime 권한이 아니다. Typed map인 env,
headers, MCP alias map, 노드 ID map, binding map은 값의 계약을 가진 map이며 무소유 open object가 아니다.
`SettingsLayout._presence`의 object는 `not/anyOf` 안의 존재 여부 조건식이다. 설정 객체 schema가 아니므로
additionalProperties를 닫으면 오히려 검사 의미가 바뀐다.

저장된 Run/Step/checkpoint의 backend-managed metadata는 서비스의 내부 영수증이다. Application
definition.metadata와 서로 다른 신뢰 경계이며 이 작업은 이를 새로 사용자 쓰기 권한으로 노출하지 않는다.
Memory summary의 내부 metadata도 파생 캐시 무결성 정보다. 반면 사용자가 작성 가능한 Memory
metadata.replaces는 삭제 대상을 지정하던 경로였으므로 전용 replaces 필드로 분리했다.

## Workflow handler 계약

공통 node 필드는 type, inputs, outputs, input_schema, output_schema, pause_before, resume_schema,
timeout_seconds, metadata다. 제어 노드는 다음 필드만 추가한다.

| type | 추가 필드 |
| --- | --- |
| branch | cases(port/when), default |
| parallel | join |
| join | wait |
| loop | body, max_iterations, while, on_limit |
| workflow | workflow |
| end | 없음 |

기존 flat action 형식은 유지한다. `type`이 소유 handler를 명시하므로 options wrapper를 추가하지 않는다.
Graph는 공통 필드를 제외한 사본을 선택 handler의 `configuration_schema()`에 검증한다.
기존 `validate(node, context)`도 지원하며 이 경우 handler가 자신의 추가 필드 allow-list를 책임진다.
schema/validator가 없는 단순 함수는 공통 필드만 받는다. AgentNode와 ToolNode는 닫힌 schema를 제공한다.
공통 필드와의 교차 조건·참조·Tool 인자 검사는 기존 validate 계약이 소유한다.

```python
from llm.core.schema import object_schema

class ReviewNode:
    @staticmethod
    def configuration_schema():
        return object_schema({"review_mode": {"enum": ["syntax", "logic"]}}, required=["review_mode"])

    async def __call__(self, node):
        return {"mode": node.definition["review_mode"]}
```

새 option이나 handler를 등록해도 GraphEngine에 field-level 분기를 추가하지 않는다.
WorkflowComponent는 구조/JSON 저장을 검증하고, 선택 환경의 handler 설정은 Graph 실행 준비에서 검사한다.

## 모델 소유 경계

RAG search method/expand/limit/candidate_count/rrf_constant/graph traversal과 분할은 RAG 알고리즘이다.
선택 EmbeddingModel이 model identity, dimensions 검증, SDK no-cache invariant를 소유한다.
RAG는 `identity`, `extract_vector`, `validate_vectors`, `cache_identity`의 결과만 이용한다.
custom client가 이 선택적 계약을 제공하지 않으면 명시적 embedding_id와 공통 single-vector 검증을 쓴다.
Model identity/벡터 무결성 메서드는 새로운 registry가 아닌 기존 주입 클라이언트의 공개 계약이다.
params/document/query 객체는 fingerprint에 통째로 포함할 수 있지만 필드 의미를 해석하지 않는다.
RerankModel은 SDK 인자를 전달하고 검색 결과의 index 검증/정렬은 검색 계약에 그대로 남는다.
공통 extraction protocol의 failure_policy/prompt_id는 RAG admission, repair/json_mode/관계 추출은
TripleExtractor가 소유한다. SDK request의 response_format 해석은 추출 모델 호출 경계에서만 수행한다.
이 고정 공개 protocol을 위해 별도 registry/interface를 추가하지 않았다.

## 수동 변경 예시

```python
# 이전: {"instructions": "review", "ui": {"color": "blue"}}
skill = {"instructions": "review", "metadata": {"ui": {"color": "blue"}}}

# 이전: ProjectConfig(theme="dark")
config = {"data": {"theme": "dark"}}

# 이전: WorkflowGraph(entry="done", label="Example")
workflow = WorkflowGraph(entry="done", metadata={"label": "Example"})

# 이전: Memory metadata.replaces가 삭제 대상이었다.
candidate = {"content": "updated fact", "status": "candidate",
             "replaces": [{"id": "old", "revision": 1}]}
```

알 수 없는 config/policy 필드는 metadata로 자동 옮기지 않는다. 실제 설정이라면 owner의 schema에
명시하고, UI 표시용 데이터라면 Application이 metadata/data에 저장하도록 직접 고친다.
옛 approval/checkpoint binding이 바뀌면 재개가 거부된다. 자동 migration/alias/cascade는 없다.
Completion schema를 직접 import하던 Application은 `llm.providers.schema.completion_schema`를
사용한다. Provider 계약의 소유 위치로 이동했으며 `llm.core.schema`에 호환 별칭은 두지 않았다.

## 검증 경계

`tests.llm.test_schema_ownership`은 정의 allow-list, Graph 옵션, custom handler/Embedding 교체,
SDK opaque kwargs 보존, metadata 무권한, 공개 schema inventory와 production AST의 accidental
open object를 검사한다. Tool constraints/Memory/Skill/Refinement/approval 회귀는 기존 suite에서 검사한다.
외부 SDK가 모든 미래 옵션을 지원한다는 뜻은 아니다. 실제 provider 지원 여부는 SDK/endpoint가 최종 판정한다.
