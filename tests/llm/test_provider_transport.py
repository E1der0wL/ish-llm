"""Actual SDK/HTTP attempts and caller-owned retry choices at the Adapter boundary."""

import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from llm.components.rag import EmbeddingModel, TripleExtractor
from llm.core.model_protocol import ModelMessage, ModelRequest, ModelRole
from llm.engines.base import BaseEngine
from llm.providers.requests import ProviderError
from llm.providers.runtime import configure_logging, litellm_sdk
from tests.llm.provider_probe import LocalServer


class ProviderTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        configure_logging(temporary.name)
        self.sdk = await asyncio.to_thread(litellm_sdk)
        self.enterContext(patch.object(self.sdk, "num_retries", None))

    async def test_real_http_default_and_provider_attempt_limits(self):
        with LocalServer() as server:
            server.fail = True
            for operation in ("completion", "embedding", "stream"):
                for options, expected in (({}, 1), ({"max_attempts": 2, "delay_seconds": 0}, 2)):
                    with self.subTest(operation=operation, options=options):
                        before = len(server.calls)
                        parameters = dict(server.params)
                        if operation == "stream":
                            request = ModelRequest([ModelMessage(ModelRole.USER, "probe")], parameters)
                            original = request.to_dict()
                            with self.assertRaises(Exception):
                                async for _ in BaseEngine().stream_completion(request, provider=options):
                                    pass
                            self.assertEqual(request.to_dict(), original)
                        else:
                            model = (EmbeddingModel if operation == "embedding" else TripleExtractor)(
                                **parameters).with_provider(options)
                            with self.assertRaises(ProviderError):
                                if operation == "embedding":
                                    await model.embed(["probe"])
                                else:
                                    await model._complete([ModelMessage(ModelRole.USER, "probe")])
                            self.assertEqual(model.params, parameters)
                            self.assertEqual(model.provider_options, options)
                        self.assertEqual(len(server.calls) - before, expected,
                                         "SDK defaults multiplied the selected Provider attempts")
                        self.assertNotIn("num_retries", parameters)
                        self.assertNotIn("max_retries", parameters)

    async def test_real_http_explicit_sdk_retry_is_not_multiplied(self):
        with LocalServer() as server:
            server.fail = True
            # LiteLLM 1.106.0's native num_retries=2 completion contract performs
            # three HTTP calls initially, then two wrapper attempts with retries=0.
            # Backend still enters the SDK exactly once; it must not multiply this.
            for settings, expected in (({"max_retries": 1}, 2), ({"num_retries": 2}, 5)):
                with self.subTest(settings=settings):
                    before = len(server.calls)
                    model = TripleExtractor(**server.params, **settings).with_provider(
                        {"max_attempts": 4, "delay_seconds": 0})
                    with patch.object(self.sdk, "acompletion", AsyncMock(wraps=self.sdk.acompletion)) as call:
                        with self.assertRaises(ProviderError):
                            await model._complete([ModelMessage(ModelRole.USER, "probe")])
                        self.assertEqual(call.await_count, 1)
                    self.assertEqual(len(server.calls) - before, expected)
                    self.assertEqual(model.params, {**server.params, **settings})

    async def test_real_http_injected_client_keeps_identity_and_retry_policy(self):
        from openai import AsyncOpenAI
        with LocalServer() as server:
            server.fail = True
            async with AsyncOpenAI(api_key=server.params["api_key"], base_url=server.params["api_base"],
                                   max_retries=2, timeout=5) as client:
                for operation in ("completion", "embedding"):
                    with self.subTest(operation=operation):
                        before = len(server.calls)
                        model = (EmbeddingModel if operation == "embedding" else TripleExtractor)(
                            **server.params, client=client).with_provider({"max_attempts": 4, "delay_seconds": 0})
                        name = "aembedding" if operation == "embedding" else "acompletion"
                        with patch.object(self.sdk, name, AsyncMock(wraps=getattr(self.sdk, name))) as call:
                            with self.assertRaises(ProviderError):
                                if operation == "embedding":
                                    await model.embed(["probe"])
                                else:
                                    await model._complete([ModelMessage(ModelRole.USER, "probe")])
                            self.assertEqual(call.await_count, 1)
                            self.assertIs(call.call_args.kwargs["client"], client)
                        self.assertEqual(len(server.calls) - before, 3)
                        self.assertEqual(client.max_retries, 2)
                        self.assertIs(model.params["client"], client)
                        self.assertNotIn("max_retries", model.params)
                        self.assertNotIn("num_retries", model.params)

    async def test_real_http_interruption_after_delta_never_retries(self):
        with LocalServer() as server:
            server.partial_stream = True
            request = ModelRequest([ModelMessage(ModelRole.USER, "probe")], server.params)
            original = request.to_dict()
            chunks = []
            with self.assertRaises(Exception):
                async for chunk in BaseEngine().stream_completion(request,
                        provider={"max_attempts": 3, "delay_seconds": 0}):
                    chunks.append(chunk)
            self.assertTrue(chunks, "The HTTP interruption must occur after an observed response delta")
            self.assertEqual(len(server.calls), 1)
            self.assertEqual(request.to_dict(), original)

    async def test_transport_preserves_explicit_nulls_host_policy_and_client_identity(self):
        good = {"choices": [{"message": {"content": "{}"}}]}
        for settings in ({"num_retries": None, "max_retries": None},
                         {"num_retries": 0, "max_retries": 0},
                         {"num_retries": 2, "max_retries": 3}):
            with self.subTest(settings=settings):
                model = TripleExtractor(model="test", **settings)
                with patch.object(self.sdk, "acompletion", AsyncMock(return_value=good)) as call:
                    await model._complete([])
                    for key, value in settings.items():
                        self.assertEqual(call.call_args.kwargs[key], value)
                self.assertEqual(model.params, {"model": "test", **settings})
        client = SimpleNamespace(max_retries=3)
        model = TripleExtractor(model="test", client=client)
        with patch.object(self.sdk, "num_retries", 4), \
                patch.object(self.sdk, "acompletion", AsyncMock(return_value=good)) as call:
            await model._complete([])
            self.assertNotIn("num_retries", call.call_args.kwargs)
            self.assertEqual(call.call_args.kwargs["max_retries"], 3)
            self.assertIs(call.call_args.kwargs["client"], client)
            self.assertEqual(self.sdk.num_retries, 4)
        self.assertEqual(model.params, {"model": "test", "client": client})
        self.assertEqual(client.max_retries, 3)
