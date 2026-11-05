"""Selected terminal observations; values never authorize process execution."""

from dataclasses import dataclass, replace
from enum import StrEnum

from llm.core.contracts import JsonValue
from llm.core.models import StepKind, StepStatus
from llm.components.tools.builtin.output import ProcessObservation
from llm.components.tools.builtin.process_values import ProcessOutput


class TerminalKind(StrEnum):
    PROCESS = 'process'
    RUN = 'run'
    STEP = 'step'


class TerminalOperation(StrEnum):
    CATALOG = 'terminal_catalog'
    STEPS = 'terminal_steps'
    PAGE = 'terminal_page'


@dataclass(frozen=True, slots=True)
class TerminalCatalog(JsonValue):
    processes: dict[str, ProcessObservation]
    runs: tuple[str, ...]
    process_error: str | None


@dataclass(frozen=True, slots=True)
class TerminalStep(JsonValue):
    id: str
    name: str
    status: StepStatus
    kind: StepKind


@dataclass(frozen=True, slots=True)
class TerminalOffsets(JsonValue):
    text: int = 0
    stdout: int = 0
    stderr: int = 0

    NEGATIVE = 'Terminal text offsets must be nonnegative'

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if min(self.text, self.stdout, self.stderr) < 0:
            raise ValueError(self.NEGATIVE)

    def after(self, page):
        if isinstance(page, ProcessOutput):
            return replace(self, stdout=page.next_stdout_offset, stderr=page.next_stderr_offset)
        return replace(self, text=page.next_offset)


@dataclass(frozen=True, slots=True)
class ProcessSource(JsonValue):
    project_id: str
    process_id: str


@dataclass(frozen=True, slots=True)
class StepSource(JsonValue):
    project_id: str
    session_id: str
    run_id: str
    step_id: str


@dataclass(frozen=True, slots=True)
class StepPage(JsonValue):
    name: str
    status: StepStatus
    error: str | None
    text: str
    offset: int
    next_offset: int
    remaining: int


@dataclass(frozen=True, slots=True)
class TerminalPage(JsonValue):
    source: ProcessSource | StepSource
    page: ProcessOutput | StepPage
    offset_unit: str = 'unicode_characters'

    INVALID_UNIT = 'Unsupported terminal observation offset unit'

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if self.offset_unit != type(self).__dataclass_fields__[type(self).offset_unit.__name__].default:
            raise ValueError(self.INVALID_UNIT)
