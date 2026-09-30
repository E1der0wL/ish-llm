# 공급자 안정화 경계

`runtime.py`만 LiteLLM 전역 초기화와 로깅을 관리한다. 최초 import 전에
`LITELLM_MODE=PRODUCTION`, 로컬 비용표, 기본 재시도 0을 설정한다. 기존 인증 환경변수는
삭제하지 않는다. SDK import를 다른 플러그인이 먼저 수행했다면 이미 발생한 dotenv 로드는
되돌릴 수 없다. 같은 프로세스에서 LiteLLM의 기본 재시도 전역값을 다시 바꾸면 거부한다.

비스트리밍 호출은 `requests.invoke`가 전체 wall deadline과 시도 횟수를 소유한다.
실제 시도마다 기존 사용량 관찰자를 통과하므로 실패·재시도도 사용량 한도에 포함된다.
사용량 예약 실패는 공급자 재시도로 우회하지 않는다. `CancelledError`는 즉시 전달한다.
HTTP timeout과 별개로 모델 클라이언트 준비·응답·backoff에 같은 deadline을 적용한다.
TripleExtractor는 JSON-mode fallback과 semantic repair에도 같은 deadline을 공유한다.

재시도 대상은 timeout/연결 오류/429/502/503/504/빈 공급자 응답이다. 인증·요청 오류,
벡터 무결성, JSON 구문·그래프 의미 오류는 transport retry 대상이 아니다. 마지막 공급자
오류는 안전한 `ProviderError.code`로 전달하고 `__cause__`에 원본을 보존한다.
JSON/의미 오류는 별도의 extraction repair 정책을 사용한다.

스트리밍은 기존 Run의 `policies.provider_retry`를 유지하며 첫 chunk를 받은 뒤에는
절대로 자동 재시도하지 않는다. SDK에는 `num_retries=max_retries=0`을 강제한다.
스트림의 전체 대기 한도는 요청 `timeout`이며, 생략하면 120초다. 동기 SDK worker는
강제로 죽일 수 없다. 소비자는 취소되지만 실제 호출이 종료될 때까지 슬롯은 반환하지 않는다.
사용자 주입 함수가 취소를 무시하거나 자체 내부 재시도를 구현하는 경우까지 강제로 제어하지는 않는다.

## 로그와 진단

`configure_logging(workspace)`를 호출하거나 `LargeLanguageModel(workspace)`를 생성하면
현재 문맥의 `workspace/logs/providers-<pid>.log`에 기록한다. 백엔드 없는 직접 모델 호출은
`~/.ish/llm-provider-logs`를 쓴다. 파일은 5MB × 4개로 회전하며 열린 목적지는 최대 16개다.
`diagnostic_scope(callback)`으로 기존 `Diagnostic` 객체를 UI/보고서 수집기에 연결할 수 있다.
중첩 scope의 관찰자도 호출된다. 엔진 응답은 기존 EngineEvent 흐름을 유지한다.

SDK 임의 로그는 요청 본문·인증을 포함할 수 있으므로 원문 대신 logger/등급/모듈/행을
기록한다. 모델 호출 실패는 별도로 operation/model/오류 코드/시도/경과 시간을 기록한다.
`py.warnings`, LiteLLM, Router, Proxy, httpx/httpcore가 콘솔로 전파되지 않는다.
애플리케이션의 stdout/stderr 전체를 전역 redirect하지 않는다.

## 임베딩 무결성과 LiteLLM 1.103.1

`caching=False`만으로 cache write가 차단되지 않는 SDK 경로 때문에 항상
`cache={"no-cache": true, "no-store": true}`도 전달한다. 호스트의 다른 작업이 소유하는
SDK cache 객체를 삭제하지 않는다. 기본 `None`을 유지하며 `False`를 비활성 상태로 쓰지 않는다.
`DEFAULT_MAX_RETRIES=0`도 설정하여 1.103.1의 `0 or DEFAULT_MAX_RETRIES` 경로를 막는다.

`normalize_litellm_embeddings` → `validate_embeddings` 순서다. 정규화는 아래가 모두
확인될 때만 허용한다.

- count 일치, cache read 허용, 실제 cache 객체 존재, 내부 `cache_hit=True`
- 호스트의 SDK 병합 경계에서 확인한 `merge_positions`(캐시된 원래 위치)
- cached/fresh 위치와 index 패턴 일치, 유한·비영·동일 차원 벡터

`cache_hit=True`만으로는 어떤 위치가 캐시에서 왔는지 증명할 수 없으므로 자동 추측하지 않는다.
정상 응답은 수정하지 않는다. 정규화는 사본에 적용하고 원래 배열 순서를 보존하며,
`embedding_index_normalized` 진단을 남긴 뒤 다시 엄격하게 검증한다.
일반 RAG 호출은 SDK cache를 끄므로 이 분기에 들어가지 않는다. `[0,0]`은 서버 오류로 거부한다.
호스트가 의도적으로 cache merge를 사용하는 별도 어댑터에서만 명시적 위치 증거를 제공한다.

`rag.provider.embedding_adapter="openai"`는 `AsyncOpenAI.embeddings.create`를 선택한다.
`openai/` 모델 접두사만 제거하며 `api_base`를 `base_url`로 전달한다. 나머지 endpoint 인자는
OpenAI SDK 계약을 따른다. completion/extraction은 계속 LiteLLM을 사용한다. 주입된
SDK client의 `max_retries`가 0이 아니면 호출 전에 거부한다.

명시적으로 주입한 모델 함수가 있으면 그 함수가 어댑터보다 우선한다. 내장 ModelClient의
호출은 공통 정책을 적용하지만, 임의의 커스텀 embedding/extractor 클래스가 내부에서 직접
호출하는 SDK까지 가로채지는 않는다. 같은 정책 바인딩을 제공하려면 `with_provider(options)`를
구현하거나 ModelClient를 재사용한다.

## 재현 검사

Linux Python 3.12.14에서:

```bash
python -m unittest llm.tests.test_provider_runtime llm.tests.test_rag_resilience -v
python -m llm.tests.provider_probe --output /tmp/provider-probe.json
```

두 번째 명령은 실제 LiteLLM/OpenAI HTTP 경로와 Chroma/BM25/Kuzu/GraphEngine을 사용한다.
응답은 로컬 고정 fixture이며 외부 모델 품질이나 사내 서버 속도를 측정하지 않는다.
sync/async LiteLLM 및 direct OpenAI 임베딩 일치, 실제 재시도 횟수, 165줄 문서 등록을 확인한다.
