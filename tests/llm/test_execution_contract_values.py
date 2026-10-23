"""Independent wire and corruption assertions for typed execution/storage evidence."""

from copy import deepcopy
import hashlib
import json
import unittest

from llm.core.agents import AgentBinding, AgentCatalog, AgentRecord
from llm.core.checkpoints import CheckpointConversation
from llm.core.steering import InstructionCheckpoint
from llm.services.runtime.checkpoints import checkpoint_message_ids
from llm.services.runtime.steering import instruction_records


class ExecutionContractValueTests(unittest.TestCase):
    def test_binding_wire_detached_properties_and_required_identity(self):
        definition = {'engine': 'test', 'system_prompt': None, 'metadata': {'items': [0, False]}}
        revision = hashlib.sha256(json.dumps(definition, ensure_ascii=False, sort_keys=True,
                                             allow_nan=False).encode()).hexdigest()
        wire = {'schema_version': 1, 'project_id': 'a' * 32, 'agent_id': 'worker',
                'definition': deepcopy(definition), 'revision': revision, 'engine': 'test'}
        binding = AgentBinding.create('a' * 32, 'worker', definition)
        self.assertEqual(binding.to_dict(), wire)
        self.assertEqual(AgentBinding.read(wire), binding)
        definition['metadata']['items'].clear()
        binding.definition['metadata']['items'].clear()
        binding.to_dict()['definition']['metadata']['items'].clear()
        self.assertEqual(binding.to_dict(), wire)
        self.assertEqual(binding.project_id, 'a' * 32)
        self.assertEqual(binding.agent_id, 'worker')
        self.assertEqual(binding.engine, 'test')
        self.assertEqual(AgentBinding.metadata(binding), {'agent_binding': wire})
        invalid = [{key: value for key, value in wire.items() if key != missing} for missing in wire]
        invalid += [{**wire, 'schema_version': value} for value in (None, True, 1.0, '1', 2)]
        invalid += [{**wire, key: value} for key, value in (
            ('engine', 'other'), ('revision', '0' * 64), ('definition', {}), ('project_id', '../other'))]
        for value in invalid:
            before = deepcopy(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                AgentBinding.read(value)
            self.assertEqual(value, before)

    def test_record_and_catalog_updates_have_no_nested_aliases_or_missing_fallback(self):
        record = AgentRecord.create('a' * 32, 'worker', {'engine': 'test'})
        self.assertEqual(record.definition, {'engine': 'test'})
        record.definition['engine'] = 'changed'
        self.assertEqual(record.definition, {'engine': 'test'})
        original = {'schema_version': 1, 'project_id': 'a' * 32, 'records': {'worker': 'b' * 64}}
        catalog = AgentCatalog.read(original, project_id='a' * 32)
        expected = deepcopy(original)
        original['records'].clear()
        catalog.to_dict()['records'].clear()
        self.assertEqual(catalog.to_dict(), expected)
        with self.assertRaises(TypeError):
            catalog.records['worker'] = 'c' * 64
        updated = catalog.with_record('other', 'c' * 64).without_record('worker')
        self.assertEqual(updated.to_dict(), {**expected, 'records': {'other': 'c' * 64}})
        self.assertEqual(catalog.to_dict(), expected)
        invalid = [{k: v for k, v in expected.items() if k != missing} for missing in expected]
        invalid += [{**expected, 'records': None}, {**expected, 'records': {'worker': None}},
                    {**expected, 'schema_version': True}, {**expected, 'schema_version': 1.0},
                    {**expected, 'project_id': 'b' * 32}, {**expected, 'extra': False}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                AgentCatalog.read(value, project_id='a' * 32)

    def test_checkpoint_reference_owner_preserves_engine_header_and_requires_original_ids(self):
        header = {'message_ids': ['history'], 'input_message_id': 'input', 'opaque': {'engine': 'private'}}
        original = deepcopy(header)
        references = CheckpointConversation.from_header(header)
        self.assertEqual(references.to_dict(), {'message_ids': ['history'], 'input_message_id': 'input'})
        self.assertEqual(header, original)
        header['message_ids'].clear()
        self.assertEqual(references.message_ids, ('history',))
        for invalid in ({}, {'message_ids': []}, {'input_message_id': 'input'},
                        {'message_ids': None, 'input_message_id': 'input'},
                        {'message_ids': [''], 'input_message_id': 'input'},
                        {'message_ids': [], 'input_message_id': ''}):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                CheckpointConversation.from_header(invalid)

    def test_instruction_updates_preserve_nested_receipts_and_original_record(self):
        payload = {'kind': 'instruction', 'status': 'input', 'target_scope': 'child',
                   'message_ids': ['instruction'], 'private': {'keep': [1]}}
        envelope = {'node_type': 'engine_record', 'engine_scope': 'child', 'checkpoint_name': 'loop',
                    'local_key': 'steering:1', 'status': 'completed', 'container': True,
                    'requires_retry': False, 'payload': payload, 'tool_usage': {'receipt': 'original'}}
        checkpoint = {'header': {'message_ids': ['history'], 'input_message_id': 'input'},
                      'records': {'child:steering:1': envelope}}
        original = deepcopy(checkpoint)
        self.assertEqual(checkpoint_message_ids(checkpoint), {'history', 'input', 'instruction'})
        _, value = next(instruction_records(checkpoint))
        self.assertEqual(value.target_scope, 'child')
        self.assertEqual(value.message_ids, ('instruction',))
        value.to_dict()['message_ids'].clear()
        updated = InstructionCheckpoint.replace_message_ids(envelope, [])
        expected = deepcopy(envelope)
        expected['payload']['message_ids'] = []
        self.assertEqual(updated, expected)
        self.assertEqual(checkpoint, original)
        invalid = [{k: v for k, v in payload.items() if k != missing}
                   for missing in ('kind', 'status', 'target_scope', 'message_ids')]
        invalid += [{**payload, 'message_ids': ['instruction', 'instruction']},
                    {**payload, 'message_ids': None}, {**payload, 'status': 'completed'}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                InstructionCheckpoint.read(value)
        corrupt = deepcopy(checkpoint)
        corrupt['records']['child:steering:1']['payload']['target_scope'] = 'other'
        with self.assertRaises(ValueError):
            checkpoint_message_ids(corrupt)

    def test_catalog_reader_rejects_non_json_mapping_without_normalizing_it(self):
        from types import MappingProxyType
        records = MappingProxyType({'worker': 'b' * 64})
        raw = {'schema_version': 1, 'project_id': 'a' * 32, 'records': records}
        with self.assertRaises((TypeError, ValueError)):
            AgentCatalog.read(raw, project_id='a' * 32)
        self.assertIs(raw['records'], records)
