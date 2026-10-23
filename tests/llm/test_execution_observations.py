"""Stored observations preserve absence and detach payloads without authority."""

from copy import deepcopy
import unittest

from llm.core.contracts import Diagnostic, OperationProgress, ResourceRef
from llm.core.execution_observations import ExecutionObservations
from llm.core.results import EngineOutput


class ExecutionObservationTests(unittest.TestCase):
    def test_run_final_and_optional_step_keep_their_existing_null_contracts(self):
        self.assertIsNone(ExecutionObservations.run_output({}))
        self.assertIsNone(ExecutionObservations.step_output({}))
        raw = {'output': None, 'progress': None, 'diagnostic': None}
        with self.assertRaises(ValueError):
            ExecutionObservations.run_output(raw)
        self.assertIsNone(ExecutionObservations.step_output(raw))
        self.assertIsNone(ExecutionObservations.progress(raw))
        self.assertIsNone(ExecutionObservations.diagnostic(raw))
        self.assertIsNone(ExecutionObservations.diagnostic_record(raw))
        self.assertEqual(ExecutionObservations.diagnostic_record({}), {})
        self.assertEqual(raw, {'output': None, 'progress': None, 'diagnostic': None})

    def test_writers_preserve_exact_fields_and_unrelated_metadata(self):
        raw = {'custom': {'value': [1]}}
        output = EngineOutput('answer', 'text', data={'items': [1]})
        diagnostic = Diagnostic('busy', 'wait', details={'original': [1]})
        progress = OperationProgress(ResourceRef('step', 'step'), 'running')
        ExecutionObservations.store_output(raw, output)
        ExecutionObservations.store_diagnostic(raw, diagnostic)
        ExecutionObservations.store_progress(raw, progress)
        self.assertEqual(raw, {'custom': {'value': [1]}, 'output': output.to_dict(),
                             'diagnostic': diagnostic.to_dict(), 'progress': progress.to_dict()})
        before = deepcopy(raw)
        ExecutionObservations.run_output(raw).data['items'].append(2)
        ExecutionObservations.step_output(raw).data['items'].append(3)
        ExecutionObservations.diagnostic(raw).details['original'].append(2)
        ExecutionObservations.diagnostic_record(raw)['details']['original'].append(3)
        self.assertEqual(raw, before)
        self.assertEqual(ExecutionObservations.progress(raw), progress)
        self.assertTrue(ExecutionObservations.has_output(raw))
        self.assertTrue(ExecutionObservations.has_diagnostic(raw))

    def test_missing_output_evidence_is_never_filled_by_either_projection(self):
        complete = EngineOutput('answer', 'partial', final=False).to_dict()
        for field in complete:
            raw = {'output': {key: value for key, value in complete.items() if key != field}}
            before = deepcopy(raw)
            for reader in (ExecutionObservations.run_output, ExecutionObservations.step_output):
                with self.subTest(field=field, reader=reader.__name__), self.assertRaises(ValueError):
                    reader(raw)
                self.assertEqual(raw, before)
