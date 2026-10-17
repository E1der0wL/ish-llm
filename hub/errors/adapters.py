"""Project backend diagnostics into presentation data without reclassifying evidence."""

import asyncio
from concurrent.futures import CancelledError
from copy import deepcopy
from dataclasses import replace
import traceback

from llm.core.contracts import Diagnostic
from .contracts import Confidence, ErrorCategory, ErrorInfo, ErrorOrigin


def from_diagnostic(value: Diagnostic, *, origin=ErrorOrigin.UNKNOWN, operation="", source=None) -> ErrorInfo:
    details = deepcopy(value.details)
    classification = value.classification
    chain = details.get("exceptions", ())
    exceptions = [item for item in chain if isinstance(item, dict)] if isinstance(chain, (list, tuple)) else []
    status_entry = next((item for item in exceptions if type(item.get("status_code")) is int), None)
    # Never combine one cause's HTTP status with another cause's error message.
    entry = status_entry if status_entry is not None else (exceptions[0] if exceptions else {})
    provider = entry.get("provider_error", {})
    detail = provider.get("message") if isinstance(provider, dict) else None
    detail = detail if isinstance(detail, str) else entry.get("message", "")
    detail = detail if isinstance(detail, str) else ""
    recovery = details.get("hub_recovery", {})
    recovery = recovery if isinstance(recovery, dict) else {}
    if origin == ErrorOrigin.UNKNOWN and value.code.startswith("provider_"):
        origin = ErrorOrigin.PROVIDER
    references = value.source.to_dict() if value.source is not None else {}
    references = {key: item for key, item in references.items() if isinstance(item, str)}
    references.update({key: details[key] for key in ("project_id", "session_id", "run_id", "step_id", "call_id")
                       if isinstance(details.get(key), str)})
    references.update(source or {})
    return ErrorInfo(code=value.code, message=value.message, origin=origin,
        operation=str(details.get("operation") or operation), stage=str(details.get("stage") or ""),
        category=ErrorCategory(classification.category), reason=classification.reason,
        confidence=Confidence(classification.confidence), evidence=deepcopy(classification.evidence),
        http_status=entry.get("status_code"), exception_type=str(entry.get("type", "")).split(".")[-1],
        finish_reason=details.get("finish_reason") if isinstance(details.get("finish_reason"), str) else "",
        detail=detail or (value.message if status_entry is None else ""), details=details, source=references,
        explanation=recovery.get("explanation") if isinstance(recovery.get("explanation"), str) else "",
        recovery=recovery.get("outcome") if isinstance(recovery.get("outcome"), str) else "")


def from_exception(error: Exception | str | ErrorInfo, *, origin=ErrorOrigin.HUB,
                   operation="", validation=False) -> ErrorInfo:
    if isinstance(error, ErrorInfo):
        return error
    if isinstance(error, (asyncio.CancelledError, CancelledError)):
        raise error
    if isinstance(error, str):
        return ErrorInfo(message=error, origin=origin, operation=operation)
    value = Diagnostic.from_exception(error, code="hub_validation" if validation else "execution_failed")
    result = from_diagnostic(value, origin=origin, operation=operation)
    # Only application-owned validation receives an application classification.
    if value.code == "hub_validation":
        result = replace(result, category=ErrorCategory.CONTRACT, reason="validation",
                         confidence=Confidence.EXPLICIT, evidence={"code": value.code})
    return replace(result, traceback="".join(traceback.format_exception(error)))
