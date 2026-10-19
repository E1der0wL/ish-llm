"""Pure final-candidate decisions over detached, Provider-independent values."""

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
import math

from llm.core.model_protocol import ModelMessage, ModelResponse, ToolDefinition, json_copy


class ReviewAction(StrEnum):
    ACCEPT = "accept"
    CONTINUE = "continue"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class CompletionReview:
    action: ReviewAction = ReviewAction.ACCEPT
    feedback: str = ""

    def __post_init__(self):
        if not isinstance(self.action, ReviewAction) or not isinstance(self.feedback, str):
            raise TypeError("Final review requires ReviewAction and text feedback")


@dataclass(frozen=True, slots=True)
class CompletionReviewObservation:
    """Prepared and original common transcript; no invocation options/credentials.

    Each callback receives a separate copy. A reviewer owns only its returned
    decision. Re-review after a crash must be pure for this observation and the
    processor's declared review_revision; arbitrary Python is not sandboxed.
    """

    iteration: int
    messages: tuple[ModelMessage, ...]
    answer: ModelResponse
    original_messages: tuple[ModelMessage, ...]
    tools: tuple[ToolDefinition, ...] = ()

    def __post_init__(self):
        if type(self.iteration) is not int or self.iteration < 1:
            raise ValueError("Review iteration must be positive")
        for values in (self.messages, self.original_messages):
            if not isinstance(values, tuple) or any(not isinstance(m, ModelMessage) for m in values):
                raise TypeError("Review requires common model messages")
        if not isinstance(self.answer, ModelResponse) or self.answer.message.calls:
            raise ValueError("Only a Tool-free final candidate can be reviewed")
        if not isinstance(self.tools, tuple) or any(not isinstance(t, ToolDefinition) for t in self.tools):
            raise TypeError("Review requires common Tool definitions")
        for name in ("messages", "answer", "original_messages", "tools"):
            object.__setattr__(self, name, deepcopy(getattr(self, name)))

    def snapshot(self):
        return deepcopy(self)

    def to_dict(self):
        return {"version": 1, "iteration": self.iteration,
                "messages": [m.to_dict() for m in self.messages],
                "answer": self.answer.to_dict(),
                "original_messages": [m.to_dict() for m in self.original_messages],
                "tools": [t.to_dict() for t in self.tools]}

    @classmethod
    def from_dict(cls, value):
        value = json_copy(value)
        if (set(value) != {"version", "iteration", "messages", "answer", "original_messages", "tools"}
                or type(value["version"]) is not int or value["version"] != 1):
            raise ValueError("Invalid final review observation")
        return cls(value["iteration"], tuple(ModelMessage.from_dict(m) for m in value["messages"]),
                   ModelResponse.from_dict(value["answer"]),
                   tuple(ModelMessage.from_dict(m) for m in value["original_messages"]),
                   tuple(ToolDefinition(**t) for t in value["tools"]))


REVIEW_DEFAULTS = {"max_continuations": 8, "max_attempts": 3,
                   "hook_timeout": 30.0, "max_feedback_chars": 4096}


def review_policy(values=None):
    if values is None:
        values = {}
    if not isinstance(values, dict) or set(values) - REVIEW_DEFAULTS.keys():
        raise ValueError("Invalid final_review policy fields")
    result = {**REVIEW_DEFAULTS, **values}
    for name in ("max_continuations", "max_attempts", "max_feedback_chars"):
        if type(result[name]) is not int or result[name] < (0 if name == "max_continuations" else 1):
            raise ValueError("Invalid final_review " + name)
    duration = result["hook_timeout"]
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid final_review hook_timeout")
    return result


def review_schema():
    return {"type": "object", "additionalProperties": False, "properties": {
        "max_continuations": {"type": "integer", "minimum": 0, "x-narrowing": "maximum"},
        "max_attempts": {"type": "integer", "minimum": 1, "x-narrowing": "maximum"},
        "hook_timeout": {"type": "number", "exclusiveMinimum": 0, "x-narrowing": "maximum"},
        "max_feedback_chars": {"type": "integer", "minimum": 1, "x-narrowing": "maximum"}}}


def review_action(decisions):
    actions = {ReviewAction(d["action"]) for d in decisions}
    return (ReviewAction.BLOCK if ReviewAction.BLOCK in actions else
            ReviewAction.CONTINUE if ReviewAction.CONTINUE in actions else ReviewAction.ACCEPT)
