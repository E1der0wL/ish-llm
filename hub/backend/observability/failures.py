"""Read the owning failed Step into Hub's error contract; no recovery or UI."""

from llm.core.contracts import DiagnosticCode

from llm.core.models import StepErrorCode

from llm.core.models import StepKind

from llm.core.models import RunErrorCode
from llm.core.models import RunStatus, StepStatus
from collections import OrderedDict

from llm.llm import Diagnostic
from ...errors.adapters import from_diagnostic, ErrorProjection
from llm.providers.requests import ProviderFailure
from ...errors.contracts import ErrorOrigin


class FailedStepEvidence:
    ABSENT = None
    EMPTY_CODE = ""
    GENERIC_CODES = (
        EMPTY_CODE,
        RunErrorCode.ENGINE_FAILED,
        StepErrorCode.FAILED,
        DiagnosticCode.EXECUTION_FAILED,
    )


def _step_evidence(steps, code, project_id, session_id, run_id):
    """Read the owning failed Step; never select an earlier recovered failure."""
    generic = FailedStepEvidence.GENERIC_CODES
    matching = [
        step
        for step in steps
        if code not in generic
        and str(step.status) == StepStatus.FAILED
        and step.diagnostic is not FailedStepEvidence.ABSENT
        and step.diagnostic.code == code
    ]
    # A Graph parent may fail after a child completed or a sibling was cancelled.
    final = max(
        matching or steps,
        key=lambda step: step.ended_at or step.started_at or step.created_at,
        default=FailedStepEvidence.ABSENT,
    )
    if final is FailedStepEvidence.ABSENT or str(final.status) != StepStatus.FAILED:
        return FailedStepEvidence.ABSENT
    diagnostic = final.diagnostic
    if diagnostic is FailedStepEvidence.ABSENT:
        return FailedStepEvidence.ABSENT
    details = diagnostic.details
    source = diagnostic.source
    for key, expected in ErrorProjection.execution_source(
        project_id, session_id, run_id, step_id=final.id
    ).items():
        if (
            details.get(key, expected) != expected
            or source is not FailedStepEvidence.ABSENT
            and getattr(source, key) not in (FailedStepEvidence.ABSENT, expected)
        ):
            return FailedStepEvidence.ABSENT
    if code not in generic and diagnostic.code != code:
        return FailedStepEvidence.ABSENT
    code = diagnostic.code if diagnostic.code not in generic else code
    origin = (
        ErrorOrigin.PROVIDER
        if ProviderFailure.owns_code(code)
        else (
            ErrorOrigin.TOOL
            if final.kind in (StepKind.TOOL, StepKind.SHELL)
            else ErrorOrigin.BACKEND
        )
    )
    return from_diagnostic(
        diagnostic,
        origin=origin,
        source=ErrorProjection.execution_source(
            project_id, session_id, run_id, step_id=final.id
        ),
    )


class FailureDiagnostics:
    READ_LIMIT = 64
    CACHE_LIMIT = 256
    REMOVE_NEWEST = False
    EMPTY_TEXT = ""

    def __init__(self, runtime):
        self.runtime = runtime
        self.cache = OrderedDict()

    async def read(self, project_id, session_id, run_ids):
        project = await self.runtime.backend.projects.aload(project_id)
        session = await project.sessions.aload(session_id)
        missing = [
            rid
            for rid in dict.fromkeys(run_ids)
            if (project_id, session_id, rid) not in self.cache
        ][: self.READ_LIMIT]
        if missing:
            for rid in missing:
                try:
                    handle = await session.run.aload(rid)
                    run = await handle.aget_data()
                except FileNotFoundError:
                    continue
                if str(run.status) != RunStatus.FAILED:
                    continue
                code = str(run.error_code or self.EMPTY_TEXT)
                try:
                    steps = await handle.steps.alist()
                except FileNotFoundError:
                    steps = []
                diagnosis = _step_evidence(steps, code, project_id, session_id, rid)
                if diagnosis is FailedStepEvidence.ABSENT:
                    result = await handle.aresult()
                    diagnosis = from_diagnostic(
                        result.diagnostic
                        or Diagnostic(
                            code or DiagnosticCode.EXECUTION_FAILED,
                            run.error or self.EMPTY_TEXT,
                        ),
                        origin=(
                            ErrorOrigin.PROVIDER
                            if ProviderFailure.owns_code(code)
                            else ErrorOrigin.BACKEND
                        ),
                        source=ErrorProjection.execution_source(
                            project_id, session_id, rid
                        ),
                    )
                self.cache[project_id, session_id, rid] = diagnosis
                while len(self.cache) > self.CACHE_LIMIT:
                    self.cache.popitem(last=self.REMOVE_NEWEST)
        return {
            rid: self.cache[project_id, session_id, rid]
            for rid in run_ids
            if (project_id, session_id, rid) in self.cache
        }
