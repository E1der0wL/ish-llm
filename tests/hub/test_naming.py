"""Titles inherit the source provider without inheriting its response contract."""

from llm.providers.litellm import LiteLLMCompletionAdapter

import tempfile
import unittest
from unittest.mock import patch

from hub.backend.project.naming import TITLE_ENGINE, TitleEngine
from hub.backend.runtime import HubConfig, HubRuntime
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from tests.hub.test_features import chunk
from tests.hub.test_live import until


class NamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_greeting_does_not_start_title_call_but_later_work_can(self):
        titles = []
        def reply(**request):
            yield chunk(content='안녕하세요!', finish='stop')
        def title(**request):
            titles.append(request)
            yield chunk(content='설정 확인', finish='stop')
        def factory(config):
            return LargeLanguageModel(config.workspace, engines={
                'loop':LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=reply)),
                TITLE_ENGINE:TitleEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=title))})
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model='test/chat'), factory)
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            sid = await runtime.new_session()
            session = runtime.sessions[sid]
            await runtime.submit(sid, '안녕?')
            await session.run.wait_idle()
            await runtime.snapshot()
            await until(lambda: not runtime.namer.tasks)
            await runtime.snapshot()
            self.assertEqual(titles, [])
            self.assertFalse(runtime.namer.tasks)
            await runtime.submit(sid, '프로젝트 설정에 대해 설명해줘')
            await session.run.wait_idle()
            await runtime.snapshot()
            await until(lambda: not runtime.namer.tasks)
            self.assertEqual(len(titles), 1)

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
                "review": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=answer)), TITLE_ENGINE: TitleEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=title))})
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, agent_definition={"engine": "review"}), factory)
            try:
                await runtime.start()
                identifier = await runtime.new_session()
                session = runtime.sessions[identifier]
                settings = {'completion': {'model': 'test/session', 'api_base': 'http://original.invalid/v1', 'api_key': 'test-key', 'response_format': {'type': 'json_object'}, 'max_tokens': 1000, 'stop': ['END']}}
                await session.run.shutdown()
                snapshot = await runtime.project.agents.asnapshot('assistant')
                await runtime.project.agents.arevise('assistant', {**snapshot['definition'], **settings}, expected_revision=snapshot['revision'])
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
                settings["completion"]["api_base"] = "http://fixed.invalid/v1"
                await session.run.shutdown()
                snapshot = await runtime.project.agents.asnapshot('assistant')
                await runtime.project.agents.arevise('assistant', {**snapshot['definition'], **settings}, expected_revision=snapshot['revision'])
                await session.run.start()
                # An external Agent edit has no Hub settings event. The public
                # catalog reconciles it at its expiry, without re-reading title
                # history on every frame in between.
                from time import monotonic
                from hub.backend.observability.reads import ObservationReads
                with patch('hub.backend.observability.reads.monotonic',
                           return_value=monotonic() + ObservationReads.RECONCILE_SECONDS):
                    await runtime.snapshot()
                await until(lambda: not runtime.namer.tasks)
                self.assertEqual((await session.aget_data()).title, "Fixed title")
                self.assertEqual(calls[1]["api_base"], "http://fixed.invalid/v1")
                self.assertEqual(len(await session.aconversation()), 2)
                self.assertEqual((await runtime.project.agents.aload("assistant"))["completion"], settings["completion"])
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
                "loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=completion)), TITLE_ENGINE: TitleEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=completion))})
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model='test/chat'), factory)
            try:
                await runtime.start()
                snapshot = await runtime.project.agents.asnapshot('_hub_title')
                await runtime.project.agents.arevise('_hub_title', {
                    **snapshot['definition'], 'completion':{'model':'test/title', 'max_tokens':1024}
                }, expected_revision=snapshot['revision'])
                identifier = await runtime.new_session()
                await runtime.submit(identifier, "Explain the saved project configuration")
                await runtime.sessions[identifier].run.wait_idle()
                await runtime.snapshot()
                await until(lambda: not runtime.namer.tasks)
                self.assertEqual([call["model"] for call in calls], ["test/chat", "test/title"])
                self.assertEqual(calls[-1]["max_tokens"], 1024)
            finally:
                await runtime.close()
