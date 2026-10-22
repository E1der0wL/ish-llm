"""Explicit final-candidate review values, configuration and decision precedence."""
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math

from llm.core.model_protocol import ModelMessage, ModelResponse, ToolDefinition, json_copy


class ReviewAction(StrEnum):
    ACCEPT = "accept"
    CONTINUE = "continue"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class CompletionReview:
    action: ReviewAction
    feedback: str

    def __post_init__(self):
        if not isinstance(self.action, ReviewAction) or not isinstance(self.feedback, str):
            raise TypeError("Review requires an explicit ReviewAction and text feedback")


@dataclass(frozen=True, slots=True)
class CompletionReviewObservation:
    """Detached common transcript; invocation parameters/credentials stay outside."""
    SCHEMA_VERSION = 1
    iteration: int
    messages: tuple[ModelMessage, ...]
    response: ModelResponse
    original_messages: tuple[ModelMessage, ...]
    tools: tuple[ToolDefinition, ...]

    def __post_init__(self):
        if type(self.iteration) is not int or self.iteration < 1:
            raise ValueError("Review iteration must be positive")
        for rows in (self.messages, self.original_messages):
            if not isinstance(rows, tuple) or any(not isinstance(m, ModelMessage) for m in rows):
                raise TypeError("Review requires common messages")
        if not isinstance(self.response, ModelResponse) or self.response.message.calls:
            raise ValueError("Review requires a Tool-free final candidate")
        if not isinstance(self.tools, tuple) or any(not isinstance(t, ToolDefinition) for t in self.tools):
            raise TypeError("Review requires common Tool definitions")
        for name in ("messages", "response", "original_messages", "tools"):
            object.__setattr__(self, name, deepcopy(getattr(self, name)))

    def snapshot(self):
        return deepcopy(self)

    def to_dict(self):
        return {"schema_version": self.SCHEMA_VERSION, "iteration": self.iteration,
                "messages": [m.to_dict() for m in self.messages], "response": self.response.to_dict(),
                "original_messages": [m.to_dict() for m in self.original_messages],
                "tools": [t.to_dict() for t in self.tools]}

    @classmethod
    def from_dict(cls, value):
        value = json_copy(value)
        if (not isinstance(value, dict) or value.keys() != {"schema_version", "iteration", "messages", "response", "original_messages", "tools"}
                or any(not isinstance(value[k], list) for k in ("messages", "original_messages", "tools"))
                or type(value["schema_version"]) is not int or value["schema_version"] != cls.SCHEMA_VERSION):
            raise ValueError("Final review observation requires every current field")
        return cls(value["iteration"], tuple(ModelMessage.from_dict(m) for m in value["messages"]),
                   ModelResponse.from_dict(value["response"]),
                   tuple(ModelMessage.from_dict(m) for m in value["original_messages"]),
                   tuple(ToolDefinition(**t) for t in value["tools"]))


class ReviewPolicy:
    INTEGER_MINIMUMS = {"max_continuations": 0, "max_attempts": 1, "max_feedback_chars": 1}
    HOOK_TIMEOUT_MINIMUM = 0
    FIELDS = frozenset((*INTEGER_MINIMUMS, "hook_timeout"))

    @classmethod
    def read(cls, value):
        # Missing/Null is disabled; an object explicitly activates and must be complete.
        if value is None:
            return None
        if not isinstance(value, dict) or value.keys() != cls.FIELDS:
            raise ValueError("Active final_review requires all explicit budgets and feedback bound")
        for name, lower in cls.INTEGER_MINIMUMS.items():
            if type(value[name]) is not int or value[name] < lower:
                raise ValueError("Invalid final_review " + name)
        duration = value["hook_timeout"]
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= cls.HOOK_TIMEOUT_MINIMUM:
            raise ValueError("final_review hook_timeout must be positive and finite")
        return deepcopy(value)

    @classmethod
    def schema(cls):
        properties = {name: {"type": "integer", "minimum": lower,
                             "x-narrowing": "maximum"}
                      for name, lower in cls.INTEGER_MINIMUMS.items()}
        properties["hook_timeout"] = {"type": "number", "exclusiveMinimum": cls.HOOK_TIMEOUT_MINIMUM, "x-narrowing": "maximum"}
        return {"type": ["object", "null"], "properties": properties,
                "required": sorted(cls.FIELDS), "additionalProperties": False}


def review_action(decisions):
    if not decisions:
        raise ValueError("Active review requires at least one verdict")
    actions = {ReviewAction(value["action"]) for value in decisions}
    for action in (ReviewAction.BLOCK, ReviewAction.CONTINUE, ReviewAction.ACCEPT):
        if action in actions:
            return action
    raise ValueError("Review has no action")


def review_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def reviewer_identity(processor):
    revision = processor.review_revision
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("Final reviewer requires an explicit immutable review_revision")
    return {"name": processor.name, "priority": processor.priority, "revision": revision}
