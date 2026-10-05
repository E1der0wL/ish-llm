from tests.llm.configuration_fixtures import rag_settings
"""예제의 공개 API 연결을 실제 로컬 색인과 고정 모델 응답으로 검증한다."""

import json
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from llm.components.rag import RAGComponent, EmbeddingModel, TripleExtractor, RerankModel
from examples.llm.graph_rag import run_demo, main, make_workflow, validate_config
from llm.llm import ProjectConfig
from tests.llm.test_loop import chunk
from tests.llm.test_rag_components import fake_embedding


async def extraction(**request):
    chunks = json.loads(request["messages"][-1]["content"])
    quote = "Atlas stores release artifacts in Harbor."
    graph = {"entities": [{"id": "a", "name": "Atlas"}, {"id": "b", "name": "Harbor"}],
             "relations": [{"source": "a", "target": "b", "type": "stores_in",
                            "source_id": c["id"], "evidence": quote} for c in chunks if quote in c["text"]]}
    return {"choices": [{"message": {"content": json.dumps(graph)}}]}


async def reranking(**request):
    # 입력의 역순을 반환하여 index와 실제 문서의 대응을 검사한다.
    return {"results": [{"index": i, "relevance_score": (i + 1) / len(request["documents"])}
                        for i in reversed(range(len(request["documents"])))]}


class AnswerModel:
    def __init__(self, *, bad_attempts=1, always_bad=False):
        self.bad_attempts, self.always_bad = bad_attempts, always_bad
        self.answers = 0
        self.requests = []

    def __call__(self, **request):
        self.requests.append(request)
        assert not request.get("tools"), "Answer Agent must not retrieve"
        inputs = json.loads(request["messages"][-1]["content"])
        assert set(inputs) == {"query", "evidence", "feedback"}
        evidence = inputs["evidence"]
        assert evidence["documents"][0]["document_id"] == "manual"
        assert evidence["relations"]
        assert all("rerank_score" in hit for hit in evidence["documents"])
        self.answers += 1
        quote = ("invented quote" if self.always_bad or self.answers <= self.bad_attempts
                 else "Atlas stores release artifacts in Harbor.")
        yield chunk(json.dumps({"answer": "Atlas는 Harbor에 배포 자료를 저장합니다.",
                                "citations": [{"document_id": "manual", "quote": quote}]}, ensure_ascii=False))
        yield chunk(finish="stop")


class GraphRAGExampleTests(unittest.IsolatedAsyncioTestCase):
    def test_missing_targeted_settings_report_error_without_materializing(self):
        config = ProjectConfig()
        before = config.to_dict()
        with self.assertRaisesRegex(ValueError, "parameters.engines.loop.config.completion.model is required"):
            validate_config(config)
        self.assertEqual(config.to_dict(), before)

    def config(self):
        return ProjectConfig(parameters={"engines": {"loop": {'policy': {'max_iterations': 3, 'request_timeout': 20}, 'config': {'completion': {'model': 'test/chat'}}}}, "components": {"rag": ProjectConfig.merge(rag_settings({'config': {'search': {'rerank': True}}}), {'config': {'embedding_params': {'model': 'test/embed'}, 'extraction_params': {'model': 'test/extract'}, 'rerank_params': {'model': 'test/rerank'}}})}})

    async def exercise(self, root, model, *, rerank_fn=reranking, config=None, **options):
        # SDK 경계만 대체하여 실제 provider_request/retry 진단을 함께 검사한다.
        call = AsyncMock(side_effect=rerank_fn)
        self.rerank_call = call
        rag = RAGComponent(embedding=EmbeddingModel(embedding_fn=fake_embedding),
                           extractor=TripleExtractor(completion_fn=extraction),
                           reranker=RerankModel())
        with patch("llm.providers.runtime.litellm_sdk", return_value=SimpleNamespace(arerank=call, cache=None)):
            return await run_demo(Path(root), config or self.config(), completion_fn=model,
                                  rag_component=rag, display=False, **options)

    async def test_crud_search_graph_tool_repair_and_reopen(self):
        with tempfile.TemporaryDirectory() as root:
            model = AnswerModel()
            report = await self.exercise(root, model)
            self.assertEqual(report["status"], "passed", report)
            self.assertEqual(report["mode"], "injected")
            self.assertEqual(report["output"]["attempts"], 2)
            self.assertTrue(report["output"]["validation"][0]["errors"])
            self.assertEqual(report["output"]["validation"][1]["errors"], [])
            self.assertTrue(all(report["checks"].values()))
            self.assertTrue(report["relations_observed"])
            self.assertEqual([h["text"] for h in report["search"]["rerank"]["documents"]],
                             list(reversed(self.rerank_call.call_args.kwargs["documents"])))
            self.assertTrue(report["checks"]["graph_rerank_scored_documents"])
            self.assertEqual(len([s for s in report["steps"] if s["kind"] == "tool"]), 1)
            self.assertEqual(report["rerank_requests"], 1)
            self.assertEqual(report["rerank_provider_retries"], 0)
            self.assertEqual(report["answer_attempts"], 2)
            self.assertEqual(self.rerank_call.await_count, 1)
            self.assertNotIn("search_rerank", report["timings"])
            inputs = [json.loads(r["messages"][-1]["content"]) for r in model.requests]
            self.assertEqual(inputs[0]["evidence"], inputs[1]["evidence"])
            self.assertEqual(inputs[0]["evidence"], report["search"]["rerank"])
            saved = json.loads(Path(report["report"]).read_text())
            self.assertEqual(saved["checks"], report["checks"])
            self.assertNotIn("output", saved)
            self.assertNotIn("query", saved)
            self.assertIsInstance(saved["search"]["hybrid"]["documents"], int)
            self.assertNotIn("Atlas stores release artifacts", Path(report["report"]).read_text())
            self.assertIn("exact quote", model.requests[1]["messages"][-1]["content"])
            self.assertIn("invented quote", model.requests[1]["messages"][-1]["content"])

    async def test_one_and_three_answer_attempts_each_retrieve_once(self):
        for bad_attempts in (0, 2):
            with self.subTest(bad_attempts=bad_attempts), tempfile.TemporaryDirectory() as root:
                model = AnswerModel(bad_attempts=bad_attempts)
                report = await self.exercise(root, model)
                self.assertEqual(report["status"], "passed", report)
                self.assertEqual(report["answer_attempts"], bad_attempts + 1)
                self.assertEqual(len(report["output"]["validation"]), bad_attempts + 1)
                self.assertEqual(report["rerank_requests"], 1)
                self.assertEqual(self.rerank_call.await_count, 1)
                evidence = [json.loads(r["messages"][-1]["content"])["evidence"] for r in model.requests]
                self.assertTrue(all(e == evidence[0] for e in evidence))

    async def test_standalone_rerank_is_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as root:
            report = await self.exercise(root, AnswerModel(), standalone_rerank=True)
            self.assertEqual(report["status"], "passed", report)
            self.assertIn("search_rerank", report["timings"])
            self.assertEqual(report["rerank_requests"], 2)
            self.assertEqual(report["answer_attempts"], 2)
            self.assertEqual(self.rerank_call.await_count, 2)

    async def test_provider_retries_are_not_semantic_requests(self):
        attempts = 0
        async def retrying(**request):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                failure = RuntimeError("PRIVATE rate limit")
                failure.status_code = 429
                raise failure
            return await reranking(**request)
        config = self.config()
        config.parameters.setdefault("components", {})["rag"]['policy']['provider'] = {"max_attempts": 3, "delay_seconds": 0}
        with tempfile.TemporaryDirectory() as root:
            report = await self.exercise(root, AnswerModel(), rerank_fn=retrying, config=config)
            self.assertEqual(report["status"], "passed", report)
            self.assertEqual((report["rerank_requests"], report["rerank_provider_retries"], attempts), (1, 2, 3))
            self.assertEqual(report["answer_attempts"], 2)

    async def test_invalid_citations_exhaust_loop_and_keep_failure_report(self):
        with tempfile.TemporaryDirectory() as root:
            report = await self.exercise(root, AnswerModel(always_bad=True))
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["run"]["status"], "failed")
            self.assertFalse(report["checks"]["run_completed"])
            self.assertEqual(report["stage"], "graph_execution")
            self.assertEqual(report["answer_attempts"], 3)
            self.assertEqual(report["rerank_requests"], 1)
            self.assertEqual(report["error"]["code"], report["run"]["error_code"])
            self.assertTrue(Path(report["report"]).is_file())

    async def test_missing_settings_fail_before_workspace_creation(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "unused"
            with self.assertRaisesRegex(ValueError, "embedding_params.model"):
                await run_demo(path, ProjectConfig(parameters={"engines": {"loop": {'config': {'completion': {'model': 'test/chat'}}}}}), display=False)
            self.assertFalse(path.exists())

    async def test_missing_reranker_fails_before_workspace_creation(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "unused"
            config = self.config()
            config.parameters.setdefault("components", {})["rag"]["config"].pop("rerank_params")
            with self.assertRaisesRegex(ValueError, "rerank_params.model"):
                await run_demo(path, config, display=False)
            self.assertFalse(path.exists())

    async def test_rerank_rate_limit_is_preserved_in_run_and_report(self):
        async def unavailable(**request):
            failure = RuntimeError("PRIVATE reranker unavailable")
            failure.status_code = 429
            raise failure

        with tempfile.TemporaryDirectory() as root:
            model = AnswerModel()
            report = await self.exercise(root, model, rerank_fn=unavailable)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["stage"], "graph_execution")
            self.assertEqual(report["failed_node"], "retrieve")
            self.assertEqual(report["run"]["status"], "failed")
            self.assertEqual(report["run"]["error_code"], "provider_rate_limit")
            self.assertEqual(report["error"]["code"], report["run"]["error_code"])
            self.assertEqual((report["rerank_requests"], report["rerank_provider_retries"]), (1, 0))
            self.assertEqual(model.answers, 0)
            saved = json.loads(Path(report["report"]).read_text())
            self.assertEqual(saved["error"]["code"], "provider_rate_limit")
            self.assertNotIn("PRIVATE", Path(report["report"]).read_text())
            run = json.loads((Path(report["project"]) / "sessions" / report["session_id"] /
                              "runs" / report["run_id"] / "run.json").read_text())
            self.assertEqual(run["error_code"], "provider_rate_limit")

    async def test_ingestion_error_is_reported_without_a_graph_run(self):
        async def unavailable(**request):
            raise RuntimeError("embedding endpoint unavailable")

        with tempfile.TemporaryDirectory() as root:
            rag = RAGComponent(embedding=EmbeddingModel(embedding_fn=unavailable),
                               extractor=TripleExtractor(completion_fn=extraction))
            report = await run_demo(Path(root), self.config(), completion_fn=AnswerModel(),
                                    rag_component=rag, display=False)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["stage"], "register_document")
            self.assertEqual(report["error"]["code"], "provider_failed")
            self.assertNotIn("endpoint unavailable", Path(report["report"]).read_text())
            self.assertNotIn("run_id", report)
            self.assertTrue(Path(report["report"]).is_file())


class WorkerContractTests(unittest.TestCase):
    def test_fresh_worker_can_import_help_without_network_or_model(self):
        payload = pickle.dumps(main).hex()
        result = subprocess.run([sys.executable, "-c",
            'import pickle,sys; pickle.loads(bytes.fromhex(sys.argv[1]))("--help")', payload],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--config", result.stdout)
        self.assertIn("--markdown", result.stdout)
        self.assertIn("--standalone-rerank", result.stdout)

    def test_retrieval_precedes_repair_in_json_workflow(self):
        workflow = make_workflow(3)
        self.assertEqual(workflow["entry"], "retrieve")
        nodes = workflow["nodes"]
        self.assertEqual(nodes["retrieve"]["inputs"], {"query": "/query"})
        self.assertEqual(nodes["retrieve"]["result_key"], "evidence")
        self.assertEqual(nodes["repair"]["max_iterations"], 3)
        self.assertEqual(nodes["repair"]["on_limit"], "fail")
        self.assertNotIn('"rag_search"', json.dumps(nodes["repair"]))
