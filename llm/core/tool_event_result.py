"""The mutable ToolExecutor result channel shared with trusted event handlers."""

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
import json

from llm.core.contracts import Diagnostic, JsonValue


class ToolOutcomeStatus(StrEnum):
    FAILED = "failed"
    DECLINED = "declined"
    NOT_EXECUTED = "not_executed"


@dataclass(frozen=True, slots=True)
class ToolOutcome(JsonValue):
    """A terminal non-success result, not permission to retry or widen authority."""
    NO_STEP = None

    status: str
    diagnostic: Diagnostic
    step_id: str | None

    def __post_init__(self):
        super(ToolOutcome, self).__post_init__()
        if self.status not in ToolOutcomeStatus or not isinstance(self.diagnostic, Diagnostic):
            raise ValueError("Invalid Tool outcome")
        if self.step_id is not self.NO_STEP and (not isinstance(self.step_id, str) or not self.step_id):
            raise ValueError("Invalid Tool outcome Step")
        if self.status != ToolOutcomeStatus.NOT_EXECUTED and self.step_id is self.NO_STEP:
            raise ValueError("Executed or declined outcomes require their Step")


class ToolEventResult:
    VALUE = "value"
    CONTENT = "content"
    PAUSED = "paused"
    RETRY = "retry"
    OUTCOME = "outcome"
    IS_PAUSED = True

    def __init__(self, target):
        # This is a live output sink. A copied dict would hide a Host handler's
        # writes from its caller and could incorrectly report missing effects.
        self._target = target

    @property
    def value(self):
        return self._target[self.VALUE]

    @property
    def content(self):
        return self._target[self.CONTENT]

    @property
    def paused(self):
        return self._target.get(self.PAUSED)

    def pause(self):
        self._target[self.PAUSED] = self.IS_PAUSED

    def yielded(self, value):
        self._target[self.VALUE] = value

    def complete(self, value, content):
        self._target.update({self.VALUE: deepcopy(value), self.CONTENT: content})

    def record_retry(self, retry):
        self._target[self.RETRY] = deepcopy(retry)

    @property
    def outcome(self):
        value = self._target.get(self.OUTCOME)
        return None if value is None else ToolOutcome.from_dict(value)

    def reject(self, outcome: ToolOutcome):
        value = outcome.to_dict()
        self.complete(value, json.dumps(value, ensure_ascii=False, allow_nan=False))
        self._target[self.OUTCOME] = value
