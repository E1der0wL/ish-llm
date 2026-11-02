"""Remembered approval data. Project owns rules; selected evaluators own config semantics."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from typing import get_type_hints

from jsonschema import Draft202012Validator

from .json_data import JsonObject
from .schema import object_schema, open_schema, OpenSchemaKind


class ApprovalRecord:
    """Closed records with one field declaration for storage and schema."""

    @classmethod
    def schema(cls):
        return object_schema(
            {item.name: deepcopy(item.metadata['schema']) for item in fields(cls)},
            required=[item.name for item in fields(cls)],
        )

    def __post_init__(self):
        hints = get_type_hints(type(self))
        for item in fields(self):
            kind = hints[item.name]
            if kind is int and type(getattr(self, item.name)) is not int:
                raise ValueError(f'{item.name} must be an integer')
            if isinstance(kind, type) and issubclass(kind, ApprovalRecord):
                if not isinstance(getattr(self, item.name), kind):
                    raise TypeError(f'{item.name} must be {kind.__name__}')
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
    project_id: str = field(metadata={'schema': {'type': 'string', 'minLength': 1}})
    session_id: str | None = field(metadata={'schema': {'type': ['string', 'null'], 'minLength': 1}})

    def matches(self, project_id, session_id):
        return self.project_id == project_id and self.session_id in (None, session_id)


@dataclass(frozen=True, slots=True)
class ApprovalOrigin(ApprovalRecord):
    run_id: str = field(metadata={'schema': {'type': 'string', 'minLength': 1}})
    request_id: str = field(metadata={'schema': {'type': 'string', 'minLength': 1}})
    request_revision: int = field(metadata={'schema': {'type': 'integer', 'minimum': 1}})
    request_fingerprint: str = field(metadata={'schema': {'type': 'string', 'minLength': 1}})
    created_at: str = field(metadata={'schema': {'type': 'string', 'minLength': 1}})


@dataclass(frozen=True, slots=True)
class RememberedApproval(ApprovalRecord):
    revision: int = field(metadata={'schema': {'type': 'integer', 'minimum': 1}})
    scope: ApprovalScope = field(metadata={'schema': ApprovalScope.schema()})
    condition: ApprovalCondition = field(metadata={'schema': ApprovalCondition.schema()})
    origin: ApprovalOrigin = field(metadata={'schema': ApprovalOrigin.schema()})
    revoked_at: str | None = field(metadata={'schema': {'type': ['string', 'null'], 'minLength': 1}})


def remember_schema(rule_schema):
    """Explicit caller choices; lifecycle fields are never caller-writable."""
    properties = deepcopy(rule_schema['properties'])
    properties.pop('remembered')
    properties['scope'] = {'enum': ['project', 'session']}
    properties['condition'] = ApprovalCondition.schema()
    value = object_schema(properties, required=['id', 'category', 'scope', 'condition'])
    value['oneOf'] = [
        {**deepcopy(branch), 'required': [key if key != 'remembered' else 'condition'
                                        for key in branch['required']]}
        for branch in rule_schema['oneOf']
    ]
    return value
