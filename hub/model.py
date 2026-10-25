"""Presentation data shared by Hub's backend adapter and widgets."""

from llm.providers.diagnostic_values import ProviderAttempt

from llm.core.models import SessionStatus, MessageStatus
from llm.core.interactions import InteractionRequest, InteractionRequestObservation
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from .errors.contracts import ErrorInfo


class ResponseActivity:
    EMPTY = ""
    THINKING = "thinking"
    RUNNING = "running"
    STREAMING = "streaming"
    RESUMING = "resuming"
    PREPARING = "preparing"


class QuestionSource(StrEnum):
    """Hub live-question presentation fields, shared by Tool input and widgets."""

    SUGGESTIONS = "choices"

    @classmethod
    def suggestions(cls, source):
        return source.get(cls.SUGGESTIONS.value, [])

    @classmethod
    def create(cls, suggestions):
        return {cls.SUGGESTIONS.value: suggestions}


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
    DRAFT: ClassVar[str] = "draft"
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


class ReasoningMessage:
    ROLE = "reasoning"
    SUFFIX = ":reasoning"

    @classmethod
    def identifier(cls, assistant_id):
        return assistant_id + cls.SUFFIX

    @classmethod
    def assistant_id(cls, identifier):
        return identifier.removesuffix(cls.SUFFIX)

    @classmethod
    def owns(cls, message):
        return message.role == cls.ROLE and message.id.endswith(cls.SUFFIX)

    @classmethod
    def create(cls, assistant_id, text, **values):
        return ChatMessage(cls.ROLE, text, id=cls.identifier(assistant_id), **values)


@dataclass(frozen=True, slots=True)
class TraceItem:
    THOUGHT_PREFIX: ClassVar[str] = "thought:"
    NO_TIME: ClassVar[str] = ""
    TOOL: ClassVar[str] = "tool"
    REASONING: ClassVar[str] = "reasoning"

    id: str
    kind: str
    text: str
    status: str = ""
    started_at: str = NO_TIME

    @classmethod
    def thought(cls, identifier, text, *, started_at=NO_TIME):
        return cls(
            cls.THOUGHT_PREFIX + identifier, cls.REASONING, text, started_at=started_at
        )


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

    @classmethod
    def state_record(cls, run_id):
        return {cls.run_id.__name__: run_id}

    @classmethod
    def state_run_id(cls, value):
        return value[cls.run_id.__name__]


@dataclass(frozen=True, slots=True)
class SessionSummary:
    WAITING_INPUT: ClassVar[str] = "waiting_input"
    WAITING_APPROVAL: ClassVar[str] = "waiting_approval"
    WAITING_STATES: ClassVar[tuple] = (WAITING_INPUT, WAITING_APPROVAL)

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
    NO_RETRY_AT: ClassVar[None] = None
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
    retry_at: float | None = NO_RETRY_AT
    kind: str = PROVIDER
    name: str = ""
    phase: str = ACTIVE

    @classmethod
    def next_attempt(cls, value):
        return value.attempt + ProviderAttempt.ORDINAL_INCREMENT


@dataclass(frozen=True, slots=True)
class AgentSummary:
    id: str
    engine: str
    purpose: str = ""

    def catalog_entry(self):
        cls = type(self)
        return {cls.engine.__name__: self.engine, cls.purpose.__name__: self.purpose}


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
    NO_PROJECT: ClassVar[str] = ""
    LIVE_OBSERVATION: ClassVar[str] = "_last_snapshot"
    NO_OBSERVATION: ClassVar[None] = None

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

    @classmethod
    def project_identifier(cls, view):
        """Observe a live view; the snapshot's own project_id remains required."""
        return getattr(view, cls.project_id.__name__, cls.NO_PROJECT)

    @classmethod
    def last_observed(cls, view):
        return getattr(view, cls.LIVE_OBSERVATION, cls.NO_OBSERVATION)

    @classmethod
    def require_observed(cls, view):
        return getattr(view, cls.LIVE_OBSERVATION)

    @classmethod
    def observe_in(cls, view, snapshot):
        setattr(view, cls.LIVE_OBSERVATION, snapshot)


class SnapshotActivity:
    """Optional live observation for refresh pacing, never stored execution evidence."""

    ABSENT = None
    INACTIVE = False
    NO_QUEUED = 0
    NO_STATUS = ""
    NO_SESSIONS = ()
    ACTIVE_STATUSES = (
        SessionStatus.RUNNING,
        MessageStatus.STREAMING,
        MessageStatus.QUEUED,
    )

    @classmethod
    def pending(cls, snapshot):
        active = (
            snapshot is cls.ABSENT
            or bool(
                getattr(
                    getattr(snapshot, HubSnapshot.run.__name__, cls.ABSENT),
                    RunSummary.active.__name__,
                    cls.INACTIVE,
                )
            )
            or bool(getattr(snapshot, HubSnapshot.queued_count.__name__, cls.NO_QUEUED))
        )
        active = active or any(
            getattr(session, SessionSummary.status.__name__, cls.NO_STATUS)
            in cls.ACTIVE_STATUSES
            or getattr(session, SessionSummary.queued_count.__name__, cls.NO_QUEUED)
            for session in getattr(
                snapshot, HubSnapshot.sessions.__name__, cls.NO_SESSIONS
            )
        )
        return active


class SessionIdentity:
    """Borrow the UI's existing (Project, Session) pair without eager reads."""

    PROJECT_POSITION = 0
    SESSION_POSITION = 1
    PAIR_SIZE = SESSION_POSITION + 1

    def __init__(self, pair):
        self._pair = pair

    @property
    def project_id(self):
        return self._pair[self.PROJECT_POSITION]

    @property
    def session_id(self):
        return self._pair[self.SESSION_POSITION]

    @classmethod
    def current(cls, view):
        return cls(
            (HubSnapshot.project_identifier(view), view.sessions[view.selected].id)
        )

    def to_tuple(self):
        return self._pair

    def prefix(self):
        return self._pair[: self.PAIR_SIZE]


@dataclass(frozen=True, slots=True)
class QuestionPresentation:
    LIVE: ClassVar[bool] = True
    STORED: ClassVar[bool] = False

    run_id: str
    live: bool
    request: InteractionRequest

    def to_record(self):
        cls = type(self)
        return {
            cls.run_id.__name__: self.run_id,
            cls.live.__name__: self.live,
            cls.request.__name__: self.request.to_dict(),
        }

    @classmethod
    def request_only(cls, request):
        return {cls.request.__name__: request.to_dict()}

    @classmethod
    def live_request(cls, run_id, request):
        return cls(run_id, cls.LIVE, request).to_record()

    @classmethod
    def stored_request(cls, run_id, request):
        return cls(run_id, cls.STORED, request).to_record()

    @classmethod
    def observe(cls, value):
        return QuestionObservation(value)

    @classmethod
    def identity(cls, project_id, session_id, value):
        item = cls.observe(value)
        return project_id, session_id, item.request.id, item.request.revision


class QuestionObservation:
    """Borrow the existing public record, including its original request alias."""

    def __init__(self, value):
        self._value = value

    def to_record(self):
        return self._value

    @property
    def run_id(self):
        return self._value[QuestionPresentation.run_id.__name__]

    @property
    def live(self):
        return self._value[QuestionPresentation.live.__name__]

    @property
    def request(self):
        return InteractionRequestObservation(
            self._value[QuestionPresentation.request.__name__]
        )


class InstructionTargetChoice:
    """Projection of the Runtime's existing ordered (target id, label) choices."""

    FIRST_TARGET = 0
    IDENTIFIER_POSITION = 0

    @classmethod
    def first_id(cls, targets):
        return targets[cls.FIRST_TARGET][cls.IDENTIFIER_POSITION]
