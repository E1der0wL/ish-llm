"""실제 SDK·로컬 HTTP·165줄 공개 문서로 Graph/RAG를 검증한다. 모델 품질 벤치마크는 아니다.

python -m llm.tests.provider_probe --output /tmp/provider-probe.json
"""

import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from llm.components.rag import EmbeddingModel
from llm.core.models import ProjectConfig
from llm.examples.graph_rag import run_demo
from llm.providers.embeddings import validate_embeddings
from llm.providers.runtime import litellm_sdk, configure_logging
from llm.providers.requests import ProviderError


class LocalServer:
    def __init__(self):
        self.calls, self.fail, self.answers = [], False, 0
        self.empty_json_mode = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.calls.append((self.path, request))
                if self.path.endswith("embeddings"):
                    if owner.fail:
                        self.send_response(503)
                        self.end_headers()
                        self.wfile.write(b'{"error":{"message":"temporarily unavailable","type":"server_error"}}')
                        return
                    texts = request["input"]
                    if isinstance(texts, str):
                        texts = [texts]
                    result = {"object": "list", "model": request["model"], "data": [
                        {"object": "embedding", "index": i, "embedding": [1., float(len(text)), 1. + text.count("Atlas")]}
                        for i, text in enumerate(texts)], "usage": {"prompt_tokens": 10, "total_tokens": 10}}
                elif self.path.endswith("rerank"):
                    documents = request["documents"]
                    result = {"id": "rerank", "results": [
                        {"index": i, "relevance_score": (i + 1) / len(documents)}
                        for i in reversed(range(len(documents)))],
                        "meta": {"billed_units": {"search_units": 1}}}
                elif not request.get("stream"):
                    payload = json.loads(request["messages"][-1]["content"])
                    chunks = payload["chunks"] if isinstance(payload, dict) else payload
                    quote = "Atlas stores release artifacts in Harbor."
                    graph = {"entities": [{"id": "a", "name": "Atlas"}, {"id": "h", "name": "Harbor"}],
                             "relations": [{"source": "a", "target": "h", "type": "STORED_IN", "source_id": c["id"],
                                            "evidence": quote} for c in chunks if quote in c["text"]]}
                    result = {"id": "extract", "object": "chat.completion", "created": 1, "model": request["model"],
                              "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(graph)},
                                           "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}
                    if owner.empty_json_mode and request.get("response_format"):
                        result = None
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    if request["messages"][-1]["role"] == "tool":
                        owner.answers += 1
                        quote = "incorrect quotation" if owner.answers == 1 else "Atlas stores release artifacts in Harbor."
                        delta = {"content": json.dumps({"answer": "Atlas stores release artifacts in Harbor.",
                            "citations": [{"document_id": "manual", "quote": quote}]})}
                        finish = "stop"
                    else:
                        delta = {"tool_calls": [{"index": 0, "id": "search-" + str(owner.answers), "type": "function",
                            "function": {"name": "rag_search", "arguments": '{"query":"Atlas", "method":"hybrid", "rerank":true}'}}]}
                        finish = "tool_calls"
                    for value, reason in ((delta, None), ({}, finish)):
                        event = {"id": "chat", "object": "chat.completion.chunk", "created": 1, "model": request["model"],
                                 "choices": [{"index": 0, "delta": value, "finish_reason": reason}]}
                        self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return
                data = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    @property
    def params(self):
        return {"model": "openai/probe", "api_base": f"http://127.0.0.1:{self.server.server_port}/v1",
                "api_key": "local-fixture-only", "timeout": 5}


async def probe(root):
    configure_logging(root)
    sdk = await asyncio.to_thread(litellm_sdk)
    output = {"mode": "local_http_fixture", "versions": {name: importlib.metadata.version(name) for name in ("litellm", "openai")}}
    with LocalServer() as server:
        async_client = EmbeddingModel(**server.params)
        inputs = ["# Probe", "Probe belongs to Test Suite."]
        asynchronous = await async_client.embed(inputs)
        synchronous = await asyncio.to_thread(sdk.embedding, **server.params, input=inputs,
            num_retries=0, max_retries=0, caching=False, cache={"no-cache": True, "no-store": True})
        direct = await async_client.with_provider({"embedding_adapter": "openai"}).embed(inputs)
        output["sync_async_direct_equal"] = (validate_embeddings(asynchronous, 2) == validate_embeddings(synchronous, 2)
                                             == validate_embeddings(direct, 2))
        # 호스트가 SDK 캐시를 사용하더라도 llm embedding은 읽기/쓰기에 참여하지 않는다.
        from litellm.caching.caching import Cache
        from unittest.mock import patch, AsyncMock
        cache = Cache(type="local")
        with patch.object(sdk, "cache", cache), patch.object(cache, "async_get_cache", AsyncMock()) as read, \
                patch.object(cache, "async_add_cache", AsyncMock()) as write:
            await async_client.embed(inputs)
            output["sdk_cache_reads"] = read.await_count
            output["sdk_cache_writes"] = write.await_count
            assert read.await_count == write.await_count == 0
        before = len(server.calls)
        server.fail = True
        start = time.monotonic()
        try:
            await async_client.with_provider({"max_attempts": 2, "delay_seconds": .01}).embed(inputs)
        except ProviderError as error:
            output["transient_failure_code"] = error.code
        output["failure_seconds"] = time.monotonic() - start
        output["actual_http_attempts"] = len(server.calls) - before
        assert output["actual_http_attempts"] == 2, "SDK retry was revived"
        server.fail = False
        from llm.components.rag import TripleExtractor
        from llm.components.rag.prompts import extraction_defaults, default_prompt
        server.empty_json_mode = True
        before = len(server.calls)
        extractor = TripleExtractor(**server.params).with_provider({"delay_seconds": 0}).with_extraction(
            {**extraction_defaults(), "json_mode": "auto"}, default_prompt())
        await extractor.extract([])
        output["json_mode_fallback_http_attempts"] = len(server.calls) - before
        server.empty_json_mode = False
        text = (Path(__file__).parents[1] / "examples/data/graph_rag_165.md").read_text(encoding="utf-8")
        assert len(text.splitlines()) == 165
        source = root / "public-165-lines.md"
        source.write_text(text)
        config = ProjectConfig(completion=server.params, component_configurations={"rag": {
            "embedding_params": server.params, "extraction_params": server.params,
            "rerank_params": {**server.params, "model": "cohere/probe",
                              "api_base": server.params["api_base"] + "/rerank"},
            "embedding_batch_size": 4, "extraction_batch_size": 4, "chunk_size": 512,
            "provider": {"wall_timeout": 15, "max_attempts": 2},
            "extraction": {"json_mode": "auto"}}})
        report = await run_demo(root / "workspace", config, markdown=source, require_relations=True, display=False)
        output.update(status=report["status"], checks=report["checks"], timing=report["timings"],
                      rag_ingestion=report["rag_ingestion"], lines=165, report=report["report"])
        assert report["status"] == "passed", report.get("error")
        candidates = report["search"]["hybrid"]["documents"]
        assert len(candidates) > 1, "Rerank ordering needs multiple candidates"
        assert [hit["id"] for hit in report["search"]["rerank"]["documents"]] == [
            hit["id"] for hit in reversed(candidates)]
        rerank_calls = [request for path, request in server.calls if path.endswith("rerank")]
        assert rerank_calls[0]["documents"] == [hit["text"] for hit in candidates]
        assert len(rerank_calls) == 3, "Direct search and both Graph repair attempts must rerank"
        output["rerank_http_calls"] = len(rerank_calls)
        output["rerank_reversed_candidates"] = True
        output["rerank"] = report["rerank"]

        # 동일 165줄 문서로 실제 CLI 진입점도 별도 프로세스에서 실행한다.
        config_path = root / "cli-config.json"
        config_path.write_text(config.serialize(), encoding="utf-8")
        server.answers = 0
        before = len(rerank_calls)
        process = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "llm.examples.graph_rag",
            "--config", str(config_path), "--workspace", str(root / "cli-workspace"),
            "--markdown", str(source), "--require-relations"], capture_output=True, text=True, timeout=120)
        cli_reports = list((root / "cli-workspace/reports").glob("*.json"))
        assert process.returncode == 0, (process.returncode, process.stderr)
        assert len(cli_reports) == 1
        cli = json.loads(cli_reports[0].read_text())
        assert cli["status"] == "passed" and cli["checks"]["agent_used_rerank"]
        assert not any(word in process.stdout + process.stderr for word in ("LiteLLM:WARNING", "Give Feedback", "dotenv"))
        output["cli"] = {"exit_code": process.returncode, "checks": cli["checks"], "timing": cli["timings"],
            "rerank": cli["rerank"], "rerank_http_calls": len([1 for path, _ in server.calls if path.endswith("rerank")]) - before}
        assert output["cli"]["rerank_http_calls"] == 3
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="provider-probe-") as directory:
        result = asyncio.run(probe(Path(directory)))
        report = args.output.with_name("graph-rag-report.json")
        report.write_text(Path(result["report"]).read_text())
        result["report"] = str(report)
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
