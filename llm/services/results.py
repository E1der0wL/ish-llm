"""Session 또는 Project 범위에서 Run의 실행 결과를 조회한다. 주입된 저장소를 사용하며 결과 자체를 별도 파일로 저장하지 않는다.

Read Run-owned execution observations from Project or Session scope."""
from llm.services.query import Query, select

from typing import Optional, Protocol, Union
import json
import hashlib

from llm.core.models import StepStatus, Project, Run, Session, SessionStatus
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
    def list_results(self, sessions: list[Session], *, include_running: bool = False,
                     query: Optional[Query] = None) -> list[Run]: ...


# Run 원본에서 Session/Project 범위의 결과를 만든다.
class RunResultQuery:
    """run.json is the sole execution source of truth; repositories own query projections.

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
    def tool_result(self, owner, session_id, run_id, step_id, *, steps, offset=0, limit=None):
        """소유 Session의 원본 Tool 결과를 부분 조회한다. 경로를 모델 입력으로 받지 않는다."""
        if type(offset) is not int or offset < 0 or limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("Invalid result slice")
        project = self.sessions.require_project(owner)
        session = self.sessions.load(project, session_id)
        run = self.runs.load(session, run_id)
        step = steps.load(run, step_id)
        if step.kind != "tool" or step.status != StepStatus.COMPLETED or step.output is None:
            raise ValueError("Completed Tool result required")
        value = step.output.data
        content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)
        return {"content": content[offset:None if limit is None else offset + limit], "offset": offset,
                "total_chars": len(content), "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "run_id": run_id, "step_id": step.id}

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
        if query is not None and type(query) is not Query:
            # 사용자 Query.apply는 기존대로 ExecutionResult를 입력받는다.
            runs = self.runs.list_results(sessions, include_running=include_running)
            return select([ExecutionResult.from_run(project.id, run) for run in runs], query)
        runs = self.runs.list_results(sessions, include_running=include_running, query=query)
        return [ExecutionResult.from_run(project.id, run) for run in runs]
