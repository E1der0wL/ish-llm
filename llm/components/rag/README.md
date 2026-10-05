# RAG — 문서와 관계를 함께 등록·검색

Markdown 문서를 분할하고 임베딩과 관계를 추출하여 Chroma/BM25/Kuzu 색인을 관리합니다. 하나의 검색에서 관련 문서와 관계·출처를 같은 데이터 세대에서 반환합니다. embedding, rerank, 추출 모델 클라이언트도 이 Component가 소유합니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | RAGComponent/RAGConflictError와 EmbeddingModel/RerankModel/TripleExtractor의 공개 import입니다. |
| [component.py](component.py) | 설정 검증과 문서·색인 generation의 원자적 공개·복제를 담당합니다. |
| [data.py](data.py) | Project 수명·잠금 경계를 유지하는 문서 CRUD·검색·Job 공개 API입니다. |
| [splitting.py](splitting.py) | Markdown 제목·섹션·청크와 위치 메타데이터를 구성합니다. |
| [embedding.py](embedding.py) | LiteLLM 단일 입력 임베딩 클라이언트와 SDK 캐시 차단 계약입니다. |
| [rerank.py](rerank.py) | 검색 후보 재정렬 클라이언트와 결과 index·score 검증입니다. |
| [_client.py](_client.py) | 모델 클라이언트의 공통 파라미터·provider 정책 연결입니다. |
| [extraction.py](extraction.py) | 관계 JSON 추출·검증, 명시적 JSON mode와 repair를 처리합니다. |
| [prompts.py](prompts.py) | 관계 추출의 기본 프로토콜 지침을 정의합니다. Project 프롬프트 저장소와 구분됩니다. |
| [ingestion.py](ingestion.py) | 청크별 bounded 임베딩, 중복 제거, 캐시·이전 벡터 재사용과 진행 전달입니다. |
| [jobs.py](jobs.py) | 등록 Job, 청크·추출 checkpoint와 명시적 재개를 관리합니다. |
| [files.py](files.py) | 세대 구성 시 파일 복사/reflink 등 Linux 파일 처리를 돕습니다. |
| [indexing.py](indexing.py) | 문서 벡터를 Chroma 색인으로 구성합니다. |
| [graph_indexing.py](graph_indexing.py) | Kuzu 엔티티·관계·출처 색인을 구성합니다. |
| [search.py](search.py) | BM25/vector/hybrid 검색, RRF 결합과 섹션 확장입니다. |
| [graph_search.py](graph_search.py) | 검색 문서·엔티티에 연결된 그래프 증거를 탐색합니다. |
| [tools.py](tools.py) | Engine에 자동 연결할 rag_search Tool의 schema와 handler입니다. |

## 시작하기

백엔드에 RAGComponent를 등록하고 Project에서 `rag`를 선택합니다. 모델과 문서 처리·검색 설정도 명시해야 합니다. 처음에는 개발 저장소의 [실행 가이드](../../../examples/llm/graph_rag.md)와 [설정 예제](../../../examples/llm/graph_rag.config.example.json)를 사용하세요. 이 예제는 Graph, RAG, reranker를 함께 검증합니다.

필수 모델·설정이 연결된 Project의 API는 다음과 같습니다.

```python
rag = await project.components.aget("rag")
document = await rag.aadd_document(
    title="프로그램 설명서",
    content="# 실행\n프로그램은 설정 파일을 읽어 실행합니다.",
    identifier="manual",
)
hits = await rag.asearch("설정 파일을 어떻게 사용하나요?")
documents = hits["documents"]
await rag.aupdate_document(
    "manual", content="# 실행\n설정 파일을 검증한 뒤 실행합니다.",
    expected_revision=document["revision"],
)
await rag.adelete_document("manual")
```

문서 API는 자체적으로 Run을 생성하지 않습니다. Engine/Tool 안에서 호출하면 기존 Run의 관찰 경계를 사용합니다. `rag` 선택 시 tools capability에 `rag_search`를 제공하므로, 이를 요청하는 Loop나 Graph ToolNode는 별도의 enable_search 호출 없이 검색할 수 있습니다. 검색 이외의 문서 CRUD 전체가 자동으로 모델 Tool로 노출되는 것은 아닙니다.

## 설정과 임베딩

설정은 `ProjectConfig.parameters.components.rag`에 저장한다.
`configuration_schema()`는 허용 형식만 제공한다. [전역 설정 계약](../../CONFIGURATION.md)을 따른다.

| 키 | 미설정 동작 |
|---|---|
| config.chunk_size / policy.embedding_concurrency | 문서 등록 시 configuration error (concurrency 범위 1–32) |
| policy.extraction.failure_policy / config.extraction_batch_size | 실패 정책은 명시; 추출을 켠 경우 batch_size도 명시 |
| config.search: method/expand/limit/candidate_count/rrf_constant | 검색 전 명시; 결합 검색은 max_hops/relation_limit도 필요 |
| config.embedding_cache_max_bytes / config.search_cache_chars | 캐시 비활성 |
| policy.provider: max_attempts/wall_timeout/delay | 최초 호출만, 자체 기한/대기 없음 |
| config.extraction.json_mode/relation_types, policy.extraction.repair_attempts | SDK format 변경·수정 재호출·선호 목록 없음 |
| config.graph | Kuzu native 옵션 |

모델 인자는 config.embedding_params/extraction_params/rerank_params에 둡니다.
`rag.aconfigure({"config": {...}, "policy": {...}})`도 같은 Project 설정을 교체합니다.
내부 모델 client의 `provider=` 명시 인자는 policy.provider의 client 출처이며,
Project에 명시된 같은 정책 키가 우선합니다. 조회만으로 설정을 저장하지 않습니다.

RAG embedding ordering does not depend on provider-reported embedding indexes.
Each document chunk is embedded independently. The application owns the chunk ordinal
and restores results to that position. Embedding concurrency is bounded by configuration.

`split_markdown(chunk_size)` → 고정 개수 worker → `embedding(input=[text])` →
`vectors[original_position]` 순서다. 문서 크기만큼 Task를 생성하지 않는다.
동일 text는 한 번만 요청하고 여러 위치에 결과를 복사한다. 한 요청은 data list 길이 1,
nonempty list/tuple 벡터, finite/nonzero 값, 설정·기존 corpus·다른 청크와 동일 차원이어야 한다.
query도 단일 요청 검증을 사용한다. reranker index는 별도 계약으로 유지한다.

`embedding_batch_size`, `embedding_batching`은 제거했고 설정 검증에서 거부한다.
기존 값을 concurrency로 변환하지 않는다. 사용자는 해당 키를 제거하고 새 설정을 명시해야 한다.
자동 분할·split checkpoint는 없다. 큰 청크는 chunk_size로 조정한다.
extraction_batch_size와 index_batch_size는 각각 관계 추출·DB 쓰기용이므로 유지한다.

LiteLLM DEFAULT_MAX_RETRIES는 import 전 환경변수와 import 후 전역값 모두 항상 0이다.
명시한 num_retries/max_retries는 수정 없이 전달한다. SDK retry가 활성화되면 llm invoke는
1회이며, 생략하거나 모두 0이면 provider.max_attempts를 적용한다. 공유 ProviderCalls 제한과
사용량 예약을 우회하지 않는다. provider_retries는 llm 외부 재시도 수이며 SDK 내부 retry는 제외한다.

## 캐시와 재개

LiteLLM에는 caching=False, cache={"no-cache": true, "no-store": true}를 전달한다.
llm VectorCache만 사용한다. 용량이 없거나 0이면 LRU를 끈다. fingerprint에는 single-chunk 계약 버전,
모델 identity, 유효 embedding params, document/query kwargs, 클라이언트 종류를 포함한다.
캐시 키에는 Project·런타임 함수 identity·text SHA-256도 반영한다. 모든 재사용 벡터를 검증한다.
문서 update는 같은 fingerprint의 동일 chunk vector를 이전 문서에서 재사용한다.
주입 클라이언트의 모델 의미를 바꾸면 embedding_id도 바꿔야 한다.

구 fingerprint는 일치하지 않으므로 다음 update에서 재계산한다. 기존 corpus는 자동 변경하지 않는다. 현재 graph_schema_version은 1이며 지원하지 않는 저장 형식은 거부한다. 이전 batch checkpoint를 새 청크 결과로 해석하지 않는다.
설정이 달라진 prepared Job은 새 Job으로 등록해야 한다.

긴 문서는 enqueue_document → arun_job을 권장한다. 실패 후 arun_job(id, retry=True)로 명시적으로
재개한다. 기존 서비스 소유 checkpoint 디렉터리 `jobs/<id>/batches/` 아래에
`embedding_<original_position>.json`을 쓴다. 이 디렉터리는 추출 checkpoint도 공유하므로 이름을 유지한다.
embedding 파일의 outer signature와 value의 fingerprint/text_hash/vector를 검증한다.
worker는 progress callback만 호출하며 RAGJobs가 workspace 잠금과 atomic_json으로 저장한다.
완료 순서와 파일 번호는 무관하고 완료된 청크는 재개 시 호출하지 않는다.

실패하면 새 청크 배정을 중단하고 진행 중 요청의 성공 checkpoint를 보존한다.
취소하면 모든 worker를 취소하고 종료를 기다린다. 완료 checkpoint는 유지하고 미완료 세대는
공개하지 않는다. 기존 active generation은 유지한다. 취소된 Job의 자동 재실행은 없다.
프로세스 재시작 시 LRU는 소실되지만 Job checkpoint와 이전 문서 벡터는 남는다.

`job()["ingestion"]`, 문서 ingestion, diagnostic_scope로 다음을 관찰한다:
chunks_total/completed/reused/cached/requested, embedding_concurrency, embedding_seconds,
provider_retries, checkpoint_reuse, active_embedding_requests, peak_embedding_requests.
requested는 중복 제거 후 새 단일 청크 요청 수(재시도 제외), reused는 이전 문서 및 중복 위치 수,
cached는 LRU로 채운 위치 수, checkpoint_reuse는 읽은 완료 checkpoint 수다.
embedding_seconds는 준비 단계 벽시계 시간이다. active/peak는 admission 대기까지 포함한 청크 호출 수로,
실제 SDK 실행 동시성은 ProviderCalls 한도가 더 낮을 수 있다.

## 추출과 그래프 실패

- `strict`: 항상 JSON mode 사용.
- `auto`: 빈 응답/공급자 파싱 오류에만 JSON mode 없는 경로로 한 번 전환.
- `off`: `response_format`을 완전히 제외. 프롬프트와 Python 검증으로 JSON 계약을 유지.

400/인증/잘못된 모델 오류에서는 JSON mode를 임의로 끄지 않는다. 명시적으로 off를 선택한다.
JSON/그래프 의미 오류는 기존 `repair_attempts`(미설정이면 수정 없음)로만 수정한다. temperature는 명시된 값만 SDK에 전달한다. Python이 이름 기반 ID·관계 타입·중복·가중치·출처를 정규화한다.
대명사의 의미를 해소해도 evidence는 원문의 정확한 부분 문자열이어야 한다.
두 번째 별도 LLM 정규화 단계는 추가하지 않았다.

`required`는 실패하면 기존 세대를 유지한다. `best_effort`는 성공한 batch의 검증된 관계만
사용하고 `graph_complete=false`, `graph_diagnostics`를 문서에 저장한다. `disabled`는
추출 호출 없이 빈 그래프 DB와 문서 색인을 함께 공개한다. 검색 응답의 `graph_complete`와
`graph_incomplete_documents`로 누락 가능성을 알린다. 실패/취소한 미완료 generation은
active가 되지 않는다. 기존 graph schema version 1는 그대로 유지한다.

`rerank` Tool 인자는 실제 reranker가 없으면 `const:false`로 제공하며 handler에서도 불가능한 True 요청을 오류로 거부한다. 공개 Python 검색 API에서 명시적으로 rerank=True를 요청하면 설정 오류를 반환한다.

자세한 공급자 경계·제한은 [Provider 설명](../../providers/README.md)을 참고한다.

[상위 안내](../README.md)
