"""Titles inherit the source provider without inheriting its response contract."""

import tempfile
import unittest

from hub.backend.naming import TITLE_ENGINE, TitleEngine
from hub.backend.runtime import HubConfig, HubRuntime
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from tests.hub.test_features import chunk
from tests.hub.test_live import until


class NamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_inherit_session_provider_retry_after_config_change_and_isolate_history(self):
        calls = []
        def answer(**kwargs):
            yield chunk(content="Useful response", finish="stop")
        def title(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("title provider failed")
            yield chunk(content="Fixed title", finish="stop")
        def factory(config):
            return LargeLanguageModel(config.workspace, engines={
                "review": LoopEngine(completion_fn=answer), TITLE_ENGINE: TitleEngine(completion_fn=title)})
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, engine="review"), factory)
            try:
                await runtime.start()
                identifier = await runtime.new_session()
                session = runtime.sessions[identifier]
                settings = {"parameters": {"engines": {"review": {'config': {'completion': {'model': 'test/session', 'api_base': 'http://original.invalid/v1', 'api_key': 'test-key', 'response_format': {'type': 'json_object'}, 'max_tokens': 1000, 'stop': ['END']}}}}}}
                await session.run.shutdown()
                await session.asave(config=settings)
                await session.run.start()
                await runtime.submit(identifier, "A request")
                await session.run.wait_idle()
                await runtime.snapshot()
                await until(lambda: not runtime.namer.tasks)
                self.assertIn(identifier, runtime.namer.errors)
                self.assertEqual(calls[0]["model"], "test/session")
                self.assertEqual(calls[0]["api_key"], "test-key")
                for key in ("response_format", "tools", "max_tokens", "stop"):
                    self.assertNotIn(key, calls[0])
                await runtime.snapshot()
                await until(lambda: not runtime.namer.tasks)
                self.assertEqual(len(calls), 1)
                settings["parameters"]["engines"]["review"]["config"]["completion"]["api_base"] = "http://fixed.invalid/v1"
                await session.run.shutdown()
                await session.asave(config=settings)
                await session.run.start()
                await runtime.snapshot()
                await until(lambda: not runtime.namer.tasks)
                self.assertEqual((await session.aget_data()).title, "Fixed title")
                self.assertEqual(calls[1]["api_base"], "http://fixed.invalid/v1")
                self.assertEqual(len(await session.aconversation()), 2)
                self.assertEqual((await session.aget_data()).config, settings)
                self.assertNotIn(identifier, runtime.namer.errors)
            finally:
                await runtime.close()

    async def test_explicit_title_model_overrides_source(self):
        calls = []
        def completion(**kwargs):
            calls.append(kwargs)
            yield chunk(content="Title", finish="stop")
        def factory(config):
            return LargeLanguageModel(config.workspace, engines={
                "loop": LoopEngine(completion_fn=completion), TITLE_ENGINE: TitleEngine(completion_fn=completion)})
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, project_config={"parameters": {"engines": {
                "loop": {'config': {'completion': {'model': 'test/chat'}}},
                TITLE_ENGINE: {'config': {'completion': {'model': 'test/title', 'max_tokens': 1024}}}}}}), factory)
            try:
                await runtime.start()
                identifier = await runtime.new_session()
                await runtime.submit(identifier, "hello")
                await runtime.sessions[identifier].run.wait_idle()
                await runtime.snapshot()
                await until(lambda: not runtime.namer.tasks)
                self.assertEqual([call["model"] for call in calls], ["test/chat", "test/title"])
                self.assertEqual(calls[-1]["max_tokens"], 1024)
            finally:
                await runtime.close()
