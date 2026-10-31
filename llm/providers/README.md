# Providers — SDK 연결과 공통 보호 경계

공통 typed 모델과 실제 SDK Adapter 사이의 계약이다. [리팩터링 계약](../../docs/llm/clean-refactor.md)과 [공통 모델 프로토콜](../../docs/llm/model-protocol.md)을 따르며 실제 Adapter·외부 프로토콜 불변식을 유지한다.

현재 SDK 지원 범위는 [pyproject.toml](../../pyproject.toml), 검증 조합과 재현 명령은
[P0 SDK 전환 기록](../../docs/llm/p0-sdk-transition.md)을 따른다.

Engine과 RAG 모델 클라이언트가 LiteLLM을 사용할 때 공유하는 경계입니다. SDK 초기화, 호출 admission, 명시적 timeout/retry, 오류 분류, 로그·진단을 담당합니다. Project/Run/Step 파일은 저장하지 않습니다.

선택적 MCP 어댑터도 이 폴더에서 SDK 연결을 담당합니다. MCP Tool 호출은 모델 호출의
retry 경로에 넣지 않으며, 승인·효과 영수증·재시도는 기존 ToolExecutor가 집행합니다.

`LiteLLMCompletionAdapter.initialize()` / `await adapter.ainitialize()`는 선택적으로
SDK를 미리 불러온다. 애플리케이션이 호출 시점을 결정하며 Backend 생성만으로 실행하지 않는다.
비동기 API는 import를 이벤트 루프 밖에서 수행하고 기존 runtime lock/cache를 공유한다.
모델 요청·인증 확인·연결 probe를 보내거나 timeout/retry/inference 설정을 만들지 않는다.
주입한 `completion_fn`은 자체 초기화를 소유하므로 이 API에서 LiteLLM을 불러오지 않는다.
기본 LiteLLM import 실패는 그대로 전달하며 다음 명시적 호출에서 다시 준비할 수 있다.
선택적인 사전 import 실패는 준비 단계만 건너뛰며, 실제 요청이 그 경로를 사용하면 SDK의
정상 오류 경로로 전달된다. 취소·프로세스 종료 예외는 이 선택적 처리에서 잡지 않는다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | provider 패키지의 역할을 설명합니다. 함수·클래스는 각 모듈에서 import합니다. |
| [runtime.py](runtime.py) | LiteLLM 지연 초기화, SDK 재시도·런타임 격리 불변식, 로그·진단 라우팅입니다. |
| [runtime_values.py](runtime_values.py) | SDK 초기화 불변식, Provider 로그 경로/출처/안전한 로그·예외 frame의 정규 Writer입니다. |
| [parameters.py](parameters.py) | 설정 컨테이너를 복사·병합하며 주입된 runtime client의 identity를 유지합니다. |
| [schema.py](schema.py) | provider 소유 Completion/Embedding/Rerank 설정 계약과 검증입니다. SDK 확장 인자는 변형하지 않고 전달합니다. |
| [request_values.py](request_values.py) | Model/Embedding 설정의 정규 조회, SDK 작업 이름, 명시 retry/client 계약과 전송 사본 Writer입니다. |
| [error_values.py](error_values.py) | SDK 예외의 상태·header 관찰을 읽습니다. 실행 허가나 retry 결정을 만들지 않습니다. |
| [response_values.py](response_values.py) | SDK completion envelope와 message 내용의 조회 Projection입니다. 필수 응답 검증은 호출 경계에서 유지합니다. |
| [wire_codec.py](wire_codec.py) | SDKWire·SDKFinishReason·SDKToolCodec·SDKContentPartCodec·SDKMessageCodec·SDKRequestCodec의 단일 Reader/Writer입니다. |
| [operations.py](operations.py) | OperationAdapter의 공통 입출력·transport 선택·실제 SDK 호출을 연결합니다. 요청 Writer·응답 Projection은 각 Provider Owner에 위임합니다. |

| [codes.py](codes.py) | SDK/runtime에 의존하지 않는 정규 Provider 오류 코드 선언입니다. |
| [options.py](options.py) | Provider의 명시적 재시도·기한 설정 Schema/Reader입니다. 생략을 보존하고 실제 시도 횟수 계산은 RetryPolicy에 위임합니다. |
| [requests.py](requests.py) | request_model 호출 경계와 invoke의 비스트리밍 시도, 명시적 wall timeout/outer retry, ProviderError 분류입니다. |
| [retry.py](retry.py) | 일시적 오류와 명시적 SDK retry를 판단하고 중첩 retry를 방지합니다. |
| [calls.py](calls.py) | ProviderCalls/ProviderLimits가 호스트의 명시적 동시 호출·대기 제한을 적용합니다. |
| [litellm.py](litellm.py) | SDK 함수 선택, bounded async 스트림/취소 연결, LiteLLM chunk 해석과 토큰 계산입니다. |
| [stream_records.py](stream_records.py) | SDK stream metadata/choice/delta Reader와 Tool 조각·완성 메시지 Writer입니다. 미완 조각을 실행 기록으로 취급하지 않습니다. |
| [stream_values.py](stream_values.py) | bounded bridge의 메시지/단계·worker 수명 선언과 SDK 요청·토큰 계수 관측을 소유합니다. |
| [streaming.py](streaming.py) | CompletionAdapter의 요청/재시도/SDK 경계, CompletionDecoder/CompletionUpdate와 응답 검증 오류 계약입니다. |
| [observations.py](observations.py) | 어댑터가 제공한 ModelCall의 사용량 해석을 기존 실행 문맥과 연결합니다. |
| [embeddings.py](embeddings.py) | 단일 입력 응답의 벡터 개수·차원·유한성·zero-vector 무결성 검사입니다. |
| [mcp.py](mcp.py) | 선택적 MCP SDK의 stdio/Streamable HTTP 연결·발견·호출·취소입니다. Tool 실행 정책은 소유하지 않습니다. |

설정은 [명시적 설정 계약](../CONFIGURATION.md)을 따른다. `runtime.py`는 LiteLLM 초기화와 진단 라우팅을 담당한다. 모드·인증 환경변수는 호스트 또는 SDK가 선택한다.

공통 사용량 서비스는 SDK 요청 필드나 응답 객체를 해석하지 않는다. Provider가
`ModelCall`의 토큰 예약·사용량 추출·오류 분류 콜백을 제공하고 서비스가 기존 원장과
명시적 한도를 집행한다. 새 어댑터의 계약은 [공통 경계](../../docs/llm/shared-contracts.md)를 참고한다.

스트리밍 chunk의 `choices/delta/tool_calls`, 조각 결합과 종료 검증은
`LiteLLMCompletionDecoder`가 소유한다. BaseEngine에는 정규화된 `CompletionUpdate`만
전달한다. 정상 종료 후 `finish()`가 반환한 최종 message도 어댑터 소유 데이터이며,
공통 계층은 내부 필드를 재해석하지 않는다. `model_token_count`도 이 어댑터에 있으며
서비스의 기존 계산기 등록 이름 `model_default`는 유지한다. 계산기 선택은 명시적 정책이다.

`LiteLLMCompletionAdapter`는 요청 준비, SDK retry 중첩 방지, 허용된 retry 인자 검증과
decoder/transport 선택을 함께 소유한다. BaseEngine은 `CompletionAdapter`를 주입받을 수
있으며 SDK 필드명으로 분기하지 않는다. 다른 어댑터도 ProviderCalls admission, 사용량,
취소, 진단, 명시적 상한 집행을 제공해야 한다. 부분 chunk 수신 이후 재시도 금지는 공통
실행 경계에서 유지한다. Loop는 canonical 메시지/Tool 대화 계약을 계속 사용한다.

정규 Provider 실패 코드는 `codes.py`의 `ProviderErrorCode`가 소유한다. 분류·발생 경계와
Engine/RAG/Hub·순수 진단 투영은 동일한 문자열 값을 참조한다. 알 수 없는 명시적
CodedError는 계속 보존하며 이 enum으로 허용 코드를 제한하지 않는다. `ProviderError`의
기존 공개 위치·생성자는 유지한다. RAG의 허용 실패 집합과 transport의 TRANSIENT는
각 owner의 별도 판단이며 코드 선언 공유가 재시도 권한·예산을 만들지 않는다.

## 초기화와 retry 계약

초기화의 code invariant:

- `LITELLM_LOCAL_MODEL_COST_MAP=True`: LiteLLM import 전에 환경변수에 강제해 외부 cost-map network fetch 대신 bundled map을 사용한다. 매 `litellm_sdk()` 진입에서도 복구하는 runtime isolation invariant이며, 사용자 inference option이나 ProjectConfig default/values가 아니다.
- `DEFAULT_MAX_RETRIES=0`: 아래의 확인된 SDK 자동 retry를 막는 Adapter 불변식이다. 명시적 요청 retry는 보존한다.

SDK를 외부 코드가 먼저 import했다면 이미 발생한 초기화 fetch를 되돌릴 수는 없다. 플러그인의 초기화 경계는 `litellm_sdk()`다.

첫 요청의 import 지연을 줄이려면 LiteLLM을 선택한 애플리케이션이 준비 시점을 명시적으로
선택할 수 있다. 기존 초기화 경계를 재사용하며, Backend가 시작할 때 모든 provider를
자동으로 준비하거나 시험 추론 요청을 보내지는 않는다.

```python
import asyncio
from llm.providers.litellm import LiteLLMCompletionAdapter

# LiteLLM 사용을 선택한 host의 명시적 준비 단계. 추론 요청/인증 확인이 아니다.
adapter = LiteLLMCompletionAdapter()
await asyncio.to_thread(adapter.initialize)
```

이 호출은 이벤트 루프 밖에서 SDK import와 위 초기화 불변식을 적용한다. Native completion
adapter는 `prepare_completion()`을 통해 측정에서 확인한 HTTP/OpenAI, MCP routing, 파일 참조
처리 모듈의 지연 import도 준비한다. 네트워크 접속·인증·시험 추론·tokenizer 다운로드는 하지 않는다.
주입 transport는 자체 초기화를 소유하며 사용하지 않는 LiteLLM을 import하지 않는다.
이미 초기화된 SDK를 재사용하며 필수 SDK import 실패는 기존 `initialize` / `sdk_import` 진단으로
전달된다. 선택적 SDK 경로의 ImportError는 준비 단계에서 필수 의존성으로 바꾸지 않으며, 실제
호출에 필요하면 SDK가 그 호출에서 검사한다. 다른 adapter는 해당 구현의 초기화 경계를 사용한다.
SDK 버전별 모든 지연 import나 첫 서버 연결 비용까지 제거했다는 보장은 없다.

LiteLLM import 전에 `os.environ["DEFAULT_MAX_RETRIES"] = "0"`을 강제로 적용한다.
`litellm_sdk()`는 import 후와 매 진입 시 `sdk.DEFAULT_MAX_RETRIES = 0`도 적용한다.
검증한 LiteLLM OpenAI embedding 경로의 `max_retries or DEFAULT_MAX_RETRIES`가
명시적 0을 숨은 retry로 바꾸지 못하게 하는 compatibility rule이다.

| 요청 설정 | LiteLLM retry | llm 외부 시도 |
|---|---|---|
| num_retries/max_retries 생략 | 기본 0 | 명시된 provider.max_attempts, 없으면 최초 호출만 |
| max_retries=2 또는 num_retries=2 | 사용자 값 그대로 | 1회 |
| num_retries=0, max_retries=0 | 0 | 명시된 provider.max_attempts, 없으면 최초 호출만 |

원본 요청·저장 설정에는 retry 키를 자동 삽입하지 않는다. 실제 SDK 전송 사본은
`sdk_transport_request()`가 소유하며 생략된 SDK retry를 0으로 보장한다. 명시한 값은
null/0/양수를 구별해 그대로 전달한다. SDK 전역 초기화만으로는 OpenAI completion의
내부 기본 2회를 막지 못하므로 동기 스트림과 비스트리밍 실제 SDK 호출 모두 이 경계를 지난다.
명시한 주입 client가 있으면 객체 identity와 그 client의 max_retries를 보존한다. LiteLLM이
전송 인자를 client에 다시 쓰므로 생략된 max_retries에는 client의 기존 값을 사용하며,
해당 속성이 없으면 null을 전달해 임의의 client 정책을 만들지 않는다.
LiteLLM은 num_retries를 OpenAI max_retries로도 해석하므로 명시한 max_retries/client/retry_policy
또는 호스트 SDK 정책 옆에 num_retries=0을 임의로 덧붙여 그 선택을 덮지 않는다.

Backend의 `max_attempts`는 기존 `RetryPolicy.attempts`로만 해석한다. 생략은 최초 호출
1회이며 별도 기본값을 선언하거나 영속 설정에 채워 넣지 않는다. 명시한 값은 Backend
시도의 상한이다. `effective_attempts()`가 명시 SDK 재시도와 Backend 재시도의 중첩을 막는다.
주입 client의 max_retries, retry_policy, 호스트가 별도로 설정한 sdk.num_retries가
활성인 경우도 보수적으로 외부 1회만 호출한다. `provider_retry_delegated`로 이를 관찰한다.
DEFAULT_MAX_RETRIES만 강제 0이며 명시적 설정이나 호스트의 client를 재작성하지 않는다.
다른 코드가 호출 도중 SDK 전역값을 변경하는 경쟁까지 제어하지는 못한다.

모든 재시도 설정을 생략한 요청은 실제 HTTP 1회, provider.max_attempts=2만 선택한 요청은
최대 2회다. 명시 SDK 재시도를 선택한 경우 하위 HTTP 요청 총수는 그 SDK/client 계약을 따른다.
이때 provider.max_attempts는 하위 HTTP 총수가 아닌 Backend 시도의 상한이다. 취소·부분 응답
이후 재시도 금지와 승인·사용량 예약·Tool 효과·Checkpoint·Resume 계약은 그대로 유지한다.

비스트리밍 호출은 명시된 경우에만 `requests.invoke`가 wall deadline과 outer retry를 적용한다. 누락 시 deadline/재시도 없이 최초 호출만 수행한다.
llm 시도마다 공유 ProviderCalls의 admission과 기존 사용량 관찰자를 통과하므로 실패·재시도도 사용량 한도에 포함된다.
SDK 내부 HTTP 재시도는 하나의 SDK 호출 안에서 일어나므로 별도 사용량 영수증으로 관찰할 수 없다.
사용량 예약 실패는 공급자 재시도로 우회하지 않는다. `CancelledError`는 즉시 전달한다.
HTTP timeout과 별개로 모델 클라이언트 준비·응답·backoff에 같은 deadline을 적용한다.
TripleExtractor는 JSON-mode fallback과 semantic repair에도 같은 deadline을 공유한다.

재시도 대상은 timeout/연결 오류/429/정수 HTTP 500–599 전체/빈 공급자 응답이다. 일반 5xx는 `provider_unavailable`로 분류한다. 예외적으로 LiteLLM의 synthetic 500 중 `empty or invalid response from llm endpoint`가 포함된 오류는 먼저 `provider_invalid_response`로 분류한다. outer retry는 명시 설정이 있을 때만 수행한다. 인증·요청 오류,
벡터 무결성, JSON 구문·그래프 의미 오류는 transport retry 대상이 아니다. 마지막 공급자
오류는 안전한 `ProviderError.code`로 전달하고 `__cause__`에 원본을 보존한다.
JSON/의미 오류는 별도의 extraction repair 정책을 사용한다.

횟수·간격·기한 계산은 `llm.policies.retry`를 재사용한다. Application 선택기는 동일 요청
재시도를 중단하거나 허용 필드를 수정할 수 있다. 고수준 모델 호출에서는 timeout만 수정하며
SDK retry/캐시/입력 정체성을 바꾸지 않는다. 요청 오류도 명시된 선택기가 유효한 수정안을
제공하는 경우에만 반복할 수 있다. [재시도 계약](../../docs/llm/retry.md).

스트리밍은 `BaseEngine.stream_completion(request, provider=...)`의 명시적 인자를 사용한다.
Loop는 `parameters.engines[이름].policy.provider`, Memory 보조 모델은
`parameters.components.memory.policy.processing.provider`에서 가져온다. 암묵적 Run retry scope는 없다.
`max_attempts`는 최초 호출을 포함하며 미설정이면 최초 호출만 한다. 첫 chunk 이후에는
자동 재시도하지 않는다. 스트리밍도 같은 SDK 우선 정책을 적용하고 사용자 값을 유지한다.
명시한 `wall_timeout`은 응답·재시도에 같은 절대 기한을 적용하되, 이벤트를 저장 중인
소비자를 취소하지 않는다. 저장 후 다음 진행에서 남은 기한을 확인한다.
`respect_retry_after=true`이면 HTTP Retry-After의 초/HTTP-date를 최소 대기로 사용한다.
`jitter=true`이면 기존 지수 백오프의 0..계산값 범위에서 대기를 분산한다. 둘 다 미설정이면
적용하지 않으며, 이 플래그만으로 재시도가 활성화되지 않는다. 서버 대기가 명시한
`max_delay_seconds`를 넘으면 원래 오류로 종료하고 `provider_retry_stopped`를 기록한다.
전체 `wall_timeout`을 넘으면 `provider_timeout`으로 종료한다. 서버 요구보다 일찍 재시도하거나
사용자 기한을 늘리지 않는다. SDK 내부 재시도는 이 옵션으로 다시 조율하지 않는다.
스트림 bridge는 자체 deadline을 만들지 않는다. SDK `timeout`, Loop `request_timeout`,
provider `wall_timeout`은 독립이다. 동기 SDK worker는
강제로 죽일 수 없다. 소비자는 취소되지만 실제 호출이 종료될 때까지 슬롯은 반환하지 않는다.
사용자 주입 함수가 취소를 무시하거나 자체 내부 재시도를 구현하는 경우까지 강제로 제어하지는 않는다.

## 로그와 진단

`configure_logging(workspace)`를 호출하거나 `LargeLanguageModel(workspace)`를 생성하면
현재 문맥의 `workspace/logs/providers-<pid>.log`에 기록한다. 백엔드 없는 직접 모델 호출은
`~/.ish/llm-provider-logs`를 쓴다. 파일은 자동 회전·삭제하지 않으며 열린 목적지 handle만 최대 16개로 관리한다.
`diagnostic_scope(callback)`으로 기존 `Diagnostic` 객체를 UI/보고서 수집기에 연결할 수 있다.
중첩 scope의 관찰자도 호출된다. 엔진 응답은 기존 EngineEvent 흐름을 유지한다.

SDK 임의 로그는 요청 본문·인증을 포함할 수 있으므로 원문 대신 logger/등급/모듈/행을
기록한다. 호출 실패는 `diagnostic_failure()`가 오류 코드, operation, stage, 경과 시간,
예외 체인과 각 예외의 파일·행·함수를 기록한다. cause 또는 숨겨지지 않은 context를 따라간다.
HTTP status와 OS errno가 있으면 포함하고, import 실패의 module 및 AttributeError의 attribute도
기록한다. 모델, 호출 ID(`call_id`), 외부 시도 번호(`attempt`), 서버 요청 ID(제공된 경우),
설치된 LiteLLM/OpenAI 패키지 버전을 포함한다. 스트리밍에는 수신 chunk 수와 Tool 개수도 있다.
기본적으로 예외 원문, 소스 줄, 지역 변수, 요청·응답 본문, 인증/URL은 기록하지 않는다.
`provider_failed`는 미분류 오류이며 서버 장애라는 뜻이 아니다. Run/Step의 오류 코드는 그대로 유지한다.

RunManager는 Project/Session/Run ID와 engine을 로그 문맥에 연결한다. 스트림 worker도 그 문맥을
상속하며, 다른 Run/workspace와 섞이지 않는다. BaseEngine Step 안의 호출은 Step ID도 연결한다.
별도 Run이 없는 직접 모델 호출에는 Run/Step ID가 없다. 같은 의미의 요청을 재시도하면
call_id는 유지하고 attempt만 증가한다. 별도 요청이나 명시적 Run 재개는 새 call_id다.
SDK 내부 HTTP 시도 수는 관찰하지 못하므로 attempt에 합산하거나 추정하지 않는다.
동기 SDK 초기화 실패는 `initialize/sdk_import`, completion 호출 실패는 `completion/request`,
iterator 생성은 `stream_open`, 수신은 `stream_read`, 종료는 `stream_close`로 구분한다.
SDK import 실패는 초기화 경계와 이를 호출한 요청 경계에 각각 기록될 수 있다.
`chunks_received=0`은 bridge가 chunk를 받지 못했다는 뜻이며 HTTP 요청이 없었다는 보장은 아니다.
SDK가 첫 `next()`에서 HTTP 요청을 시작할 수도 있다. 비스트리밍은 `invoke` 단계로 기록한다.
기존 오류 뒤의 cleanup 오류는 `secondary=true`이며 원래 실패를 대체하지 않는다.
취소를 provider 실패로 바꾸지 않고, 로그 기록 장애도 원래 실행 결과를 바꾸지 않는다.

상세 로깅은 실행 정책이 아닌 Host 로깅 인프라 설정이다. Project/Engine의 retry 정책과
별개로 Application이 명시적으로 선택한다. 기존 프로젝트 설정을 변경하지 않는다.

```python
from llm.llm import BackendServices, LargeLanguageModel, ProviderDiagnostics

backend = LargeLanguageModel("workspace", services=BackendServices(
    provider_diagnostics=ProviderDiagnostics(
        error_details=True, request_summary=True, max_detail_chars=2000,
    ),
))
```

`error_details`는 서버 오류의 message/type/code/param과 endpoint를 기록한다. Endpoint에서
인증 userinfo/query/fragment는 제외한다. **서버 오류 메시지 자체에는 사내 내용이 포함될 수 있다.**
이 설정은 오류 문자열을 안전한 공개 데이터로 변환한다는 보장이 아니다.
`request_summary`는 메시지 역할·텍스트 길이·Tool 호출/결과 대응 개수만 기록하며
본문, Tool 인자, Tool ID, 정의 원문은 저장하지 않는다. 요약은 관찰용이며 요청을 수정하지 않는다.
`max_detail_chars`는 명시했을 때만 개별 오류 문자열을 자른다. 기본값은 상세 수집 없음이다.
원본 요청/응답 전체 보관, 자동 로그 삭제·보관 기간은 추가하지 않았다. Application이 보관을 관리한다.
서비스 밖에서 호출할 때는 `logging_scope(workspace, diagnostics=ProviderDiagnostics(...))`를 쓸 수 있다.
마지막 호출 실패의 Diagnostic은 기존 Step 실패 이벤트에도 연결되어 UI에서 조회할 수 있다.

Engine 응답 검증은 transport 오류와 구분한다.

| 코드 | 의미 |
|---|---|
| `completion_invalid_response` | choice/Tool fragment/종료 순서 등 응답 계약 위반 |
| `completion_incomplete` | `length`, `content_filter`, 종료 사유 누락 등 미완료 응답; finish_reason 별도 기록 |
| `completion_output_limit` | 명시한 출력 문자 제한 초과 |
| `completion_tool_limit` | 명시한 Tool 호출 수 제한 초과 |
| `completion_argument_limit` | 명시한 Tool 인자/ID 길이 제한 초과 |
| `step_timeout` | BaseEngine이 집행한 명시적 Step 기한 초과(Loop request_timeout 포함); SDK timeout과 별개 |

이 코드들은 자동 transport retry 대상이 아니다. 이미 받은 출력은 기존 저장 경로에 남는다.
알려진 transport 오류는 사용자 주입 completion 함수에서도 동일하게 분류하지만, 그 함수의
미분류 내부 오류는 `engine_failed` fallback을 유지한다. RunManager는 SDK 오류를 직접 해석하지 않는다.

Hub 기본 workspace를 사용하는 Linux 환경에서 최근 오류 위치는 다음으로 확인한다.

```bash
tail -n 30 ~/.ish/hub-workspace/logs/providers-*.log
```

workspace를 지정했다면 그 경로의 `logs/`를 확인한다. 로그 한 줄은 시간·등급 뒤의 JSON이며
`details.run_id`로 해당 Run을 찾고 `details.exceptions`의 마지막 예외/마지막 frame부터 확인한다.
기존 로그에 없던 traceback은 복구되지 않으므로 변경 적용 후 재현한 오류부터 상세 위치가 남는다.

`py.warnings`, LiteLLM, Router, Proxy, httpx/httpcore, `dotenv` 및 하위 logger가 콘솔로 전파되지 않는다.
애플리케이션의 stdout/stderr 전체를 전역 redirect하지 않는다.

## 임베딩 무결성

RAG `EmbeddingModel`은 `caching=False`만으로 cache write가 차단되지 않는 SDK 경로 때문에
`cache={"no-cache": true, "no-store": true}`도 전달한다. 호스트의 다른 작업이 소유하는
SDK cache 객체를 삭제하지 않는다. 일반 `ModelClient`의 명시적 cache kwargs는 유지한다.

RAG embedding ordering does not depend on provider-reported embedding indexes.
Each document chunk is embedded independently. The application owns the chunk ordinal
and restores results to that position. Embedding concurrency is bounded by configuration.

`extract_single_embedding()`은 data가 길이 1인 list인지와 유한·비영·동일 차원 벡터를
검증한다. index는 없거나 임의 값이어도 읽지 않는다. 문서와 query 모두 동일하다.
index 정규화, partial-cache merge_positions 복원, embedding 배치 분할은 제거했다.
여러 후보를 재정렬하는 **reranker의 results[*].index 검증은 그대로 유지**한다.

RAG의 embedding_concurrency는 양의 정수이며 문서 등록 시 명시해야 한다. 고정 개수의 asyncio worker가
단일 청크 요청을 처리하며 백엔드 ProviderCalls의 더 작은 제한도 존중한다.
VectorCache, 동일 텍스트 중복 제거, unchanged reuse, Job 청크 checkpoint는 유지한다.

모든 기본 임베딩은 LiteLLM `aembedding`으로 전달한다. 모델 접두사와 클라이언트 수명은
LiteLLM이 처리한다. SDK 함수 교체는 `LiteLLMOperations(call=...)`를 사용하고,
RAG/Vision은 선택 operation adapter의 공통 결과를 받는다. 내장 ModelClient의
호출은 공통 정책을 적용하지만, 임의의 커스텀 embedding/extractor 클래스가 내부에서 직접
호출하는 SDK까지 가로채지는 않는다. 같은 정책 바인딩을 제공하려면 `with_provider(options)`를
구현하거나 ModelClient를 재사용한다.

## Completion processor 경계

Loop의 Completion processor는 선택 adapter의 `validate_processing(candidate, original=...)`
검증을 통과해야 합니다. LiteLLM의 `num_retries`, `max_retries`, `retry_policy`, `client`
보호는 LiteLLM adapter가 소유합니다. 다른 SDK는 자신의 필드를 검증하며 공통 처리기에
SDK 필드 이름을 추가하지 않습니다. canonical Tool/message/stream 계약은 공통 계층이 유지합니다.

CompletionAdapter는 `ModelRequest`를 받아 raw chunk를 전달하고, Decoder는 공통
`CompletionUpdate`와 최종 `ModelResponse`를 만든다. `OperationAdapter`는 비스트리밍
`ModelResponse`/`EmbeddingResult`/`RerankResult`를 반환한다. SDK 인자·응답·retry·보조 호출
필터·실제 invocation 관찰은 Adapter 소유다. `SDK_RETRY_FIELDS`는 `SDKRetryParameters.RETRY_FIELDS`의 기존 공개 참조이며
보조 복구 호출의 중첩 SDK retry는 0, 제목/Memory의 명시적 retry 값은 기존 계약대로 유지한다.
RAG의 강제 embedding cache-off와 일반 직접 Adapter 호출의 명시 cache 선택은 구분한다.
순수 벡터 검증은 `core/embeddings.py`가 소유하고 SDK envelope 추출만 Provider에서 한다.

## 재현 검사

Linux Python 3.12.14에서:

```bash
python -m unittest tests.llm.test_provider_runtime tests.llm.test_rag_resilience -v
python -m tests.llm.provider_probe --output /tmp/provider-probe.json
```

두 번째 명령은 실제 LiteLLM HTTP 경로와 Chroma/BM25/Kuzu/GraphEngine을 사용한다.
응답은 로컬 고정 fixture이며 외부 모델 품질이나 사내 서버 속도를 측정하지 않는다.
sync/async LiteLLM 임베딩 일치, SDK retry 중첩 방지, 165줄 문서 등록과 rerank를 확인한다.

[상위 안내](../README.md)


## 요청·호출 Owner 연결

`ModelParameters`와 `EmbeddingParameters`의 정규 선언은 request_values에 있고 schema의 기존
import 위치는 같은 타입을 참조한다. 이 경계는 원본의 생략/null/명시값을 바꾸지 않는다.
`ProviderRequestSchema`는 알려진 Completion 필드 제약을 한 번 선언하고 작업별 Schema를
fresh copy로 투영한다. provider 확장 인자와 주입 runtime 객체는 그대로 보존한다.
재시도 수정안의 보호 필드·client identity 검증은 `LiteLLMParameters`가 소유한다.

`SDKRetryParameters`는 SDK의 선택된 retry만 읽고 전송 사본에 숨은 기본 재시도를 막는다.
Backend 시도 계산은 계속 기존 RetryPolicy에만 있다. `RetryAfter`는 HTTP header를 해석하고
기한·backoff·선택기와 실제 실행 순서는 기존 retry/requests 경계에 남는다. `ProviderFailure`의
정수 HTTP 범위·synthetic 응답·예외 이름 분류와 미분류 사용자 예외 보존도 동일하다.
`ProviderCallStats`는 active/waiting의 공개 사본을 만들고 ProviderCalls의 잠금·실제 종료 후
release·취소된 waiter 정리 순서를 유지한다. 새로운 실행 기본값이나 레거시 Reader는 없다.



Codec의 공개 함수는 각 Owner의 한 구현으로 연결된다. Tool call/definition의 필수 필드·
original argument text·opaque 확장·presence 상태는 SDKToolCodec이, text/image/opaque 부분은
SDKContentPartCodec이, role/result/누락·null·빈 call shape는 SDKMessageCodec이 읽고 쓴다.
SDKWire는 finite JSON 복사·확장 충돌·원본 인자 문자열 비교 형식만 소유한다. request의
messages/tools/tool_choice는 SDKRequestCodec의 전송 Writer에서만 조립하며 Backend 내부
소비자는 공통 ModelRequest/ModelMessage/ToolDefinition 타입을 사용한다. 저장 형식이나
추가 승인·Tool 호출 권한을 만들지 않는다. 정상/손상 입력 모두 기존 Reader와 같은 결과·
오류 순서를 유지한다.


비스트리밍 LiteLLMOperations는 요청의 공통 exchange를 검증하고 호출을 조율한다.
LiteLLMParameters가 single/nonstreaming 판정, 명시 JSON 출력 의도, embedding/rerank 인자와
RAG cache-off/vision 고정 인자의 선언을 소유한다. 이 Writer는 이미 복사된 전송 인자만
변경하며 원본 ModelRequest와 사용자 설정에는 기본값을 저장하지 않는다.
SDKOperationResponse는 SDK 객체의 opaque 확장을 투영하고, SDKCompletionResponse/
SDKEmbeddingResponse/SDKRerankResponse는 각 응답을 공통 결과 타입으로 변환한다.
단일 choice·벡터 무결성·ranking index·사용량 unknown 규칙과 오류 순서는 기존과 같다.
명시 client/transport의 identity·retry·timeout 선택은 유지하며 새 호출이나 retry를 만들지 않는다.



ProviderDiagnostics는 opt-in 상세 수집의 기존 flag/문자 상한을 소유한다.
ProviderRequestDiagnostics/ProviderErrorDiagnostics/ProviderFailureObservation은 요청 요약,
서버 오류 상세와 첫 HTTP cause의 원인 관찰만 작성한다. SDKError는 같은 header/body 조회를
제공하고 SDKMappingMessage는 요청 Mapping, SDKMessage는 SDK 응답 객체의 조회를 유지한다.
진단을 읽는 시점과 모델/header/body의 조회 순서도 유지한다. 본문·Tool 이름/ID는 요약에
넣지 않으며 상세 서버 문자열은 기존 error_details 선택을 따른다.

ProviderLogPaths/ProviderLogging은 로그 경로·handle LRU·라이브러리 라우팅을,
ProviderLogSource/LoggingStatus/LibraryLog/LogRecord/ExceptionRecord는 각 직렬화 모양을
소유한다. Provider의 workspace 로그 경로는 Project/Run 저장 경로와 독립이다.
표준 logging.FileHandler.handleError 이름과 append 쓰기/실패 계수/후속 쓰기 복구를 유지한다.
SDKRuntime은 bundled cost map·기본 retry 0·배너 억제의 기존 초기화 불변식만 선언한다.
실제 import와 lock, ContextVar 격리, 관찰자 복사·오류 격리와 I/O 순서는 runtime에 남는다.
로그 장애가 원래 실행 실패/취소를 바꾸거나 진단 데이터가 실행 허가·저장 증거가 되지 않는다.

LiteLLMCompletionAdapter의 입력 소유권·보조 호출 필터·processor 보호는 LiteLLMParameters의
정규 해석을 사용한다. 기존 Adapter의 주입 함수·decoder와 클래스 필드 선택을 보존한다.
SDKValue는 Mapping/attribute 조회·model_dump·usage/opaque 정규화의 단일 구현이다.
기존 private helper 위치는 같은 구현에 위임하며 저장 형식의 이중 Reader를 만들지 않는다.

SDKStreamMetadata/Choice/Delta가 선택적 SDK 조각을 읽고, SDKToolFragment가 부분 Tool의
ID/name/arguments·opaque 확장 결합과 길이를 소유한다. Decoder의 호출 map은 이 객체를
보관하며 고정 wire dict를 직접 해석하지 않는다. 완성 메시지는 SDKStreamMessage Writer와
기존 codec/공통 모델 검증을 거쳐야 한다. metadata를 choice 검증보다 먼저 관찰하는 순서,
부분 응답의 이벤트·오류·누적 상태, 미완/중복 Tool 거부와 원본 서명을 유지한다.

StreamBridge의 큐 이벤트·poll 간격·daemon/정리 프로토콜은 실행 불변식이며 사용자 retry나
새 deadline을 만들지 않는다. admission 취득 후 같은 worker에서 stream을 읽고 닫으며,
worker의 실제 종료 때만 release한다. 취소 시 전달 중단, 원래 실패 우선과 부수 cleanup
진단, ContextVar 전파 순서는 그대로다. ProviderTokenAccounting은 Adapter별 예약 입력만
해석하고 승인·예산 집행은 기존 공통 Owner에 남는다.

Provider의 기존 요청/응답/진단/codec 실제 소비 연결은 로컬 전수 검토를 마쳤다. 전체 통합
상태와 원격 완료는 별도 Gate41 결과를 따른다.
