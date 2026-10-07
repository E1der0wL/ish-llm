# Naming conventions and semantic vocabulary

이 문서는 ish-llm의 명명 계약이다. 같은 이름은 같은 의미로 사용한다. 이름을 짧게
만들기 위해 의미나 소유권을 바꾸지 않는다. 실행 계층은 Project → Session → Run → Step이며,
Component는 데이터/capability, Engine은 실행 전략을 소유한다.

## 표기와 길이

PEP 8을 따른다. 클래스는 PascalCase, 함수·변수·속성은 snake_case, 상수는
UPPER_SNAKE_CASE, 내부 구현은 `_` 접두사를 사용한다. 명사는 데이터·상태·identity,
public callable은 가능하면 동사로 시작한다. receiver가 이미 제공하는 문맥은 반복하지 않는다.
이름은 가능한 한 1~2 semantic unit으로 구성하며 정확성을 위해 더 길어질 수 있다.
예를 들어 `describe_project_config`는 Backend에서 Project 편집 schema를 고르는 API다.

`names()`, `snapshot()`, `status()`, `identity()`, `binding()`, `schema()` 같은
명확한 관용 조회/직렬화 계약은 유지한다. 명사형이라는 이유만으로 모든 API를 바꾸지 않는다.
`with_config()`는 원본을 변경하지 않는 사본 생성을 표현하는 관용적 fluent API다.
동기/비동기 facade는 `create/acreate`, `load/aload`, `list/alist` 규칙을 유지한다.

## Config와 설정 API

`config`는 사용자/Project/구현체에 명시적으로 저장되거나 전달되는 기능 설정이다.
임시 실행 프레임, 외부 runtime 호출 옵션, 서비스 의존성 묶음의 대체어가 아니다.
`ProjectConfig`, `Session.config`, `parameters.engines[name].config`,
`parameters.components[name].config`는 유지한다. config/policy envelope 전체를
다루는 설정 API 역시 아래 동작 어휘를 따른다.

| 이름 | 의미 | 실제 API |
| --- | --- | --- |
| `get_config` | 저장된 명시 설정 조회 | `Component.get_config(project)`, `ComponentData.aget_config()` |
| `resolve_config` | 명시 계층·참조를 병합/검증하여 유효값 해석 | `Engine.resolve_config(config, name, session_config=...)`, `ComponentData.aresolve_config()` |
| `validate_config` | 후보 검증, 저장하지 않음 | `Component.validate_config(data)`, `ProjectHandle.avalidate_config(config)` |
| `configure` | 설정 변경 | `ComponentData.aconfigure(data, expected_version=...)` |
| `describe_config` | schema 또는 UI 편집 설명 반환 | `Engine.describe_config()`, `Component.describe_config()`, `ProjectHandle.adescribe_config()` |
| `with_config` | 명시 설정을 적용한 실행 사본 생성 | RAG/Graph/model client, 원본을 변경하지 않음 |

`EngineContext.resolve_config()`와 `ProjectConfig.resolve_engine_config()`는 Project/Session의
명시 데이터 사본을 조합한다. 선택 Engine의 최종 해석·검증은 그 Engine의 `resolve_config`가 한다.
`LargeLanguageModel.describe_project_config()`는 등록된 구현체의 Project 편집 schema,
`describe_host()`는 Host 자원의 읽기 전용 설명이다.

`ParameterLayout` (`core/parameters.py`)은 내부 인자를 공개 config/policy 경로에
분류·배치한다. 값이나 정책을 생성하지 않는다. Engine의 `parameter_key`는
`parameters.engines[key]`를 선택하며 schema의 `x-parameter-key`와 일치한다.
`ProjectConfig.validate_json()`은 임의 JSON-safe 객체 검증이다. config 의미 검증이나
정책 검증을 대신하지 않는다.

## Policy, runtime, Settings

`policy`는 실행의 판단·허용·제한·재시도·예산 규칙이다. Project가 영속 정책의
최상위 권한이며 Application이 값을 선택한다. `RunPolicy`, `ContextPolicy`,
`CompletionPolicy`, `ProjectPolicyResolver`는 해당 판단을 표현/집행하므로 유지한다.

Host 기술적 승인/거절 함수, runner, classifier, operation adapter 묶음은 `ToolRuntime`이다.
`BackendServices.tool_runtime`으로 연결하며 Project `policies.tools/approval/tool_retry`와
구분한다. 승인·retry·receipt·Step lifecycle의 소유권은 변경하지 않는다.

`OutputBuffer`는 델타 저장 batching/flush 설정이다. max_chars는 flush 조건이지
출력 자르기가 아니다. Host 초기 구성을 `BackendServices.output_buffer`로 주입하고,
기존 Project `policies.output` 값은 `for_project()`에서 적용한다. 저장 키는 변경하지 않는다.
`BackendServices` (`services/composition.py`)는 저장소/함수/공유 자원 조립 객체다.
`LogConfig`는 Host가 명시하는 로깅 설정이며 `DomainLogger(config=...)`가 사용한다.

LLM domain에서 `settings`를 config/policy/인자의 모호한 총칭으로 사용하지 않는다.
Hub의 `SettingsService`, Settings 화면, GeneralSettings는 Project config/policy,
사용자 profile/appearance/host 정보 등을 묶는 **UI aggregate**이므로 유지한다.
`HubConfig`는 Application의 startup 선택·workspace·model·profile과 확장 factory를
설정하는 실제 bootstrap configuration이므로 유지한다.

## 조회와 변경 동사

| 어휘 | 의미 |
| --- | --- |
| `get` | 알려진 이름의 등록 객체/현재 단일 값 조회. `EngineRegistry.get(name)` |
| `load` | identifier로 persistent record 읽기 |
| `resolve` | layer/reference/requirement 해석. Component capability 및 Engine request binding |
| `find` | 조건 탐색, 결과가 없을 수 있음 |
| `list` | 실제 객체/record 목록 |
| `names` | 이름 목록만 반환 |
| `create` | 새 객체/record 생성 |
| `save` | 전체 값 저장·교체 |
| `update` | 일부 변경 |
| `configure` | config 변경 |
| `set` | 단순 대입, 일반 config mutation 이름으로 쓰지 않음 |
| `initialize` | lifecycle 초기화, 설정 resolution과 구별 |

`EngineRegistry.resolve_request()`는 for_request factory를 해석하므로 resolve를 유지한다.
`ComponentRegistry.resolve()`는 선택 capability들을 구성하므로 get으로 바꾸지 않는다.

## Identity와 변경 식별자

| 어휘 | 의미 |
| --- | --- |
| `id` | domain object 고유 식별자 |
| `identifier` | API 호출자가 지정하는 record/object ID. 별개의 identity 종류가 아님 |
| `name` | registry/runtime namespace 이름 |
| `key` | 특정 scope의 lookup/reference/checkpoint/operation 연결 키 |
| `title` | 사람이 읽는 표시 제목. identity 아님 |
| `revision` | 객체·definition·contract 변경 identity |
| `version` | schema/storage/protocol 형식 버전 |

`expected_version`, `config_version`, `component_versions`, snapshot의 `version`은 기존
CAS/change token 계약이다. 이번에는 저장/비교 의미를 보존하며 rename하지 않는다.
metadata는 부가 정보이며 외부 metadata만으로 새 authority를 얻지 못한다.
Run/Step metadata의 내부 실행 bookkeeping을 naming 때문에 이동하지 않는다.

## 클래스 suffix

Registry는 등록/조회, Repository는 영속 접근, Manager는 lifecycle 조율,
Handle은 특정 객체에 bound된 facade, Execution/Executor는 실행 경계,
Contract는 trusted invariant/declaration, Policy는 행동 규칙을 뜻한다.
`ToolContract`, `ToolClassification`, `ToolExecutor`, `AgentComponent`, `AgentExecution`,
`AgentNode`, `ComponentRegistry`, `EngineRegistry`를 유지한다.
`required_components`는 정확한 persistent Component identity 의존성,
`required_capabilities`는 consumer가 요구하는 실행 기능, `capabilities`는 제공 기능이다.

## 외부 API와 저장 계약 예외

외부 SDK의 `config`, `retry_policy` 등 upstream keyword는 바꾸지 않는다.
Graph의 자체 LangGraph 호출용 dict는 `invoke_options`다.

기존 승인/checkpoint 지문의 `tool_policy`, Loop/Memory 지문 입력의 `settings`,
snapshot의 `configuration`, `configuration_key`, `policy_schema`는 저장/관찰 payload의
확정된 필드다. Python runtime API 이름과 별개이며 변경하지 않는다. 이 키를 runtime
속성의 alias로 제공하지 않는다. 그 외 기존 ProjectConfig JSON, 정책/Component 키,
도메인 필드, Workflow schema와 버전은 그대로다. 자동 migration/호환 별칭은 없다.

과거 reports와 handoff의 날짜별 기록은 당시 API를 설명하는 역사 기록이므로 수정하지 않는다.
새 코드는 이 문서와 현재 public API를 따른다.

## 이번 감사의 변경 목록

| 이전 | 현재 | 이유 |
| --- | --- | --- |
| ToolPolicy / tool_policy 인자 | ToolRuntime / tool_runtime | Host 실행 어댑터 |
| ToolExecutionScope의 policy / settings 인자 | runtime / policy | Host 어댑터와 Project 규칙 구별 |
| ServiceConfig | BackendServices | dependency/runtime wiring |
| OutputPolicy / output_policy 인자 | OutputBuffer / output_buffer | 출력 저장 batching |
| LogSettings | LogConfig | 명시 로깅 설정 |
| SettingsLayout / settings_layout | ParameterLayout / parameter_layout | config/policy 경로 분류 |
| settings_name | parameter_key | Engine 인자 경로 선택 |
| EngineContext.settings | resolve_config | Project/Session 조합 |
| ProjectConfig.for_engine | resolve_engine_config | Engine 경로 포함 명시값 해석 |
| ProjectConfig.validate_settings | validate_json | 임의 JSON-safe 검사 |
| CompletionPolicy.validate_settings/from_settings | validate_config/from_config | 정책 설정 검증/구성 |
| configuration_schema | describe_config | 설정 schema 설명 |
| configuration (Component) | get_config | 저장 설정 조회 |
| configuration (Engine) / effective_configuration | resolve_config | 유효값 계산 |
| configuration (Project/Session facade) | describe_config | UI view |
| configuration (서비스 조립 객체) | describe | Host 구현 관찰 |
| validate_configuration | validate_config | 후보 검증 |
| configured | with_config | 설정된 사본 생성 |
| EngineRegistry.resolve | get | 단순 dictionary lookup |
| Graph runtime self.config | self.invoke_options | LangGraph 호출 인자 |
| backend.project_schema / host_configuration | describe_project_config / describe_host | 설명용 API |
| policy_schema callable | describe_policies | 정책 schema 설명 |

새 abstraction은 추가하지 않았다. class/module 이동은 이름 정합성에 필요한
`core/settings.py → core/parameters.py`, `services/configuration.py → services/composition.py`뿐이다.

Python import·호출부와 사용자 정의 Engine/Component/client의 메서드 구현은 위 이름으로
직접 수정해야 한다. 저장된 Project/Session/Workflow/승인 기록은 수정하지 않는다.
UI schema annotation `x-settings-key`는 `x-parameter-key`로 바뀌며, Host 관찰 응답도
`tool_runtime`/`output_buffer`를 사용한다. 이는 저장 config의 필드 변경이 아니다.
Hub의 조회·편집 연결도 같은 API를 사용한다.
