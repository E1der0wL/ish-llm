# RAG 등록 안정화와 저사양 설정

설정은 `ProjectConfig.component_configurations.rag`에 저장한다. 컴포넌트의
`configuration_schema()`가 UI에 같은 키·타입·기본값을 제공한다.

| 키 | 타입 | 기본값 |
|---|---|---|
| `provider.max_attempts` | integer 1–10 | 2 (최초 호출 포함) |
| `provider.wall_timeout` | number > 0 | 120초 |
| `provider.delay_seconds` | number >= 0 | 0.25초 |
| `provider.max_delay_seconds` | number >= 0 | 2초 |
| `embedding_batching.max_batch_size` | integer >= 1 | 128 |
| `embedding_batching.max_batch_chars` | integer >= 1 / null | null |
| `embedding_batching.max_split_depth` | integer 0–8 | 0 (분할 끔) |
| `embedding_batching.cache_max_bytes` | integer >= 0 | 16,777,216 |
| `extraction.json_mode` | `strict` / `auto` / `off` | `strict` |
| `extraction.failure_policy` | `required` / `best_effort` / `disabled` | `required` |

모델별 `num_retries`/`max_retries`는 LiteLLM에 그대로 전달하고 생략하면 SDK 설정을 따른다.
SDK retry가 활성화되었거나 기본 정책이 불명확하면 llm invoke는 한 번만 실행하여 중첩을
방지한다. SDK retry가 없는 경우만 `provider.max_attempts`를 적용한다.
임베딩 공급자 라우팅은 모두 LiteLLM `aembedding`이 담당한다.

기존 `embedding_batch_size`와 새 `max_batch_size` 중 작은 값으로 배치한다. 문자 수 제한도
함께 적용한다. 한 청크가 문자 제한보다 크면 조용히 자르지 않고 설정 오류로 거부한다.
`chunk_size`도 함께 줄여야 한다. timeout/502/503/504만 제한 깊이의 이등분을 허용한다.
인덱스·NaN·Inf·0 벡터·차원 오류와 429는 분할하지 않는다. 깊이 d의 최대 분할 트리는
2^(d+1)-1개 batch 호출이며 각 호출은 provider 시도/시간 한도가 따로 있다.

SDK 캐시는 사용하지 않는다. llm 메모리 LRU에는 원문이 아닌 fingerprint와 검증된 벡터를
저장한다. Project/클라이언트/모델/실제 인자/텍스트 해시로 구분하고 partial hit의 원래
위치를 llm이 직접 복원한다. 용량 0이면 LRU를 끈다. 문서 update는 이전 세대에 저장된
동일 청크 벡터도 재사용한다. 모델·인자·차원 검증이 맞아야 하며 모델/설정 변경 시 재사용하지
않는다. 임의 주입 클라이언트의 모델 의미가 바뀌면 개발자가 `embedding_id`도 바꿔야 한다.

2026-10-01 fingerprint 구성 변경 이전에 저장된 벡터는 새 fingerprint와 일치하지 않아
다음 update에서 재계산된다. 기존 corpus와 검색은 보존하며 저장 버전은 바꾸지 않는다.
이전 작업과 현재 설정의 fingerprint가 다른 Job은 새 Job으로 등록해야 한다. 완료 checkpoint를
새 모델 설정의 결과로 간주하거나 기존 저장 내용을 자동 마이그레이션하지 않는다.

긴 문서는 `enqueue_document` → `arun_job`을 권장한다. 실패하면 같은 Job을
`arun_job(id, retry=True)`로 재개한다. 완료 batch와 분할된 하위 batch를 검증 후 재사용한다.
분할 결정도 `embedding_<offset>_split.json`에 기록하므로 이미 분할한 큰 요청은 다시 보내지 않는다.
재시작 후 자동 모델 호출은 없다. 등록 요청의 설정이 달라졌다면 새 Job을 만들어야 한다.
`job()["ingestion"]` 및 `diagnostic_scope`에서 청크/문자/배치 수, 시간, cache hit,
checkpoint reuse, provider retry를 볼 수 있다. 원문이나 API 키는 진단에 넣지 않는다.
프로세스 종료 시 메모리 LRU는 사라지지만 Job checkpoint/기존 문서 벡터는 남는다.

## 추출과 그래프 실패

- `strict`: 항상 JSON mode 사용.
- `auto`: 빈 응답/공급자 파싱 오류에만 JSON mode 없는 경로로 한 번 전환.
- `off`: `response_format`을 완전히 제외. 프롬프트와 Python 검증으로 JSON 계약을 유지.

400/인증/잘못된 모델 오류에서는 JSON mode를 임의로 끄지 않는다. 명시적으로 off를 선택한다.
JSON/그래프 의미 오류는 기존 `repair_attempts`(기본 2)로만 수정한다. 모든 내장 추출 호출은
temperature=0이다. Python이 이름 기반 ID·관계 타입·중복·가중치·출처를 정규화한다.
대명사의 의미를 해소해도 evidence는 원문의 정확한 부분 문자열이어야 한다.
두 번째 별도 LLM 정규화 단계는 추가하지 않았다.

`required`는 실패하면 기존 세대를 유지한다. `best_effort`는 성공한 batch의 검증된 관계만
사용하고 `graph_complete=false`, `graph_diagnostics`를 문서에 저장한다. `disabled`는
추출 호출 없이 빈 그래프 DB와 문서 색인을 함께 공개한다. 검색 응답의 `graph_complete`와
`graph_incomplete_documents`로 누락 가능성을 알린다. 실패/취소한 미완료 generation은
active가 되지 않는다. 기존 graph schema version 2는 그대로 유지한다.

`rerank` Tool 인자는 실제 reranker가 없으면 `const:false`로 제공하며 handler에서도 False를
강제한다. 공개 Python 검색 API에서 명시적으로 rerank=True를 요청하면 설정 오류를 반환한다.

자세한 공급자 경계·제한은 [Provider 설명](../../providers/README.md)을 참고한다.
