"""Composer requests and target projections; llm owns questions and delivery receipts."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from llm.core.contracts import JsonValue
from llm.core.interactions import InteractionRequest
from llm.core.models import RunStatus


class QuestionAction(StrEnum):
    LIST = 'list'
    TARGETS = 'targets'
    SAVE = 'save'
    DELIVER = 'deliver'


@dataclass(frozen=True, slots=True)
class QuestionList(JsonValue):
    run_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QuestionRun(JsonValue):
    run_id: str


@dataclass(frozen=True, slots=True)
class QuestionAnswer(JsonValue):
    run_id: str
    request: InteractionRequest
    option_id: str | None
    value: Any


@dataclass(frozen=True, slots=True)
class QuestionDelivery(JsonValue):
    FOREIGN_RUN = "The answer must target the question's original Run"

    run_id: str
    request_id: str
    target_run_id: str
    targets: tuple[str, ...]

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if self.target_run_id != self.run_id:
            raise ValueError(self.FOREIGN_RUN)


@dataclass(frozen=True, slots=True)
class QuestionTarget(JsonValue):
    run_id: str
    target_id: str
    label: str


@dataclass(frozen=True, slots=True)
class QuestionTargets(JsonValue):
    status: RunStatus
    targets: tuple[QuestionTarget, ...]


@dataclass(frozen=True, slots=True)
class ApprovalAnswer(JsonValue):
    saved: bool
    continued: bool
    continue_requested: bool
