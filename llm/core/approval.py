"""Remembered approval data. Project owns rules; selected evaluators own config semantics."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields, replace
from enum import StrEnum
from typing import ClassVar, TYPE_CHECKING, get_type_hints

from jsonschema import Draft202012Validator

from .json_data import JsonObject
from .schema import object_schema, open_schema, OpenSchemaKind, SchemaKeyword

if TYPE_CHECKING:
    from .policies import ApprovalRule


class ApprovalRecord:
    """Closed records with one field declaration for storage and schema."""

    SCHEMA = 'schema'
    INVALID_INTEGER = '{name} must be an integer'
    INVALID_RECORD = '{name} must be {kind}'

    @classmethod
    def schema(cls):
        return object_schema(
            {item.name: deepcopy(item.metadata[cls.SCHEMA]) for item in fields(cls)},
            required=[item.name for item in fields(cls)],
        )

    def __post_init__(self):
        hints = get_type_hints(type(self))
        for item in fields(self):
            kind = hints[item.name]
            if kind is int and type(getattr(self, item.name)) is not int:
                raise ValueError(self.INVALID_INTEGER.format(name=item.name))
            if isinstance(kind, type) and issubclass(kind, ApprovalRecord):
                if not isinstance(getattr(self, item.name), kind):
                    raise TypeError(self.INVALID_RECORD.format(name=item.name, kind=kind.__name__))
        value = self.to_dict()
        JsonObject.validate(value)
        Draft202012Validator(self.schema()).validate(value)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        JsonObject.validate(value)
        Draft202012Validator(cls.schema()).validate(value)
        hints = get_type_hints(cls)
        return cls(**{
            key: hints[key].from_dict(item)
            if isinstance(hints[key], type) and issubclass(hints[key], ApprovalRecord)
            else deepcopy(item)
            for key, item in value.items()
        })


@dataclass(frozen=True, slots=True)
class ApprovalCondition(ApprovalRecord):
    implementation: str = field(metadata={'schema': {'type': 'string', 'minLength': 1}})
    revision: str = field(metadata={'schema': {'type': 'string', 'minLength': 1}})
    config: dict = field(metadata={'schema': open_schema(
        'selected approval evaluator', category=OpenSchemaKind.IMPLEMENTATION)})


@dataclass(frozen=True, slots=True)
class ApprovalScope(ApprovalRecord):
    PROJECT_WIDE: ClassVar[None] = None
    project_id: str = field(metadata={'schema': {'type': 'string', 'minLength': 1}})
    session_id: str | None = field(metadata={'schema': {'type': ['string', 'null'], 'minLength': 1}})

    def matches(self, project_id, session_id):
        return self.project_id == project_id and self.session_id in (self.PROJECT_WIDE, session_id)


@dataclass(frozen=True, slots=True)
class ApprovalOrigin(ApprovalRecord):
    run_id: str = field(metadata={'schema': {'type': 'string', 'minLength': 1}})
    request_id: str = field(metadata={'schema': {'type': 'string', 'minLength': 1}})
    request_revision: int = field(metadata={'schema': {'type': 'integer', 'minimum': 1}})
    request_fingerprint: str = field(metadata={'schema': {'type': 'string', 'minLength': 1}})
    created_at: str = field(metadata={'schema': {'type': 'string', 'minLength': 1}})


@dataclass(frozen=True, slots=True)
class RememberedApproval(ApprovalRecord):
    INITIAL_REVISION: ClassVar[int] = 1
    REVISION_STEP: ClassVar[int] = 1
    NOT_REVOKED: ClassVar[None] = None

    revision: int = field(metadata={'schema': {'type': 'integer', 'minimum': INITIAL_REVISION}})
    scope: ApprovalScope = field(metadata={'schema': ApprovalScope.schema()})
    condition: ApprovalCondition = field(metadata={'schema': ApprovalCondition.schema()})
    origin: ApprovalOrigin = field(metadata={'schema': ApprovalOrigin.schema()})
    revoked_at: str | None = field(metadata={'schema': {'type': ['string', 'null'], 'minLength': 1}})

    def updated(self, *, condition):
        return replace(self, revision=self.revision + self.REVISION_STEP, condition=condition)

    def revoke(self, instant):
        return replace(self, revision=self.revision + self.REVISION_STEP, revoked_at=instant)


class RememberScope(StrEnum):
    PROJECT = 'project'
    SESSION = 'session'


@dataclass(frozen=True, slots=True)
class RememberRequest:
    """Caller choices projected onto ApprovalRule; lifecycle fields stay private."""

    scope: str = field(metadata={'enum': [scope.value for scope in RememberScope]})
    condition: ApprovalCondition = field(metadata=ApprovalCondition.schema())
    rule: 'ApprovalRule'

    @classmethod
    def schema(cls, rule_schema):
        from .policies import ApprovalRule

        properties = deepcopy(rule_schema[SchemaKeyword.PROPERTIES])
        properties.pop(ApprovalRule.remembered.__name__)
        properties.update({item.name: deepcopy(dict(item.metadata))
                           for item in fields(cls) if item.metadata})
        value = object_schema(properties, required=[ApprovalRule.id.__name__, ApprovalRule.category.__name__,
                                                    cls.scope.__name__, cls.condition.__name__])
        value[SchemaKeyword.ONE_OF] = [
            {**deepcopy(branch), SchemaKeyword.REQUIRED: [
                key if key != ApprovalRule.remembered.__name__ else cls.condition.__name__
                for key in branch[SchemaKeyword.REQUIRED]]}
            for branch in rule_schema[SchemaKeyword.ONE_OF]
        ]
        return value

    @classmethod
    def read(cls, value):
        from .policies import ApprovalRule

        Draft202012Validator(cls.schema(ApprovalRule.schema())).validate(value)
        options = deepcopy(value)
        scope = options.pop(cls.scope.__name__)
        condition = ApprovalCondition.from_dict(options.pop(cls.condition.__name__))
        return cls(scope, condition, ApprovalRule.from_dict(options))

    def bound_scope(self, project_id, session_id):
        return ApprovalScope(project_id, session_id if self.scope == RememberScope.SESSION
                             else ApprovalScope.PROJECT_WIDE)


def remember_schema(rule_schema):
    """Explicit caller choices; lifecycle fields are never caller-writable."""
    return RememberRequest.schema(rule_schema)
