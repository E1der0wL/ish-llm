"""Memory's stored Schema and runtime processing boundary enforce one current contract."""

from copy import deepcopy
import unittest

from jsonschema import Draft202012Validator

from llm.components.memory.component import MemoryComponent
from llm.components.memory.processing import resolve_processing_config
from tests.llm import test_memory_processing as memory


class MemoryProcessingConfigTests(unittest.TestCase):
    def test_output_limit_rejects_invalid_values_at_both_boundaries(self):
        component = MemoryComponent()
        for value in (False, True, 0, -1, 1.0, 1.5, '', [], {}, None):
            config = {'policy': {'processing': {'max_output_chars': value}}}
            before = deepcopy(config)
            for name, validate in (('component', component.validate_config), ('runtime', resolve_processing_config)):
                with self.subTest(value=value, boundary=name), self.assertRaises((TypeError, ValueError)):
                    validate(config)
                self.assertEqual(config, before)

    def test_processing_modes_and_unknown_fields_are_closed_at_runtime(self):
        component = MemoryComponent()
        for group, key, values in (
            ('config', 'summary_format', (None, False, '', 'unsupported')),
            ('config', 'extract_scope', (None, False, '', 'unsupported')),
            ('policy', 'failure_mode', (None, False, '', 'unsupported')),
            ('config', 'unexpected_processing_option', (True,)),
        ):
            for value in values:
                config = {group: {'processing': {key: value}}}
                before = deepcopy(config)
                for name, validate in (('component', component.validate_config), ('runtime', resolve_processing_config)):
                    with self.subTest(key=key, value=value, boundary=name), self.assertRaises((TypeError, ValueError)):
                        validate(config)
                    self.assertEqual(config, before)

    def test_explicit_disabled_nullable_and_minimum_values_survive(self):
        component = MemoryComponent()
        config = {'config': {'processing': {'priority': 0, 'summary_format': 'text', 'extract_scope': 'session'}},
                  'policy': {'processing': {'recall': False, 'summarize': False, 'extract': False,
                      'recall_every': 0, 'context_tokens': None, 'timeout_seconds': None,
                      'provider': None, 'max_output_chars': 1, 'failure_mode': 'raise'}}}
        before = deepcopy(config)
        component.validate_config(config)
        Draft202012Validator(component.describe_config()).validate(config)
        resolved = resolve_processing_config(config)
        self.assertEqual(resolved, {**config['config']['processing'], **config['policy']['processing']})
        resolved['priority'] = 20
        self.assertEqual(config, before)


class MemoryProcessingConfigConsumerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = memory.MemoryProcessingTests.asyncSetUp
    configure = memory.MemoryProcessingTests.configure

    async def test_invalid_output_limit_preserves_all_stored_bytes_and_does_not_call_model(self):
        config = await self.memory.aget_config()
        before = {str(p.relative_to(self.project.paths.root)): p.read_bytes()
                  for p in self.project.paths.root.rglob('*') if p.is_file()}
        for value in (False, 0, 1.0, None):
            invalid = deepcopy(config)
            invalid.setdefault('policy', {}).setdefault('processing', {})['max_output_chars'] = value
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                await self.memory.aconfigure(invalid)
            after = {str(p.relative_to(self.project.paths.root)): p.read_bytes()
                     for p in self.project.paths.root.rglob('*') if p.is_file()}
            self.assertEqual(after, before)
        self.assertEqual(self.aux.requests, [])
