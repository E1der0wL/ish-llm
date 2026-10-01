# 공급자 안정화 경계

설정은 [명시적 설정 계약](../CONFIGURATION.md)을 따른다. `runtime.py`는 LiteLLM 초기화와 진단 라우팅을 담당한다. 비용표·모드·인증 환경변수는 호스트 또는 SDK가 선택한다.

LiteLLM import 전에 `os.environ["DEFAULT_MAX_RETRIES"] = "0"`을 강제로 적용한다.
`litellm_sdk()`는 import 후와 매 진입 시 `sdk.DEFAULT_MAX_RETRIES = 0`도 적용한다.
LiteLLM 1.103.1 OpenAI embedding 경로의 `max_retries or DEFAULT_MAX_RETRIES`가
명시적 0을 숨은 retry로 바꾸지 못하게 하는 compatibility rule이다.

| 요청 설정 | LiteLLM retry | llm 외부 시도 |
|---|---|---|
| num_retries/max_retries 생략 | 기본 0 | 명시된 provider.max_attempts, 없으면 최초 호출만 |
| max_retries=2 또는 num_retries=2 | 사용자 값 그대로 | 1회 |
| num_retries=0, max_retries=0 | 0 | 명시된 provider.max_attempts, 없으면 최초 호출만 |

요청 dict에는 retry 키를 자동 삽입하지 않는다. `effective_attempts()`가 중첩을 막는다.
주입 client의 max_retries, retry_policy, 호스트가 별도로 설정한 sdk.num_retries가
활성인 경우도 보수적으로 외부 1회만 호출한다. `provider_retry_delegated`로 이를 관찰한다.
DEFAULT_MAX_RETRIES만 강제 0이며 명시적 설정이나 호스트의 client를 재작성하지 않는다.
다른 코드가 호출 도중 SDK 전역값을 변경하는 경쟁까지 제어하지는 못한다.

비스트리밍 호출은 명시된 경우에만 `requests.invoke`가 wall deadline과 outer retry를 적용한다. 누락 시 deadline/재시도 없이 최초 호출만 수행한다.
llm 시도마다 공유 ProviderCalls의 admission과 기존 사용량 관찰자를 통과하므로 실패·재시도도 사용량 한도에 포함된다.
SDK 내부 HTTP 재시도는 하나의 SDK 호출 안에서 일어나므로 별도 사용량 영수증으로 관찰할 수 없다.
사용량 예약 실패는 공급자 재시도로 우회하지 않는다. `CancelledError`는 즉시 전달한다.
HTTP timeout과 별개로 모델 클라이언트 준비·응답·backoff에 같은 deadline을 적용한다.
TripleExtractor는 JSON-mode fallback과 semantic repair에도 같은 deadline을 공유한다.

재시도 대상은 timeout/연결 오류/429/502/503/504/빈 공급자 응답이다. 인증·요청 오류,
벡터 무결성, JSON 구문·그래프 의미 오류는 transport retry 대상이 아니다. 마지막 공급자
오류는 안전한 `ProviderError.code`로 전달하고 `__cause__`에 원본을 보존한다.
JSON/의미 오류는 별도의 extraction repair 정책을 사용한다.

스트리밍은 기존 Run의 `policies.provider_retry`를 유지하며 첫 chunk를 받은 뒤에는
llm은 자동 재시도하지 않는다. 스트리밍도 같은 SDK 우선 정책을 적용하고 사용자 값을 유지한다.
스트림 bridge는 자체 deadline을 만들지 않는다. SDK `timeout`과 Loop wrapper request_timeout은 독립이다. 동기 SDK worker는
강제로 죽일 수 없다. 소비자는 취소되지만 실제 호출이 종료될 때까지 슬롯은 반환하지 않는다.
사용자 주입 함수가 취소를 무시하거나 자체 내부 재시도를 구현하는 경우까지 강제로 제어하지는 않는다.

## 로그와 진단

`configure_logging(workspace)`를 호출하거나 `LargeLanguageModel(workspace)`를 생성하면
현재 문맥의 `workspace/logs/providers-<pid>.log`에 기록한다. 백엔드 없는 직접 모델 호출은
`~/.ish/llm-provider-logs`를 쓴다. 파일은 자동 회전·삭제하지 않으며 열린 목적지 handle만 최대 16개로 관리한다.
`diagnostic_scope(callback)`으로 기존 `Diagnostic` 객체를 UI/보고서 수집기에 연결할 수 있다.
중첩 scope의 관찰자도 호출된다. 엔진 응답은 기존 EngineEvent 흐름을 유지한다.

SDK 임의 로그는 요청 본문·인증을 포함할 수 있으므로 원문 대신 logger/등급/모듈/행을
기록한다. 모델 호출 실패는 별도로 operation/model/오류 코드/시도/경과 시간을 기록한다.
`py.warnings`, LiteLLM, Router, Proxy, httpx/httpcore가 콘솔로 전파되지 않는다.
애플리케이션의 stdout/stderr 전체를 전역 redirect하지 않는다.

## 임베딩 무결성과 LiteLLM 1.103.1

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

RAG의 embedding_concurrency는 1–32이며 문서 등록 시 명시해야 한다. 고정 개수의 asyncio worker가
단일 청크 요청을 처리하며 백엔드 ProviderCalls의 더 작은 제한도 존중한다.
VectorCache, 동일 텍스트 중복 제거, unchanged reuse, Job 청크 checkpoint는 유지한다.

모든 기본 임베딩은 LiteLLM `aembedding`으로 전달한다. 모델 접두사와 클라이언트 수명은
LiteLLM이 처리한다. 명시적으로 주입한 모델 함수는 기존 확장 계약대로 사용한다. 내장 ModelClient의
호출은 공통 정책을 적용하지만, 임의의 커스텀 embedding/extractor 클래스가 내부에서 직접
호출하는 SDK까지 가로채지는 않는다. 같은 정책 바인딩을 제공하려면 `with_provider(options)`를
구현하거나 ModelClient를 재사용한다.

## 재현 검사

Linux Python 3.12.14에서:

```bash
python -m unittest llm.tests.test_provider_runtime llm.tests.test_rag_resilience -v
python -m llm.tests.provider_probe --output /tmp/provider-probe.json
```

두 번째 명령은 실제 LiteLLM HTTP 경로와 Chroma/BM25/Kuzu/GraphEngine을 사용한다.
응답은 로컬 고정 fixture이며 외부 모델 품질이나 사내 서버 속도를 측정하지 않는다.
sync/async LiteLLM 임베딩 일치, SDK retry 중첩 방지, 165줄 문서 등록과 rerank를 확인한다.
