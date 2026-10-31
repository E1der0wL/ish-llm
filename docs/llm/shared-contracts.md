# 공통 경계와 지원 범위

공통 계층은 선택된 구현체의 공개 계약을 사용한다. 구현체 이름이나 SDK 요청 필드로
실행 의미를 추측하지 않는다. 아래 계약은 설정 기본값·재시도 권한·승인 권한을 만들지 않는다.

## 부분 출력과 체크포인트

`EngineContext.resume_outputs`는 명시적 `partial_output=True` 재개에서만 제공되는
`ResumeOutput(run_id, output, step)` 사본이다. 서비스는 미확정 출력과 미완료 소유 Step을
조회한다. Step이 없는 Run 출력도 포함할 수 있다. 원본 output.metadata는 변경하지 않는다.

Engine은 자기 Step 메타데이터를 해석해 재사용할 참고 자료를 선택한다. Loop는 자신의
checkpoint 이름·실행 scope·iteration이 맞는 llm Step만 선택한다. 완료된 Tool 결과,
승인·효과 영수증은 부분 출력으로 복원하지 않는다. Graph는 자식 Engine에 사본을 전달하고
자식 Engine이 자신의 scope를 검사한다. 원본 Run·출력·체크포인트는 수정하지 않는다.

`run.checkpoint()` / `await run.acheckpoint()`는 저장된 체크포인트가 정확히 하나일 때
그 스냅샷을 반환한다. 초기 재개 사본 생성 전에 실패한 Run은 저장된 resume 참조의
명시적 이름을 사용하며 기존 lineage/digest 검증을 거친다. 이름을 해석할 근거가 없거나
여러 개이면 이름을 명시해야 한다. `graph` 또는 `loop`를 기본 선택하지 않으며 등록된
Engine을 실행하거나 새로운 체크포인트를 생성하지 않는다. 내부 저장소에 오래된 Run
스냅샷을 직접 전달하는 호출자는 체크포인트 이름도 명시한다.

중첩 기록의 외피는 `core.checkpoints.build_envelope` / `unwrap_record`가 소유한다.
기존 `node_type=engine_record`, `engine_scope`, `checkpoint_name`, `local_key`,
`status`, `container`, `requires_retry` 형식을 유지한다. payload는 선택된 자식 Engine의
소유다. 이 외피는 Graph 전용 설정이 아니며 다른 합성 Engine도 같은 계약을 사용할 수 있다.
공통 지시 처리는 `kind=instruction` 기록만 해석한다. 임의의 자식 JSON을 재귀 탐색하거나
필드 이름이 비슷하다는 이유로 지시·승인을 추정하지 않는다.

## 모델 사용량 관찰

Provider는 `providers.observations.ModelCall`로 다음 정보를 명시한다.

- `operation`, `model`: 관찰용 identity.
- `count_tokens(counter)`: 호출 입력과 출력 상한을 포함한 보수적 예약량.
- `read_usage(response)`: `total_tokens` 등 정규화된 사용량 사전. 알 수 없으면 빈 사전.
- `classify_error(error)`: 호출 경계가 소유하는 오류 코드.

콜백은 동기·무효과 해석 함수다. 사용량 서비스는 SDK 요청을 불투명하게 전달하고,
Provider 콜백의 비음수 정수 예약량과 정규화된 사용량만 검증한다. 원래의 사용량 원장,
Run/Project 한도 집행과 저장 잠금은 서비스에 남는다. 토큰 제한이 없으면 예약량 계산을
호출하지 않지만 실행 기록은 계속 저장한다. 사용량을 모르면 예약량을 임의로 해제하지 않는다.

LiteLLM 요청의 `aembedding`/`arerank`/completion 구분과 `max_tokens` 등 해석은
`providers.litellm.describe_call`에 있다. 새 Provider는 자기 ModelCall을 제공한다.
공통 서비스에 새 SDK 이름이나 필드를 추가할 필요가 없다. SDK kwargs는 변경하지 않는다.

서비스의 기본 계산기 등록 이름 `model_default`는 유지한다. LiteLLM 토큰 계산 구현은
`providers.litellm.model_token_count`가 소유하며 서비스는 계산기를 등록·선택할 뿐이다.

사용량 상한이 설정된 Run에서 capability resolver는 동기 `model_usage(project)`로
Project의 Component 호출 영수증 목록을 제공해야 한다. 비활성 Component의 기존 사용량도
포함한다. Component 호출 자체가 없는 구현은 빈 목록을 명시적으로 반환한다. 이 계약이
없거나 결과가 영수증 dict의 목록이 아니면 `usage_configuration`으로 실행 전에 거부한다.
`ComponentToolResolver`는 기존 사용량 조회를 재사용하고 custom resolver도 같은 계약을
제공한다. 서비스는 resolver의 구체 클래스나 내부 registry 필드를 검사해 집계를 생략하지
않는다. 상한이 없으면 이 조회를 호출하지 않지만 새로운 실행의 사용량 기록은 계속한다.
선택적 동기 `validate(project)`는 resolver 자신의 capability 접수 검증이다.

## 스트리밍 응답 경계

`providers.streaming.CompletionDecoder`는 시도마다 생성된다. `decode(chunk)`는 SDK
chunk를 `CompletionUpdate`의 텍스트·추론 텍스트·identity·사용량·종료 정보로 변환한다.
사용량은 비음수 정수 값의 중첩 dict다. decoder는 검증 중 실패 전까지 받은 관찰을 yield할
수 있다. `finish()`는 정상 종료를 검증하고 공통 `ModelResponse`를 반환한다.
BaseEngine은 정규 usage/identity/종료 관찰을 기록하며 성공한 경우에만 호출자의
`list[ModelResponse]` capture에 적용한다. 완전히 수신한 실제 사용량은 응답 검증 실패에도 보존한다.
factory에는 `max_tool_calls`, `max_argument_chars`, `max_output_chars`가 전달된다.
None은 추가 제한 없음이다. 명시된 상한은 decoder가 자신의 프로토콜 의미로 집행해야
하며 무시하거나 다른 기본값으로 대체하면 안 된다.

LiteLLM의 choices/delta/tool_calls 해석, Tool 조각 결합, 종료 조건은 Provider에 있다.
BaseEngine은 CompletionResult와 이벤트 수명, 명시적 재시도·기한을 관리한다. 한 chunk라도
수신한 뒤에는 파싱 실패·부분 응답을 자동 재호출하지 않는다. 취소는 기존 bridge와 generator
정리 경로를 따른다. decoder는 Tool을 실행하거나 승인·영수증·저장 상태를 만들지 않는다.
이 계약은 응답 해석 경계이며, Loop의 메시지/Tool 대화 프로토콜 자체를 바꾸지는 않는다.
LiteLLM의 `stream=True/n=1` 준비는 Provider의 `prepare_stream`이 수행한다. 저수준
동기→비동기 bridge는 전달받은 요청을 변경하지 않고 transport와 취소만 연결한다.

## 설정 제한과 스키마 표현

`x-narrowing=maximum`은 부모의 명시적 수치 제한을 자식이 늘리거나 null로 해제하지
못하게 한다. 이 확장 키는 **직접 properties 경로의 inline numeric schema**에 선언한다.
`$defs`/`$ref`로 간접 선언하거나 `allOf`/`anyOf`/`oneOf`, 배열 items, 조건부 schema 안에
숨긴 narrowing 선언은 등록과 resolution에서 거부한다. 지원하지 않는 제한을 무시하지 않는다.

이 제한은 cross-scope narrowing 확장 계약에만 적용된다. 일반 JSON Schema의
참조·조합·enum·자료 예시는 기존처럼 사용할 수 있다. 값은 계속 Project → Session → Agent →
명시적 host 순서로 해석하며 missing/null을 구분한다. 새로운 설정값은 생성하지 않는다.

`core.schema.schema_nodes()`는 JSON Schema의 실제 schema 위치만 순회한다. 스키마
검사, ParameterLayout의 참조 이동, UI의 `x-resource` 투영은 이 순회를 공유한다.
`properties`의 이름이 `default`나 `$ref`여도 그 이름을 키워드로 해석하지 않는다.
`const`/`enum`/`examples`/확장 annotation의 내부는 데이터이므로 수정·검사하지 않는다.
반면 해당 이름의 property에 선언된 schema는 정상 검사한다. UI는 등록된 자원과 원래
enum의 교집합만 제공하며 owner의 제약을 넓히지 않는다. 가능한 자원이 없으면 false
schema 조건을 추가한다. 사용자 값이나 숨은 기본값을 생성하는 동작은 없다.

## 텍스트 문맥 처리와 JSON 데이터

CompletionPipeline은 텍스트 문맥 처리 계약이다. 현재 사용자 입력은 문자열이며 원문을
정확한 접두사로 보존한다. 압축할 수 있는 내용과 Tool 호출/결과 쌍은 기존 보호 규칙을 따른다.
활성 Tool 교환을 제거하면 그 요약을 현재 입력에 참고 자료로 덧붙이고 제거한 호출 ID를
명시한다. 멀티모달 현재 입력이나 다른 요약 배치는 자동 변환하지 않고 명확히 거부한다.
압축 시점·모델·목표 크기는 계속 Application 또는 선택된 Component의 명시적 설정이다.

`JsonValue`는 JSON 기본형, dict/list, dataclass, Enum, Path와 Union을 처리한다.
`Optional[T]`와 `T | None`은 같은 의미다. 고정 tuple은 각 위치의 타입과 길이,
`tuple[T, ...]`는 각 항목 타입을 검사한다. 지원하지 않는 Python 타입은 추측하거나
문자열로 바꾸지 않고 거부한다. 이것은 모든 Python 객체를 위한 범용 serializer가 아니다.

`Diagnostic`은 소유자가 제공한 계산형/저장형 진단을 동일하게 보존한다.
예외의 선택적 `diagnostic`/`code` 관찰 속성이 일반 예외를 발생시키면 해당 정보가 없는
것으로 처리하고 원래 오류·cause로 진단을 만든다. 관찰 실패가 Run 실패 처리·다음 queued
요청·shutdown을 중단시키지 않는다. cancellation은 삼키지 않으며 `CodedError`만 실행
분류 권한을 가진다는 규칙도 유지한다.
`ErrorClassification`은 문서화된 backend 오류 코드에 대한 관찰용 분류다. 새로운 의미는
`unknown`으로 남아도 원래 code/details는 소실되지 않는다. exception/Component 이름별
분기나 분류 기반 자동 재시도는 추가하지 않는다.

## 확장 경계 보강 — 9개 항목

| 항목 | 소유자와 현재 계약 |
| --- | --- |
| 1. JSON Schema 참조 | `validate_local_refs`는 `schema_nodes`로 실제 schema 위치만 검사한다. `$ref`라는 사용자 property 이름이나 const/default/examples의 JSON은 참조로 해석하지 않는다. |
| 2. Graph Tool 승인 | 처리기가 `node.pause(request)`로 효과 이전의 승인을 명시한다. 처리기 등록 이름이 `tool`인지 검사하지 않는다. ToolNode는 ToolExecutor에서 전달된 효과 이전 승인만 연결한다. |
| 3. 중첩 체크포인트 | 부모 invocation key는 불투명한 문자열이다. 자식 payload는 자식 소유이며 공통 외피만 부모가 읽는다. |
| 4. Agent 지침 | `Engine.with_instructions(context, before=..., after=...)`가 자원 지침과 자체 지침의 합성을 소유한다. AgentExecution은 자식의 config 경로를 읽지 않는다. |
| 5. 스트리밍 Provider | `CompletionAdapter`가 요청 준비·재시도 판단/수정 검증·SDK 연결·decoder·ModelCall을 제공한다. BaseEngine은 SDK 필드를 해석하지 않는다. |
| 6. 선택 Agent | 실제 선택한 Agent의 정의/자원/Engine binding을 승인 대기 전 저장하고 실행·재개 시 비교한다. 전체 Agent 목록을 재개 지문에 넣지 않는다. |
| 7. Engine 선택 키 | 명시한 `parameter_key`를 사용하며 등록된 공유 Engine에 재대입하지 않는다. 순차·중첩 작업은 Graph Workflow로 구성한다. |
| 8. 중첩 사용자 입력 | 외부 binding의 `selection=True`는 `{option_id, value}`를 보존한다. 입력 값으로 선택지를 역추측하지 않고 자식의 원래 decision 형식으로 전달한다. |
| 9. Tool 결과 조회 | `CompletionMessage.result_ref`에 정확한 Run/Step 참조를 전달한다. Memory 미리보기와 `memory_tool_result`는 `run_id + step_id`를 사용한다. |

### 중첩 실행과 승인

`EngineCheckpointScope`는 부모/자식 checkpoint 이름을 제한하지 않는다. 부모 owner 문자열을
JSON 배열로 파싱하지 않으며 새 key는 `[owner, "engine", child_name, local_key]`이다.
자식 이벤트가 공통 status를 쓰지 않으면 `lifecycle={status, container}`를 명시한다.
외피 status는 started/completed/waiting만 허용한다. waiting은 원본 InteractionRequest가
필수다. 부모는 private status 이름이나 node_type으로 실행 의미를 추측하지 않는다.
외피와 원본 요청의 지문·키·선택지를 검증하고 자식 decision을 복원한다.

`node.pause`는 approve/deny와 bool 값을 가진, 추가 입력 없는 효과 이전 approval만 지원한다.
입력이 필요한 자식 Engine은 자신의 InteractionRequest와 checkpoint bridge를 사용한다.
취소된 처리기의 pause/emit/중첩 호출은 모두 차단한다. 효과 이후 Tool에서 던진 ASK는
ToolExecutor가 uncertain failure로 거부하며 pause로 바뀌지 않는다.

선택형 handler는 `selection_binding=True`와 순수 동기 `binding(definition, context)`를
선언한다. Tool handler는 `bind_arguments(arguments, context)`로 선택된 구현의 binding을
반환할 수 있다. 이 Tool은 durable checkpoint key가 필요하다. 현재 `retry_fields`와의
조합은 거부한다. 선택 identity를 바꾸려면 새 invocation과 새 승인을 사용해야 한다.
동일 인자의 명시적 안전 재시도 계약은 그대로다.

선택 기록은 대기/완료된 Agent와 실제 선택된 하위 Agent까지 검사한다. 자식 Engine의
`validate_selections(context)`가 로컬 기록을 검증하고 하위 구현에 위임한다. BaseEngine은
Tool 선택 검증을 제공하며 Graph는 handler 선택 검증도 수행한다. 독립 custom Engine이
선택형 자식을 재사용한다면 이 계약을 구현하고 실행/validate_resume에서 호출해야 한다.
미사용 Agent 수정·삭제는 binding 비교 대상이 아니다. 저장 정의의 구조 검증과 Workflow에
명시된 참조/순환/역량의 기존 사전 검증은 유지한다.

`agent_run`은 미사용 Agent의 Engine factory나 자원을 실행 준비하지 않는다.
선택한 Agent에 필요한 capability만 `EngineContext.with_capabilities`로 요청한다.
서비스의 resolver가 Run-local 사본을 재사용하며 신규 Component를 설치/활성화하지 않는다.
자식 Tool 목록과 scope는 기존 부모 제약으로 계속 좁힌다. capability 조회는 승인이나
Tool 권한을 생성하지 않는다. 없는 resolver/역량은 추측하지 않고 거부한다.

### 모델 어댑터와 결과 참조

`BaseEngine(completion_adapter=...)`는 기본 LiteLLM 어댑터를 교체하는 Host 경계다.
`completion_fn`/`completion_decoder`는 실제 `LiteLLMCompletionAdapter`의 생성자에서만 받는다.
공통 호출은 `ModelRequest`/`ModelResponse`이며 Engine에 구 SDK 인자를 직접 전달하는 경로는 없다. 어댑터는 provider admission·진단·취소에 참여하고 명시된 제한을
집행해야 한다. 자체 SDK 오류를 Run code로 전달하려면 trusted CodedError로 정규화한다.
Loop에서 사용하는 어댑터는 `describe_config`/`validate_request`/`validate_processing`도 제공하고 Loop의
공통 메시지/Tool 교환과 입력 소유권·보조 호출 옵션 계약을 지원해야 한다. 새로운 SDK 어댑터 구현 자체는
이번 변경에 포함하지 않는다. 어댑터 타입과 선택적 `execution_binding`도 재개에 묶인다.
동일 어댑터의 동작을 교체하면 Host가 binding/revision을 변경해야 한다.

`result_ref`는 SDK kwargs에 들어가지 않는 provenance다. 처리기가 Tool 본문을 요약해도
참조를 바꾸지 못하며 완료된 Tool을 재개에서 재사용할 때 원본 Run/Step을 유지한다.
동일 Run의 다른 Agent가 같은 tool_call_id를 써도 조회가 섞이지 않는다. 조회 서비스는
Session 소유권, 완료된 tool Step, 실제 output을 확인한다. source identity만으로 권한을
추가하지 않는다. Tool call ID는 모델 대화의 호출/결과 연결에만 계속 사용한다.

승인·효과 원장·명시적 재개·CAS의 소유권은 유지한다. 공통 응답을 담는 Loop의 현재
Checkpoint 형식은 `LoopEngine.CHECKPOINT_FORMAT` (`loop-iterations-v2`)이다.
`CompletionResult` Reader는 nullable `termination`도 필수 필드로 요구한다.
구형·불완전 증거는 거부하며 원본을 자동 변환하거나 누락 필드를 복원하지 않는다.
단 중첩 checkpoint key/decision과 선택 binding 형식이 바뀌었으므로 과거 실행 checkpoint의
재개 호환을 보장하지 않는다. 자동 변환·호환 별칭은 없으며 변경 전 대기 실행은 새 요청으로
시작해야 한다. 완료된 Tool을 새 요청에서 임의 재실행하지 않도록 기존 작업 결과를 확인한다.

## 실행·관찰 경계의 일관성

- `Engine.bind_execution(context)`는 선택 구현이 소유한 JSON 실행 identity를 반환하는 선택적
  동기 계약이다. AgentExecution은 Engine 종류에 관계없이 이를 저장·비교한다. Loop는 선택
  adapter의 타입과 `execution_binding`을 포함한다. 코드/SDK 동작이 바뀌면 Host가 이 identity나
  기존 revision을 변경해야 한다. 호출 가능한 Python 객체의 코드 변경을 자동 추측하지 않는다.
- Graph 구조, handler schema, Workflow 참조와 순환은 사전 검사한다. `selection_binding=True`
  handler의 private 설정·자원 검사는 선택된 노드의 승인/실행 전에 수행한다. 재개는 실제 선택한
  하위 환경도 검증한다. `discover_graph`는 구조 참조만, `graph_engine`은 실제 선택한 환경을
  반환한다. 미선택 Agent의 잘못된 private 옵션이 다른 분기의 실행을 막지 않는다.
- 추가 지시 예약의 정적 경로는 등록 Engine의 공개 지원 모드를 기준으로 열거한다. 실제 선택한
  factory 결과도 다시 검사하며, 미지원 실행기에 지시를 강제 전달하지 않는다.
- `EngineEvent(STEP_COMPLETED, checkpoint=<CHECKPOINT record event>)`는 출력·Step 완료와
  재개 receipt를 같은 StorageIO 트랜잭션에서 확정한다. `STEP_STARTED`와 `STEERING`도
  미리 준비한 checkpoint를 첨부할 수 있으며 initialize를 허용한다. 초기 checkpoint와 채널 OPEN은
  같은 실행 준비 완료를 나타내므로 한 ACK를 공유할 수 있다. 완료 이벤트에는 계속 record만 허용한다.
  중첩 scope는 첨부 checkpoint도 같은 공개 bridge로 변환하고 ACK 후에만 캐시한다.
  Loop Tool과 Graph 노드가 사용한다. 저장 실패 시 시작 기록은 남고 완료 결과로 재사용하지 않는다.
  효과가 불확실하므로 명시적 재시도 승인 없이 실행을 반복하지 않는다.
  서비스 구독자는 commit 이후 CHECKPOINT와 STEP_COMPLETED 알림을 계속 받는다. CHECKPOINT를
  관찰한 시점에는 해당 Step도 이미 완료 저장되어 있다. 저장 실패 시 두 성공 알림 모두 발행하지 않는다.
- `InteractionRequest.find_approval()`은 approve 의미의 선택지가 정확히 하나이고 추가 필수 입력이
  없어도 유효할 때만 그 선택지를 반환한다. Project 정책은 그 경우에만 자동 receipt를 만들 수 있다.
  confirmation의 명시적 resume도 같은 규칙을 사용하며 option ID를 `approve`로 가정하지 않는다.
  모호한 선택이나 필수 입력은 사용자에게 남기며, 명시적 응답 없이 resume하면 거부한다.
- Component의 `model_usage`, `history_references`, `references`는 소유자가 저장 위치와 빈 상태를
  해석한다. 조회 서비스는 로컬 directory 존재를 추정 근거로 삼지 않는다. 비활성 Component도
  보존된 사용량/참조를 반환해야 하며, 외부 저장 오류는 자료가 없다고 처리하지 않고 전파한다.
- CompletionPipeline은 공통 메시지·Tool·stream 계약을 보호하고, 호출자가 주입한
  `validate_parameters(candidate, original=...)`가 adapter-owned 옵션을 검증한다. Loop는 반드시
  선택 adapter의 `validate_processing`을 연결한다. LiteLLM의 retry 필드와 client 보호는
  LiteLLM adapter에만 존재한다. 새 SDK는 자기 필드를 검증하고 공통 pipeline을 수정하지 않는다.
  live client를 포함한 Host/processor는 trusted 코드이며 내부 객체를 직접 변조해서는 안 된다.
- 공통 Step 실패는 `failure_code`와 Diagnostic details를 함께 보존한다.
  진단의 code만으로 실행 오류 권한을 승격하지 않는다.
- EventContext의 사용자 metadata 갱신은 서비스 소유 `execution_started`, `restart`, `diagnostic`도
  거부한다. 실행 전 중단이라는 근거와 재시작 연결은 RunManager가 기록하며 이벤트 handler가 만들지 않는다.

이 변경은 새 제품 정책·기본값·저장 버전·자동 migration을 추가하지 않는다. 저장된 approval과
선택 binding이 달라지면 새 요청이 필요하며, 옛 checkpoint에 대한 호환 변환은 제공하지 않는다.
