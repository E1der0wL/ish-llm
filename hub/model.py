"""Presentation data shared by Hub's backend adapter and widgets."""

from llm.providers.diagnostic_values import ProviderAttempt

from llm.core.models import SessionStatus, MessageStatus, RunStatus
from llm.core.interactions import InteractionRequest, InteractionRequestObservation, InteractionStatus
from llm.core.schema import SchemaKeyword, SchemaType
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
    RESUME_PREPARING = "resume_preparing"
    PREPARING = "preparing"
    SUMMARIZING = "summarizing"


class QuestionSource(StrEnum):
    """Hub question presentation fields, shared by Tool input and widgets."""

    SUGGESTIONS = "choices"
    STEP_ID = "step_id"

    @classmethod
    def suggestions(cls, source):
        return source.get(cls.SUGGESTIONS.value, [])

    @classmethod
    def create(cls, suggestions, step_id=""):
        return {cls.SUGGESTIONS.value: suggestions, cls.STEP_ID.value: step_id}


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
class AnswerDraft:
    """Unverified display observation, never conversation content or model input."""

    WRITING: ClassVar[str] = "draft_streaming"
    REVIEWING: ClassVar[str] = "draft_reviewing"

    text: str
    phase: str = WRITING


@dataclass(frozen=True, slots=True)
class InstructionDisplay:
    """An attached steering receipt; never replaces the stored user message."""

    id: str
    text: str
    status: str
    time: str = ""
    targets: tuple["InstructionTargetDisplay", ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class InstructionTargetDisplay:
    name: str
    status: str
    reason: str = ""


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
    workflow: str = ""
    workflow_states: tuple[tuple[str, str], ...] = ()
    draft: AnswerDraft | None = None
    instructions: tuple[InstructionDisplay, ...] = ()


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
    QUESTION: ClassVar[str] = "question"
    REASONING: ClassVar[str] = "reasoning"
    WORKFLOW: ClassVar[str] = "workflow"
    SUMMARY: ClassVar[str] = "summary"

    id: str
    kind: str
    text: str
    status: str = ""
    started_at: str = NO_TIME
    workflow_states: tuple[tuple[str, str], ...] = ()
    details: tuple[tuple[str, str], ...] = ()
    handled_failure: bool = False

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
class RequestStarted:
    """Persisted Run-start proof for a request row; never execution authority."""

    project_id: str
    session_id: str
    message_id: str
    run_id: str


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
    participant_ids: tuple[str, ...] = ()
    participant_groups: tuple = ()


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
    details: tuple[tuple[str, str], ...] = ()
    handled_failure: bool = False


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
    title: str = ""

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
    participant_ids: tuple[str, ...] = ()
    active_run_ids: tuple[str, ...] = ()

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
    run_id: str
    request: InteractionRequest
    nonblocking: bool = False
    response: dict | None = None
    delivery: dict | None = None
    status: str = 'pending'
    run_status: str | None = None

    def to_record(self):
        cls = type(self)
        return {
            cls.run_id.__name__: self.run_id,
            cls.request.__name__: self.request.to_dict(),
            cls.nonblocking.__name__: self.nonblocking,
            cls.response.__name__: self.response,
            cls.delivery.__name__: self.delivery,
            cls.status.__name__: self.status,
            cls.run_status.__name__: self.run_status,
        }

    @classmethod
    def request_only(cls, request):
        return {cls.request.__name__: request.to_dict()}

    @classmethod
    def nonblocking_request(cls, run_id, request, *, response=None, delivery=None,
                            status=InteractionStatus.PENDING, run_status=None):
        return cls(run_id, request, True,
                   response.to_dict() if response is not None else None,
                   delivery, status, run_status).to_record()

    @classmethod
    def stored_request(cls, run_id, request, *, run_status=None):
        return cls(run_id, request, run_status=run_status).to_record()

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

    def with_run_status(self, status):
        return {**self._value, QuestionPresentation.run_status.__name__: str(status)}

    def with_response(self, response):
        from llm.core.interactions import InteractionResponse, InteractionStatus
        parsed = InteractionResponse.from_dict(response)
        return {**self._value, QuestionPresentation.response.__name__: parsed.to_dict(),
                QuestionPresentation.status.__name__: InteractionStatus.ANSWERED.value}

    def with_delivery(self, delivery):
        from copy import deepcopy
        return {**self._value, QuestionPresentation.delivery.__name__: deepcopy(delivery)}

    def retaining_answer(self, previous):
        """A stale UI observation cannot erase a successfully saved answer/receipt."""
        from copy import deepcopy
        result = deepcopy(self._value)
        for name in (QuestionPresentation.response.__name__, QuestionPresentation.delivery.__name__):
            if previous and previous.get(name) and not result.get(name):
                result[name] = deepcopy(previous[name])
        return result

    @property
    def run_id(self):
        return self._value[QuestionPresentation.run_id.__name__]

    @property
    def nonblocking(self):
        return self._value.get(QuestionPresentation.nonblocking.__name__, False)

    @property
    def response(self):
        return self._value.get(QuestionPresentation.response.__name__)

    @property
    def delivery(self):
        return self._value.get(QuestionPresentation.delivery.__name__)

    @property
    def status(self):
        return self._value.get(QuestionPresentation.status.__name__, InteractionStatus.PENDING)

    @property
    def source_finished(self):
        # Missing targets/observations are not evidence that a Run has ended.
        # PAUSED still permits the original blocking answer/resume workflow.
        return self._value.get(QuestionPresentation.run_status.__name__) in (
            RunStatus.COMPLETED, RunStatus.INTERRUPTED, RunStatus.CANCELLED, RunStatus.FAILED)

    @property
    def text_answer(self):
        return (self.request.kind == InteractionRequest.KIND_INPUT and self.request.options.count == 1
                and (self.request.input_schema or {}).get(SchemaKeyword.TYPE) == SchemaType.STRING)

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
