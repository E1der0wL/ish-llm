"""Presentation data shared by Hub's backend adapter and widgets."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    text: str
    time: str = ""
    status: str = ""
    id: str = ""
    author: str = ""
    elapsed_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    run_id: str | None = None
    message: ChatMessage | None = None


@dataclass(frozen=True, slots=True)
class SessionSummary:
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
    name: str
    status: str


@dataclass(frozen=True, slots=True)
class RunSummary:
    id: str
    status: str
    engine: str
    elapsed_seconds: float | None
    active: bool
    steps: tuple[StepSummary, ...]
    has_reasoning: bool
    completion_count: int
    model: str
    tokens: int | None
    error: str


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
    status: str = "idle"
    queued_count: int = 0
    model_required: bool = False
    title_error: str = ""
    engines: tuple[str, ...] = ()
    file_root: str = ""
    notifications: tuple[SessionNotification, ...] = ()
    components: tuple[str, ...] = ()
