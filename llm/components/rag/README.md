# RAG — 문서와 관계를 함께 등록·검색

`aadd_document` / `aupdate_document` / `aenqueue_document`에 선택적 `description`을 전달할 수
있습니다. Backend는 설명을 모델로 자동 생성하지 않습니다. `alist_catalog(limit=..., cursor=...)`와
자동 제공되는 `rag_list_documents` Tool은 검색/임베딩/추출 없이 제목·설명·revision 목록을 읽습니다.
목록은 같은 atomic generation의 `catalog.json`에 함께 공개됩니다. 페이지 도중 세대가 바뀌면
cursor를 거부하므로 첫 페이지부터 다시 조회합니다. catalog 없는 기존 세대는 자동 변환하지
않으며 해당 API 사용 전 원문으로 명시적으로 재등록/갱신해야 합니다.

`ajob_progress(job_id)`는 현재 단계와 확인 가능한 `completed/total/unit`을 반환합니다.
단계는 validating_input → splitting → embedding → extracting → validating → indexing → writing_metadata → publishing → completed입니다.
추출을 끈 경우 해당 단계는 생략됩니다. 임베딩은 chunk, 추출은 처리한 batch 기준이며 실패한
best_effort batch의 처리 완료는 그래프 성공을 의미하지 않습니다. 전체량을 모르는 단계는
total=None입니다. 단계 비율은 계산할 수 있지만 전체 남은 시간/종합 퍼센트는 추측하지 않습니다.
공개 단계는 기존 workspace 직렬화 경계여서 조회가 완료까지 기다릴 수 있습니다.
취소/실패 시 활성 세대를 보존하는 계약과 Job checkpoint 재사용은 유지됩니다.

Markdown 문서를 분할하고 임베딩과 관계를 추출하여 Chroma/BM25/Kuzu 색인을 관리합니다. 하나의 검색에서 관련 문서와 관계·출처를 같은 데이터 세대에서 반환합니다. embedding, rerank, 추출 모델 클라이언트도 이 Component가 소유합니다.

공개 capability와 Tool 이름은 [identity.py](identity.py)가 선언합니다. Component·Tool 등록과
Agent 선택은 같은 선언을 사용하며 모델/저장 경계에는 기존 문자열 값을 전달합니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | RAGComponent/RAGConflictError와 EmbeddingModel/RerankModel/TripleExtractor의 공개 import입니다. |
| [component.py](component.py) | 설정 검증과 문서·색인 generation의 원자적 공개·복제를 담당합니다. |
| [data.py](data.py) | Project 수명·잠금 경계를 유지하는 문서 CRUD·검색·Job 공개 API입니다. |
| [chunk_records.py](chunk_records.py) | 분할 문서·섹션·청크의 형식·순서/ID·내용 digest와 원본/섹션 연결 검증을 소유합니다. |
| [splitting.py](splitting.py) | MarkdownSplitter가 fence·제목 계층·문단 분할을 소유하며 정규 섹션/청크 Writer로 결과를 구성합니다. |
| [embedding.py](embedding.py) | 단일 입력 임베딩 클라이언트와 정규 결과 검증입니다. 모델/차원 키는 Provider의 ModelParameters/EmbeddingParameters를 사용합니다. |
| [rerank.py](rerank.py) | 검색 후보 재정렬 클라이언트와 결과 index·score 검증입니다. |
| [_client.py](_client.py) | ModelClient가 주입 Adapter·파라미터 사본·명시 retry 선택기·프로세스 캐시 식별 계약을 소유합니다. |
| [extraction.py](extraction.py) | 관계 JSON 추출·검증, 명시적 JSON mode와 repair를 처리합니다. |
| [prompts.py](prompts.py) | 관계 추출의 기본 프로토콜 지침을 정의합니다. Project 프롬프트 저장소와 구분됩니다. |
| [ingestion.py](ingestion.py) | 청크별 bounded 임베딩, 중복 제거, 캐시·이전 벡터 재사용과 진행 전달입니다. |
| [cache.py](cache.py) | 명시적인 용량 안에서 불변 세대의 코퍼스 읽기 사본을 보관합니다. |
| [jobs.py](jobs.py) | 등록 Job, 청크·추출 checkpoint와 명시적 재개를 관리합니다. |
| [status.py](status.py) | 저장과 공개 소비자가 공유하는 RAG Job 상태값의 단일 선언입니다. |
| [job_storage.py](job_storage.py) | 필수 Job 목록·리비전·worker lease identity와 원자적 저장을 소유합니다. |
| [files.py](files.py) | 세대 구성 시 파일 복사/reflink 등 Linux 파일 처리를 돕습니다. |
| [indexing.py](indexing.py) | 문서 벡터를 Chroma 색인으로 구성합니다. |
| [graph_indexing.py](graph_indexing.py) | Kuzu 엔티티·관계·출처 색인을 구성합니다. |
| [search.py](search.py) | BM25/vector/hybrid 검색, RRF 결합과 섹션 확장입니다. |
| [graph_search.py](graph_search.py) | 검색 문서·엔티티에 연결된 그래프 증거를 탐색합니다. |
| [tools.py](tools.py) | rag_search와 rag_list_documents Tool의 schema와 handler입니다. |

## 시작하기

RAG는 분할·검색·세대 공개를 소유하고, 모델 요청 인자는 선택 모델 클라이언트가 소유합니다.
`embedding_params`, `document_kwargs`, `query_kwargs`, `rerank_params`, `extraction_params`는
해당 클라이언트의 `describe_config()`/`with_config()` 계약으로 검증·전달합니다.
RAG는 `model`, `dimensions`, `api_base` 같은 child-private 필드를 직접 해석하지 않습니다.
새 클라이언트는 자체 schema와 공개 메서드를 제공하면 되며 중앙 registry는 필요 없습니다.
일반 record CRUD는 `metadata`만 허용합니다. 문서는 아래 document API로 등록합니다.

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
updated = await rag.aupdate_document(
    "manual", content="# 실행\n설정 파일을 검증한 뒤 실행합니다.",
    expected_generation=document["generation"],
    expected_revision=document["revision"],
)
await rag.adelete_document("manual", expected_generation=updated["generation"],
                           expected_revision=updated["revision"])
```

문서의 `generation`은 생성 시 고정되며 `revision`은 수정마다 증가합니다. CAS에는 읽은 두 값을
함께 전달합니다. 하나만 전달하면 오류이며 삭제·재생성된 같은 ID에 과거 편집을 적용할 수 없습니다.
둘 다 생략한 무조건 수정·삭제 API도 유지하므로 UI에서는 위와 같이 쌍을 전달하세요.
코퍼스 색인 공개 generation과 문서 generation은 별개입니다. 검색 결과와 백업·복제도 문서의
쌍을 보존합니다. generation 없는 옛 저장 자료는 자동 변환하지 않습니다.
[Revision 계약](../../../docs/llm/revisions.md)을 참고하세요.

문서 API는 자체적으로 Run을 생성하지 않습니다. Engine/Tool 안에서 호출하면 기존 Run의 관찰 경계를 사용합니다. `rag` 선택 시 tools capability에 `rag_search`, `rag_list_documents`를 제공하므로 Loop나 Graph ToolNode가 검색·목록을 조회할 수 있습니다. 문서 CRUD 전체가 자동으로 모델 Tool로 노출되는 것은 아닙니다.

## 설정과 임베딩

설정은 `ProjectConfig.parameters.components.rag`에 저장한다.
`describe_config()`는 허용 형식만 제공한다. [전역 설정 계약](../../CONFIGURATION.md)을 따른다.

추출 실패 정책과 JSON 모드의 허용 값은 `prompts.py`의 `ExtractionFailurePolicy`와
`ExtractionJSONMode`가 소유한다. Schema, 추출 실행과 Hub 생성 프리셋은 같은 선언의
문자열 값을 사용한다. 설정 누락의 의미와 `best_effort`가 허용하는 실패 범위는 유지한다.

| 키 | 미설정 동작 |
|---|---|
| config.chunk_size / policy.embedding_concurrency | 문서 등록 시 configuration error (concurrency 범위 양의 정수) |
| policy.extraction.failure_policy / config.extraction_batch_size | 실패 정책은 명시; 추출을 켠 경우 batch_size도 명시 |
| config.search: method/expand/limit/candidate_count/rrf_constant | 검색 전 명시; 결합 검색은 max_hops/relation_limit도 필요 |
| config.embedding_cache_max_bytes / config.query_cache_max_bytes / config.snapshot_cache_max_bytes / config.search_cache_chars | 해당 캐시 비활성 |
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

`RAGComponent.initialize`는 새 저장소에서만 `identity.json`, 명시적인 빈 `active.json`과
필수 `corpus.manifest.json`을 생성합니다. RAG owner는 현재 version·Project/Component 소유권과
identity/active/corpus/catalog revision을 검증합니다. 목록은 정확한 필드·타입·정렬/중복·revision을
검사하며 코퍼스 본문을 파싱하지 않습니다. snapshot은 코퍼스와 목록 투영의 일치도 검사합니다.
게시 직전 현재 원본을 재검증하고 새 catalog/corpus·active·manifest를 같은 workspace transaction으로
확정합니다. 유효한 형식의 이전 포인터나 다른 identity도 원래 연결 증거와 다르면 거부합니다. 기존 저장소의 파일·필드 소실은 조회·재선택·compact·백업에서 거부하며
identity 재발급이나 빈 코퍼스 복구를 하지 않습니다. 세대가 남아 있는데 포인터가 null인
경우도 정리를 허용하지 않습니다. [영속 수명주기 계약](../../../docs/llm/data-lifecycle.md)과
[단계별 검증 범위](../../../docs/llm/lifecycle-verification.md)를 따릅니다.

### 조회 최적화

`config.query_cache_max_bytes`는 query embedding 벡터, `config.snapshot_cache_max_bytes`는
코퍼스 조회 사본의 메모리 용량입니다. 둘 다 명시한 경우에만 활성화되며 0은 비활성입니다.
기본 용량·자동 확대·숨은 문서 수 제한은 없습니다. `aconfigure`는 설정 전체 교체이므로
기존 설정을 유지하려면 읽은 값에 필요한 키를 병합해서 전달합니다.

Query 캐시는 Project, 모델 identity, 실제 모델 인자, document/query kwargs, 호출 구현과
질문 해시를 구분합니다. hit도 finite/nonzero 벡터와 모델/코퍼스 차원을 검증합니다.
실패·취소 결과는 보관하지 않으며 검색 결과나 rerank 결과는 캐시하지 않습니다.
모델 호출이 필요한 miss는 기존 ProviderCalls·사용량 관찰 경로를 그대로 사용합니다.

코퍼스 캐시는 세대·코퍼스 identity·필수 corpus revision·파일 경로와 stat 정보를 함께 검사합니다. 파일 삭제·교체나
수정은 캐시로 숨기지 않습니다. 원본은 계속 `corpus.json`이고 반환값은 독립 사본입니다.
query/rerank 모델 호출 전후에는 게시 revision과 corpus/catalog의 파일 정보를 다시 비교하고
필수 색인 존재를 확인합니다. 소실·교체·변조는 결과 반환 전에 거부하며 코퍼스 본문은 반복
파싱하지 않습니다. 정상 조회의 코퍼스 1회 파싱과 기존 세대/설정 충돌 계약을 유지합니다.
캐시 용량은 보관하는 Python payload 크기로 계산하며 프로세스 전체 RSS 제한을 뜻하지 않습니다.
코퍼스가 용량보다 크면 보관하지 않습니다. 재시작하면 두 메모리 캐시는 사라집니다.
저장 형식을 분할하거나 기존 데이터를 자동 변환하지 않습니다.

### 변경된 부분만 준비

본문과 준비 fingerprint가 동일하고 이전 추출이 완전하면 제목·설명·메타데이터 수정은
임베딩·관계 추출을 호출하지 않습니다. 문서 revision은 증가하고 새 코퍼스 세대를 공개합니다.
벡터·그래프가 동일하면 복사한 DB의 내용 갱신도 생략합니다. 원본 세대는 수정하지 않습니다.
모델·프롬프트·추출 설정·분할 설정이 달라졌거나 이전 추출이 불완전하면 일반 준비 경로를 사용합니다.

본문 수정 시 성공한 추출 배치의 입력 청크(순서·ID·제목·텍스트), 추출 구현·인자·프롬프트·정책이
동일한 경우 관계를 재사용합니다. `extraction_manifest`에는 배치 signature, 엔티티 ID와 검증
digest만 저장하며 그래프 원문을 중복 저장하지 않습니다. 재사용 시 정확한 인용과 digest를 검증하고
기존 extracted_at을 유지합니다. 실패한 best_effort 배치는 manifest에 넣지 않습니다.
청크 위치/ID가 바뀌면 보수적으로 다시 추출하며 ID를 추측하여 재매핑하지 않습니다.
manifest/fingerprint가 없거나 일치하지 않으면 재계산하며 저장 데이터의 자동 변환은 없습니다.
커스텀 모델 구현의 의미를 변경하면 해당 구현의 공개 identity/params도 변경해야 합니다.

### 일괄 등록

`aadd_documents(documents)`는 호출자가 명시한 문서 목록을 준비한 후 **한 세대**로 공개합니다.
각 항목은 `identifier`(선택), `title`, `content`, `metadata`(선택), `description`(선택)입니다.
생성 시 identifier/metadata의 부재 또는 None에만 초기값을 적용하며 False/0/잘못된 타입은
거부합니다. 저장한 입력에서는 RAGComponent.INPUT_FIELDS의 모든 필드가 필수이며,
RAGComponent.validate_input와 JobStorage가 Job의 문서 수·순서·ID·digest를 함께 검증합니다.
description은 부재/None/문자열을 허용합니다. 저장 Reader·claim·재개 계획·backup/clone에서
소실된 필드를 생성 기본값으로 채우지 않고 원본을 보존하며 거부합니다.
새 문서 생성만 지원하며 중복 ID나 이미 등록된 ID를 덮어쓰지 않습니다.

```python
documents = [
    {"identifier": "guide", "title": "Guide", "content": "# Guide\n...", "description": "사용 지침"},
    {"identifier": "reference", "title": "Reference", "content": "# Reference\n..."},
]
saved = await rag.aadd_documents(documents)
```

문서는 순차 준비하고 각 문서 내부 임베딩에 명시된 `policy.embedding_concurrency`를 적용합니다.
별도 문서 동시성이나 자동 batch size는 만들지 않습니다. 호출자가 한 번에 전달하는 목록이 공개 단위입니다.
기존 RAG `required`/`best_effort`/`disabled` 추출 정책은 그대로 적용됩니다.
준비 실패·준비 중 취소·최종 CAS 충돌이면 일부 문서만 공개하지 않습니다.
동기 저장 트랜잭션이 이미 시작된 뒤 취소되면 기존 StorageIO 계약대로 완료까지 기다립니다.
이때 공개가 완료될 수 있으므로 호출 취소를 rollback 영수증으로 해석하지 않습니다.

재시작 가능한 일괄 등록은 `aenqueue_documents(documents)`로 **하나의 Job**을 만든 뒤
`arun_job(job_id)`를 호출합니다. 단건 Job API와 별개인 명시적 입력 형태이며 자동 묶음은 없습니다.

```python
job = await rag.aenqueue_documents(documents)
await rag.arun_job(job["id"])
# 실패 또는 프로세스 중단 뒤에는 호출자가 명시적으로:
# await rag.arun_job(job["id"], retry=True)
```

`retry`는 자동 재시도 정책이 아니라 해당 호출의 명시적 재시도 허가입니다.
공개 `arun_job`만 기본값 `False`를 소유하며 내부 실행기에 그대로 전달합니다.
`True`와 `False` 이외의 값(`None`, 숫자, 문자열 포함)은 잠금·상태 변경·모델 호출 전에 거부합니다.
`False` 또는 생략은 대기 작업만 실행하며, 실패·중단된 작업의 재실행을 허용하지 않습니다.

일괄 Job은 `document_ids`를 기록합니다. `batches/document_<position>.json`에 완료한 문서 준비 결과,
`batches/d<position>_embedding_<ordinal>.json`과 `d<position>_graph_<offset>.json`에 작업 중 조각을
보관합니다. 입력/설정 signature가 일치하는 결과만 재사용합니다. 모든 문서가 준비된 뒤 한 번 공개하고
Job 완료를 같은 저장 트랜잭션에 기록합니다. 완료 기록 실패로 트랜잭션이 롤백되면 준비 결과를
재사용해 모델 호출 없이 다시 공개합니다. 재개 시 동일한 준비 결과가 이미 공개되어 있으면
공개를 반복하지 않습니다. 재개 포기·삭제·worker lease는 단건 Job과 같은 계약입니다.
`job.ingestion.documents_total/documents_completed/document_index`로 문서 진행을 조회할 수 있으며
기존 `ajob_progress`는 현재 문서의 임베딩·추출 단계와 공개 단계를 계속 제공합니다.
기본 Tool 목록에 쓰기 Tool을 자동 추가하지 않습니다. Application 또는 신뢰한 Tool 구현이 API를 호출합니다.

LiteLLM에는 caching=False, cache={"no-cache": true, "no-store": true}를 전달한다.
llm VectorCache만 사용한다. 용량이 없거나 0이면 LRU를 끈다. fingerprint에는 single-chunk 계약 버전,
모델 identity, 유효 embedding params, document/query kwargs, 클라이언트 종류를 포함한다.
캐시 키에는 Project·런타임 함수 identity·text SHA-256도 반영한다. 모든 재사용 벡터를 검증한다.
문서 update는 같은 fingerprint의 동일 chunk vector를 이전 문서에서 재사용한다.
주입 클라이언트의 모델 의미를 바꾸면 embedding_id도 바꿔야 한다.

구 fingerprint는 일치하지 않으므로 다음 update에서 재계산한다. 기존 corpus는 자동 변경하지 않는다. 현재 graph_schema_version은 1이며 지원하지 않는 저장 형식은 거부한다. 이전 batch checkpoint를 새 청크 결과로 해석하지 않는다.
설정이 달라진 prepared Job은 새 Job으로 등록해야 한다.

RAG 생성 때 필수 jobs.manifest.json과 jobs 디렉터리를 확정한다. Job 생성은 입력·Job·목록과
identity가 있는 worker.lock을 함께 저장한다. 정확한 Job 집합·리비전·소유권을 검증하며
소실·미선언 기록을 빈 큐로 해석하지 않는다. 조회·재시도·maintenance는 lease를 만들지 않는다.
입력 소실은 재개 계획에서 missing/실행 불가로 보고하고 backup·clone은 거부한다.
유효한 Job 메타데이터 조회와 안전성이 확인된 명시 삭제는 기존 진단·삭제 계약을 따른다.

Job은 생성 시 미취소 False·미포기 None·빈 실패 이력 []·진행/관찰 객체를 명시한다.
다섯 상태값은 공개 `RAGJobStatus`가 선언하며 JobStorage의 허용 집합·전이 표와 RAGJobs,
Data·Hub 소비자는 이 선언을 참조한다. 저장 문자열과 전이 의미는 그대로이며 공개 상태값은
상태 변경·재개·삭제 권한을 부여하지 않는다. JobStorage는 필수 증거와 원자적 저장을 소유한다.
필드 소실을 이 초기 상태로 복원하지 않는다. 실행한 Job은 시작 시각·worker PID·설정 증거,
실패/실행 후 취소는 종료 시각과 완전한 진단을 요구한다. 실행 전 취소와 구분하며 기록·
목록을 같은 transaction으로 확정한다. 취소와 포기는 조회·재시작·재시도로 철회할 수 없다.
재시도 입장 거부는 실패 이력을 늘리지 않고 기존 진단을 보존한다. 명시된 Diagnostic source는
제공자의 관찰 출처를 유지하며 실행/재시도 허가는 Job 상태·취소·포기·lease/설정으로 검사한다.
이 필드/상태 검증은 조회·재개 계획·backup/clone과 Hub 소비자에 동일하게 적용한다.

긴 문서는 enqueue_document → arun_job을 권장한다. 실패 후 arun_job(id, retry=True)로 명시적으로
재개한다. 기존 서비스 소유 checkpoint 디렉터리 `jobs/<id>/batches/` 아래에
`embedding_<original_position>.json`을 쓴다. 이 디렉터리는 추출 checkpoint도 공유하므로 이름을 유지한다.
embedding 파일의 outer signature와 value의 fingerprint/text_hash/vector를 검증한다.
worker는 progress callback만 호출하며 RAGJobs가 workspace 잠금 아래 JobStorage에 저장을 위임한다.
완료 순서와 파일 번호는 무관하고 보존된 완료 checkpoint는 재개 시 호출하지 않는다.
Job 목록은 각 Job의 revision과 checkpoint별 hash를 소유한다. 준비/배치 원본과 목록을 같은
transaction으로 확정하며 아직 생성하지 않은 경우만 None이다. 선언된 원본의 소실·Null·변조나
미선언 추가는 재실행 허가가 되지 않는다. Reader·Writer·claim·재개 계획·backup/clone에서 검증한다.
배치 envelope는 필수 key/signature/value를 저장하고 key가 실제 파일 경로와 같아야 한다.
완료 영수증의 파일명만 바꿔 이미 수행한 chunk를 새 작업으로 오인하지 않으며, key가 없는
기존 기록도 자동 복원하지 않는다. Writer는 JobStorage.write_batch의 같은 선언을 사용한다.
embedding payload는 fingerprint/text_hash/vector와 실제 chunk/chunk_size를 필수로 저장합니다.
extraction payload는 graph와 실제 요청 chunks/chunk_size를 저장하며 인용을 그 원문과 대조합니다.
Component는 검증된 Job 입력을 저장된 chunk_size로 분할해 문서 위치·ordinal/offset·chunk를
확인합니다. 이 검증은 현재 모델 설정에 의존하지 않습니다. 기존 요청 signature 비교와 설정
변경 시 재개 거부는 유지하며, 백업 검증을 위해 모델 인자 전체를 복제하지 않습니다.
준비·게시 문서의 필수 원문·절/청크·벡터/profile·추출 manifest·저장 그래프 증거는 동일한 RAG
문서 owner가 검증한다. 게시 코퍼스는 정확한 root 필드·문서 ID·모든 문서/profile 일치를 요구한다.
빈 코퍼스만 명시 None profile을 허용한다. 새 세대 생성 전과 snapshot cache 저장 전에 원본을
검증하며 필수 필드 소실은 조회·추가/수정·compact·재선택·backup/clone에서 거부한다. JobStorage는 원래 입력 digest·문서 순서·내용·초기 revision·시도 configuration의 연결을
검증한다. 배치의 document_N 준비 영수증도 동일하다. hash가 맞아도 빈 문서·필수 필드 소실·
입력 불일치는 재개/backup/clone을 허가하지 않는다. metadata는 정규 JSON hash로 숫자/bool 타입도
구분한다. 실행 시도 없는 준비 기록은 거부한다.
임베딩 배치 영수증의 fingerprint/text_hash/vector 필수 필드·signature·유효 벡터는 ingestion owner가
생성/재사용과 JobStorage Reader/Writer/backup에서 함께 검증한다. 단건/배치 문서 위치도
RAGComponent가 확인하며 손상 payload를 비어 있는 새 작업으로 복원하지 않는다.
저장된 graph 배치는 extraction owner가 정규 entity/relation의 정확한 필드·ID·참조·필수 metadata를
검증한다. 저장 metadata 소실은 모델 응답용 기본값으로 복원하지 않는다. 준비 재사용은 이어서 실제
chunk의 원문 인용과 기존 signature/configuration을 검증한다.
비활성/불완전 그래프의 명시 진단과 정상 재시도의 기존 모델 효과 재사용은 유지한다.
공개와 완료 확정 후 별도 transaction으로 준비 자료를 정리한다. 정리 실패는 준비 자료와 목록을
함께 되돌리며 이미 확정된 공개는 유지한다. 실제 장애/중단 검증 범위는
[검증 기록](../../../docs/llm/lifecycle-verification.md)에 남긴다.

실패하면 새 청크 배정을 중단하고 진행 중 요청의 성공 checkpoint를 보존한다.
취소하면 모든 worker를 취소하고 종료를 기다린다. 완료 checkpoint는 유지하고 미완료 세대는
공개하지 않는다. 기존 active generation은 유지한다. 취소된 Job의 자동 재실행은 없다.
프로세스 재시작 시 LRU는 소실되지만 Job checkpoint와 이전 문서 벡터는 남는다.

`checkpoint_plan(job_id)`는 Run과 같은 `CheckpointPlan`을 반환한다. 실패·대기 Job은
`abandon_job(job_id, expected_revision=...)`으로 재개를 포기하고 새 계획을 확인한 뒤
`delete_job(job_id, expected_revision=...)`으로 입력·체크포인트·Job 기록을 정리할 수 있다.
완료·취소 Job은 포기를 생략한다. 살아 있는 worker lease는 삭제를 막으며 공개 문서·색인은 유지한다.
각 API는 `a` 접두사의 비동기 버전도 제공한다. [공통 계약과 예제](../../../docs/llm/checkpoints.md)를 참고한다.

`ingestion_records.py`의 EmbeddingCheckpoint는 단일 청크 영수증의 필드·서명·검증을,
EmbeddingProfile/EmbeddingEvidence는 저장된 모델·차원·벡터 조회를 소유한다.
BatchCheckpointName은 임베딩/그래프 배치 이름을 소유하며, 실제 원본 청크와의 대조는
Component가 계속 수행한다. 누락된 영수증 필드는 채우지 않는다.
IngestionObservation은 통계·진행 Projection을 소유한다. 메타데이터만 변경하는 준비 결과는
기존의 별도 필드 집합을 유지하며 일반 임베딩 통계를 기본값으로 추가하지 않는다.

`job()["ingestion"]`, 문서 ingestion, diagnostic_scope로 다음을 관찰한다:
chunks_total/completed/reused/cached/requested, embedding_concurrency, embedding_seconds,
provider_retries, checkpoint_reuse, active_embedding_requests, peak_embedding_requests.
requested는 중복 제거 후 새 단일 청크 요청 수(재시도 제외), reused는 이전 문서 및 중복 위치 수,
cached는 LRU로 채운 위치 수, checkpoint_reuse는 읽은 완료 checkpoint 수다.
embedding_seconds는 준비 단계 벽시계 시간이다. active/peak는 admission 대기까지 포함한 청크 호출 수로,
실제 SDK 실행 동시성은 ProviderCalls 한도가 더 낮을 수 있다.

## 추출과 그래프 실패

관계 검색은 `config.search.relation_ranking`을 지정하지 않으면 출처/ID의 안정 순서를
사용합니다. `"source"`는 그 순서를 명시하고, `"support_count"`는 각 탐색 frontier 안에서
근거 개수 내림차순을 선택합니다. 문서 hit 순서와 BFS 깊이의 계약은 유지합니다.
전역 최적 경로/최대 신뢰 관계를 자동 선택하지 않습니다.

반환 관계의 `support_count`는 동일 정규화 관계를 지지하는 서로 다른 문서/청크 쌍의
개수이며 진실 확률이나 품질 점수가 아닙니다. 내부 graph JSON/Kuzu의 기존 `weight` 열은
같은 정수 통계로 유지하므로 graph_schema_version=1과 저장 데이터는 변경하지 않습니다.
응답에 옛 weight 별칭은 추가하지 않습니다. 정렬 설정 변경은 색인 내용을 바꾸지 않습니다.

`method/expand/limit/candidate_count/rrf_constant/max_hops/relation_limit`는 호출자가
명시합니다. backend가 검색 품질 임계값을 선택하지 않습니다. `RELATION_TYPES`와
`default_prompt()`는 앱/예제가 명시적으로 선택하는 샘플이며 자동 적용하지 않습니다.
의미 지침·few-shot·선호 ontology는 `config.extraction.prompt_id/relation_types`로 지정합니다.
기계적인 JSON/엔티티 ID/evidence/출처 검증은 교체할 수 없는 backend 계약입니다.

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
