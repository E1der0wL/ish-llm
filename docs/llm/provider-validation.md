> 현재 설정 계약은 [CONFIGURATION.md](../../llm/CONFIGURATION.md)를 따른다. 아래 이전 측정 수치는 해당 명시적 fixture에 대한 기록이다.

# 단일 청크 임베딩 실행 검증

이번 변경은 llm/ 및 허용된 루트 테스트 파일에 한정한다.
Project → Session → Run → Step, RAG atomic generation publish,
TripleExtractor의 검증·repair·graph schema version 1와 reranker index 계약은 유지한다.

## 실행 계약

1. providers/runtime.py의 litellm_sdk()에서 import 전에 DEFAULT_MAX_RETRIES 환경변수를
   항상 문자열 "0"으로 설정하고, import 후와 재진입 시 SDK 전역값도 정수 0으로 복구한다.
   토큰 계산기도 같은 초기화 경계를 사용한다.
2. request의 num_retries/max_retries는 삽입·수정하지 않는다. effective_attempts()는
   명시적 SDK retry가 켜지면 외부 1회만 허용한다. 생략/0이면 provider.max_attempts를 쓴다.
   주입 client·retry_policy·호스트 num_retries 정책에도 외부 retry를 중첩하지 않는다.
3. RAG embedding ordering does not depend on provider-reported embedding indexes.
   Each document chunk is embedded independently. The application owns the chunk ordinal
   and restores results to that position. Embedding concurrency is bounded by configuration.
4. embedding_concurrency 명시 필수, 정수 양의 정수. 고정 개수 asyncio worker가 text별 한 요청을 수행한다.
   문서의 청크 수만큼 Task를 만들지 않는다. ProviderCalls와 사용량 관찰도 그대로 통과한다.
5. extract_single_embedding은 data list 길이 1과 유한·비영·nonempty·동일 차원을 검사한다.
   index는 읽지 않는다. query도 동일하다. 기존 corpus와 다른 청크 차원도 공개 전에 검사한다.
6. VectorCache와 unchanged reuse의 fingerprint에는 새 단일 청크 계약, 모델, 유효 params,
   document/query kwargs를 넣는다. text SHA-256으로 구분하고 모든 캐시 벡터를 검증한다.
   동일 text는 한 번 요청하여 모든 원래 위치에 배치한다. 이전 fingerprint는 재계산한다.
7. Job checkpoint는 기존 서비스 소유 디렉터리 jobs/<id>/batches/embedding_<ordinal>.json에
   signature + {fingerprint, text_hash, vector}로 저장한다. progress → RAGJobs → 잠금/atomic_json
   경계를 유지한다. 그래프 checkpoint의 위치·내용은 바꾸지 않는다.
8. 실패하면 새 청크를 배정하지 않고 진행 중 성공 checkpoint를 보존한다. 취소하면 worker를
   모두 취소·회수한다. CancelledError는 retry하지 않는다. 완료 checkpoint와 기존 활성 세대를 유지한다.

## 제거 및 설정 변경

- embedding_batch_size, embedding_batching의 max_batch_size/max_batch_chars/max_split_depth/cache_max_bytes.
  구 설정은 검증 오류로 안내하고 concurrency로 재해석하지 않는다.
- batches(), embed_batch(), split_batch(), embedding_<offset>_split checkpoint 작성.
- validate_embeddings(), normalize_litellm_embeddings(), merge_positions와 embedding index 정렬/정규화 진단.
- embedding_batches_total/completed, batch_splits, batch_items와 index_normalizations 통계.

새 cache 크기는 embedding_cache_max_bytes(미설정/0이면 비활성)다.
extraction_batch_size/index_batch_size는 관계 추출/DB I/O용이므로 유지한다.
LiteLLM cache는 caching=False + no-cache/no-store로 계속 차단한다.
임베딩 저수준 EmbeddingModel은 LiteLLM 응답을 반환하는 확장 API로 유지하며,
단일 청크 계약은 RAGComponent 문서·검색 경로가 적용한다.

## 회귀 테스트

- DEFAULT_MAX_RETRIES import 전 환경 0, 기존 환경 5 덮어쓰기, SDK 전역 0, 외부 변경 복구.
- num_retries=2/max_retries=3 그대로 전달, 생략 시 키 미삽입, SDK·llm retry 비중첩.
- 10개 청크 지연/완전 역순 완료, index 0 또는 누락, data 길이 0/2 거부.
- concurrency 1/2/4, 고정 worker 수, provider 제한 2, Facade를 통한 공유 admission.
- NaN/Inf/zero/dimension, 캐시·checkpoint 손상, 기존 corpus 및 query 차원 불일치 거부.
- 동일 text 중복 제거, VectorCache hit, unchanged update, fingerprint 변경 시 재계산.
- 일부 청크 실패 후 완료된 0/1/3 재사용·2만 재호출, 파일 ordinal과 완료 순서 분리.
- 취소 시 완료 파일 보존·worker 회수·슬롯 반환·active generation 불변.
- 기존 reranker의 index 기반 재정렬·Graph Tool 호출 검증 유지.

Linux Python 3.12.14, LiteLLM 1.103.1, OpenAI 2.54.0.
Linux 파일시스템 사본과 SHA-256 manifest를 tests/llm/run_linux.py로 생성한다.

```bash
/home/user/.cache/ish-provider-sdk-fbmj8dmh/.venv-linux312/bin/python \
  /mnt/d/WorkSpace/ish/tests/llm/run_linux.py --source /mnt/d/WorkSpace/ish \
  --python /home/user/.cache/ish-provider-sdk-fbmj8dmh/.venv-linux312/bin/python --full
# 생성된 Linux snapshot에서
.venv-linux312/bin/python -m tests.llm.provider_probe --output /tmp/chunk-embedding-probe.json
```

## 최종 실행 결과

2026-10-01 최종 결과: **루트 852개 + Provider/RAG 34개 = 886개 통과**, 실패·skip 0.
루트 suite 260.160초, 최종 집중 검사 12.408초.
전체 suite 사본: `/home/user/.cache/ish-provider-90bv2m3x`.
최신 HTTP probe 포함 사본: `/home/user/.cache/ish-provider-bw07r054`.
후자와 현재 Python/JSON 소스의 SHA-256 일치를 확인했다.

165줄 `examples/llm/data/graph_rag_165.md`, chunk_size=1000, 요청당 fixture 지연 30ms.
실제 LiteLLM aembedding HTTP와 파일 Job checkpoint, Chroma/Kuzu를 사용했다.

| 동시성 | 청크 수 | embedding HTTP 요청 | embedding 시간 | 전체 등록 시간 | cold cache/reuse | retry | warm cache |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 13 | 13 | 1.069초 | 2.559초 | 0 / 0 | 0 | 13 |
| 2 | 13 | 13 | 0.855초 | 1.089초 | 0 / 0 | 0 | 13 |
| 4 | 13 | 13 | 0.736초 | 0.971초 | 0 / 0 | 0 | 13 |

실제 peak 청크 요청은 1/2/4, 완료 후 active는 모두 0. 최초 실행에는 라이브러리 초기화 비용도
포함되어 있으므로 전체 등록 시간 비율을 순수 동시성 효과로 해석하지 않는다.
이 측정의 예제는 저사양 환경을 고려해 **2**를 명시했다. 라이브러리는 기본 동시성을 생성하지 않는다. warm 등록은 embedding HTTP 요청 0회다.

추가 HTTP 검사:

- retry 인자 생략 + provider.max_attempts=2: 실패 HTTP 요청 정확히 2회.
- num_retries=0/max_retries=0 + provider.max_attempts=2: 정확히 2회.
- 명시적 max_retries=1 + provider.max_attempts=4: SDK 내부 retry 포함 2회(곱해지지 않음).
- SDK cache read/write 0회, sync/async 임베딩 동일.
- JSON mode null 응답 두 번 후 plain JSON fallback 성공: 3회.
- Graph/RAG/rerank 23개 체크와 별도 CLI 23개 체크 모두 통과.
- reranker의 역순 index 매핑 확인. 직접 검색과 두 Graph Agent에서 rerank HTTP 각 3회.

원본 로그/수치는 Git 제외 경로 `tests/llm/reports/`의 focused.txt, suite.txt,
chunk-embedding-http.json, chunk-embedding-http.txt, graph-rag-report.json,
embedding-source-verification.json에 기록했다.

## 한계

비교 실행은 실제 LiteLLM HTTP와 파일 Job/Chroma/Kuzu를 사용하되 응답은 로컬 고정 fixture다.
따라서 사내 모델의 정확도·처리량·2GB corpus 성능·장시간 운영을 입증하지 않는다.
SDK 내부 HTTP retry는 하나의 SDK 호출로 관찰되어 provider_retries에 포함되지 않는다.
임의 주입 클라이언트가 내부적으로 직접 SDK를 호출하거나 취소를 무시하면 같은 제어를 보장하지 않는다.
다른 코드의 SDK 전역값 변경은 진입 시 복구하지만 실행 도중 경쟁까지 막지는 못한다.
이전 batch 설정/미완료 Job을 자동 변환하지 않으며 필요한 경우 설정 수정·새 Job 등록이 필요하다.

## 변경 파일

- llm/providers/runtime.py, retry.py, requests.py, embeddings.py, calls.py
- llm/components/rag/_client.py, component.py, data.py, ingestion.py, jobs.py
- llm/services/api.py, lifecycle/components.py, runtime/policies.py
- tests/llm/test_provider_runtime.py, test_rag_resilience.py, provider_probe.py
- tests/llm/test_rag_components.py, test_settings_consistency.py, test_project_component_settings.py
- examples/llm/graph_rag.py, graph_rag.config.example.json, configuration_workflow.config.example.json
- llm/providers/README.md, docs/llm/provider-validation.md, llm/components/rag/README.md, examples/llm/graph_rag.md
