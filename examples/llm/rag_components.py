"""공개 Component API로 문서 CRUD·검색 및 선택적인 Loop 검색을 실행한다.

python -m examples.llm.rag_components --model gemini/gemini-3.1-flash-lite
--ask를 주면 내장 검색 Tool을 사용하는 LoopEngine도 실행한다.
키는 GEMINI_API_KEY 환경변수에서 읽어 런타임 객체에만 전달한다.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

from llm.llm import LargeLanguageModel
from llm.components.rag import EmbeddingModel
from llm.components.rag import RAGComponent, TripleExtractor
from llm.core.models import RunStatus
from llm.engines.loop import LoopEngine


async def run(args: argparse.Namespace) -> dict:
    """예제는 서비스 API만 조합한다. 분할·색인·검색·Tool 구현은 컴포넌트에 둔다."""
    key = os.environ["GEMINI_API_KEY"]
    component = RAGComponent(
        embedding=EmbeddingModel(model=args.embedding_model, api_key=key),
        extractor=TripleExtractor(model=args.model, api_key=key, temperature=0),
    )
    # rag 한 곳에서 문서·벡터·관계를 관리하고 함께 검색한다.
    engines = ({"loop": LoopEngine(completion_kwargs={"model": args.model, "api_key": key},
                                   max_iterations=8)} if args.ask else {})
    async with LargeLanguageModel(args.workspace, components=[component], engines=engines) as backend:
        project = await backend.projects.acreate("RAG Component demo", components=["rag"], config={"parameters": {"components": {"rag": {
                "chunk_size": 2000, "embedding_concurrency": 2, "extraction_batch_size": 32,
                "extraction": {"failure_policy": "required"},
                "graph": {"buffer_pool_size": 64 * 1024 * 1024, "max_num_threads": 2},
                "search": {"method": "hybrid", "expand": "section", "limit": 5, "candidate_count": 20,
                           "rrf_constant": 60, "max_hops": 2, "relation_limit": 30},
                "document_kwargs": {"session_type": "RETRIEVAL_DOCUMENT"},
                "query_kwargs": {"session_type": "RETRIEVAL_QUERY"}}}}})
        rag = await project.components.aget("rag")
        text = await asyncio.to_thread(args.markdown.read_text, encoding="utf-8")
        document = await rag.aadd_document(title=args.markdown.name, content=text,
                                          metadata={"source": args.markdown.name})
        print("Project:", project.id, "Document:", document["id"])
        search = await rag.asearch(args.query, expand="section", limit=3)
        graph = await rag.agraph_search(args.seed, max_hops=2)
        report = {"project_id": project.id, "document_id": document["id"],
                  "search": search, "hits": search["documents"], "graph": graph}
        if args.ask:
            session = await project.sessions.acreate("Document question")
            run = await (await session.run.submit(args.ask, engine="loop")).wait()
            result = await run.aresult()
            steps = await run.steps.alist()
            report["run"] = {"id": run.id, "status": str(result.status),
                             "steps": [{"kind": s.kind, "name": s.name, "status": str(s.status)} for s in steps]}
            if result.status != RunStatus.COMPLETED:
                raise RuntimeError(f"Run {run.id} failed; inspect its persisted Run/Step records")
            report["answer"] = (await run.aresponse()).content
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.exercise_crud:
            updated = await rag.aupdate_document(document["id"],
                content=text + "\n\n## 추가 기록\n\n예제 문서를 수정했습니다.\n", expected_revision=document["revision"])
            assert updated["revision"] == 2
            await rag.adelete_document(document["id"], expected_revision=2)
            assert not await rag.alist_documents()
            assert not await rag.asearch_documents(args.query)
            print("등록·수정·삭제 완료")
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[2] /
                        "tests/llm/reports/runs/component-workspace")
    parser.add_argument("--markdown", type=Path, default=Path(__file__).parent / "fixtures/rag_operations.md")
    parser.add_argument("--model", required=True)
    parser.add_argument("--embedding-model", default="gemini/gemini-embedding-001")
    parser.add_argument("--query", default="오리온 백업 주기와 보존 기간")
    parser.add_argument("--seed", default="아틀라스")
    parser.add_argument("--exercise-crud", action="store_true")
    parser.add_argument("--ask", help="Run a LoopEngine question using the component's search tools")
    args = parser.parse_args(argv)
    if not os.environ.get("GEMINI_API_KEY"):
        parser.error("Set GEMINI_API_KEY first")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
