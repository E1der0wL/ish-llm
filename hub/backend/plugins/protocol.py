"""Hub plugin transport declarations, not execution or persistence authority."""

from dataclasses import dataclass
from enum import StrEnum

from llm.core.contracts import JsonValue
from llm.core.interactions import InteractionView
from llm.core.models import MessageStatus, RunStatus
from llm.core.questions import QuestionView, AnswerDeliveryMode


class PluginTransport:
    DISPATCH = 'plugin_call'


@dataclass(frozen=True, slots=True)
class PluginDelivery(JsonValue):
    mode: AnswerDeliveryMode
    target_run_id: str | None = None
    targets: tuple[str, ...] | None = None
    recipients: tuple[str, ...] | None = None


class PluginOperation(StrEnum):
    REGISTER = "register"
    OPEN_SESSION = "open_session"
    SUBMIT = "submit"
    RECONNECT = "reconnect"
    POLL = "poll"
    CANCEL = "cancel"
    INTERRUPT = "interrupt"
    STEER = "steer"
    RESPOND = "respond"
    CANCEL_INTERACTION = "cancel_interaction"
    RESUME_PLAN = "resume_plan"
    RESUME = "resume"
    RECEIPT = "receipt"
    QUESTIONS = "questions"
    ANSWER_QUESTION = "answer_question"
    CANCEL_QUESTION = "cancel_question"
    DELIVER_ANSWER = "deliver_answer"
    GET_DELIVERY = "get_delivery"
    QUESTION_TARGETS = "question_targets"


@dataclass(frozen=True, slots=True)
class PluginReference(JsonValue):
    EMPTY = "Plugin request identities must not be empty"
    FOREIGN_PROJECT = "Plugin request reference belongs to another Project"

    generation: str
    project_id: str
    session_id: str
    request_id: str

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not all((self.generation, self.project_id, self.session_id, self.request_id)):
            raise ValueError(self.EMPTY)


@dataclass(frozen=True, slots=True)
class PluginSession(JsonValue):
    project_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class PluginRegistration(JsonValue):
    plugin_id: str
    project_id: str
    agents: tuple[str, ...]
    created: bool


@dataclass(frozen=True, slots=True)
class PluginProject(JsonValue):
    plugin_id: str
    project_id: str
    name: str
    agents: tuple[str, ...]


class PluginOutputKind(StrEnum):
    DELTA = "delta"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True)
class PluginOutput(JsonValue):
    """The value is an opaque native output; the plugin transport does not interpret it."""

    run_id: str
    agent_id: str | None
    kind: PluginOutputKind
    value: dict


@dataclass(frozen=True, slots=True)
class PluginRun(JsonValue):
    id: str
    agent_id: str | None
    engine: str
    status: RunStatus
    error: str | None
    error_code: str | None
    interactions: tuple[InteractionView, ...]
    questions: tuple[QuestionView, ...]


@dataclass(frozen=True, slots=True)
class PluginBatch(JsonValue):
    INITIAL_REVISION = -1
    INITIAL_CURSOR = 0

    request_id: str
    status: MessageStatus
    runs: tuple[PluginRun, ...]
    outputs: tuple[PluginOutput, ...]
    cursors: dict[str, int]
    revision: int
    terminal: bool
