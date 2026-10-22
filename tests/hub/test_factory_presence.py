"""Explicit factory collections retain their registrations and effects."""

from llm.providers.litellm import LiteLLMCompletionAdapter

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hub.backend.runtime import HubConfig, HubRuntime
from llm.components.prompts import PromptComponent
from llm.engines.loop import LoopEngine
from tests.llm.test_loop import ScriptedCompletion, chunk


class FalseyDict(dict):
    def __bool__(self):
        return False


class FalseyTuple(tuple):
    def __bool__(self):
        return False


class FalseyList(list):
    def __bool__(self):
        return False


class FactoryValidationTests(unittest.TestCase):
    def test_invalid_collections_and_entries_are_rejected_without_constructing_factories(self):
        calls = []
        def factory():
            calls.append('constructed')
            return LoopEngine()
        for field, values in (
            ('engine_factories', (False, 0, '', [], (), {'loop': None}, {1: factory}, {'': factory}, {'_hub_hidden': factory})),
            ('component_factories', (False, 0, '', {}, set(), [None], (factory, 1))),
        ):
            for value in values:
                with self.subTest(field=field, value=repr(value)), self.assertRaises((TypeError, ValueError)):
                    HubConfig(**{field: value})
        self.assertEqual(calls, [])


class FactoryConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def test_falsey_engine_mapping_runs_only_the_explicit_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            explicit_path, default_path = root / 'explicit.txt', root / 'default.txt'
            explicit = ScriptedCompletion([chunk('explicit', finish='stop')])
            default = ScriptedCompletion([chunk('default', finish='stop')])
            def injected(**request):
                with explicit_path.open('a') as stream:
                    stream.write('model\n')
                yield from explicit(**request)
            def fallback(**request):
                with default_path.open('a') as stream:
                    stream.write('model\n')
                yield from default(**request)
            settings = HubConfig(root / 'data', model='test/model', auto_title=False,
                engine_factories=FalseyDict(loop=lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=injected))))
            runtime = HubRuntime(settings)
            try:
                with patch('hub.backend.execution.retries.HubLoopEngine',
                           lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=fallback))):
                    await runtime.start()
                identifier = await runtime.new_session()
                await runtime.submit(identifier, 'use the explicit model')
                await runtime.sessions[identifier].run.wait_idle()
                handle = (await runtime.sessions[identifier].run.alist())[-1]
                result = await handle.aresult()
                self.assertEqual(str(result.status), 'completed')
                self.assertEqual({
                    'explicit_effects': explicit_path.read_text() if explicit_path.exists() else '',
                    'default_effects': default_path.read_text() if default_path.exists() else '',
                    'explicit_calls': len(explicit.requests), 'default_calls': len(default.requests),
                }, {'explicit_effects': 'model\n', 'default_effects': '', 'explicit_calls': 1, 'default_calls': 0})
            finally:
                await runtime.close()

    async def test_falsey_component_sequences_preserve_override_and_real_record(self):
        for collection in (FalseyTuple, FalseyList):
            with self.subTest(collection=collection.__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                effect = root / 'component-factory.txt'
                component = PromptComponent()
                def factory():
                    with effect.open('a') as stream:
                        stream.write('constructed\n')
                    return component
                runtime = HubRuntime(HubConfig(root / 'data', auto_title=False,
                    component_factories=collection([factory])))
                try:
                    await runtime.start()
                    prompts = await runtime.project.components.aget('prompts')
                    await prompts.acreate({'messages': [{'role': 'system', 'content': 'explicit record'}]},
                                          identifier='factory-record')
                    record = await prompts.aload('factory-record')
                    self.assertEqual(record['messages'], [{'role': 'system', 'content': 'explicit record'}])
                    self.assertEqual({'effects': effect.read_text() if effect.exists() else '',
                        'same_component': runtime.backend.project_manager.components.get('prompts') is component},
                        {'effects': 'constructed\n', 'same_component': True})
                finally:
                    await runtime.close()

    async def test_none_and_empty_collections_keep_core_registrations(self):
        for engines, components in ((None, None), ({}, ()), ({}, [])):
            with self.subTest(engines=engines, components=components), tempfile.TemporaryDirectory() as root:
                runtime = HubRuntime(HubConfig(root, auto_title=False,
                    engine_factories=engines, component_factories=components))
                try:
                    await runtime.start()
                    self.assertEqual((await runtime.snapshot()).engines, ('loop', 'graph'))
                    self.assertIn('prompts', (await runtime.project.aget_data()).components)
                finally:
                    await runtime.close()
