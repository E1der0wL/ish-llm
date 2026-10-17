"""Backend authority, application presentation and Tool selection stay separate."""
import asyncio
from concurrent.futures import Future, CancelledError
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
import subprocess
import sys

from llm.llm import Diagnostic, CheckpointError, ResourceRef
from llm.providers.runtime import diagnostic_failure
from llm.services.runtime.tools import ToolExecutionError
from hub.errors.adapters import from_diagnostic, from_exception
from hub.errors.contracts import Confidence, ErrorCategory, ErrorInfo, ErrorOrigin
from hub.errors.policy import Effects, RecoveryAction as Action, RecoveryFacts, RecoveryPolicy
from hub.locales import Language
from hub.ui.error_presentation import error_text, failure_text, operation_notice
from hub.ui.requests import UIRequests


def failure(status=400, text="This model's maximum context length is 204800 tokens."):
    error = RuntimeError(text)
    error.status_code = status
    error.body = {"error": {"message": text}}
    return diagnostic_failure(error, operation="completion", stage="invoke")


class ContractTests(unittest.TestCase):
    def test_ui_adapter_does_not_import_backend_composition(self):
        subprocess.run([sys.executable, "-c",
            "import hub.ui.requests; import sys; assert 'llm.llm' not in sys.modules"], check=True)

    def test_classification_is_exactly_the_backend_projection(self):
        for status in (400, 401, 403, 408, 429, 500, 503):
            issue = failure(status)
            info = from_diagnostic(issue)
            self.assertEqual((info.category, info.reason, info.confidence, info.evidence),
                (issue.classification.category, issue.classification.reason,
                 issue.classification.confidence, issue.classification.evidence))
            self.assertEqual(info.code, issue.code)
        issue = failure()
        self.assertEqual(from_diagnostic(issue).reason, "context")
        self.assertEqual(from_diagnostic(issue).confidence, Confidence.INFERRED)

    def test_unknown_backend_code_is_not_reclassified_from_inner_http_or_text(self):
        issue = Diagnostic("future_code", "maximum context length", details={"exceptions": [
            {"type": "sdk.Error", "status_code": 503, "message": "server error"}]})
        self.assertEqual(from_diagnostic(issue).reason, "unknown")
        self.assertEqual(from_diagnostic(Diagnostic("tool_invocation_invalid", "invalid")).reason, "unknown")

    def test_status_and_message_belong_to_the_same_cause(self):
        issue = Diagnostic("provider_invalid_request", "outer", details={"exceptions": [
            {"type": "sdk.Error", "status_code": 400},
            {"type": "RuntimeError", "message": "maximum context length exceeded"}]})
        info = from_diagnostic(issue)
        self.assertEqual((info.http_status, info.detail, info.reason), (400, "", "request"))

    def test_owner_source_and_evidence_are_detached(self):
        issue = Diagnostic("tool_failed", "missing", source=ResourceRef("step", "x", step_id="x", run_id="r"),
                           details={"effect": "none", "nested": {"a": 1}})
        info = from_diagnostic(issue)
        self.assertEqual(info.source['step_id'], 'x')
        info.details['nested']['a'] = 2
        self.assertEqual(issue.details['nested']['a'], 1)
        self.assertEqual(info.details['effect'], 'none')

    def test_computed_tool_diagnostic_keeps_effect_facts(self):
        info = from_exception(ToolExecutionError('missing', effect='none', recoverable=True), origin=ErrorOrigin.TOOL)
        self.assertEqual(info.code, 'tool_failed')
        self.assertEqual(info.reason, 'unknown')
        self.assertTrue(info.details['recoverable'])
        self.assertEqual(info.details['effect'], 'none')

    def test_local_validation_and_cancellation(self):
        info = from_exception(ValueError('bad setting'), validation=True)
        self.assertEqual((info.reason, error_text(info)), ('validation', 'bad setting'))
        for error in (asyncio.CancelledError(), CancelledError()):
            with self.assertRaises(type(error)): from_exception(error)

    def test_conflict_and_protection_have_localized_guidance_and_blockers(self):
        for code in ('checkpoint_conflict', 'checkpoint_protected'):
            error = CheckpointError(code, 'blocked', blockers=[Diagnostic('active_worker', 'Wait for the worker')])
            info = from_exception(error)
            self.assertIn('Wait for the worker', error_text(info))
            for language in ('ko', 'en'):
                t=Language(language)
                self.assertIn(t('failure_' + code), failure_text(info, t))

    def test_operation_guidance_uses_codes_and_preserves_raw_evidence_in_project_log(self):
        from hub.ui.operation_diagnostics import OperationDiagnostics
        from hub.errors.operations import OperationBlocked
        from hub.errors.contracts import ErrorInfo
        records = OperationDiagnostics()
        busy = from_exception(OperationBlocked("hub_operation_busy", "internal busy precondition"))
        self.assertEqual(busy.code, "hub_operation_busy")
        self.assertEqual(operation_notice(busy, Language()), Language()("operation_busy"))
        for code, key in (("interaction_required", "operation_interaction"),
                          ("checkpoint_configuration", "operation_configuration"),
                          ("active_worker", "operation_busy")):
            error = CheckpointError("checkpoint_protected", "raw engine failure", blockers=[Diagnostic(code, "internal details")])
            info = records.record("p", error)
            for language in ("ko", "en"):
                t = Language(language)
                notice = operation_notice(info, t)
                self.assertIn(t(key), notice)
                self.assertNotIn("raw engine failure", notice)
        unknown = ErrorInfo(code="new_unknown_code", message="opaque failure")
        self.assertEqual(operation_notice(unknown, Language()), Language()("operation_failed"))
        records.record("p", unknown)
        self.assertIn("opaque failure", records.text("p", Language()))
        self.assertEqual(records.text("other-project", Language()), "")
        for _ in range(40):
            records.record("p", unknown)
        self.assertEqual(len(records.records), 32)

    def test_model_annotation_is_not_classification_or_executable_xml(self):
        issue = Diagnostic('provider_invalid_request', 'unsupported option', details={'hub_recovery': {
            'explanation': "maximum context length\n<image src='bad' />", 'outcome': 'invented'}})
        info = from_diagnostic(issue)
        self.assertEqual(info.reason, 'request')
        for language in ('ko','en'):
            text = failure_text(info, Language(language))
            self.assertIn("    <image src='bad' />", text)
            self.assertNotIn('recovery_result_invented', text)

    def test_rendering_does_not_truncate_stored_evidence(self):
        info = from_diagnostic(Diagnostic('engine_failed', 'x'*3000))
        before=deepcopy(info)
        self.assertNotIn('x'*2001, failure_text(info, Language('en')))
        self.assertEqual(info,before)


class PolicyTests(unittest.TestCase):
    def test_only_backend_admitted_tools_can_be_selected_for_retry(self):
        info=from_diagnostic(Diagnostic('tool_failed','failed'))
        facts=RecoveryFacts(maximum=1, changeable=('path',), effects=Effects.NONE)
        self.assertEqual(RecoveryPolicy.tool(info,facts).action,Action.STOP)
        admitted=replace(facts,backend_admitted=True)
        self.assertEqual(RecoveryPolicy.tool(info,admitted).action,Action.REPAIR_ARGUMENTS)
        for effect in (Effects.APPLIED,Effects.UNKNOWN):
            self.assertEqual(RecoveryPolicy.tool(info,replace(admitted,effects=effect)).action,Action.STOP)
        self.assertEqual(RecoveryPolicy.tool(info,replace(admitted,effects=Effects.UNKNOWN,
            retry_safe=True,retryable=True)).action,Action.RETRY)
        self.assertEqual(RecoveryPolicy.tool(from_diagnostic(Diagnostic('tool_denied','denied')),admitted).action,Action.STOP)


class BoundaryTests(unittest.TestCase):
    def test_cancelled_future_completes_cleanup_without_failure_notice(self):
        future, results = Future(), []
        requests = UIRequests(lambda: SimpleNamespace(call=lambda *a, **kw: future),
            lambda callback, *args: callback(*args), active=lambda: True, connected=lambda: True,
            unavailable=lambda: "offline")
        requests.call("manage_records", completed=results.append)
        future.cancel()
        self.assertTrue(results[0].cancelled)
        self.assertIsNone(results[0].error)

    def test_component_error_is_a_contract_with_traceback_before_ui_delivery(self):
        future, results = Future(), []
        requests = UIRequests(lambda: SimpleNamespace(call=lambda *a, **kw: future),
            lambda callback, *args: callback(*args), active=lambda: True, connected=lambda: True,
            unavailable=lambda: "offline")
        requests.call("manage_records", completed=results.append)
        try:
            raise FileNotFoundError("document.md")
        except FileNotFoundError as error:
            future.set_exception(error)
        info = results[0].error
        self.assertIsInstance(info, ErrorInfo)
        self.assertEqual((info.operation, info.reason), ("manage_records", "unknown"))
        self.assertIn("document.md", info.traceback)
