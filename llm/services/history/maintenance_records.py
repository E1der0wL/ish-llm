"""History maintenance records and paths; selection remains in its service Owner."""

from llm.core.contracts import Diagnostic, ResourceRef
from llm.core.models import ResourceKind, Run
from llm.core.plans import RecoveryPlan, PlanBlockerCode


class HistoryMaintenanceStorage:
    MAINTENANCE = "maintenance"
    RECOVERY = "recovery"
    RETENTION_HISTORY = "retention_history"
    RETENTION_INTENT = "retention.json"
    RECORD_SUFFIX = ".json"
    PENDING_RECORDS = "*.json"

    @classmethod
    def pending(cls, session):
        return any((session.paths.state / cls.MAINTENANCE).glob(cls.PENDING_RECORDS))

    @classmethod
    def recovery_path(cls, project, identifier):
        return project.paths.state / cls.RECOVERY / (identifier + cls.RECORD_SUFFIX)

    @classmethod
    def retention_path(cls, session):
        return session.paths.state / cls.MAINTENANCE / cls.RETENTION_INTENT

    @classmethod
    def receipt_directory(cls, session):
        return session.paths.state / cls.RETENTION_HISTORY


class HistoryIssueCode:
    RUNTIME_ATTACHED = PlanBlockerCode.RUNTIME_ATTACHED.value
    OFFLINE_FILE_SESSION_REQUIRED = "offline_file_session_required"
    PENDING_RETENTION = "pending_retention"
    INVALID_INPUT_REFERENCE = "invalid_input_reference"
    STALE_SESSION = "stale_session"
    MISSING_RUN_REFERENCE = "missing_run_reference"
    STALE_RUN = "stale_run"
    INVALID_ASSISTANT_REFERENCE = "invalid_assistant_reference"
    MISSING_ASSISTANT = "missing_assistant"
    STALE_ASSISTANT = "stale_assistant"
    STALE_STEP = "stale_step"
    MISSING_CHECKPOINT_MESSAGE = "missing_checkpoint_message"
    OUTPUT_GAP = "output_gap"
    UNREADABLE_HISTORY = "unreadable_history"
    COMPONENT_INTEGRITY = "component_integrity"


class RecoveryDiagnostic:
    DETAIL = "detail"
    REPAIRABLE = "repairable"
    COMPONENT = ResourceRef.component.__name__
    ERROR = "error"
    CODE_SEPARATOR = "_"
    WORD_SEPARATOR = " "
    ABSENT = None
    NOT_REPAIRABLE = False
    REPAIRABLE_EVIDENCE = True

    @classmethod
    def create(
        cls, project_id, code, *, session=ABSENT, run=ABSENT, detail=ABSENT, repairable=NOT_REPAIRABLE
    ):
        source = ResourceRef(
            ResourceKind.RUN if run else ResourceKind.SESSION if session else ResourceKind.PROJECT,
            run or session or project_id,
            project_id=project_id,
            session_id=session,
            run_id=run,
        )
        return Diagnostic(
            code,
            code.replace(cls.CODE_SEPARATOR, cls.WORD_SEPARATOR),
            source=source,
            details={cls.DETAIL: detail, cls.REPAIRABLE: repairable},
        )

    @classmethod
    def repairable(cls, diagnostic):
        return diagnostic.details[cls.REPAIRABLE]

    @classmethod
    def component_detail(cls, name, error):
        return {cls.COMPONENT: name, cls.ERROR: str(error)}


class HistoryEvidence:
    SOURCES = "sources"


class RecoveryEvidence:
    PROJECT = "project"
    SOURCES = HistoryEvidence.SOURCES
    ISSUES = RecoveryPlan.issues.__name__
    REPAIR_SESSIONS = RecoveryPlan.repair_sessions.__name__

    @classmethod
    def result(cls, issues, actions):
        return {cls.ISSUES: [item.to_dict() for item in issues], cls.REPAIR_SESSIONS: actions}

    @classmethod
    def sources(cls, project, signatures, result):
        return {cls.PROJECT: project, cls.SOURCES: signatures, **result}


class RecoveryProgress:
    STATUS = Run.status.__name__
    PLAN = "plan"
    COMPLETED_SESSIONS = "completed_sessions"
    STARTED_AT = Run.started_at.__name__
    ENDED_AT = Run.ended_at.__name__
    RUNNING = "running"
    COMPLETED = "completed"

    def __init__(self, record):
        self._record = record

    @classmethod
    def start(cls, plan, started_at):
        return cls(
            {cls.STATUS: cls.RUNNING, cls.PLAN: plan, cls.COMPLETED_SESSIONS: [], cls.STARTED_AT: started_at}
        )

    @property
    def record(self):
        return self._record

    @property
    def completed_sessions(self):
        return self._record[self.COMPLETED_SESSIONS]

    def complete_session(self, identifier):
        self.completed_sessions.append(identifier)

    def finish(self, ended_at):
        self._record.update({self.STATUS: self.COMPLETED, self.ENDED_AT: ended_at})
