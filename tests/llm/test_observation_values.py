"""Independent observation wire shapes, omissions and detached open evidence."""

from copy import deepcopy
import unittest

from llm.core.contracts import Diagnostic, DiagnosticSeverity
from llm.core.diagnostics import classify_diagnostic
from llm.providers.diagnostic_values import ProviderRequestFailure, ProviderRequestHint
from llm.providers.streaming import CompletionTermination


class ObservationValueTests(unittest.TestCase):
    def test_fallback_exception_shape_and_optional_boundary_are_preserved(self):
        error = ValueError('original')
        error.__cause__ = RuntimeError('cause')
        expected = {'exceptions': [{'type': 'builtins.ValueError'}, {'type': 'builtins.RuntimeError'}]}
        self.assertEqual(Diagnostic.from_exception(error).details, expected)
        self.assertEqual(Diagnostic.from_exception(error, boundary='').details, {**expected, 'boundary': ''})
        boundary = {'custom': ['original']}
        value = Diagnostic.from_exception(error, boundary=boundary)
        boundary['custom'].clear()
        value.to_dict()['details']['boundary']['custom'].clear()
        self.assertEqual(value.details['boundary'], {'custom': ['original']})
        self.assertEqual(tuple(item.value for item in DiagnosticSeverity), ('info', 'warning', 'error'))
        self.assertEqual(type(value.severity), str)

    def test_provider_hint_preserves_partial_and_extended_observation(self):
        self.assertEqual(ProviderRequestFailure('context', 2, 400).details(), {
            'request_failure': {'reason': 'context', 'cause_index': 2, 'status_code': 400}})
        details = {'request_failure': {'reason': 'context', 'custom': {'source': ['original']}}}
        expected = deepcopy(details['request_failure'])
        hint = ProviderRequestHint.from_details(details)
        details['request_failure']['custom']['source'].clear()
        hint.to_dict()['custom']['source'].clear()
        self.assertEqual(hint.reason, 'context')
        self.assertEqual(hint.to_dict(), expected)
        # This is optional display evidence, not a required execution receipt.
        diagnostic = Diagnostic('provider_invalid_request', 'request', details={'request_failure': expected})
        classification = classify_diagnostic(diagnostic)
        self.assertEqual(classification.to_dict(), {'category': 'resource', 'reason': 'context',
            'confidence': 'inferred', 'evidence': {'code': 'provider_invalid_request', **expected}})
        self.assertFalse(hasattr(classification, 'retry'))
        for value in (None, [], {}, {'reason': 'unknown'}, {'reason': []}):
            with self.subTest(value=value):
                self.assertIsNone(ProviderRequestHint.from_details({'request_failure': value}))

    def test_termination_projection_never_promotes_unknown_evidence(self):
        for value, expected in ((None, False), ('length', False), ('output_limit', True), ({'unexpected': []}, False)):
            details = {'termination': deepcopy(value)}
            before = deepcopy(details)
            observed = CompletionTermination.from_details(details)
            self.assertEqual(observed.output_limited, expected)
            self.assertEqual(details, before)
        self.assertFalse(CompletionTermination.from_details({}).output_limited)
        value = Diagnostic('completion_incomplete', 'stopped', details={'termination': 'output_limit'})
        self.assertEqual(value.classification.to_dict(), {'category': 'resource', 'reason': 'output',
            'confidence': 'explicit', 'evidence': {'code': 'completion_incomplete', 'termination': 'output_limit'}})
