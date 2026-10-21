"""Read the owning failed Step into Hub's error contract; no recovery or UI."""

from llm.core.models import RunStatus, StepStatus
from collections import OrderedDict

from llm.llm import Diagnostic
from ...errors.adapters import from_diagnostic
from ...errors.contracts import ErrorOrigin


def _step_evidence(steps, code, project_id, session_id, run_id):
    """Read the owning failed Step; never select an earlier recovered failure."""
    generic = ("", "engine_failed", "step_failed", "execution_failed")
    matching = [step for step in steps if code not in generic and str(step.status) == StepStatus.FAILED
                and step.diagnostic is not None and step.diagnostic.code == code]
    # A Graph parent may fail after a child completed or a sibling was cancelled.
    final = max(matching or steps, key=lambda step: step.ended_at or step.started_at or step.created_at, default=None)
    if final is None or str(final.status) != StepStatus.FAILED:
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
    code = diagnostic.code if diagnostic.code not in generic else code
    origin = (ErrorOrigin.PROVIDER if code.startswith("provider_") else
              ErrorOrigin.TOOL if final.kind in ("tool", "shell") else ErrorOrigin.BACKEND)
    return from_diagnostic(diagnostic, origin=origin,
        source={"project_id": project_id, "session_id": session_id, "run_id": run_id, "step_id": final.id})


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
                if str(run.status) != RunStatus.FAILED:
                    continue
                code = str(run.error_code or "")
                try:
                    steps = await handle.steps.alist()
                except FileNotFoundError:
                    steps = []
                diagnosis = _step_evidence(steps, code, project_id, session_id, rid)
                if diagnosis is None:
                    result = await handle.aresult()
                    diagnosis = from_diagnostic(result.diagnostic or Diagnostic(code or "execution_failed", run.error or ""),
                        origin=ErrorOrigin.PROVIDER if code.startswith("provider_") else ErrorOrigin.BACKEND,
                        source={"project_id": project_id, "session_id": session_id, "run_id": rid})
                self.cache[project_id, session_id, rid] = diagnosis
                while len(self.cache) > 256:
                    self.cache.popitem(last=False)
        return {rid: self.cache[project_id, session_id, rid] for rid in run_ids
                if (project_id, session_id, rid) in self.cache}
