"""Project backend diagnostics into presentation data without reclassifying evidence."""

import asyncio
from concurrent.futures import CancelledError
from copy import deepcopy
from dataclasses import replace
import traceback

from llm.core.contracts import Diagnostic, DiagnosticCode, ErrorReason, ResourceRef
from llm.core.diagnostics import ClassificationEvidence
from llm.providers.diagnostic_values import (
    ProviderAttempt,
    ProviderDiagnosticObservation,
)
from llm.providers.streaming import CompletionTermination
from llm.providers.requests import ProviderFailure
from .contracts import Confidence, ErrorCategory, ErrorInfo, ErrorOrigin, HubErrorCode
from .recovery_annotation import RecoveryAnnotation


class ErrorProjection:
    NO_TEXT = ""
    NO_SOURCE = None
    EXCEPTION_IS_VALIDATION = False
    REFERENCE_FIELDS = (
        ResourceRef.project_id.__name__,
        ResourceRef.session_id.__name__,
        ResourceRef.run_id.__name__,
        ResourceRef.step_id.__name__,
        ProviderAttempt.call_id.__name__,
    )

    @classmethod
    def references(cls, value, details, source):
        references = value.source.to_dict() if value.source is not cls.NO_SOURCE else {}
        references = {
            key: item for key, item in references.items() if isinstance(item, str)
        }
        references.update(
            {
                key: details[key]
                for key in cls.REFERENCE_FIELDS
                if isinstance(details.get(key), str)
            }
        )
        references.update(source or {})
        return references


def from_diagnostic(
    value: Diagnostic,
    *,
    origin=ErrorOrigin.UNKNOWN,
    operation=ErrorProjection.NO_TEXT,
    source=ErrorProjection.NO_SOURCE
) -> ErrorInfo:
    details = deepcopy(value.details)
    classification = value.classification
    provider = ProviderDiagnosticObservation(details)
    # A status and its message always come from the same observed cause.
    detail = provider.detail
    recovery = RecoveryAnnotation.observe(details)
    if origin == ErrorOrigin.UNKNOWN and ProviderFailure.owns_code(value.code):
        origin = ErrorOrigin.PROVIDER
    references = ErrorProjection.references(value, details, source)
    termination = CompletionTermination.from_details(details)
    return ErrorInfo(
        code=value.code,
        message=value.message,
        origin=origin,
        operation=provider.operation(operation),
        stage=provider.stage,
        category=ErrorCategory(classification.category),
        reason=classification.reason,
        confidence=Confidence(classification.confidence),
        evidence=deepcopy(classification.evidence),
        http_status=provider.http_status,
        exception_type=provider.exception_type,
        raw_finish_reason=CompletionTermination.raw_finish_reason(details),
        termination=termination.normalized,
        detail=detail
        or (value.message if not provider.has_http_status else ErrorProjection.NO_TEXT),
        details=details,
        source=references,
        explanation=recovery.explanation,
        recovery=recovery.outcome,
    )


def from_exception(
    error: Exception | str | ErrorInfo,
    *,
    origin=ErrorOrigin.HUB,
    operation=ErrorProjection.NO_TEXT,
    validation=ErrorProjection.EXCEPTION_IS_VALIDATION
) -> ErrorInfo:
    if isinstance(error, ErrorInfo):
        return error
    if isinstance(error, (asyncio.CancelledError, CancelledError)):
        raise error
    if isinstance(error, str):
        return ErrorInfo(message=error, origin=origin, operation=operation)
    value = Diagnostic.from_exception(
        error,
        code=HubErrorCode.VALIDATION if validation else DiagnosticCode.EXECUTION_FAILED,
    )
    result = from_diagnostic(value, origin=origin, operation=operation)
    # Only application-owned validation receives an application classification.
    if value.code == HubErrorCode.VALIDATION:
        result = replace(
            result,
            category=ErrorCategory.CONTRACT,
            reason=ErrorReason.VALIDATION.value,
            confidence=Confidence.EXPLICIT,
            evidence=ClassificationEvidence(value.code).to_dict(),
        )
    return replace(
        result,
        traceback=ErrorProjection.NO_TEXT.join(traceback.format_exception(error)),
    )
