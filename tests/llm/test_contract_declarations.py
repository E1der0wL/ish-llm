"""Independent wire assertions for owner projections; no lifecycle-policy changes."""
from copy import deepcopy
from pathlib import Path
import unittest

from jsonschema import ValidationError

from llm.core.approval import RememberRequest, ApprovalOrigin, RememberedApproval
from llm.core.policies import ApprovalRule
from llm.components.tools.builtin.path_access import PathAccess
from llm.components.tools.builtin.output import ProcessObservation
from llm.components.tools.builtin.process_values import ProcessSnapshot, StoredOutput
from llm.components.workflows.inspection import InspectionLocation
from llm.services.runtime.client_inputs import InputReceipt
from llm.services.infrastructure.storage import revision_token


class ContractDeclarationTests(unittest.TestCase):
    def test_sdk_and_speech_emit_the_same_request_observation(self):
        import asyncio
        from types import SimpleNamespace
        from llm.providers.audio import speech_admission
        from llm.providers.runtime import diagnostic_scope
        from llm.providers.stream_values import ProviderSDKObservation

        events = []
        async def speech():
            async with speech_admission('aspeech', 'test-adapter'):
                pass
        with diagnostic_scope(events.append):
            ProviderSDKObservation.request('completion', {}, SimpleNamespace(cache=None))
            asyncio.run(speech())
        requests = [(event.code, event.details['operation']) for event in events
                    if event.code == 'provider_request']
        self.assertEqual(requests, [('provider_request', 'completion'), ('provider_request', 'aspeech')])
        response = next(event for event in events if event.code == 'speech_response')
        self.assertEqual(response.details['status'], 'completed')
        self.assertIsNone(response.details['usage'])

    def test_remember_request_excludes_lifecycle_and_preserves_unknown_basis(self):
        wire = {'id': 'rule', 'category': 'file.write', 'scope': 'session', 'max_risk': 30,
                'condition': {'implementation': 'schema', 'revision': 'v1', 'config': {}}}
        request = RememberRequest.read(wire)
        self.assertEqual(request.bound_scope('p', 's').to_dict(), {'project_id': 'p', 'session_id': 's'})
        self.assertEqual(request.rule.to_dict(), {'id': 'rule', 'category': 'file.write', 'max_risk': 30})
        unknown = {k: v for k, v in wire.items() if k != 'max_risk'}
        unknown.update(scope='project', basis='explicit_unknown')
        request = RememberRequest.read(unknown)
        self.assertEqual(request.bound_scope('p', 's').to_dict(), {'project_id': 'p', 'session_id': None})
        self.assertIsNone(request.rule.max_risk)
        self.assertEqual(request.rule.basis, 'explicit_unknown')
        for extra in ({'remembered': {}}, {'status': 'approved'}, {'max_risk': None}, {'max_risk': True}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                RememberRequest.read({**wire, **extra})
        schema = RememberRequest.schema(ApprovalRule.schema())
        schema['properties']['condition']['properties'].clear()
        self.assertIn('implementation', RememberRequest.schema(ApprovalRule.schema())['properties']['condition']['properties'])

    def test_remembered_edit_keeps_identity_scope_origin_and_closed_patch(self):
        request = RememberRequest.read({'id': 'rule', 'category': 'file.write', 'scope': 'project', 'max_risk': 30,
            'condition': {'implementation': 'schema', 'revision': 'v1', 'config': {}}})
        memory = RememberedApproval(1, request.bound_scope('p', 's'), request.condition,
            ApprovalOrigin('r', 'i', 1, 'fingerprint', 'time'), None)
        rule = ApprovalRule('rule', 'file.write', 30, memory)
        changed = rule.updated({'max_risk': 20})
        expected = deepcopy(rule.to_dict())
        expected['max_risk'] = 20
        expected['remembered']['revision'] = 2
        self.assertEqual(changed.to_dict(), expected)
        self.assertEqual(rule.remembered.revision, 1)
        for patch in ({}, {'scope': 'session'}, {'id': 'new'}, {'remembered': {}}, {'basis': 'explicit_unknown'}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                rule.updated(patch)
        unknown = ApprovalRule('rule', 'file.write', None, memory, 'explicit_unknown')
        with self.assertRaises(ValidationError):
            unknown.updated({'max_risk': 0})

    def test_path_schema_and_wire_share_fields_without_mutable_schema_leak(self):
        wire = {'roots': ['/tmp'], 'follow_symlinks': True}
        access = PathAccess.read(wire)
        self.assertEqual(access.roots, (Path('/tmp'),))
        self.assertEqual(access.to_dict(), wire)
        schema = PathAccess.schema()
        schema['properties']['roots']['items']['minLength'] = 0
        self.assertEqual(PathAccess.schema()['properties']['roots']['items']['minLength'], 1)
        self.assertEqual(PathAccess.read({'roots': [], 'follow_symlinks': False}).roots, ())
        for invalid in ({'roots': ['/tmp']}, {**wire, 'extra': True}, {**wire, 'roots': ['relative']}):
            with self.subTest(invalid=invalid), self.assertRaises((ValueError, ValidationError)):
                PathAccess.read(invalid)

    def test_input_projection_keeps_existing_fingerprint_and_missing_null(self):
        expected = {'content': 'hello', 'recipients': ['agent']}
        payload = InputReceipt.request_payload('hello', ('agent',))
        self.assertEqual(payload, expected)
        self.assertEqual(InputReceipt.bind('request', payload), revision_token({'kind': 'request', 'payload': expected}))
        explicit_null = InputReceipt.request_payload('hello', ['agent'], None)
        self.assertEqual(explicit_null, {**expected, 'request_context': None})
        self.assertNotEqual(InputReceipt.bind('request', payload), InputReceipt.bind('request', explicit_null))
        self.assertEqual(InputReceipt.instruction_payload('r', 'hint', None, reserved=False),
                         {'run_id': 'r', 'content': 'hint', 'targets': None})
        self.assertEqual(InputReceipt.replacement_payload('r', 'i', None),
                         {'run_id': 'r', 'instruction_id': 'i', 'content': None})

    def test_inspection_location_keeps_escaping_zero_and_explicit_null(self):
        self.assertEqual(InspectionLocation.from_path(('nodes', 'a/b~', 'input'), 'custom').to_dict(),
            {'path': '/nodes/a~1b~0/input', 'owner': 'custom', 'node_id': 'a/b~', 'workflow_path': []})
        self.assertEqual(InspectionLocation.from_path(('edges', 0, 'target'), 'workflows').to_dict(),
            {'path': '/edges/0/target', 'owner': 'workflows', 'edge_index': 0, 'workflow_path': []})
        self.assertEqual(InspectionLocation.from_path(('nodes', None), 'workflows').to_dict()['node_id'], None)
        self.assertEqual(InspectionLocation.from_path((), 'workflows').to_dict(), {'path': '', 'owner': 'workflows'})

    def test_live_observation_does_not_require_published_output_references(self):
        snapshot = ProcessSnapshot('p', 'running', None, 10, ['cmd'], '/tmp', 'start', None, False, '', '', False)
        record = StoredOutput.from_snapshot(snapshot, sources=None, stdout_chars=0, stderr_chars=0, preview_chars=0)
        observation = ProcessObservation('runtime', record)
        self.assertEqual(observation.to_dict(), {'source': 'runtime', 'record': record.to_dict()})
        with self.assertRaisesRegex(ValueError, 'Invalid process observation'):
            ProcessObservation('unknown', record)

    def test_text_readers_keep_unicode_boundaries_and_distinct_error_contracts(self):
        import io
        from unittest.mock import patch
        from llm.services.infrastructure.text_stream import text_blocks
        from llm.services.infrastructure.captured_text import CapturedText
        with patch('llm.services.infrastructure.text_stream.BUFFER_BYTES', 2):
            self.assertEqual(''.join(text_blocks(io.BytesIO('가😀끝'.encode('utf-8')))), '가😀끝')
            with self.assertRaises(UnicodeDecodeError):
                list(text_blocks(io.BytesIO(b'\xff')))
        capture = CapturedText()
        self.addCleanup(capture.close)
        capture.extend(b'\xea\xb0')
        self.assertEqual(capture.read(0, None), '')
        capture.extend(b'\x80\xff')
        capture.finish()
        self.assertEqual(capture.read(0, None), '가\ufffd')
        self.assertEqual(capture.characters, 2)

    def test_text_line_index_preserves_all_existing_line_endings(self):
        import io
        from llm.components.tools.builtin.file_text import TextIndex
        endings = ['\r\n', '\n', '\r', '\v', '\f', '\x1c', '\x1d', '\x1e', '\x85', '\u2028', '\u2029']
        source = ''.join('가😀' + ending for ending in endings) + '끝'
        index = TextIndex(io.BytesIO(source.encode('utf-8')), max_bytes=None)
        self.addCleanup(index.close)
        self.assertEqual(index.count, len(endings) + 1)
        self.assertEqual([index.read(i + 1) for i in range(index.count)], source.splitlines(keepends=True))
        self.assertEqual([index.read(i + 1, keepends=False) for i in range(index.count)], source.splitlines())

    def test_cancelled_and_unexecuted_receipts_keep_distinct_wire_and_evidence(self):
        import json
        from llm.core.step_event_metadata import StepEventMetadata
        from llm.engines.loop.records import LoopToolRecord
        from llm.engines.loop.review import LoopIterationRecord
        from llm.services.runtime.tool_errors import ToolRedirected, ToolExecutionError
        error = ToolRedirected(['instruction'], effect=ToolExecutionError.NO_EFFECT)
        self.assertEqual(StepEventMetadata.cancelled('execution_redirected'), {'reason': 'execution_redirected'})
        self.assertEqual(StepEventMetadata.cancelled('execution_redirected', error.diagnostic),
                         {'reason': 'execution_redirected', 'diagnostic': error.diagnostic.to_dict()})
        args = dict(name='write', arguments={'text': 'keep'}, boundary='next', instructions=['instruction'])
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                wire = LoopToolRecord.deferred(**args, cancelled=cancelled)
                self.assertEqual('cancelled' in wire, cancelled)
                self.assertEqual(json.loads(LoopToolRecord(wire).deferred_content(**args)), {
                    'status': 'cancelled_no_effect' if cancelled else 'not_executed',
                    'reason': 'superseded_by_instruction', 'instruction_ids': ['instruction']})
                with self.assertRaisesRegex(ValueError, 'selection changed'):
                    LoopToolRecord(wire).deferred_content(**{**args, 'instructions': ['another']})
        self.assertFalse(LoopIterationRecord.is_interrupted({'status': 'not_applied'}))
        with self.assertRaisesRegex(ValueError, 'interrupted Loop iteration'):
            LoopIterationRecord.is_interrupted({'status': 'not_applied', 'redirected': False})
        outcome = LoopToolRecord.deferred_outcome('prior_call').to_dict()
        self.assertEqual(outcome['status'], 'not_executed')
        self.assertIsNone(outcome['step_id'])
        self.assertEqual(outcome['diagnostic']['details'], {'after_call_id': 'prior_call'})

    def test_response_fingerprint_distinguishes_remember_omission_without_aliasing(self):
        from llm.core.interactions import InteractionResponse
        from llm.services.runtime.client_inputs import response_payload
        response = InteractionResponse(request_id='a' * 32, request_revision=1, request_fingerprint='b' * 64,
                                       option_id='yes', value=True)
        remembered = {'scope': 'session', 'condition': {'config': {'const': 'keep'}}}
        expected = {'run_id': 'r', 'response': response.comparable()}
        self.assertEqual(response_payload('r', response), expected)
        self.assertEqual(response_payload('r', response, remember=None), {**expected, 'remember': None})
        payload = response_payload('r', response, remember=remembered)
        remembered['condition']['config']['const'] = 'changed'
        self.assertEqual(payload['remember']['condition']['config']['const'], 'keep')
