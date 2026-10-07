# 통합 RAG 컴포넌트

문서 분할, 임베딩, 트리플 추출, BM25/Chroma/Kuzu 색인과 검색은 모두
`llm/components/rag`가 담당한다. 공개 컴포넌트와 Project 디렉토리는 `rag` 하나다.
문서 등록 시 임베딩과 관계를 함께 준비하고, 검색 시 문서와 관련 관계·출처를 함께 반환한다.
UI에서는 ComponentData의 비동기 API를 사용한다.

## 구성과 문서 CRUD

```python
from llm.llm import LargeLanguageModel
from llm.components.rag import RAGComponent, EmbeddingModel, TripleExtractor
from llm.engines.loop import LoopEngine

component = RAGComponent(
    embedding=EmbeddingModel(model=embedding_model, api_key=api_key),
    extractor=TripleExtractor(model=extraction_model, api_key=api_key, temperature=0),
)
async with LargeLanguageModel(
    workspace, components=[component],
    engines={"loop": LoopEngine()},
) as backend:
    project = await backend.projects.acreate("설명서", components=["rag"], config={
        "parameters": {"engines": {"loop": {"config": {"completion": {
            "model": completion_model, "api_key": api_key}}}}, "components": {"rag": {
            "config": {"chunk_size": 1000,
                "extraction_batch_size": 4, "search_cache_chars": 200000,
                "index_batch_size": 64,
                "search": {"method": "hybrid", "expand": "section", "limit": 5,
                    "rerank": False, "max_hops": 2, "relation_limit": 30,
                    "candidate_count": 20, "rrf_constant": 60}},
            "policy": {"embedding_concurrency": 2,
                       "extraction": {"failure_policy": "required"}}}}}})
    rag = await project.components.aget("rag")
    document = await rag.aadd_document(
        title="운영 설명서", content=markdown_text,
        identifier="manual", metadata={"source": "manual.md"},
    )
    result = await rag.asearch("백업 정책", method="hybrid", expand="section")
    updated = await rag.aupdate_document("manual", content=new_text,
                                        expected_revision=document["revision"])
    documents = await rag.alist_documents()
    await rag.adelete_document("manual", expected_revision=updated["revision"])
```

`aadd_document`와 `aupdate_document`는 embedding과 extractor가 필요하다. 모델 객체는
런타임에 주입하며 자동으로 모델을 고르지 않는다. TripleExtractor는 LiteLLM acompletion을
사용한다. SLM 또는 LLM을 선택하거나 `async extract(chunks)` 구현을 주입할 수 있다.
관계의 양 끝 엔티티와 원문에 실제로 있는 인용을 검증한 다음 저장한다. 오류나 취소가
준비 단계에서 발생하면 이전 버전을 유지한다. 모델 준비 중 다른 작업은 계속 진행할 수 있다.
문서 수정 시 동일 chunk vector는 재사용하고 변경 chunk와 트리플을 준비한 뒤 세대를 교체한다.

`create/save/update/delete`와 그 비동기 별칭은 `records/`의 열린 JSON **정의 CRUD**다.
문서 API와 별개이며 정의 저장은 임베딩을 호출하지 않는다. 예전 records 파일을 자동으로
문서로 가져오지 않는다. 직접 문서 API는 Run/Step이나 백그라운드 job을 만들지 않는다.

## 통합 검색

```python
result = await rag.asearch(
    "백업 정책", method="hybrid", expand="section", limit=5,
    max_hops=2, relation_limit=30,
)
hits = result["documents"]
relations = result["relations"]
sources = result["sources"]
```

| 반환 키 | 내용 |
| --- | --- |
| query | 입력 질의 |
| documents | 일치 문단, document_id/section_id, title/metadata/revision, 확장 context, score |
| entities | 반환 관계의 엔티티 id/name |
| relations | source/target 이름, type, document_id/source_id, 원문 evidence, hop |
| sources | 검색 문단과 관계 근거 문단의 text/title/metadata/revision |

method는 `hybrid`(BM25와 Chroma 순위 결합), `bm25`, `vector`다. score는 확률이 아니다.
expand는 `chunk`, `section`(하위 절 포함), `document`다. limit는 문서 문단 수(1~100),
relation_limit는 전체 관계 수, max_hops는 탐색 깊이를 양의 정수로 명시한다. 임의의 제품 상한은 적용하지 않는다.
RerankModel을 `reranker=`로 주입하면 `rerank=True`로 후보를 재정렬할 수 있다.

**실제로 검색된 문단 ID**에 근거가 있는 관계를 첫 단계로 가져오고, 그 양 끝 엔티티의
나가는 관계를 추가 탐색한다. 확장된 context 전체를 그래프 시작점으로 사용하지 않는다.
검색 시 엔티티 이름을 별도 SLM으로 추출하지 않는다. 코퍼스 본문은 검색당 한 번 읽고
모델 await 뒤에는 작은 identity/generation 포인터로 유효성을 검사한다. 재정렬 후에는
검색을 다시 실행하지 않는다. 문서와 관계·출처 버전은 같은
색인 세대에서 읽는다. 준비 중 세대가 바뀌면 `RAGConflictError`로 알리며 자동 재시도하지 않는다.
문서가 검색되어도 연결된 관계가 없으면 relations는 빈 배열이다. 관계 근거는 검색된
문서 목록 밖의 문서에도 있을 수 있으며 sources에서 해당 문단과 버전을 확인할 수 있다.

```python
# 문서 목록만 필요할 때: 종전 asearch의 list 반환 형식
hits = await rag.asearch_documents("백업 정책", method="bm25")
# 정확한 엔티티 이름을 알고 있을 때의 보조 API
relations = await rag.agraph_search("아틀라스", max_hops=2, limit=100)
```

같은 이름의 엔티티를 정규화하여 문서 간 공유하고 관계 근거는 문서별로 보존한다.
문서를 삭제해도 다른 문서의 근거는 남는다. 현재는 동명이인을 자동으로 구분하지 않는다.

## LoopEngine과 연결

Project가 `rag`를 선택하면 `rag_search` Tool 하나가 자동 제공된다.
별도 ToolComponent 등록이나 검색 활성화/비활성화 옵션은 없다. 과거 search_tools_enabled
값도 해석하지 않는다. 모델이 필요에 따라 검색을 호출하며 결과는 다음 completion으로
전달된다. ToolExecutor의 이벤트를 통해 해당 Run에 Tool Step이 기록된다.

```python
session = await project.sessions.acreate("설명서 질문")
run = await (await session.run.submit("설명서를 검색해서 백업 정책을 설명해 줘", engine="loop")).wait()
steps = await run.steps.alist()
response = await run.aresponse()
```

내장 Tool은 Run이 속한 Project에 고정되고 호출마다 수명을 검사한다. 호스트의 외부
검색 서비스는 BuiltinTools의 external_rag_search/external_graphrag_search 어댑터와
구별된다. 외부 어댑터의 corpus/query/options는 호스트 계약이다.

## 저장과 기존 데이터 이전

```text
project/rag/
├── records/                   # 정의 JSON
├── identity.json              # 삭제 후 재생성을 구분하는 ID
├── active.json                # 공개된 세대 ID
└── generations/<id>/
    ├── corpus.json            # 원문·절·문단·벡터·관계·revision
    ├── chroma/                # 벡터 색인
    └── graph.kuzu             # 근거 관계 색인
```

BM25는 저장 문단에서 재구성한다. 새 DB를 완성하고 닫은 뒤 active.json을 원자적으로
교체한다. DB 준비는 공유 workspace 잠금과 StorageIO에서 수행한다. 변경된 문서만
모델을 호출하며 기존 DB 복사본에서 변경 문서의 벡터/관계를 갱신한다. 첫 생성과 사용자
_build 재정의는 전체 생성 경로를 쓴다. 복제와 삭제에는 모델이 필요 없다.
영속 등록 작업, 준비 결과 재사용, 임베딩 배치와 남아 있는 전체 corpus/DB 복사 비용은
[장시간 실행 안내](long-running.md)를 참고한다.
공개 포인터 교체 직후 디스크 오류가 발생하면 이미 새 세대가 공개되었을 수 있으므로
재조회 후 명시적으로 재시도한다. 비활성 세대 정리는 `await rag.acompact()`로 재시도한다.

RAGComponent/rag가 문서와 관계를 함께 관리한다. 구형 graphrag 병합 함수와 벡터 전용
세대 읽기는 제공하지 않는다. 공개 세대에는 corpus.json의 graph.entities/relations와
실제 graph.kuzu가 모두 필요하며 누락되면 오류다. 원래 관계가 없는 정상 문서는 빈 배열과
빈 그래프 색인을 가진다. 빈 코퍼스는 정상적인 빈 검색 결과를 반환한다.

기존 사용자 디렉토리나 기록을 자동 삭제·이전하지 않는다. 현재 API로 문서를 등록하면
임베딩과 관계를 함께 생성한다. 문서 목록만 필요하면 asearch_documents를 사용한다.

## 검증과 범위

```powershell
.\.venv312\Scripts\python.exe -m unittest tests.llm.test_unified_rag tests.llm.test_rag_components tests.llm.test_search_tools -v
.\.venv312\Scripts\python.exe -m unittest discover -s tests/llm -t . -v
```

전체 395개 테스트가 Python 3.12.14에서 통과했다(233.377초).
로그는 tests/llm/reports/history/unified-rag-suite-python312.txt다. 실제 Chroma/Kuzu/BM25와 고정 모델 응답을 사용한다.
실모델 예제는 [실행 안내](markdown-rag-test.md)를 참고한다.
현재는 소규모 로컬 코퍼스를 위한 구현이다. 완전한 Markdown AST, 한국어 형태소 분석,
대규모 증분 색인, 영속 백그라운드 job, 동명이인 해소, 커뮤니티 요약 기반 전역 GraphRAG는
제공하지 않는다. 검색·추출 품질은 사용하는 모델과 문서로 별도 평가해야 한다.

컴포넌트 설정은 `project.json`의 `config.parameters["components"]`에만 저장한다.
ComponentData.configure 편의 API도 이 설정을 갱신한다.
