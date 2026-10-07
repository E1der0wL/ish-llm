# Project 정책, Host 자원, 실행 불변식

이 문서는 설정 소유권의 현재 계약이다. 영속 실행 관계는 Project → Session → Run → Step이며
Engine은 실행 전략, Component는 선택된 데이터와 capability를 소유한다.
Application은 제품의 한계값·프리셋·위험 분류를 선택한다. Backend는 그 값을 검증하고 집행한다.

## 설정 inventory

| 범위 | PROJECT: 저장·해석되는 값 | HOST: 주입하는 구현/공유 자원 |
| --- | --- | --- |
| 서비스 | policies.context/run/approval/tools/tool_retry/usage/retention/output, conversation_storage | 저장소, Conversation factory, context builder, token counter, policy resolver, logger, events, backups, observability sink |
| Tool | allowed_tools, max_calls, timeout_seconds, max_output_chars, argument_constraints, retry 횟수/지연 | ToolPolicy.authorize/runner/operation_key/operation_probe/classify/retry_safe_tools/revision |
| Loop | config.completion/system_prompt/buffer_size; policy.max_iterations/request_timeout/tool_timeout/max_tool_calls/max_argument_chars/max_output_chars/completion/provider | completion_fn, settings_name |
| Graph | workflow 선택; config.buffer_size/cleanup_timeout; policy.max_steps/max_parallelism/timeout_seconds/max_nested_depth | handlers, revision, config_keys, settings_name |
| PreparationStep | policy.timeout_seconds | action, name, kind, settings_name |
| RAG | chunk/search/extraction/ingestion/cache/index 값; model params, provider 정책 | embedding/reranker/extractor, schema/validator/factory 구현 |
| Memory | 검색·처리·추출·보관 정책, config.processing.extract_prompt_id | completion_fn/token_counter/search_fn |
| 다른 Component | 각 configuration_schema의 config/policy, 정의 레코드 | OCR/backend/MCP connector/Tool dependency installer 등 선택 구현 |
| BuiltinTools | Tool 인자로 전달하는 max_file_bytes, timeout_seconds, max_output_bytes 등; 정책 제약으로 고정/축소 가능 | 작업 root, shell, checks, 기능 가용성, 외부 adapters |
| ProviderCalls | caller의 SDK/outer timeout/retry는 각 Project 구현 설정 | ProviderLimits.max_active/max_waiting/wait_seconds: Backend 전체 공유 슬롯 용량 |
| 저장/출력 | 결과 의미를 제한하는 retention/output 정책 | OutputPolicy.batch_size/max_delay/max_chars는 flush 기준이며 출력 삭제·절단이 아님; cache/index stride |
| 프로세스 | Tool 시간 제한은 Project scope | interpreter, command/root, worker pool/IPC/memory 용량, OS 격리 구현 |

`backend.host_configuration()`은 서비스 자원의 읽기 전용 설명이다. callable/client/인증값을
직렬화하지 않으며 Project에 저장하거나 변경하는 API가 아니다. 공유 admission 대기 용량은
Project 요청 시간 제한과 다르다. ProviderLimits는 미설정이면 제한을 추가하지 않는다.

`BaseEngine.step(..., timeout_seconds=...)`와 `stream_completion(..., provider=...,
max_output_chars=...)`는 호출별 집행 도구다. 호출자가 자신의 Project 설정에서 해석한 값을
넘기며 BaseEngine 생성자에 전역 실행 제한을 저장하지 않는다. ToolExecutor의 호출별 제한도
Run의 ToolExecutionScope와 더 좁은 쪽으로 결합한다. helper 직접 호출은 서비스 설정 해석을
자동 수행하지 않으므로 custom Engine은 자기 configuration_schema와 해석 책임을 유지한다.

## 계층과 UI

내장 Engine은 Project → Session → Agent 순서다. missing은 상속, nullable 일반 설정의 null은
명시적 해제다. `x-narrowing=maximum` 제한은 child가 증가시키거나 null로 해제할 수 없다.
Project에서 제한이 없으면 child가 자체 한계를 선택할 수 있다. Tool child scope의 allow-list와
fixed/bounded/selectable 제약도 부모를 넓히지 못한다.

주입 RAG client의 명시 설정은 Project보다 낮은 baseline이다. Project에서 덮어쓸 수 있으며
client schema/validator가 의미를 검증한다. 기본 client factory도 저장 단계에서 같은 검증을 한다.
SDK kwargs는 사용자가 명시한 값만 전달한다. 설정 조회의 values/sources/overridden/editable은
관찰용이며 Host product override를 만들어 저장하지 않는다. 일반 schema helper의 host 표시는
사용자 adapter용으로 남지만 내장 Engine의 정책 우선순위에는 사용하지 않는다.

## 불변식 inventory

- Loop `stream=True`, 단일 completion choice `n=1`: 스트림/Tool 응답 연결 프로토콜.
- LiteLLM `DEFAULT_MAX_RETRIES=0`: SDK의 `max_retries or DEFAULT_MAX_RETRIES` 호환성 문제 차단.
  명시 num_retries/max_retries는 보존하고 SDK retry와 outer retry를 곱하지 않는다.
- `LITELLM_LOCAL_MODEL_COST_MAP=True`: 초기화의 외부 cost-map fetch 격리. inference 정책이 아니다.
- RAG embedding no-cache/no-store: llm의 ordinal/cache/checkpoint가 매핑을 소유한다.
- 저장/schema version=1, JSON 무결성, local schema refs, path/symlink, CAS, atomic generation,
  Workflow cycle, checkpoint identity, 승인 binding, uncertain effect 재시도 보호.
- worker 취소·정리 및 stream/queue backpressure는 자원 수명 계약이다. 취소 회수 한계는 정상 실행
  시간 제한과 분리한다. Graph cleanup_timeout을 사용자가 설정했다면 Graph config가 소유한다.
- Tool schema default는 Python signature와 일치해야 한다. 모델 입력의 risk/metadata는 권한이 아니다.

위 불변식은 Project가 해제하지 못한다. extraction temperature 등의 일반 SDK tuning은
Project/client가 명시한 값이며 불변식으로 승격하지 않는다.

## Tool 승인·위험 분류

Project `policies.approval`은 enabled, risk_scheme, rules를 가진다. rule의 category는 정확히
일치하며 max_risk는 0 이상의 정확한 integer다. bool/float/문자열/음수는 거부한다.
같은 scheme이고 risk ≤ max_risk일 때만 자동 응답을 기록한다. scheme 불일치나 unknown(null),
불확실한 효과의 재시도는 자동 승인하지 않는다. 응답 저장은 resume를 자동 호출하지 않는다.

```python
from llm.components.tools import ToolClassification
from llm.services.runtime.tools import ToolPolicy
from llm.services.configuration import ServiceConfig

def classify(call):
    # 실제 프로그램에서는 Application이 검토한 기준을 사용한다.
    # call.arguments는 fixed/bounded/selectable 적용과 schema 검증을 마친 사본이다.
    return ToolClassification(category="file.read", risk_scheme="my-app-v1", risk=10)

services = ServiceConfig(tool_policy=ToolPolicy(classify=classify, revision="2"))
policies = {
    "tools": {"allowed_tools": ["file_read"], "max_calls": 5},
    "approval": {"enabled": True, "risk_scheme": "my-app-v1", "rules": [
        {"id": "read", "category": "file.read", "max_risk": 10}]},
}
```

숫자는 Application 예시다. Backend는 shell 문자열 위험도를 추정하거나 low/medium/high 구간을
정하지 않는다. Tool.classification은 정적 fallback, ToolPolicy.classify는 동기/비동기 신뢰
callback이다. 둘 다 없으면 unknown이다. callback은 최종 인자를 받으며 모델이 보낸 `risk`
필드를 자동 신뢰하지 않는다. low/medium/high 표시는 Hub의 `hub-risk-v1` presentation에만 있다.

승인 checkpoint action에는 실제 Tool 이름·인자·분류·ToolContract와 scope binding을 저장한다.
scope binding은 Project 제약·예산·retry와 Host adapter revision을 포함한다. 재개 시 값이
달라지면 fail-fast한다. classifier/실행기 코드의 의미가 바뀌면 Host가 revision을 올려야 한다.
authorize의 기술적 거절은 저장된 approve로도 우회할 수 없다. ToolContract.approval_required는
신뢰한 구현의 요구이며 일반 authorize=True가 이를 자동 승인하지 않는다.

## 저장 Agent 위임

`AgentComponent(engines=registry)`는 `agents`와 `tools` capability를 제공한다. 기본 backend는
자기 EngineRegistry를 연결한다. engines를 연결하지 않은 AgentComponent는 정의 CRUD만 제공한다.
Project가 agents를 선택하면 Loop/ToolNode에 `agent_run`을 제공할 수 있다.

```json
{"agent_id": "reviewer", "input": {"request": "이 변경을 검토해줘"}}
```

Tool은 저장 ID만 받는다. inline engine/model/tools/prompt나 권한 override를 받지 않는다.
`engines/agents.py::AgentExecution`을 Graph AgentNode와 AgentDelegation이 공유한다. 별도 실행
시스템이나 Run을 만들지 않고 부모 Tool Step 아래 Agent·LLM·Tool/Graph Step을 연결한다.
Graph-backed Agent는 기존의 다른 Graph environment 선택 계약을 유지한다.

부모 Tool allow-list/인자 제약/호출 예산/취소/usage 관찰을 공유한다. 중첩 승인에서는 waiting
checkpoint를 먼저 저장하고 부모 Run을 paused로 끝낸다. 명시적 resume는 새 Run에서 완료 효과와
completion을 재사용한다. 안정된 invocation key와 runtime_tool_usage는 호출 예산과 logical
request 계측이 재개 때 초기화되거나 중복되지 않도록 기존 checkpoint에 저장한다.
Agent 입출력 schema, require_tool, timeout 검증은 공통 AgentExecution이 계속 소유한다.

동적으로 Tool이 선택한 Agent에는 부모 Workflow의 사전 steering target을 연결하지 않는다.
부모 Loop에 추가한 지시는 다음 부모 completion 경계에서 소비한다. 직접 AgentNode의 기존
steering 경로와 예약은 유지한다. 임의 custom Engine이 durable pause를 지원하려면 기존
checkpoint/EngineEvent 계약을 구현해야 한다. container Tool에 business operation_key를
부여하지 않고 실제 effect Tool에 적용한다.

## Component 의존성과 정의

`required_components = ("component_id", ...)`는 정확한 Component ID 의존성이다. tuple이며
중복·자기 참조·잘못된 ID를 거부한다. 등록 순서와 무관하고 서로 의존하는 두 Component를
같이 선택할 수 있다. capability(`tools` 등) 대체 가능성이나 record별 선택 리소스와 다르다.
기본은 빈 tuple이다. 선택적 `resources.skills` 때문에 Agent 전체에 skills를 강제하지 않는다.

Project create/save/selection/remove/backup/restore는 선택 집합을 검사한다. 누락 시
`ComponentDependencyError(code="component_dependency_missing", missing={...}, unavailable=(...))`
를 제공하고 디렉터리 변경 전에 실패한다. discovery는 `project_schema()["x-components"]`의
required_components다. Hub는 의존성과 누락 항목을 표시한다. Backend는 설치·자동 선택·설정
생성·cascade 삭제·자동 migration을 하지 않는다.

DefinitionComponent의 기본 schema는 닫힌 빈 object다. custom plugin이 열린 레코드를 원하면
`schema = open_schema("plugin-owned record", category="implementation")`를 명시한다.
일반 Component serializer가 JSON-safe 데이터를 다루는 것과 Backend 정의의 허용 필드는 다르다.
기존 Agent/Skill/Prompt/Goal/MCP/Workflow/Refinement의 닫힌 계약은 유지된다.

## 수동 변경 안내

- ToolPolicy(allowed_tools/max_calls/argument_constraints/max_retries/...) → Project policies.tools/tool_retry.
- LoopEngine/GraphEngine/PreparationStep 실행 scalar → parameters.engines.<name>.config/policy.
- BaseEngine의 limit constructor → 구현체 설정에서 해석 후 step/stream_completion 호출에 전달.
- MemoryComponent(extract_prompt=...) → Prompt 레코드 + config.processing.extract_prompt_id.
- BuiltinTools(max_seconds/max_output_bytes/max_file_bytes) → 명시 Tool 인자와 Project 제약.
- risk="low" → Application이 정한 risk_scheme과 숫자. 기존 승인 payload 자동 변환 없음.
- Skill `hub_template_version` → `metadata.hub_template_version`. 기존 파일을 자동 바꾸지 않음.

저장 버전은 1을 유지한다. 아직 출시 전이며 이전 constructor/승인 계약의 호환 별칭이나
자동 데이터 migration을 추가하지 않았다. 기존 잘못된 설정/approval binding은 명확히 거부한다.
