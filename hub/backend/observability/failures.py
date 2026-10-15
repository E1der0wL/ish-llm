"""Explain evidence from the same failed Run; never execute or retry work."""

import asyncio
from collections import OrderedDict
from datetime import datetime

from ...model import FailureDiagnosis
from .provider_logs import read_provider_logs


def diagnose(code, records):
    """Use structured codes/status/types, not guesses from arbitrary log text."""
    exceptions = []
    for row in records:
        details = row.get("details", {}).get("details", {})
        if isinstance(details, dict):
            chain = [item for item in details.get("exceptions", ()) if isinstance(item, dict)]
            if chain:
                exceptions = chain
    # The final attempt is more relevant than earlier errors that were retried.
    latest = exceptions[-1] if exceptions else {}
    status = next((item["status_code"] for item in reversed(exceptions) if isinstance(item.get("status_code"), int)), None)
    status = status if isinstance(status, int) else None
    exception = str(latest.get("type", "")).split(".")[-1]
    kind = {
        "provider_rate_limit": "rate_limit", "provider_unavailable": "unavailable",
        "provider_authentication": "authentication", "provider_authentication_error": "authentication",
        "provider_timeout": "timeout", "run_timeout": "timeout", "context_limit_exceeded": "context",
        "provider_context_window_exceeded": "context", "usage_budget_exceeded": "budget",
        "provider_bad_request": "request",
        "provider_invalid_request": "request", "provider_connection": "connection",
        "provider_empty_response": "empty", "provider_invalid_response": "empty",
    }.get(code, "unknown")
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
    return FailureDiagnosis(kind, code, status, exception)


class FailureDiagnostics:
    def __init__(self, runtime):
        self.runtime = runtime
        self.cache = OrderedDict()

    async def read(self, project_id, session_id, run_ids):
        project = await self.runtime.backend.projects.aload(project_id)
        session = await project.sessions.aload(session_id)
        missing = [rid for rid in dict.fromkeys(run_ids) if (project_id, session_id, rid) not in self.cache][:64]
        if missing:
            workspace = getattr(self.runtime.backend, "workspace", self.runtime.config.workspace)
            provider = await asyncio.to_thread(read_provider_logs, workspace, project_id)
            activity = [row.to_dict() for row in await project.aactivity(limit=300, newest_first=False)]
            for rid in missing:
                try:
                    handle = await session.run.aload(rid)
                    run = await handle.aget_data()
                except FileNotFoundError:
                    continue
                if str(run.status) != "failed":
                    continue
                def same_run(row):
                    source = row.get("source", {})
                    return source.get("session_id") == session_id and source.get("run_id") == rid
                records = [row for row in provider if row.get("scope") == "project" and same_run(row)]
                codes = [row.get("code") for row in activity if same_run(row) and row.get("code")]
                code = str(run.error_code or (codes[-1] if codes else ""))
                if code == "engine_failed" and records:
                    # Some providers raise an uncoded SDK exception. Only use
                    # its log if the final failed operation was that model call.
                    steps = await handle.steps.alist()
                    final = max(steps, key=lambda step: step.started_at or step.created_at, default=None)
                    if final is not None and final.kind == "llm" and str(final.status) == "failed" and final.started_at:
                        started = datetime.fromisoformat(final.started_at).timestamp()
                        def in_step(row):
                            try:
                                logged = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S,%f").timestamp()
                                return logged >= started - .001
                            except (ValueError, KeyError):
                                return False
                        records = [row for row in records if in_step(row)]
                        if records:
                            code = records[-1]["code"]
                # A provider error earlier in this Run may have recovered before
                # an unrelated Tool failure. Do not attribute that old error to it.
                self.cache[project_id, session_id, rid] = diagnose(code, records if code.startswith("provider_") else ())
                while len(self.cache) > 256:
                    self.cache.popitem(last=False)
        return {rid: self.cache[project_id, session_id, rid] for rid in run_ids
                if (project_id, session_id, rid) in self.cache}
