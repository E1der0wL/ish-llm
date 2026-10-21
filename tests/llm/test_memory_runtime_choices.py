"""Memory runtime injection preserves explicit falsey callables and rejects invalid choices."""

import unittest
from unittest.mock import patch

from llm.components.memory.processing import MemoryProcessor
from tests.llm import test_memory_processing as memory


class FalseyCallable:
    def __init__(self, callback):
        self.callback = callback

    def __bool__(self):
        return False

    def __call__(self, *args, **kwargs):
        return self.callback(*args, **kwargs)


class MemoryRuntimeChoiceTests(unittest.TestCase):
    def test_explicit_falsey_model_and_counter_keep_their_identity(self):
        model = FalseyCallable(lambda **kwargs: iter(()))
        counter = FalseyCallable(lambda text: 0)
        processor = MemoryProcessor(None, completion_fn=model, token_counter=counter, priority=0)
        self.assertIs(processor.completion_fn, model)
        self.assertIs(processor.token_counter, counter)
        self.assertEqual(processor.priority, 0)

    def test_wrong_runtime_choices_fail_at_the_owner_before_any_call(self):
        for value in (False, 0, '', [], {}, object()):
            for field in ('completion_fn', 'token_counter'):
                with self.subTest(field=field, value=repr(value)), self.assertRaises(TypeError):
                    MemoryProcessor(None, **{field: value})
        for value in (None, False, True, 1.5, '0'):
            with self.subTest(priority=value), self.assertRaises(TypeError):
                MemoryProcessor(None, priority=value)

    def test_none_model_selects_default_and_explicit_integer_priority_is_preserved(self):
        def default(**kwargs):
            raise AssertionError('configuration must not invoke the model')
        with patch('llm.components.memory.processing.completion', default):
            processor = MemoryProcessor(None, completion_fn=None, priority=-1)
        self.assertIs(processor.completion_fn, default)
        self.assertIsNone(processor.token_counter)
        self.assertEqual(processor.priority, -1)


class MemoryRuntimeConsumerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = memory.MemoryProcessingTests.asyncSetUp
    configure = memory.MemoryProcessingTests.configure
    run_loop = memory.MemoryProcessingTests.run_loop

    async def test_falsey_injected_auxiliary_model_is_the_only_actual_model_effect(self):
        intended = self.root / 'injected-model-effects.txt'
        unexpected = self.root / 'default-model-effects.txt'
        def injected(**request):
            with intended.open('a') as stream:
                stream.write('auxiliary\n')
            yield from self.aux(**request)
        def default(**request):
            with unexpected.open('a') as stream:
                stream.write('unexpected default\n')
            yield from self.aux(**request)
        self.component.completion_fn = FalseyCallable(injected)
        await self.configure(extract=True)
        configuration = await self.memory.aget_config()
        with patch('llm.components.memory.processing.completion', default):
            run, main = await self.run_loop()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual({'injected': intended.read_text() if intended.exists() else '',
                          'default': unexpected.read_text() if unexpected.exists() else '',
                          'auxiliary_calls': len(self.aux.requests), 'main_calls': len(main.requests)},
                         {'injected': 'auxiliary\n', 'default': '', 'auxiliary_calls': 1, 'main_calls': 1})
        self.assertEqual(await self.memory.aget_config(), configuration)
