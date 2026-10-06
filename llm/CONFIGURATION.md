# 명시적 설정 계약

Host의 `ServiceConfig(tool_policy=ToolPolicy(argument_constraints=...))`로 모든 Tool의 인자
권한을 fixed/bounded/selectable로 제한할 수 있다. Component 설정과 별개인 명시적 host
권한이며 모델 인자로 변경하지 못한다. [계약과 예제](../docs/llm/tool-constraints.md).

ish-llm은 사용자가 설정하지 않은 정책을 대신 결정하지 않는다. SDK 옵션을 생략하면 SDK의 native behavior를 사용하며, 프로토콜·저장 무결성·취소 회수에 필요한 강제값만 예외로 둔다.

Backend가 소유한 설정은 closed by default다. ProjectConfig 최상위는 `policies`, `parameters`,
`data`이며, Application 확장은 `data`에 둔다. 각 구현체는 자기 설정만 해석한다.
선택 child의 인자와 provider SDK pass-through는 해당 child/adapter가 검증하며 상위 구현체는
private 옵션을 복제하지 않는다. 모든 열린 객체에는 명시적인 소유자가 있어야 한다.
[소유권 inventory와 수동 변경 예](../docs/llm/schema-ownership.md)를 참고한다.

## 해석과 출처

- **missing**: 현재 계층에는 키가 없다. 상위 명시값을 상속하고, 모든 계층에서 없으면 최종 `values`에도 없다.
- **null**: null을 허용하는 필드의 명시값이다. 상위 값을 덮어쓴다. 자체 timeout에서 null은 제한 해제다. SDK 옵션의 null은 그대로 전달한다(지원 여부는 SDK 계약에 따른다).
- **value**: 검증한 명시값을 사용한다. 0/False/빈 문자열을 truthiness fallback으로 바꾸지 않는다.

ProjectConfig는 `policies`와 대상별 전달용 `parameters`를 구분한다. `parameters.engines.<설정 이름>`은 Engine, `parameters.components.<이름>`은 Component가 검증·해석한다. 동일한 이름의 인자도 다른 대상으로 자동 전달하지 않는다. Loop의 SDK 인자는 `parameters.engines.<설정 이름>.config.completion`에 둔다. Project의 최상위 completion은 없다.

Engine 옵션의 순서는 **Project → Session → Agent → host constructor/factory**다. Session에는 명시한 override만 저장하고 실행 시 최신 Project 사본을 상속한다. Session 생성 시 Project 값을 복사하는 계층은 없다. 일반 `resolve_configuration`은 호출자가 제공한 명시적 계층 순서와 마지막 host만 병합한다.

Agent completion은 model 없는 부분 설정도 허용한다. 모델이 모든 계층에서 없으면 호출 전에 오류다. Agent purpose를 system_prompt로 자동 변환하지 않으며, 명시적으로 선택한 Skill만 상속된 프롬프트에 결합한다. system_prompt=null은 상위 프롬프트를 해제한다. prompt 조회 API도 누락 키를 빈 문자열로 바꾸지 않으며 update_prompt(None)으로 명시적 해제가 가능하다.

Component의 설정은 **주입 client.params → ProjectConfig.parameters["components"][name]**이다. 주입 client의 provider 정책은 `model_providers`에 client→project 출처와 함께 표시하며, 추출 정책도 client→project 순서다. Component는 Session 소유가 아니다. 실행 정책은 ProjectConfig.policies의 명시 설정을 Run 시작 시 스냅샷으로 저장한다. 정책과 Component 설정은 Project 소유이며 Session에 저장하려 하면 오류다. Tool 재시도에서는 명시된 host ToolPolicy가 최우선이며 host의 null도 정책을 해제한다. host 자원 한도(ProviderLimits)는 공유 실행 자원에 별도로 적용된다.

`values/sources/overridden/editable`은 유지한다. 사용자 설정 출처 `default`는 없다. 강제값은 `enforced`에 따로 표시할 수 있다. Schema는 허용 형식만 설명하며 값 생성에 사용하지 않는다. `ProjectConfig()`는 빈 section만 가진다.

## 정책 소유권

모든 구현체의 공개 설정은 아래 외형을 사용한다. 두 section은 생략할 수 있는 object이며
비어 있는 설정을 조회하거나 저장할 때 자동으로 생성하지 않는다. section 자체의 null은
거부하고, null을 지원하는 개별 필드에서만 상속값을 해제한다.

```text
ProjectConfig
├─ policies                       서비스가 집행하는 공통 정책
└─ parameters
   ├─ engines.<등록 이름>
   │  ├─ config                   기능·모델·SDK 인자
   │  └─ policy                   해당 Engine의 실행 결정·제한
   └─ components.<이름>
      ├─ config                   기능·입력·검색·backend 선택
      └─ policy                   해당 Component의 실행·실패·보관 정책
```

| 구현체 | config | policy |
|---|---|---|
| Loop | completion(SDK dict), system_prompt, buffer_size | completion(입력 예산), provider(외부 재시도), max_iterations, request_timeout, tool_timeout, max_tool_calls, max_argument_chars, max_output_chars |
| Graph | buffer_size, cleanup_timeout | max_steps, max_parallelism, timeout_seconds, max_nested_depth |
| Preparation | 별도 정의한 기능 인자 | timeout_seconds |
| Pipeline | stages.<단계 이름>에 각 단계의 config/policy | 현재 없음 |
| RAG | chunk_size, embedding_params, extraction_params, rerank_params, document_kwargs, query_kwargs, search, graph, extraction_batch_size, index_batch_size, embedding_cache_max_bytes, search_cache_chars, extraction.json_mode/prompt_id/relation_types | embedding_concurrency, provider, ingestion.max_active, retention.job_max_age_seconds, extraction.repair_attempts/failure_policy |
| Memory | cache_records, search_status/search_limit, processing.completion/priority/extract_scope/summary_chars/recall_limit/recall_query_chars | tool_write_status, max_search_results, processing의 자동 실행 여부·입력/출력/시간 한도·실패 방식·provider |
| Vision | completion, ocr.backend/backends | limits.max_bytes/max_pixels, ocr.timeout, provider |
| Tool/BuiltinTool | enabled | 현재 없음 |
| Skill/MCP/Agent/Workflow/Prompt | 확장 구현체가 선언한 기능 설정 | 확장 구현체가 선언한 처리 정책 |

숫자라는 이유로 policy에 넣지는 않는다. search.limit은 원하는 검색 결과의 크기이고,
embedding_concurrency는 실제 동시 실행 상한이다. SDK의 timeout/num_retries/max_retries는
SDK 인자이므로 config의 모델 dict에 남는다. provider.wall_timeout/max_attempts는
ish가 집행하는 제한이므로 policy에 둔다. 서로 복사하거나 자동 연동하지 않는다.

Agent/Workflow의 **정의 레코드**, Tool parameter schema, 요청별 engine_options.workflow는
이 envelope의 적용 대상이 아니다. Agent.engine_options에는 선택한 Engine의 config/policy를
넣으며 Graph Agent의 workflow 선택은 같은 engine_options의 workflow에 둔다.
직접 생성자의 명시적 개별 인자는 같은 경로의 host 값으로 표시한다. 함수/client/runner는
JSON 설정에 저장하지 않는다.

재사용 정책 **알고리즘만** [policies/](policies/README.md)에 둔다. CompletionPolicy는
설정 계층이나 저장소를 모르며 구성한 호출자에게 선택된 입력 또는 정책 오류를 반환한다.
RunPolicy/ContextPolicy/ToolPolicy처럼 실행 수명과 결합된 정책은 해당 서비스에 남는다.
새로운 dict마다 Policy 클래스를 만들거나 여러 정책을 하나의 만능 객체로 합치지 않는다.

설정 분류 선언 `SettingsLayout`은 생성자 명시값, 공개 schema, 내부 인자 사본의 경로를
맞춘다. 저장된 평면 설정을 변환하는 호환 계층이 아니다. 평면 설정은 거부한다.
열린 config의 사용자 확장 필드와 SDK dict는 보존하되 이미 알려진 policy 필드를
config에 잘못 배치하면 검증 오류다.
분류를 선언하지 않은 schema 필드는 생성 시 거부한다. 중첩 config의 확장 허용 여부도
원래 선언을 따르며 알려진 키를 옮길 때 같은 컨테이너의 사용자 확장값을 제거하지 않는다.
새 구현체는 `implementation_schema(config=..., policy=...)`로 직접 schema를 작성해도 된다.
SettingsLayout을 상속하거나 내부 실행 알고리즘을 공통 클래스로 바꾸는 것은 필수가 아니다.

`config.input_policy`처럼 내부 정책 인자 이름을 확장 키로 넣어 우회할 수 없다.
선언된 경로와 충돌하지 않는 확장 키와 SDK dict 내부 키는 계속 보존한다.
SettingsLayout은 직접 매핑한 필드의 루트 `required`, `$defs`/`definitions`와 로컬
JSON Pointer 참조를 공개 경로에 맞춰 보존한다. 통째로 매핑한 하위 schema의 조건도 유지한다.
Pipeline은 하위 Engine schema의 참조 루트를 분리해 중첩 단계에서도 로컬 `$ref`의 의미를 유지한다.
분할하는 컨테이너의 `allOf`/조건부 제약, 중첩 `required`/정의, 별도 schema resource/anchor 등
안전하게 재배치할 수 없는 제약은 명시적으로 거부한다. 복합 조건은 변환 도우미를 거치지 않고
`implementation_schema()`의 최종 config/policy 경로에 직접 선언한다. 조건을 조용히 버리지 않는다.

호스트가 SDK dict 일부를 지정하면 해당 leaf만 `x-host-override`와 `editable=false`로
표시한다. null·배열은 교체 값이고 빈 dict는 하위 값을 덮어쓰지 않는다.
Hub는 저장값과 현재 적용값/출처를 구분한다. 고정 필드는 읽기 전용이며, 일부 경로만 고정된
JSON 편집기는 나머지 경로만 변경할 수 있다. 적용값·client 값·runtime 추정값을 저장값에
자동으로 복사하지 않는다. 여러 Engine이 설정 키를 공유하면 각 Engine의 적용값을 표시한다.

공통 `policies`는 서비스가 집행하는 context/run/approval/tool_retry/usage/retention/output만
해석한다. Loop 모델 입력은 `parameters.engines[이름].policy.completion`의 CompletionPolicy,
스트리밍 재시도는 같은 Engine의 `policy.provider`가 담당한다. Memory 보조 모델에는
`parameters.components.memory.policy.processing.provider`를 사용하며 RAG/Vision도 각자의 policy.provider를
사용한다. 이전 `policies.completion/provider_retry`는 오류로 거부하고 자동 변환하지 않는다.

RunPolicy는 공통 대기열·기한·capability 확장 제한을 표현한다. 클래스는 실제 실행·검증
책임이 필요할 때만 만들고 값을 담는 JSON마다 Policy 클래스를 추가하지 않는다.
토큰 계산기는 호스트 자원이다. 공통 누적 토큰 한도는 `policies.usage.counter`로 선택하며
Engine policy.completion.counter와 독립이다. 등록된 계산기는 UI schema에서 조회할 수 있다.
Loop policy.completion/policy.provider 전체의 null은 상속값을 해제한다. missing은 상위 명시값을
상속하고 모두 missing이면 정책 객체나 SDK 옵션을 생성하지 않는다.
[설정 예·실행 경계](../docs/llm/project-policies.md)를 참고한다.

## 미설정 시 실행

| 대상 | 동작 |
|---|---|
| Run | 실행 시간·대기 요청 수·capability 탐색 횟수의 자체 제한 없음 |
| Loop | 반복·요청·Tool 시간 및 출력/인자/Tool 수 제한 없음 |
| ToolExecutor | 추가 deadline·출력 잘림·자동 재시도 없음; 취소·승인·효과 원장 유지 |
| Graph / node | 자체 deadline 없음. max_steps/max_parallelism/depth 미설정 시 자체 제한 없음; LangGraph 옵션은 생략 |
| Completion SDK | timeout/temperature/max_tokens/top_p/retry/stream_options/tool_choice 등 미설정 키 생략 |
| Non-streaming provider | 최초 호출 한 번. outer retry와 wall deadline 없음 |
| Streaming bridge | 자체 deadline 없음. SDK timeout을 전체 스트림 시간으로 재해석하지 않음 |
| Context/Loop policy.completion/usage | 필터·토큰 상한·사용량 상한 없음 |
| Retention | 자동 보관 상한 없음. 명시적 정리 상한에는 unit도 명시해야 함 |
| Memory processing | recall/summarize/extract/compress가 명시적으로 활성화될 때만 실행 |
| RAG | 아래 필요한 알고리즘 설정은 사용 시 명확한 오류. 미설정 cache는 비활성, rerank/repair는 비활성 |

Loop의 `config.completion.timeout`, `policy.request_timeout`, `policy.provider.wall_timeout`, Component의 자체 provider 기한,
Run/Graph/node/Tool timeout은 서로 독립이다. Wrapper 시간을 SDK 인자에 복사하지 않는다.

Step의 명시 기한은 provider 진행을 중단하지만 yield된 이벤트를 저장하는 서비스 Task를 타이머로 취소하지 않는다. 저장 후 Engine이 다시 진행할 때 같은 절대 기한을 검사해 timeout 실패로 기록한다. 따라서 기한이 초기화되지 않으며 사용자 interrupt와 혼동되지 않는다.

## 제거한 암묵적 값

| 위치 | 제거/변경 |
|---|---|
| core/configuration.py, core/policies.py | built-in defaults 계층·정책 leaf 생성 제거 |
| core/schema.py, Component base/schema | Schema default·default_configuration 기반 생성 제거 |
| engines/loop/engine.py, engines/base.py | 반복·시간·수량·출력 cap, SDK timeout/tool_choice/stream_options 자동 삽입 제거 |
| engines/graph/engine.py | steps/parallelism/depth/timeout 숨은 한도 제거 |
| engines/pipeline/engine.py | Preparation timeout 제거 |
| providers/requests.py, litellm.py | provider 기본 재시도/wall deadline·stream 120초 fallback 제거 |
| providers/calls.py | active/waiting/admission wait 기본 한도 제거 |
| providers/runtime.py | 모드/log 환경변수 강제 선택·자동 로그 회전 삭제 제거. bundled 비용표 선택은 runtime isolation invariant |
| services/runtime/tools.py, processes.py | Tool/프로세스 시간·출력·메모리·동시성 cap 제거 |
| services/runtime/_worker.py | 미설정 core dump 제한을 0으로 강제하지 않고 부모 OS 자원 설정 상속 |
| components/tools/builtin | 파일/출력/프로세스/검색 개수 cap 제거. Shell, 검색 재귀·대소문자 선택은 명시 |
| services/api.py, results.py | 상태/출력/Tool 결과 조회 기본 개수/문자 제한 제거 |
| services/infrastructure/logging.py | 명시적 회전 설정 없이 로그 삭제하지 않음 |
| components/rag | 분할/동시성/검색/추출/cache/library 기본 설정 생성 제거 |
| components/memory | 자동 처리 및 검색/쓰기 정책 기본값 생성 제거 |

## RAG 설정

문서 등록에는 `chunk_size`, `embedding_concurrency`(양의 정수), `extraction.failure_policy`가 필요하다. 추출을 활성화하면 `extraction_batch_size`도 필요하다. 모델은 주입하거나 `embedding_params`/`extraction_params`로 명시한다.

검색에는 method/expand/limit, candidate_count/rrf_constant가 필요하다. 결합 검색에는 max_hops/relation_limit도 필요하다. 지원하는 호출 인자로 명시할 수도 있다. `rerank` 미설정은 재정렬 단계를 실행하지 않는다. 필요하면 reranker와 rerank=true를 명시한다.

`embedding_cache_max_bytes`, `search_cache_chars` 미설정은 캐시 비활성. unchanged chunk 재사용과 Job checkpoint는 별도의 무결성 기반 재사용이다. `index_batch_size` 미설정은 요청한 문서 전체를 library에 전달한다. `ingestion.max_active` 미설정은 거절 한도를 만들지 않는다.

Kuzu buffer_pool_size/max_num_threads와 Chroma 설정은 명시된 옵션만 전달한다. Chroma 거리 메트릭/telemetry의 복제 기본값을 전달하지 않는다. 라이브러리 native 한도 때문에 실패할 수 있으므로 저사양 배포는 예제처럼 필요한 자원 설정을 명시한다.

추출 json_mode/repair_attempts/relation_types가 없으면 SDK format을 변경하거나 수정 재호출·선호 관계 목록을 만들지 않는다. **temperature=0은 강제값이 아니다.** 재현성을 원하면 extraction_params에 명시한다. JSON/evidence/출처 검증 자체는 항상 적용한다.

## 유지하는 강제값과 구현 선택

| 항목 | 이유 / 사용자 override |
|---|---|
| Loop stream=True, n=1 검증; 추출 stream=False | 이벤트 스트림/단일 assistant/JSON 응답 프로토콜. 해당 Engine/client에서 변경 불가 |
| RAG caching=False, no-cache/no-store | LiteLLM partial-cache의 벡터 대응 무결성. 변경 불가; llm VectorCache는 명시 설정 |
| DEFAULT_MAX_RETRIES=0 | LiteLLM 1.103.1 `max_retries or DEFAULT_MAX_RETRIES` 버그 경계. import 전 env, import 후 및 재진입 SDK global에 강제. 명시적 retry kwargs는 변경하지 않음 |
| LITELLM_LOCAL_MODEL_COST_MAP=True | LiteLLM 초기화 시 외부 cost-map network fetch 대신 bundled map을 사용하는 runtime isolation invariant. import 전과 재진입 시 env에 강제하며 사용자 override 불가. 사용자 inference option이나 ProjectConfig default/values가 아님 |
| domain storage_version=1, workflow schema_version=1, graph schema_version=1 | 저장 형식 계약; 자동 migration/옛 default 복원 없음 |
| checkpoint fingerprint·참조·vector·evidence 검증 | 잘못된 결과 재사용/공개 방지. 변경 불가 |
| Graph DAG/명시적 bounded loop/합류 검증 | 도달성·재개 단위·분기 실행 의미. 임의 cycle 불가; 사용자가 반복 횟수 명시 |
| LangGraph retry_policy=None | 효과가 있을 수 있는 노드를 SDK가 자동 재실행하지 못하게 함. 재개는 명시 API |
| Chroma embedding_function=None | 이미 검증된 vector를 저장하며 별도 모델을 library가 호출하지 못하게 함 |
| 취소 정리 | 숨은 5초 제한 없음. Graph는 명시 cleanup_timeout 후 또는 미설정 시 즉시 PendingWork로 남은 정리를 넘김. scope revoke, cancel, SIGKILL, Session 보호 유지 |
| Project Tool worker capture 1 MiB / protocol 8 MiB | subprocess IPC가 backend 메모리를 무한히 점유하지 않게 하는 경계. 초과 시 명시적인 worker 오류로 실패하며 출력 truncation 성공은 없음. Project inference 설정이 아님 |
| observability recent 256 | 비영속 관찰 캐시 sizing. label/code 자르기와 failure-code 128종 제한은 없음. 누적 통계/도메인 원본은 보존 |
| queue 8/32/64, 출력 flush 1/.025s/65536chars | backpressure/저장 batching. 내용을 버리거나 실행 횟수를 제한하지 않음. 명시 drop 정책은 별도 |
| storage index stride128, projection cache4096, conversation cache32, log handle LRU16 | 인덱싱·메모리 eviction만 조절. 원본 데이터/조회 범위는 유지 |
| JSON/파일 atomic replacement, effect receipt, CAS, parent-death/process group | 저장·외부 효과·취소의 정확성. 사용자 설정처럼 저장하지 않음 |
| SDK 로그/배너 콘솔 억제 | 터미널은 ish UI 소유. dotenv 및 하위 logger도 파일로 격리하고 설정 없는 자동 삭제는 하지 않음 |
| 오류 cause/context 관찰 최대 32개·순환 중단 | 비정상 예외 체인이 종료 처리를 막지 않게 하는 내부 관찰 상한. 실행/retry 정책이나 ProjectConfig 값이 아님 |

llm은 기본 Project/Session·마지막 선택·UI 엔진 선택 정책을 소유하지 않는다. `create()`는 새 Project와 명시한 Component만 만든다. 등록된 구현체 목록은 활성화/자동 실행과 다르다. 파일 저장이라는 저장 형식 계약, 초기 queued/pending 상태, 생성 ID/시각, revision, 데이터 레코드의 구조적 초기값은 실행 정책 default와 구분한다. 메모리의 note/project/confirmed 초기 레코드 형식은 CRUD 계약이며 자동 recall·추출 활성화와 무관하다.

기존 최상위 completion/engines/component_configurations/session_defaults/default_engine은 거부한다. 자동 변환·옛 기본값 복원·기존 파일 수정은 하지 않는다. 새 ProjectConfig를 명시적으로 작성해야 한다. 선택한 컴포넌트의 `configure/aconfigure`는 `parameters.components[name]`을 교체하며 Project의 저장 잠금과 버전 검사를 공유한다.

보관 토큰 계산에는 `policies.retention.counter`와 계산기에 필요한 `counter_params`를 명시한다. 예: `{"counter": "model_default", "counter_params": {"model": "openai/my-model"}}`. 여러 엔진이 사용하는 모델 중 하나를 임의로 선택하지 않는다. 보관할 messages는 서비스가 전달하므로 counter_params에서 덮어쓸 수 없다.

미설정 재시도에서 최초 호출 한 번, 지연 없음, 예약 토큰 없음, 보존 개수 추가 보호 없음 등의 중립 동작은 settings에 `1/0`을 생성하는 것이 아니다. 코드의 `get(..., 0/1)`이 이런 수학적 중립값인지 별도 감사한다.

## 이전 데이터와 사용 예

기존 저장값은 수정하지 않는다. 과거에 생략한 값은 계속 missing이다. 필수 설정이 없으면 해당 기능 사용 전에 오류가 발생한다. 기존 명시된 숫자는 보존한다. 변경된 fingerprint와 맞지 않는 재개/캐시는 안전하게 거부 또는 재계산한다.

```python
config = ProjectConfig(parameters={
    "engines": {
        "loop": {
            "config": {"completion": {"model": "openai/my-model"}},
            "policy": {"request_timeout": 300, "tool_timeout": None},
        },
    },
    "components": {
        "rag": {
            "config": {
                "chunk_size": 1000,
                "embedding_params": {"model": "openai/my-embedding"},
                "search": {
                    "method": "hybrid", "expand": "section", "limit": 5,
                    "candidate_count": 20, "rrf_constant": 60,
                    "max_hops": 2, "relation_limit": 30,
                },
            },
            "policy": {
                "embedding_concurrency": 2,
                "extraction": {"failure_policy": "disabled"},
            },
        },
    },
})
```

숫자는 이 예제의 명시적 선택이며 라이브러리 기본값이 아니다. Session에서 request_timeout=null을 설정하면 300초를 해제한다. 사용자가 Engine constructor에 값을 명시하면 host 우선으로 고정된다.

## 감사와 검증

Vision의 빈 설정은 `{}`다. OCR 호출은 명시한 backend 또는 Project의 ocr.backend를 사용하며
둘 다 없으면 오류다. 등록된 Tesseract를 자동 선택하지 않는다. VLM model과 prompt도 명시해야
하며 OCR timeout, provider retry/deadline, 이미지 크기 상한을 생성하지 않는다.
Tesseract language/psm 및 Pillow 인코딩·이미지 안전 검사는 미설정 시 native 동작을 사용한다.
Vision의 stream=False/n=1은 단일 분석 결과 프로토콜, PNG 가공본은 손실 없는 저장 표현,
format_version=1/hash/불변 image ID는 출처 무결성 계약이다. 이는 사용자 설정 leaf로 저장하지
않는다. 자세한 schema/API는 [Vision](components/vision/README.md)에 있다.

`tests/llm/audit_configuration.py`는 production Python 전체의 fallback/default 패턴과 분류를 수집한다. 보고서의 개별 발생 위치를 변경 시 재검토한다. `tests.llm.test_explicit_configuration`은 빈 설정·상속/null·SDK kwarg 생략·독립 timeout·native library 옵션·정책 비활성 계약을 검증한다. Provider/RAG 테스트는 retry de-duplication, cache/checkpoint/vector mapping/cancellation을 계속 검증한다.
