"""Presentation data shared by Hub's backend adapter and widgets."""

from llm.core.models import SessionStatus
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from .errors.contracts import ErrorInfo


class QuestionSource(StrEnum):
    """Hub live-question presentation fields, shared by Tool input and widgets."""

    SUGGESTIONS = "choices"


class RAGAction(StrEnum):
    """Special RAG management commands shared by dispatch and UI."""

    VISION_SETTINGS = "vision_settings"
    JOBS = "jobs"
    JOB_DELETE_PLAN = "job_delete_plan"
    DELETE_JOB = "delete_job"
    CANCEL_JOB = "cancel_job"
    IMPORT = "import"
    RETRY = "retry"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    PREPARING: ClassVar[str] = "preparing"

    role: str
    text: str
    time: str = ""
    status: str = ""
    id: str = ""
    author: str = ""
    elapsed_seconds: float | None = None
    phase: str = ""
    tools: tuple["StepSummary", ...] = ()
    trace: tuple["TraceItem", ...] = ()
    activity: str = ""
    run_id: str = ""
    failure: ErrorInfo | None = None


@dataclass(frozen=True, slots=True)
class TraceItem:
    id: str
    kind: str
    text: str
    status: str = ""
    started_at: str = ""


@dataclass(frozen=True, slots=True)
class SessionView:
    """A sidebar entry and its cached conversation, independent of demo data."""

    title: str
    status: str = ""
    messages: tuple[ChatMessage, ...] = ()
    id: str = ""


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    run_id: str | None = None
    message: ChatMessage | None = None


@dataclass(frozen=True, slots=True)
class SessionSummary:
    WAITING_INPUT: ClassVar[str] = "waiting_input"
    WAITING_APPROVAL: ClassVar[str] = "waiting_approval"

    id: str
    title: str
    status: str
    queued_count: int = 0


@dataclass(frozen=True, slots=True)
class SessionNotification:
    sequence: int
    project_id: str
    session_id: str
    title: str
    status: str


@dataclass(frozen=True, slots=True)
class StepSummary:
    WAITING_APPROVAL: ClassVar[str] = "waiting_approval"
    RETRYING: ClassVar[str] = "retrying"

    name: str
    status: str
    kind: str = ""
    id: str = ""
    started_at: str = ""
    source_id: str = ""


@dataclass(frozen=True, slots=True)
class RetrySummary:
    PROVIDER: ClassVar[str] = "provider"
    TOOL: ClassVar[str] = "tool"
    RECOVERY: ClassVar[str] = "recovery"
    ACTIVE: ClassVar[str] = "active"
    WAITING: ClassVar[str] = "waiting"
    REPAIRING: ClassVar[str] = "repairing"
    RETRYING: ClassVar[str] = "retrying"
    REJECTED: ClassVar[str] = "rejected"
    ANALYZING: ClassVar[str] = "analyzing"
    COMPACTING: ClassVar[str] = "compacting"

    attempt: int
    maximum: int
    retry_at: float | None = None
    kind: str = PROVIDER
    name: str = ""
    phase: str = ACTIVE


@dataclass(frozen=True, slots=True)
class AgentSummary:
    id: str
    engine: str
    purpose: str = ""


@dataclass(frozen=True, slots=True)
class RunSummary:
    id: str
    status: str
    engine: str
    elapsed_seconds: float | None
    active: bool
    steps: tuple[StepSummary, ...]
    error: str
    retry: RetrySummary | None = None


@dataclass(frozen=True, slots=True)
class HubSnapshot:
    project_id: str
    project_title: str
    model: str
    storage: str
    sessions: tuple[SessionSummary, ...]
    selected_id: str
    messages: tuple[ChatMessage, ...]
    run: RunSummary | None = None
    status: str = SessionStatus.IDLE.value
    queued_count: int = 0
    model_required: bool = False
    title_error: ErrorInfo | None = None
    engines: tuple[str, ...] = ()
    file_root: str = ""
    notifications: tuple[SessionNotification, ...] = ()
    components: tuple[str, ...] = ()
    questions: tuple[dict, ...] = ()
    agents: tuple[AgentSummary, ...] = ()
