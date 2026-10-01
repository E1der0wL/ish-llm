> 모든 알고리즘 설정은 [명시적 설정 계약](../CONFIGURATION.md)을 따른다. 동봉 JSON의 값을 명시해 실행한다.

# 사내 GraphEngine + RAG 통합 테스트

`graph_rag.py`는 실제 LiteLLM 모델과 Chroma/BM25/Kuzu를 함께 실행한다.
API 키는 환경 변수가 아닌 JSON을 역직렬화한 `ProjectConfig`에서 읽는다.
각 실행은 별도 테스트 Project를 만들며 기본 Project와 원본 Markdown 파일은 변경하지 않는다.
`llm/` 안에 있으므로 이 폴더만 ish에 배포해도 사용할 수 있다.

## 설정

`graph_rag.config.example.json`을 로컬의 `~/graph-rag-config.json`으로 복사한 뒤
다음 네 곳의 model, api_base, api_key를 수정한다. 실제 값은 생성된 Project에도 저장된다.

| ProjectConfig 위치 | 용도 |
| --- | --- |
| `completion` | Graph 노드의 Agent가 사용하는 대화 모델. 스트리밍과 JSON 답변 생성 필요; 검색 Tool은 Graph가 실행 |
| `component_configurations.rag.embedding_params` | 문서·질의 벡터를 생성하는 임베딩 모델 |
| `component_configurations.rag.extraction_params` | 문서에서 엔티티·관계·인용을 추출하는 모델. JSON 응답 지원 필요 |
| `component_configurations.rag.rerank_params` | 검색 후보의 관련성 점수를 계산하고 순서를 재정렬하는 모델 |

OpenAI 호환 서버는 `openai/모델명`을 사용한다. 다른 provider는 해당 LiteLLM 모델명을
사용하고 필요 없는 api_base를 삭제한다. 임베딩과 대화 모델은 서로 다른 서버·키를 써도 된다.
rerank는 OpenAI chat/embedding API와 별도 프로토콜이다. 예제의 `cohere/모델명`과
`https://서버/v1/rerank`는 Cohere 호환 rerank 서버용이다. 실제 서버가 지원하는 LiteLLM
rerank provider/URL로 설정해야 하며, 대화 모델의 `openai/` 접두사를 그대로 복사하지 않는다.
이 예제는 reranker 설정이 없으면 Project를 만들기 전에 실패한다. 라이브러리 자체에서는
rerank가 여전히 선택 기능이다. 이 통합 예제에는 `search.rerank=true`도 명시해야 한다.
Graph의 `retrieve`는 query만 Tool에 전달하고 나머지는 Project 검색 설정을 상속하며,
직접 비교용 BM25/vector/hybrid 검사에서는 명시적으로 false를 전달한다.
공급자별 문서/질의 옵션이 필요하면 RAG 설정에 `document_kwargs`, `query_kwargs`를
추가할 수 있다. 모델이 실제로 지원하는 옵션을 사용한다.

## 관계 추출과 수정

추출용 기본 프롬프트는 few-shot을 포함하며 테스트 Project의
`prompts/records/rag-triples.json`에 저장된다. `--extraction-prompt /path/prompt.json`으로
`{"messages":[{"role":"system","content":"..."}, ...]}` 형식의 사용자 정의를 넣을 수 있다.
JSON 구조·엔티티 참조·원문 인용 검증은 사용자 프롬프트와 무관하게 유지한다.
`component_configurations.rag.extraction.prompt_id`가 이 레코드를 선택한다.

`component_configurations.rag.extraction.repair_attempts`는 **추가 수정 호출 횟수**이며
예제에서 2를 명시하며, 미설정/0이면 수정 재호출을 하지 않는다. 검증 오류·직전 JSON·원본 chunks를 다시 전달한다.
연결 오류·취소·사용량 한도는 이 수정 루프로 재시도하지 않는다. 각 호출은 사용량에 포함된다.
예제는 extraction_params.temperature=0을 명시한다. 라이브러리는 temperature를 강제하지 않는다.
`engines.loop` 설정과 답변 검증 `--max-attempts`에는 영향을 주지 않는다.

`extraction.relation_types`로 선호 타입 목록을 지정한다. 예: USES, DEPENDS_ON,
PART_OF, CONFIGURES 등이며 뜻이 맞지 않으면 새로운 타입도 허용한다.
대소문자만 다른 지정 타입은 정규화하지만 의미·방향이 다른 관계를 임의 병합하지 않는다.

관계 검색 결과에 `document_id`, `source_id`, `evidence`, `metadata`, `extracted_at`,
`weight`를 반환한다. 시각과 문서 ID는 프로그램이 부여한다. weight는 같은 정규화된
트리플을 뒷받침하는 **서로 다른 문서/청크 쌍의 수**다. 모델의 확신도나 진실 확률이 아니다.
동일 응답 중복·재시도·동일 문서 재등록 횟수는 가산하지 않고 수정/삭제 시 재계산한다.
문서 graph 원본에는 문서 내부 가중치, Kuzu 검색에는 현재 코퍼스 가중치를 제공한다.
검색 문단 우선순위를 유지하며 같은 문단/탐색 노드 내에서 높은 가중치를 우선 반환한다.

Kuzu 속성이 추가되어 RAG `corpus.json`의 `graph_schema_version=2`가 필요하다.
이전 RAG 색인은 수정하지 않고 명확한 오류로 거부한다. 새 테스트 Project에 원문을 다시 등록한다.
Project/Session/Run/Step의 `storage_version=2`와는 별도 버전이다.

`engines.loop.max_iterations`는 Agent 한 번의 모델 호출 반복 상한이다.
`--max-attempts`는 답변 검증·수정 회차 상한이며 두 제한은 서로 다른 범위를 가진다.
검색 횟수와는 무관하다. 첫 답변에 실패해도 검색·rerank 결과는 그대로 재사용한다.
실행 시 지정한 모델의 API 비용이 발생하며 등록/수정 시 임베딩과 관계 추출을 다시 호출한다.

## ish에서 실행

최신 llm 폴더를 `config.PLUGIN_SCRIPT_DIR / "llm"`에 배치한다.
다음 코드를 실제 `.ishrc.py`에 추가하고 ish를 다시 시작한다.

```python
from functools import partial

llm_plugin = plugin.get("llm")
if llm_plugin is not None:
    from llm.examples.graph_rag import main as graph_rag_test

    prompt.set_tool(
        "graph-rag-test",
        function=partial(
            graph_rag_test,
            "--config", "~/graph-rag-config.json",
            "--workspace", "~/.ish/graph-rag-test",
        ),
    )
```

```sh
# 내장 예제 문서: 문서 검색뿐 아니라 관계 검색 결과도 필수로 확인한다.
graph-rag-test

# 실제 사내 Markdown: 질의에는 문서에서 쓰는 고유명사/키워드를 포함한다.
graph-rag-test --markdown /home/user/manual.md --query "제품명 설정 파일의 필수 항목과 적용 순서를 설명해줘"

# 문서에 명확한 관계가 있어 관계 검색도 반드시 성공해야 하는 경우
graph-rag-test --markdown /home/user/manual.md --query "제품명과 저장소의 관계" --require-relations
```

별도 Python 환경에서 실행할 때는 llm 폴더의 부모를 import 경로에 두고 다음을 실행한다.
ish가 설치한 의존성을 외부 Python이 자동으로 공유하지는 않는다.

```sh
python -m llm.examples.graph_rag --config ~/graph-rag-config.json

# 배포에 포함된 165줄 공개 문서로 등록 → 검색 → rerank → Graph Agent까지 검사
python -m llm.examples.graph_rag --config ~/graph-rag-config.json \
  --markdown llm/examples/data/graph_rag_165.md --require-relations

# 저수준 RAG API의 직접 rerank도 추가 검사: 총 semantic rerank 요청은 2회
python -m llm.examples.graph_rag --config ~/graph-rag-config.json --standalone-rerank
```

## 수행하는 검증

1. RAG·Agent·Workflow 컴포넌트를 선택한 파일 저장 Project 생성.
2. Markdown 등록: 분할, 임베딩, 관계 추출, 실제 Chroma/Kuzu 색인.
3. 짧은 별도 문서로 생성·수정·revision 조회·삭제 검증. 본 문서는 유지.
4. BM25, vector, hybrid 검색 결과 확인. 범위·확장은 Project 설정을 상속하며 rerank=false로 직접 검사를 수행한다.
   삭제 문서의 출처가 남지 않는지 검사한다. 별도 `search_rerank` 단계는 `--standalone-rerank`일 때만 실행한다.
5. Agent와 Workflow JSON을 저장하고 공개 API로 다시 읽어 동일성 확인.
6. GraphEngine의 `retrieve` ToolNode가 `rag_search`를 한 번 실행하고 결과 전체를 `/evidence`에 저장한다.
   이후 `Agent → JSON/원문 인용 검증 → 분기 → 오류 피드백 → 답변 재생성`만 반복한다.
   검증에 성공하면 답변 출력 후 종료하고, 상한까지 실패하면 Run도 실패한다.
7. `node_id=retrieve`, `kind=tool`, `name=rag_search`로 Step을 찾고 유한한 rerank 점수와 원문·출처 보존을 확인한다.
   별도 검색의 동점 순위가 달라질 수 있으므로 저장된 문서의 chunk/metadata/revision/확장 문맥과 직접 비교한다.
   실제 모델이 기존 순서를 유지해도 정상이다. 고정 응답 검사에서는 역순 반환을 검증한다.
   Run·Step 완료 상태와 저장된 Assistant 응답도 확인한다.
8. 백엔드를 종료하고 새로 열어 Run·Step·저장된 검색 결과·Workflow·검색 색인을 조회한다. 재조회에는 rerank를 사용하지 않는다.

이전 구조는 `repair → answer Agent → rag_search/rerank → validate → retry`였다.
현재는 **retrieve once → answer repair many**다.

```text
retrieve / rerank (한 번)
        ↓
repair loop
 ├ answer (query + evidence + feedback)
 ├ validate
 ├ route → 성공이면 종료
 └ feedback → 같은 evidence로 answer 재생성
        ↓
publish
```

답변 Agent는 검색 Tool/resource와 require_tool 정책을 갖지 않는다. 검색 결과는 지시가 아닌 근거로만 사용한다.
JSON/인용 오류 수정 동안 `/evidence`를 변경하지 않으며, 근거 부족에 따른 자동 재검색도 하지 않는다.
기본 실행의 semantic rerank는 답변 시도 횟수와 무관하게 정확히 1회다. Provider retry/backoff는
기존 사용자 설정을 따르며 예제에서 횟수·지연을 변경하거나 고정 sleep을 추가하지 않는다.

문서 CRUD는 Component API에서 수행하므로 별도의 Run을 만들지 않는다.
Graph 실행 중 검색과 모델 호출은 하나의 소유 Run에 Step으로 기록된다.
엔진이나 예제 검증 노드가 도메인 persistence 파일을 직접 수정하지 않는다.

답변 검증은 JSON 형식, 답변 존재, 문서 ID, 인용문의 원문 일치를 확인한다.
**답변의 의미적 정확성이나 질문에 대한 충분성까지 자동 판정하지 않는다.**
실제 모델이 첫 시도에 유효한 답변을 내면 재수정 분기는 실행되지 않으며,
보고서의 `answer_attempts`와 `validation`(회차·성공 여부·오류 수)으로 실제 경로를 확인한다.
Python 반환값은 기존 `output.attempts/validation`도 제공하지만 디스크 보고서에는 답변 원문을 저장하지 않는다.
관계가 없는 사용자 문서는 정상일 수 있으므로 `--require-relations`를 생략하면 빈 관계도 허용한다.
이때 `relations_observed=false`를 성공적인 관계 추출 품질 검증으로 해석하지 않는다.

## 결과와 로그

- 종료 코드: 성공 0, 검증/실행 실패 1, Ctrl+C 130.
- `workspace/reports/graph-rag-<id>.json`: 단계별 소요 시간, 검색 건수,
  검증 결과, Project/Session/Run ID, Step 목록, 실패 단계.
  `rerank`에는 모델명·반환 수·점수, `retrieval`에는 Tool Step ID·상태·문서 수를 기록한다.
  `timings.search_rerank`는 standalone 옵션에서만 존재한다. 기본 검색은 `graph_execution` 시간에 포함된다.
  문서 원문·질의·답변 본문은 보고서에 기록하지 않는다.
- `workspace/projects/<project-id>/`: 실제 도메인 기록과 RAG 데이터.
- `workspace/logs/providers-<pid>.log`: LiteLLM 등의 WARNING 이상 진단.

설정 파일 자체가 잘못되면 Project 생성 전에 종료한다. 작업 도중 실패하면 보고서를 남기고
만들어진 테스트 자료를 보존한다. 중단/실패를 자동 재개하거나 기존 Project를 덮어쓰지 않는다.
total_tokens는 Graph Run에서 보고된 사용량이며, Run 밖에서 수행한 임베딩·추출 비용까지
합친 테스트 전체 청구량이 아니다. usage를 제공하지 않는 모델에서는 null일 수 있다.

`rerank_requests`는 `provider_request(operation=arerank)` 진단으로 센 **semantic 호출 수**이고,
`rerank_provider_retries`는 같은 요청 안의 ish provider 추가 시도 수다. 예를 들어 `1 / 2`는
semantic 호출 한 번에 provider 시도 세 번이다. SDK 내부 HTTP retry는 이 카운터가 관찰하지 못하므로
실제 HTTP 횟수와 같다고 해석하지 않는다. 직접 주입한 fake 함수는 provider_request를 만들지 않으므로
회귀 테스트는 SDK 경계를 대체하여 실제 진단 경로를 사용한다.

정상 기본 실행은 `rerank_requests=1`, `answer_attempts=1..max_attempts`, Run completed다.
429가 발생해도 실패 검증용 ValueError로 덮지 않고 `run.error_code`를 `error.code`에 보존한다.
`provider_diagnostics`의 stage/operation/attempt, `failed_node=retrieve`, `retrieval` 상태로 실패 위치를 확인한다.
이 예제의 RetrievalNode는 기존 ToolNode를 재사용하고 공급자 실패를 기존 typed Run 오류로 전달한다.
`rerank_requests=1`인데 429라면 답변 repair의 중복 검색보다 서버 capacity/rate 정책이나 다른 클라이언트의
트래픽을 먼저 확인한다. 민감한 원문은 보고서에 추가하지 않는다.

worker 초기화에서 LiteLLM의 로컬 비용표를 선택하므로 GitHub 비용표 갱신을 시도하지 않는다.
이 설정은 모델 API 접속을 끄는 옵션이 아니다. 임의 print나 네이티브 라이브러리의 출력까지
모두 차단하는 TUI 출력 격리 기능은 이 예제의 범위에 포함하지 않는다.

자동 회귀 검사는 실제 Chroma/Kuzu/BM25와 고정 모델 응답으로 재수정 성공, 상한 실패,
저장 후 재조회, worker import를 검증한다. CLI에는 모의 모델로 대체하는 fallback이 없으며
사내에서 실행하면 설정한 실제 모델을 호출한다. 장시간/대용량 성능 시험은 별도로 수행한다.

## 저사양 서버 안정화 설정

예제는 공통 ProviderRuntime을 사용한다. SDK 전역값이나 logger를 예제 내부에서 변경하지 않는다.
문서 등록은 영속 RAGJob을 생성하고 실행한다. 실패 보고서의 `ingestion_job_id`로 해당 Project의
`rag.arun_job(job_id, retry=True)`를 명시적으로 호출하면 완료 청크 checkpoint를 재사용한다.
예제를 다시 실행하면 새 테스트 Project/Job을 생성하므로 자동 재개하지 않는다.

설정 예제는 JSON mode auto, graph required, chunk_size=1000, embedding_concurrency=2다.
문서 청크마다 독립 LiteLLM 요청을 보내고 고정 개수 worker가 원래 위치로 벡터를 복원한다.
RAG embedding ordering does not depend on provider-reported embedding indexes.
Each document chunk is embedded independently. The application owns the chunk ordinal
and restores results to that position. Embedding concurrency is bounded by configuration.

embedding_batch_size/embedding_batching은 제거했다. 예전 설정은 직접 해당 키를 제거하고
embedding_concurrency를 지정해야 한다. 자동으로 값을 변환하지 않는다.
LiteLLM DEFAULT_MAX_RETRIES는 항상 0이며, 명시적 num_retries/max_retries는 그대로 전달한다.
명시적 SDK retry가 켜지면 llm invoke는 1회, 아니면 provider.max_attempts를 적용한다.
상세 설정은 [RAG 설정](../components/rag/README.md)을 참고한다.

보고서 파일에는 검색 원문·사용자 질의·답변 본문을 저장하지 않는다. 검색 건수/그래프 완전성,
오류 코드/operation/model/시도·시간, `rag_ingestion` 통계를 확인할 수 있다.
기존 domain/사용량 저장소는 authoritative 기록을 계속 소유한다.

외부 모델 없이 실제 SDK HTTP 경로를 먼저 검사하려면 Linux Python 3.12.14에서:

```bash
python -m llm.tests.provider_probe --output /tmp/provider-probe.json
```

이 검사는 165줄 공개 fixture, Chroma/BM25/Kuzu, 실제 SDK rerank HTTP 요청,
Graph ToolNode 검색과 Loop 답변 수정, 별도 CLI 실행을 검증한다. 두 번 답변을 생성해도
각 실행의 rerank HTTP 요청은 한 번이며, 반환 index대로 실제 후보 순서가 바뀌는지 검사한다.
고정 HTTP 응답 서버의 측정 시간이므로 사내 모델 서버 처리시간 예측값으로 사용하면 안 된다.

provider_probe는 같은 165줄 문서를 concurrency 1/2/4로 등록하여 chunks_total, 요청 수,
임베딩 시간, 전체 등록 시간, 캐시/재사용 수, retry 수를 embedding_benchmark에 기록한다.
fixture는 요청당 30ms 지연이며 실제 모델 처리량 측정이 아니다. 예제 설정은 속도와 무관하게 2를 명시한다. 라이브러리 기본값은 없다.
