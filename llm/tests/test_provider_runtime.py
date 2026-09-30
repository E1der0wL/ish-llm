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
from unittest.mock import AsyncMock, patch

from llm.components.rag import EmbeddingModel, TripleExtractor
from llm.components.rag.prompts import extraction_defaults, default_prompt
from llm.providers.embeddings import validate_embeddings, normalize_litellm_embeddings, EmbeddingIntegrityError
from llm.providers.requests import invoke, ProviderError
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
        self.assertEqual(sdk.DEFAULT_MAX_RETRIES, 0)
        self.assertIsNone(sdk.cache)
        original = response()
        with patch.object(sdk, "aembedding", AsyncMock(return_value=original)) as call:
            output = await EmbeddingModel(model="openai/test", num_retries=9, max_retries=9,
                caching=True, cache={"no-store": False}).embed(["a", "b"])
        self.assertIs(output, original)
        request = call.call_args.kwargs
        self.assertEqual((request["num_retries"], request["max_retries"]), (0, 0))
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
                self.assertEqual(await invoke("aembedding", {}, call, {"delay_seconds": 0}), "ok")
            self.assertEqual(call.await_count, 2)
        for status in (429, 502, 503, 504):
            failure = RuntimeError("server")
            failure.status_code = status
            call = AsyncMock(side_effect=failure)
            with self.assertRaises(ProviderError):
                await invoke("aembedding", {}, call, {"delay_seconds": 0})
            self.assertEqual(call.await_count, 2)
        self.assertEqual(len(observed), 6)
        self.assertTrue(any(e.code == "provider_retry" for e in events))

    async def test_permanent_errors_never_retry(self):
        for status in (400, 401, 403, 404):
            failure = RuntimeError("invalid")
            failure.status_code = status
            call = AsyncMock(side_effect=failure)
            with self.assertRaises(ProviderError):
                await invoke("aembedding", {}, call, {"delay_seconds": 0})
            self.assertEqual(call.await_count, 1)
        for failure in (ValueError("semantic validation"), EmbeddingIntegrityError("embedding_index_corruption", "bad")):
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
            task = asyncio.create_task(invoke("aembedding", {}, call, {"delay_seconds": 5, "max_delay_seconds": 5}))
            await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, .2)

    async def test_logs_do_not_write_sdk_text_or_secrets_to_tui_or_file(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            for name in ("LiteLLM", "LiteLLM Router", "httpx", "httpcore", "py.warnings"):
                logging.getLogger(name).warning("api_key=PRIVATE document PRIVATE-DOC")
        self.assertEqual(out.getvalue() + err.getvalue(), "")
        content = next((Path(self.temp.name) / "logs").glob("providers-*.log")).read_text()
        self.assertIn("provider_library_log", content)
        self.assertIn("WARNING", content)
        self.assertNotIn("PRIVATE", content)

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

    async def test_sdk_client_retry_guard_and_invalid_response_envelope(self):
        with self.assertRaisesRegex(ValueError, "max_retries=0"):
            await EmbeddingModel(model="test", client=SimpleNamespace(max_retries=3), embedding_fn=AsyncMock()).embed(["text"])
        call = AsyncMock(return_value={"choices": []})
        with self.assertRaisesRegex(ProviderError, "invalid response"):
            await invoke("acompletion", {}, call, {"delay_seconds": 0})
        self.assertEqual(call.await_count, 2)
        error = RuntimeError("Empty or invalid response from LLM endpoint")
        error.status_code = 400
        call = AsyncMock(side_effect=error)
        with self.assertRaisesRegex(ProviderError, "invalid request"):
            await invoke("acompletion", {}, call, {"delay_seconds": 0})
        self.assertEqual(call.await_count, 1)

    async def test_sdk_import_does_not_block_other_diagnostics(self):
        from llm.providers import runtime
        entered, released = threading.Event(), threading.Event()
        def slow_import(name):
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
with tempfile.TemporaryDirectory() as directory:
    os.chdir(directory)
    Path('.env').write_text('invalid text = "unterminated')
    sdk = litellm_sdk()
    assert os.environ['LITELLM_MODE'] == 'PRODUCTION'
    assert sdk.DEFAULT_MAX_RETRIES == 0
'''
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("dotenv", result.stderr)


class EmbeddingTests(unittest.TestCase):
    def test_valid_and_reordered(self):
        self.assertEqual(validate_embeddings(response(), 2), [[1., 2.], [3., 4.]])
        self.assertEqual(validate_embeddings(response((1, 0)), 2), [[3., 4.], [1., 2.]])

    def test_all_integrity_failures(self):
        invalid = [response((0, 0)), response((0, 2)), response((False, 1)), response((None, 1)), response((0,)),
                   response(vectors=[[0., 0.], [1., 2.]]), response(vectors=[[1.], [1., 2.]]),
                   response(vectors=[[float('nan'), 1.], [1., 2.]]), response(vectors=[[float('inf'), 1.], [1., 2.]])]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(EmbeddingIntegrityError):
                validate_embeddings(item, 2)

    def test_known_partial_merge_preserves_order_then_validates(self):
        events = []
        raw = response((0, 0), _hidden_params={"cache_hit": True})
        with diagnostic_scope(events.append):
            normalized = normalize_litellm_embeddings(raw, 2, cache_read_allowed=True,
                cache_object=object(), merge_positions=[0])
        self.assertEqual(validate_embeddings(normalized, 2), [[1., 2.], [3., 4.]])
        self.assertEqual([r["index"] for r in raw["data"]], [0, 0])
        self.assertEqual(events[0].code, "embedding_index_normalized")

    def test_no_guess_when_disabled_unproven_or_malformed(self):
        raw = response((0, 0), _hidden_params={"cache_hit": True})
        for options in ({}, {"cache_read_allowed": True, "cache_object": object()},
                        {"cache_read_allowed": True, "cache_object": None, "merge_positions": [0]}):
            with self.assertRaises(EmbeddingIntegrityError):
                validate_embeddings(normalize_litellm_embeddings(raw, 2, **options), 2)
        for invalid in (response((0, 4), _hidden_params={"cache_hit": True}),
                        response((0, 0), vectors=[[0, 0], [1, 2]], _hidden_params={"cache_hit": True})):
            with self.assertRaises(EmbeddingIntegrityError):
                validate_embeddings(normalize_litellm_embeddings(invalid, 2,
                    cache_read_allowed=True, cache_object=object(), merge_positions=[0]), 2)


class ExtractionModeTests(unittest.IsolatedAsyncioTestCase):
    def client(self, call, mode):
        return TripleExtractor(model="test", completion_fn=call, response_format={"type": "json_object"}).with_provider(
            {"delay_seconds": 0}).with_extraction({**extraction_defaults(), "json_mode": mode}, default_prompt())

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
