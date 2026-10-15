"""Explain evidence from the same failed Run; never execute or retry work."""

from collections import OrderedDict

from ...model import FailureDiagnosis


def diagnose(code: str, *, details: dict | None = None) -> FailureDiagnosis:
    """Use structured codes/status/types, not guesses from arbitrary log text."""
    details = details or {}
    exceptions = [item for item in details.get("exceptions", ()) if isinstance(item, dict)]
    # The final attempt is more relevant than earlier errors that were retried.
    latest = exceptions[-1] if exceptions else {}
    status = next((item["status_code"] for item in reversed(exceptions) if isinstance(item.get("status_code"), int)), None)
    status = status if isinstance(status, int) else None
    exception = str(latest.get("type", "")).split(".")[-1]
    finish_reason = details.get("finish_reason")
    finish_reason = finish_reason if isinstance(finish_reason, str) else ""
    kind = {
        "provider_rate_limit": "rate_limit", "provider_unavailable": "unavailable",
        "provider_authentication": "authentication", "provider_authentication_error": "authentication",
        "provider_timeout": "timeout", "run_timeout": "timeout", "context_limit_exceeded": "context",
        "provider_context_window_exceeded": "context", "usage_budget_exceeded": "budget",
        "provider_bad_request": "request",
        "provider_invalid_request": "request", "provider_connection": "connection",
        "provider_empty_response": "empty", "provider_invalid_response": "empty",
        "completion_invalid_response": "completion_invalid",
        "completion_incomplete": {"length": "completion_length", "content_filter": "completion_filtered"}.get(
            finish_reason, "completion_incomplete"),
        "completion_output_limit": "completion_output_limit",
        "completion_tool_limit": "completion_tool_limit",
        "completion_argument_limit": "completion_argument_limit",
        "step_timeout": "step_timeout",
    }.get(code, "unknown")
    # Engine validation/deadlines retain their own meaning, even if an older
    # provider exception exists in the exception chain.
    if code.startswith("completion_") or code == "step_timeout":
        return FailureDiagnosis(kind, code, status, exception, finish_reason)
    if status in (401, 403):
        kind = "authentication" if status == 401 else "permission"
    elif status == 429:
        kind = "rate_limit"
    elif status in (500, 502, 503, 504):
        kind = "unavailable"
    elif status in (408,):
        kind = "timeout"
    elif status in (400, 404, 422):
        kind = "request"
    elif exception in ("TimeoutError", "ReadTimeout", "ConnectTimeout", "APITimeoutError"):
        kind = "timeout"
    elif exception in ("ConnectionError", "ConnectError", "APIConnectionError", "gaierror"):
        kind = "connection"
    elif exception == "FileNotFoundError":
        kind = "file_missing"
    elif exception == "PermissionError":
        kind = "permission"
    return FailureDiagnosis(kind, code, status, exception, finish_reason)


def _step_evidence(steps, code, project_id, session_id, run_id):
    """Read the owning failed Step; never select an earlier recovered failure."""
    generic = ("", "engine_failed", "step_failed", "execution_failed")
    matching = [step for step in steps if code not in generic and str(step.status) == "failed"
                and step.diagnostic is not None and step.diagnostic.code == code]
    # A Graph parent may fail after a child completed or a sibling was cancelled.
    final = max(matching or steps, key=lambda step: step.ended_at or step.started_at or step.created_at, default=None)
    if final is None or str(final.status) != "failed":
        return None
    diagnostic = final.diagnostic
    if diagnostic is None:
        return None
    details = diagnostic.details
    source = diagnostic.source
    for key, expected in (("project_id", project_id), ("session_id", session_id),
                          ("run_id", run_id), ("step_id", final.id)):
        if (details.get(key, expected) != expected or
                source is not None and getattr(source, key) not in (None, expected)):
            return None
    if code not in generic and diagnostic.code != code:
        return None
    return diagnose(diagnostic.code if diagnostic.code not in generic else code, details=details)


class FailureDiagnostics:
    def __init__(self, runtime):
        self.runtime = runtime
        self.cache = OrderedDict()

    async def read(self, project_id, session_id, run_ids):
        project = await self.runtime.backend.projects.aload(project_id)
        session = await project.sessions.aload(session_id)
        missing = [rid for rid in dict.fromkeys(run_ids) if (project_id, session_id, rid) not in self.cache][:64]
        if missing:
            for rid in missing:
                try:
                    handle = await session.run.aload(rid)
                    run = await handle.aget_data()
                except FileNotFoundError:
                    continue
                if str(run.status) != "failed":
                    continue
                code = str(run.error_code or "")
                try:
                    steps = await handle.steps.alist()
                except FileNotFoundError:
                    steps = []
                diagnosis = _step_evidence(steps, code, project_id, session_id, rid) or diagnose(code)
                self.cache[project_id, session_id, rid] = diagnosis
                while len(self.cache) > 256:
                    self.cache.popitem(last=False)
        return {rid: self.cache[project_id, session_id, rid] for rid in run_ids
                if (project_id, session_id, rid) in self.cache}
