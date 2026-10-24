"""Explicit final-candidate review values, configuration and decision precedence."""

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math

from llm.core.model_protocol import ModelMessage, ModelResponse, ToolDefinition, json_copy
from llm.core.schema import SchemaBoolean, SchemaExtension, SchemaKeyword, SchemaNarrowing, SchemaType


class ReviewAction(StrEnum):
    ACCEPT = "accept"
    CONTINUE = "continue"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class CompletionReview:
    INVALID = "Review requires an explicit ReviewAction and text feedback"
    INVALID_RECORD = "Review verdict provenance or action is invalid"
    action: ReviewAction
    feedback: str

    def __post_init__(self):
        if not isinstance(self.action, ReviewAction) or not isinstance(self.feedback, str):
            raise TypeError(self.INVALID)

    def record(self, identity):
        return {
            **identity,
            type(self).action.__name__: str(self.action),
            type(self).feedback.__name__: self.feedback,
        }

    @classmethod
    def validate_record(cls, verdict, identity):
        if (
            not isinstance(verdict, dict)
            or verdict.keys() != {*identity, cls.action.__name__, cls.feedback.__name__}
            or any(verdict[k] != v for k, v in identity.items())
            or verdict[cls.action.__name__] not in tuple(ReviewAction)
            or not isinstance(verdict[cls.feedback.__name__], str)
        ):
            raise ValueError(cls.INVALID_RECORD)

    @classmethod
    def feedback_of(cls, value):
        return value[cls.feedback.__name__]

    @classmethod
    def action_of(cls, value):
        return value[cls.action.__name__]


@dataclass(frozen=True, slots=True)
class CompletionReviewObservation:
    """Detached common transcript; invocation parameters/credentials stay outside."""

    SCHEMA_VERSION = 1
    SCHEMA_FIELD = "schema_version"
    FIRST_ITERATION = 1
    INVALID_ITERATION = "Review iteration must be positive"
    INVALID_MESSAGES = "Review requires common messages"
    INVALID_RESPONSE = "Review requires a Tool-free final candidate"
    INVALID_TOOLS = "Review requires common Tool definitions"
    INVALID_RECORD = "Final review observation requires every current field"
    iteration: int
    messages: tuple[ModelMessage, ...]
    response: ModelResponse
    original_messages: tuple[ModelMessage, ...]
    tools: tuple[ToolDefinition, ...]

    def __post_init__(self):
        if type(self.iteration) is not int or self.iteration < self.FIRST_ITERATION:
            raise ValueError(self.INVALID_ITERATION)
        for rows in (self.messages, self.original_messages):
            if not isinstance(rows, tuple) or any(not isinstance(m, ModelMessage) for m in rows):
                raise TypeError(self.INVALID_MESSAGES)
        if not isinstance(self.response, ModelResponse) or self.response.message.calls:
            raise ValueError(self.INVALID_RESPONSE)
        if not isinstance(self.tools, tuple) or any(not isinstance(t, ToolDefinition) for t in self.tools):
            raise TypeError(self.INVALID_TOOLS)
        cls = type(self)
        for name in (
            cls.messages.__name__,
            cls.response.__name__,
            cls.original_messages.__name__,
            cls.tools.__name__,
        ):
            object.__setattr__(self, name, deepcopy(getattr(self, name)))

    def snapshot(self):
        return deepcopy(self)

    def to_dict(self):
        cls = type(self)
        return {
            cls.SCHEMA_FIELD: self.SCHEMA_VERSION,
            cls.iteration.__name__: self.iteration,
            cls.messages.__name__: [m.to_dict() for m in self.messages],
            cls.response.__name__: self.response.to_dict(),
            cls.original_messages.__name__: [m.to_dict() for m in self.original_messages],
            cls.tools.__name__: [t.to_dict() for t in self.tools],
        }

    @classmethod
    def from_dict(cls, value):
        value = json_copy(value)
        if (
            not isinstance(value, dict)
            or value.keys()
            != {
                cls.SCHEMA_FIELD,
                cls.iteration.__name__,
                cls.messages.__name__,
                cls.response.__name__,
                cls.original_messages.__name__,
                cls.tools.__name__,
            }
            or any(
                not isinstance(value[k], list)
                for k in (cls.messages.__name__, cls.original_messages.__name__, cls.tools.__name__)
            )
            or type(value[cls.SCHEMA_FIELD]) is not int
            or value[cls.SCHEMA_FIELD] != cls.SCHEMA_VERSION
        ):
            raise ValueError(cls.INVALID_RECORD)
        return cls(
            value[cls.iteration.__name__],
            tuple(ModelMessage.from_dict(m) for m in value[cls.messages.__name__]),
            ModelResponse.from_dict(value[cls.response.__name__]),
            tuple(ModelMessage.from_dict(m) for m in value[cls.original_messages.__name__]),
            tuple(ToolDefinition(**t) for t in value[cls.tools.__name__]),
        )


class ReviewPolicy:
    class Field:
        MAX_CONTINUATIONS = "max_continuations"
        MAX_ATTEMPTS = "max_attempts"
        MAX_FEEDBACK_CHARS = "max_feedback_chars"
        HOOK_TIMEOUT = "hook_timeout"

    INTEGER_MINIMUMS = {Field.MAX_CONTINUATIONS: 0, Field.MAX_ATTEMPTS: 1, Field.MAX_FEEDBACK_CHARS: 1}
    HOOK_TIMEOUT_MINIMUM = 0
    DISABLED = None
    FIELDS = frozenset((*INTEGER_MINIMUMS, Field.HOOK_TIMEOUT))
    INCOMPLETE = "Active final_review requires all explicit budgets and feedback bound"
    INVALID_BUDGET = "Invalid final_review "
    INVALID_TIMEOUT = "final_review hook_timeout must be positive and finite"

    @classmethod
    def read(cls, value):
        # Missing/Null is disabled; an object explicitly activates and must be complete.
        if value is cls.DISABLED:
            return cls.DISABLED
        if not isinstance(value, dict) or value.keys() != cls.FIELDS:
            raise ValueError(cls.INCOMPLETE)
        for name, lower in cls.INTEGER_MINIMUMS.items():
            if type(value[name]) is not int or value[name] < lower:
                raise ValueError(cls.INVALID_BUDGET + name)
        duration = value[cls.Field.HOOK_TIMEOUT]
        if (
            type(duration) not in (int, float)
            or not math.isfinite(duration)
            or duration <= cls.HOOK_TIMEOUT_MINIMUM
        ):
            raise ValueError(cls.INVALID_TIMEOUT)
        return deepcopy(value)

    @classmethod
    def hook_timeout(cls, value):
        return value[cls.Field.HOOK_TIMEOUT]

    @classmethod
    def max_feedback_chars(cls, value):
        return value[cls.Field.MAX_FEEDBACK_CHARS]

    @classmethod
    def max_attempts(cls, value):
        return value[cls.Field.MAX_ATTEMPTS]

    @classmethod
    def max_continuations(cls, value):
        return value[cls.Field.MAX_CONTINUATIONS]

    @classmethod
    def schema(cls):
        properties = {
            name: {
                SchemaKeyword.TYPE: SchemaType.INTEGER,
                SchemaKeyword.MINIMUM: lower,
                SchemaExtension.NARROWING: SchemaNarrowing.MAXIMUM,
            }
            for name, lower in cls.INTEGER_MINIMUMS.items()
        }
        properties[cls.Field.HOOK_TIMEOUT] = {
            SchemaKeyword.TYPE: SchemaType.NUMBER,
            SchemaKeyword.EXCLUSIVE_MINIMUM: cls.HOOK_TIMEOUT_MINIMUM,
            SchemaExtension.NARROWING: SchemaNarrowing.MAXIMUM,
        }
        return {
            SchemaKeyword.TYPE: [SchemaType.OBJECT, SchemaType.NULL],
            SchemaKeyword.PROPERTIES: properties,
            SchemaKeyword.REQUIRED: sorted(cls.FIELDS),
            SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT,
        }


class ReviewDecision:
    PRECEDENCE = (ReviewAction.BLOCK, ReviewAction.CONTINUE, ReviewAction.ACCEPT)
    MISSING_VERDICT = "Active review requires at least one verdict"
    MISSING_ACTION = "Review has no action"


def review_action(decisions):
    if not decisions:
        raise ValueError(ReviewDecision.MISSING_VERDICT)
    actions = {ReviewAction(CompletionReview.action_of(value)) for value in decisions}
    for action in ReviewDecision.PRECEDENCE:
        if action in actions:
            return action
    raise ValueError(ReviewDecision.MISSING_ACTION)


class ReviewFingerprint:
    SORT_KEYS = True
    ENSURE_ASCII = False
    ALLOW_NAN = False


def review_digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=ReviewFingerprint.SORT_KEYS,
            ensure_ascii=ReviewFingerprint.ENSURE_ASCII,
            allow_nan=ReviewFingerprint.ALLOW_NAN,
        ).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ReviewerIdentity:
    REVISION_ATTRIBUTE = "review_revision"
    INVALID_REVISION = "Final reviewer requires an explicit immutable review_revision"
    INVALID_RECORD = "Invalid reviewer identity"
    name: str
    priority: int
    revision: str

    @classmethod
    def validate_record(cls, identity):
        if (
            not isinstance(identity, dict)
            or identity.keys() != {cls.name.__name__, cls.priority.__name__, cls.revision.__name__}
            or any(
                not isinstance(identity[k], str) or not identity[k].strip()
                for k in (cls.name.__name__, cls.revision.__name__)
            )
            or type(identity[cls.priority.__name__]) is not int
        ):
            raise ValueError(cls.INVALID_RECORD)

    @classmethod
    def name_of(cls, identity):
        return identity[cls.name.__name__]

    @classmethod
    def order_key(cls, identity):
        return identity[cls.priority.__name__], identity[cls.name.__name__]

    @classmethod
    def from_processor(cls, processor):
        revision = processor.review_revision
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError(cls.INVALID_REVISION)
        return cls(processor.name, processor.priority, revision)

    def to_dict(self):
        cls = type(self)
        return {
            cls.name.__name__: self.name,
            cls.priority.__name__: self.priority,
            cls.revision.__name__: self.revision,
        }


def reviewer_identity(processor):
    return ReviewerIdentity.from_processor(processor).to_dict()
