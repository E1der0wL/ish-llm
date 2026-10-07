"""실제 최종 인자·취소·무결성 경계를 검증한다. 외부 인증은 사용하지 않는다."""

import asyncio
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from llm.components.rag import EmbeddingModel, TripleExtractor
from llm.components.rag.prompts import default_prompt
from llm.providers.embeddings import extract_single_embedding, EmbeddingIntegrityError
from llm.providers.requests import invoke, ProviderError, error_code
from llm.providers.runtime import configure_logging, diagnostic_scope, litellm_sdk


def response(indexes=(0, 1), vectors=None, **extra):
    return {"data": [{"index": index, "embedding": vector} for index, vector in
                     zip(indexes, vectors or [[1., 2.], [3., 4.]])], **extra}


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        configure_logging(self.temp.name)

    async def test_final_litellm_params_and_globals(self):
        sdk = await asyncio.to_thread(litellm_sdk)
        original = response()
        with patch.object(sdk, "DEFAULT_MAX_RETRIES", 7), patch.object(sdk, "num_retries", 4), \
                patch.object(sdk, "aembedding", AsyncMock(return_value=original)) as call:
            output = await EmbeddingModel(model="openai/test", num_retries=2, max_retries=3,
                caching=True, cache={"no-store": False}).embed(["a", "b"])
            self.assertIs(litellm_sdk(), sdk)
            self.assertEqual((sdk.DEFAULT_MAX_RETRIES, sdk.num_retries), (0, 4))
        self.assertIs(output, original)
        request = call.call_args.kwargs
        self.assertEqual((request["num_retries"], request["max_retries"]), (2, 3))
        self.assertEqual(request["cache"], {"no-cache": True, "no-store": True})
        self.assertIs(request["caching"], False)

    async def test_transient_bounded_retry_and_attempt_observation(self):
        from llm.providers.observations import model_observer
        events, observed = [], []
        async def observer(operation, request, call):
            observed.append(operation)
            return await call(**request)
        for failure in (TimeoutError(), ConnectionResetError(), ProviderError("provider_empty_response")):
            call = AsyncMock(side_effect=[failure, "ok"])
            with diagnostic_scope(events.append), model_observer(observer):
                self.assertEqual(await invoke("aembedding", {}, call, {"max_attempts": 2, "delay_seconds": 0}), "ok")
            self.assertEqual(call.await_count, 2)
        for status in (429, 500, 501, 502, 503, 504, 599):
            failure = RuntimeError("server")
            failure.status_code = status
            call = AsyncMock(side_effect=failure)
            with self.assertRaises(ProviderError):
                await invoke("aembedding", {}, call, {"max_attempts": 2, "delay_seconds": 0})
            self.assertEqual(call.await_count, 2)
        self.assertEqual(len(observed), 6)
        self.assertTrue(any(e.code == "provider_retry" for e in events))

    async def test_sdk_retry_never_multiplies_invoke_attempts(self):
        for request in ({"num_retries": 2}, {"max_retries": 3},
                        {"num_retries": 0, "max_retries": 3}, {"retry_policy": {"TimeoutErrorRetries": 2}}):
            call = AsyncMock(side_effect=TimeoutError())
            events = []
            with diagnostic_scope(events.append), self.assertRaises(ProviderError):
                await invoke("aembedding", request, call, {"max_attempts": 4, "delay_seconds": 0})
            self.assertEqual(call.await_count, 1)
            self.assertEqual(call.call_args.kwargs, request)
            self.assertTrue(any(e.code == "provider_retry_delegated" for e in events))
        call = AsyncMock(side_effect=TimeoutError())
        with self.assertRaises(ProviderError):
            await invoke("aembedding", {"num_retries": 0, "max_retries": 0}, call,
                         {"max_attempts": 3, "delay_seconds": 0})
        self.assertEqual(call.await_count, 3)

    async def test_sdk_defaults_and_plain_model_client_are_preserved(self):
        from llm.components.rag._client import ModelClient
        from llm.components.rag import RerankModel
        from llm.providers.litellm import completion
        sdk = await asyncio.to_thread(litellm_sdk)
        for operation, model, invoke_model in (
                ("aembedding", EmbeddingModel(model="test"), lambda m: m.embed(["a", "b"])),
                ("arerank", RerankModel(model="test"), lambda m: m.rerank("q", ["d"])),
                ("acompletion", TripleExtractor(model="test"), lambda m: m._invoke(messages=[]))):
            result = response() if operation == "aembedding" else {"choices": [{"message": {"content": "{}"}}]}
            with patch.object(sdk, operation, AsyncMock(return_value=result)) as call:
                await invoke_model(model)
                self.assertNotIn("num_retries", call.call_args.kwargs)
                self.assertNotIn("max_retries", call.call_args.kwargs)
                await invoke_model(model.with_config({"num_retries": 2, "max_retries": 3}))
                self.assertEqual(call.call_args.kwargs["num_retries"], 2)
                self.assertEqual(call.call_args.kwargs["max_retries"], 3)
        with patch.object(sdk, "completion", Mock(return_value=iter(()))) as call:
            completion(model="test", num_retries=2, max_retries=3)
            self.assertEqual(call.call_args.kwargs, {"model": "test", "num_retries": 2, "max_retries": 3})
            completion(model="test")
            self.assertEqual(call.call_args.kwargs, {"model": "test"})
        for default, attempts in ((2, 3), (0, 3)):
            with patch.object(sdk, "DEFAULT_MAX_RETRIES", default), patch.object(sdk, "num_retries", None), \
                    patch.object(sdk, "aembedding", AsyncMock(side_effect=TimeoutError())) as call:
                with self.assertRaises(ProviderError):
                    await EmbeddingModel(model="test", num_retries=0, max_retries=0).with_provider(
                        {"max_attempts": 3, "delay_seconds": 0}).embed(["text"])
                self.assertEqual(call.await_count, attempts)
        call = AsyncMock(return_value=response())
        await ModelClient("aembedding", call, {"model": "test", "caching": True,
            "cache": {"no-store": False}})._invoke(input=["a", "b"])
        self.assertTrue(call.call_args.kwargs["caching"])
        self.assertEqual(call.call_args.kwargs["cache"], {"no-store": False})

    async def test_stream_retry_delegation_and_no_retry_after_delta(self):
        from llm.engines.base import BaseEngine
        for params, partial, expected in (({"num_retries": 2}, False, 1),
                ({"max_retries": 3}, False, 1), ({}, False, 3), ({}, True, 1)):
            requests = []
            def stream(**request):
                requests.append(request)
                if partial:
                    yield {"choices": [{"delta": {"content": "partial"}}]}
                raise TimeoutError()
            with self.assertRaises(Exception):
                async for _ in BaseEngine(completion_fn=stream).stream_completion({"model": "test", **params},
                        provider={"max_attempts": 3, "delay_seconds": 0}):
                    pass
            self.assertEqual(len(requests), expected)
            for key in ("num_retries", "max_retries"):
                self.assertEqual(key in requests[0], key in params)
                if key in params:
                    self.assertEqual(requests[0][key], params[key])

    async def test_reranker_tool_defends_direct_calls_without_mutating_arguments(self):
        from llm.components.rag.tools import search_tools
        from tests.llm.configuration_fixtures import rag_settings
        for present in (False, True):
            data = SimpleNamespace(name="rag", has_reranker=lambda: present,
                resolve_config=lambda: {"values": {"config": {"search": {**rag_settings()["config"]["search"], "rerank": True}}}},
                asearch=AsyncMock(return_value={"documents": []}))
            tool = search_tools(data).get("rag_search")
            spec = tool.parameters["properties"]["rerank"]
            if not present:
                self.assertEqual(spec, {"type": "boolean", "const": False})
            else:
                self.assertNotIn("const", spec)
                self.assertNotIn("default", spec)
            arguments = {"query": "q", "rerank": True}
            if present:
                await tool.handler(arguments)
                self.assertTrue(data.asearch.call_args.kwargs["rerank"])
            else:
                with self.assertRaisesRegex(ValueError, "reranker"):
                    await tool.handler(arguments)
                data.asearch.assert_not_called()
            self.assertTrue(arguments["rerank"])

    async def test_permanent_errors_never_retry(self):
        for status in (400, 401, 403, 404):
            failure = RuntimeError("invalid")
            failure.status_code = status
            call = AsyncMock(side_effect=failure)
            with self.assertRaises(ProviderError):
                await invoke("aembedding", {}, call, {"max_attempts": 2, "delay_seconds": 0})
            self.assertEqual(call.await_count, 1)
        for failure in (ValueError("semantic validation"), EmbeddingIntegrityError("embedding_result_count", "bad")):
            call = AsyncMock(side_effect=failure)
            with self.assertRaises(Exception):
                await invoke("aembedding", {}, call, {})
            self.assertEqual(call.await_count, 1)

    async def test_deadline_and_cancellation_during_call_and_backoff(self):
        closed = asyncio.Event()
        async def block(**request):
            try:
                await asyncio.sleep(10)
            finally:
                closed.set()
        started = time.monotonic()
        with self.assertRaisesRegex(ProviderError, "timeout"):
            await invoke("aembedding", {}, block, {"wall_timeout": .03, "delay_seconds": 0})
        self.assertLess(time.monotonic() - started, .5)
        self.assertTrue(closed.is_set())
        for call in (block, AsyncMock(side_effect=TimeoutError())):
            task = asyncio.create_task(invoke("aembedding", {}, call, {"max_attempts": 2, "delay_seconds": 5, "max_delay_seconds": 5}))
            await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, .2)

    async def test_logs_do_not_write_sdk_text_or_secrets_to_tui_or_file(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            for name in ("LiteLLM", "LiteLLM Router", "httpx", "httpcore", "dotenv", "dotenv.main", "py.warnings"):
                logging.getLogger(name).warning("api_key=PRIVATE document PRIVATE-DOC")
        self.assertEqual(out.getvalue() + err.getvalue(), "")
        content = next((Path(self.temp.name) / "logs").glob("providers-*.log")).read_text()
        self.assertIn("provider_library_log", content)
        self.assertIn("WARNING", content)
        self.assertNotIn("PRIVATE", content)
        self.assertIn('"logger": "dotenv.main"', content)

    def test_all_integer_5xx_and_wrapped_causes_are_unavailable(self):
        for status in (*range(500, 600), 499, 600, "500", None):
            with self.subTest(status=status):
                failure = RuntimeError("server")
                failure.status_code = status
                expected = ("provider_unavailable" if isinstance(status, int) and 500 <= status < 600
                            else "provider_invalid_request" if status == 499 else "provider_failed")
                self.assertEqual(error_code(failure), expected)
                wrapped = RuntimeError("wrapped")
                wrapped.__cause__ = failure
                self.assertEqual(error_code(wrapped), expected)

    async def test_500_without_explicit_retry_calls_once(self):
        failure = RuntimeError("server")
        failure.status_code = 500
        call = AsyncMock(side_effect=failure)
        with self.assertRaises(ProviderError) as caught:
            await invoke("aembedding", {}, call, {})
        self.assertEqual(caught.exception.code, "provider_unavailable")
        self.assertEqual(call.await_count, 1)

    async def test_only_specific_synthetic_500_precedes_generic_5xx(self):
        for status, message, expected in (
                (500, "LiteLLM: Empty or Invalid Response From LLM Endpoint", "provider_invalid_response"),
                (500, "internal server error", "provider_unavailable"),
                (500, "empty or invalid response from another service", "provider_unavailable"),
                (500, "received: none", "provider_unavailable"),
                (502, "empty or invalid response from llm endpoint", "provider_unavailable"),
                (503, "empty or invalid response from llm endpoint", "provider_unavailable"),
                (429, "empty or invalid response from llm endpoint", "provider_rate_limit")):
            with self.subTest(status=status, message=message):
                failure = RuntimeError(message)
                failure.status_code = status
                self.assertEqual(error_code(failure), expected)
                wrapped = RuntimeError("outer")
                wrapped.__cause__ = failure
                self.assertEqual(error_code(wrapped), expected)
                call = AsyncMock(side_effect=failure)
                with self.assertRaises(ProviderError) as caught:
                    await invoke("acompletion", {}, call, {})
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(call.await_count, 1)

    def test_runtime_isolation_is_set_before_import_and_restored(self):
        import os
        from llm.providers import runtime
        def import_sdk(name):
            self.assertEqual(name, "litellm")
            self.assertEqual(os.environ["LITELLM_LOCAL_MODEL_COST_MAP"], "True")
            self.assertEqual(os.environ["DEFAULT_MAX_RETRIES"], "0")
            return SimpleNamespace()
        with patch.dict(os.environ, LITELLM_LOCAL_MODEL_COST_MAP="False", DEFAULT_MAX_RETRIES="5"), \
                patch.object(runtime, "_sdk", None), \
                patch.object(runtime.importlib, "import_module", side_effect=import_sdk) as loader:
            sdk = runtime.litellm_sdk()
            os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "False"
            sdk.DEFAULT_MAX_RETRIES = 7
            self.assertIs(runtime.litellm_sdk(), sdk)
            self.assertEqual(os.environ["LITELLM_LOCAL_MODEL_COST_MAP"], "True")
            self.assertEqual(sdk.DEFAULT_MAX_RETRIES, 0)
            loader.assert_called_once_with("litellm")

    async def test_concurrent_log_scopes_and_logging_disk_failure(self):
        from llm.providers.runtime import logging_scope, diagnostic, logging_status
        async def write(name):
            with logging_scope(Path(self.temp.name) / name):
                await asyncio.sleep(.01)
                diagnostic("test_" + name)
        await asyncio.gather(write("first"), write("second"))
        first = next((Path(self.temp.name) / "first/logs").glob("*.log")).read_text()
        second = next((Path(self.temp.name) / "second/logs").glob("*.log")).read_text()
        self.assertIn("test_first", first)
        self.assertNotIn("test_second", first)
        self.assertIn("test_second", second)
        out, err = io.StringIO(), io.StringIO()
        before = logging_status()["failed_writes"]
        with logging_scope(Path(self.temp.name) / "disk-full"), patch("pathlib.Path.mkdir", side_effect=OSError("disk full")), \
                redirect_stdout(out), redirect_stderr(err):
            diagnostic("test_disk_failure")
        self.assertEqual(logging_status()["failed_writes"], before + 1)
        self.assertEqual(out.getvalue() + err.getvalue(), "")

    async def test_sdk_client_retry_delegation_and_invalid_response_envelope(self):
        client = SimpleNamespace(max_retries=3)
        call = AsyncMock(side_effect=TimeoutError())
        with self.assertRaises(ProviderError):
            await EmbeddingModel(model="test", client=client, embedding_fn=call).embed(["text"])
        self.assertEqual(call.await_count, 1)
        self.assertIs(call.call_args.kwargs["client"], client)
        call = AsyncMock(return_value={"choices": []})
        with self.assertRaisesRegex(ProviderError, "invalid response"):
            await invoke("acompletion", {}, call, {"max_attempts": 2, "delay_seconds": 0})
        self.assertEqual(call.await_count, 2)
        error = RuntimeError("Empty or invalid response from LLM endpoint")
        error.status_code = 400
        call = AsyncMock(side_effect=error)
        with self.assertRaisesRegex(ProviderError, "invalid request"):
            await invoke("acompletion", {}, call, {"max_attempts": 2, "delay_seconds": 0})
        self.assertEqual(call.await_count, 1)

    async def test_sdk_import_does_not_block_other_diagnostics(self):
        from llm.providers import runtime
        entered, released = threading.Event(), threading.Event()
        def slow_import(name):
            import os
            assert os.environ["DEFAULT_MAX_RETRIES"] == "0"
            entered.set()
            released.wait(2)
            return SimpleNamespace(cache=None)
        with patch.object(runtime, "_sdk", None), patch.object(runtime.importlib, "import_module", slow_import):
            pending = asyncio.create_task(asyncio.to_thread(runtime.litellm_sdk))
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            timer = threading.Timer(.4, released.set)
            timer.start()
            try:
                start = time.monotonic()
                runtime.diagnostic("test_during_import")
                self.assertLess(time.monotonic() - start, .3)
            finally:
                released.set()
                timer.cancel()
                await pending

    def test_fresh_import_disables_dotenv_before_sdk(self):
        code = '''
import os, tempfile
from pathlib import Path
import dotenv
def forbidden(*args, **kwargs):
    raise AssertionError("dotenv auto load")
dotenv.load_dotenv = forbidden
from llm.providers.runtime import litellm_sdk
os.environ['DEFAULT_MAX_RETRIES'] = '5'
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'False'
os.environ['LITELLM_MODE'] = 'PRODUCTION'  # Explicit host choice, not a runtime invariant.
with tempfile.TemporaryDirectory() as directory:
    os.chdir(directory)
    Path('.env').write_text('invalid text = "unterminated')
    sdk = litellm_sdk()
    assert os.environ['LITELLM_MODE'] == 'PRODUCTION'
    assert os.environ['DEFAULT_MAX_RETRIES'] == '0'
    assert os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] == 'True'
    assert sdk.DEFAULT_MAX_RETRIES == 0
    sdk.DEFAULT_MAX_RETRIES = 7
    assert litellm_sdk().DEFAULT_MAX_RETRIES == 0
'''
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("dotenv", result.stderr)


class EmbeddingTests(unittest.TestCase):
    def test_single_vector_ignores_index(self):
        for index in (0, 17, None, False, "arbitrary"):
            self.assertEqual(extract_single_embedding(response((index,))), [1., 2.])
        self.assertEqual(extract_single_embedding({"data": [{"embedding": (1., 2.)}]}), [1., 2.])

    def test_result_count_and_vector_integrity(self):
        for rows in (None, [], [{"embedding": [1.]}] * 2, {}, [{"embedding": []}], [{}]):
            with self.subTest(rows=rows), self.assertRaises(EmbeddingIntegrityError):
                extract_single_embedding({"data": rows})
        for vector in ([0, 0], [float("nan"), 1], [float("inf"), 1], [True, 1], "vector"):
            with self.subTest(vector=vector), self.assertRaises(EmbeddingIntegrityError):
                extract_single_embedding({"data": [{"embedding": vector}]})
        with self.assertRaises(EmbeddingIntegrityError):
            extract_single_embedding(response((0,)), dimensions=3)


class ExtractionModeTests(unittest.IsolatedAsyncioTestCase):
    def client(self, call, mode):
        return TripleExtractor(model="test", completion_fn=call, response_format={"type": "json_object"}).with_provider(
            {"max_attempts": 2, "delay_seconds": 0}).with_extraction({**{"repair_attempts": 2, "json_mode": "strict", "failure_policy": "required"}, "json_mode": mode}, default_prompt())

    async def test_modes_and_bounded_auto_fallback(self):
        good = {"choices": [{"message": {"content": '{"entities":[],"relations":[]}'}}]}
        for mode in ("strict", "off"):
            call = AsyncMock(return_value=good)
            await self.client(call, mode).extract([])
            self.assertEqual("response_format" in call.call_args.kwargs, mode == "strict")
        call = AsyncMock(side_effect=[None, None, good])
        await self.client(call, "auto").extract([])
        self.assertEqual(call.await_count, 3)
        self.assertNotIn("response_format", call.call_args.kwargs)
        self.assertIn("response_format", call.call_args_list[1].kwargs)
        broken = AsyncMock(return_value=None)
        with self.assertRaises(ProviderError):
            await self.client(broken, "auto").extract([])
        self.assertEqual(broken.await_count, 4)

    async def test_pronoun_literal_and_semantic_repairs_only(self):
        chunks = [{"id": "c1", "text": "Atlas is operated by Platform Team. It stores artifacts in Harbor."}]
        graph = {"entities": [{"id": "a", "name": "Atlas"}, {"id": "h", "name": "Harbor"}],
                 "relations": [{"source": "a", "target": "h", "source_id": "c1", "type": "STORED_IN",
                                "evidence": "It stores artifacts in Harbor."}]}
        def response_for(value):
            return {"choices": [{"message": {"content": json.dumps(value)}}]}
        good = response_for(graph)
        graph["relations"][0]["evidence"] = "Atlas stores artifacts in Harbor."
        call = AsyncMock(side_effect=[response_for(graph), good])
        events = []
        with diagnostic_scope(events.append):
            result = await self.client(call, "auto").extract(chunks)
        self.assertEqual(result["relations"][0]["evidence"], "It stores artifacts in Harbor.")
        self.assertEqual(call.await_count, 2)
        self.assertFalse(any(e.code in ("provider_retry", "provider_json_mode_fallback") for e in events))
