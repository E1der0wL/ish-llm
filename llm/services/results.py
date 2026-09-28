"""Session 또는 Project 범위에서 Run의 실행 결과를 조회한다. 주입된 저장소를 사용하며 결과 자체를 별도 파일로 저장하지 않는다.

Read Run-owned execution observations from Project or Session scope."""
from llm.services.query import Query, select

from typing import Optional, Protocol, Union
import json
import hashlib

from llm.core.models import Project, Run, RunStatus, Session, SessionStatus
from llm.core.results import ExecutionResult
from llm.services.infrastructure.locking import WorkspaceOwnership, workspace_locked


class SessionReader(Protocol):
    def _owner(self, session: Session, *, allow_deleted: bool = False) -> Project: ...

    # 공개 API
    @property
    def ownership(self) -> WorkspaceOwnership: ...

    def require_project(self, project: Project, *, allow_deleted: bool = False) -> Project: ...

    def load(self, project: Project, session_id: str) -> Session: ...

    def list(self, project: Project, *, include_deleted: bool = False) -> list[Session]: ...


class RunReader(Protocol):
    def load(self, session: Session, run_id: str) -> Run: ...
    def list(self, session: Session) -> list[Run]: ...


# Run 원본에서 Session/Project 범위의 결과를 만든다.
class RunResultQuery:
    """No result files or cache: run.json is the sole execution source of truth.

    Project lookups traverse Sessions. Pass a Session for a direct Run lookup. Custom
    Run repositories can be injected; RunManager uses its configured repository.
    """

    def __init__(self, sessions: SessionReader, runs: Optional[RunReader] = None) -> None:
        if runs is None:
            # Runtime import keeps the query protocol independent of orchestration.
            from llm.services.runtime.runs import RunRepository
            runs = RunRepository()
        self.sessions = sessions
        self.runs = runs

    def _scope(self, owner: Union[Project, Session], include_deleted: bool):
        if isinstance(owner, Session):
            project = self.sessions._owner(owner, allow_deleted=True)
            session = self.sessions.load(project, owner.id)
            if session.status == SessionStatus.DELETED and not include_deleted:
                return project, []
            return project, [session]
        project = self.sessions.require_project(owner, allow_deleted=True)
        return project, self.sessions.list(project, include_deleted=include_deleted)

    # 공개 API
    @property
    def ownership(self):
        return self.sessions.ownership

    @workspace_locked
    def tool_result(self, owner, session_id, run_id, tool_call_id, *, steps, offset=0, limit=16000):
        """소유 Session의 원본 Tool 결과를 부분 조회한다. 경로를 모델 입력으로 받지 않는다."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 64000:
            raise ValueError("Invalid result slice")
        project = self.sessions.require_project(owner)
        session = self.sessions.load(project, session_id)
        run = self.runs.load(session, run_id)
        matches = [s for s in steps.list(run) if s.kind == "tool" and s.status == "completed"
                   and s.metadata.get("tool_call_id") == tool_call_id]
        if len(matches) != 1 or matches[0].output is None:
            raise ValueError("Completed Tool result not found uniquely")
        value = matches[0].output.data
        content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)
        return {"content": content[offset:offset + limit], "offset": offset,
                "total_chars": len(content), "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "run_id": run_id, "step_id": matches[0].id}

    @workspace_locked
    def load(self, owner: Union[Project, Session], run_id: str) -> ExecutionResult:
        project, sessions = self._scope(owner, include_deleted=True)
        for session in sessions:
            try:
                run = self.runs.load(session, run_id)
            except FileNotFoundError:
                continue
            return ExecutionResult.from_run(project.id, run)
        raise FileNotFoundError("Run does not exist in this scope")

    @workspace_locked
    def list(self, owner: Union[Project, Session], *, include_deleted: bool = False,
             include_running: bool = False, query: Optional[Query] = None) -> list[ExecutionResult]:
        if query is not None and not isinstance(query, Query):
            raise TypeError("query must be Query or None")
        project, sessions = self._scope(owner, include_deleted)
        runs = sorted((run for session in sessions for run in self.runs.list(session)
                       if include_running or run.status not in (RunStatus.PENDING, RunStatus.RUNNING)),
                      key=lambda run: (run.ended_at or run.started_at or "", run.id))
        if query is not None and type(query) is not Query:
            # 사용자 Query.apply는 기존대로 ExecutionResult를 입력받는다.
            return select([ExecutionResult.from_run(project.id, run) for run in runs], query)
        # 페이지 밖의 Run에 대한 completion 복사·토큰 집계를 생략한다.
        return [ExecutionResult.from_run(project.id, run) for run in select(runs, query)]
