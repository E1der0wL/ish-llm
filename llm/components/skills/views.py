"""Skills Tool request and public response Owners; no execution authority or storage."""

from copy import deepcopy
from dataclasses import MISSING, asdict, dataclass, field, fields
from typing import ClassVar

from jsonschema import Draft202012Validator

from llm.core.schema import object_schema, validate_declared_fields
from llm.core.skills import SkillName


class _Request:
    @classmethod
    def schema(cls):
        declarations = fields(cls)
        result = object_schema(
            {item.name: deepcopy(item.metadata["schema"]) for item in declarations},
            required=[item.name for item in declarations if item.default is MISSING])
        selectors = [item.name for item in declarations if item.metadata.get("selection")]
        if selectors:
            result["oneOf"] = [{"required": [name], "not": {"required": [other]}}
                               for name in selectors for other in selectors if name != other]
        return result

    @classmethod
    def from_dict(cls, value):
        schema = cls.schema()
        error = next(Draft202012Validator(schema).iter_errors(value), None)
        if error:
            raise ValueError(f"Invalid Skill Tool request: {error.message}")
        validate_declared_fields(value, schema["properties"])
        return cls(**deepcopy(value))


@dataclass(frozen=True)
class SkillListRequest(_Request):
    NAME: ClassVar[str] = "skill_list"
    query: str = field(default="", metadata={"schema": {"type": "string"}})
    after: str = field(default="", metadata={"schema": SkillName.SCHEMA})
    limit: int | None = field(default=None, metadata={"schema": {"type": "integer", "minimum": 1}})


@dataclass(frozen=True)
class SkillReadRequest(_Request):
    NAME: ClassVar[str] = "skill_read"
    identifier: str = field(metadata={"schema": SkillName.SCHEMA})
    expected_revision: str | None = field(default=None, metadata={"schema": {"type": "string", "minLength": 1}})


@dataclass(frozen=True)
class SkillResourceRequest(_Request):
    NAME: ClassVar[str] = "skill_read_resource"
    identifier: str = field(metadata={"schema": SkillName.SCHEMA})
    index: int | None = field(default=None, metadata={"schema": {"type": "integer", "minimum": 0}, "selection": True})
    path: str | None = field(default=None, metadata={"schema": {"type": "string", "minLength": 1}, "selection": True})


@dataclass(frozen=True)
class SkillSummary:
    id: str
    revision: str
    name: str
    description: str

    @classmethod
    def from_snapshot(cls, snapshot):
        return cls(snapshot.id, snapshot.revision, snapshot.name, snapshot.description)

    def matches(self, terms):
        haystack = (self.name + " " + self.description).casefold()
        return all(term in haystack for term in terms)


@dataclass(frozen=True)
class SkillPage:
    skills: list[SkillSummary]
    total: int
    next_after: str | None

    @classmethod
    def select(cls, snapshots, request):
        terms = request.query.casefold().split()
        matches = [summary for snapshot in sorted(snapshots, key=lambda item: item.id)
                   if (summary := SkillSummary.from_snapshot(snapshot)).matches(terms)]
        remaining = [row for row in matches if row.id > request.after]
        page = remaining if request.limit is None else remaining[:request.limit]
        return cls(page, len(matches), page[-1].id if len(page) < len(remaining) else None)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class SkillReadView:
    id: str
    revision: str
    definition: dict

    @classmethod
    def from_snapshot(cls, snapshot):
        return cls(snapshot.id, snapshot.revision, snapshot.to_dict())

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class SkillFileView:
    path: str
    text: str

    @classmethod
    def describe(cls, path, content, evidence):
        return {**asdict(cls(path, content.decode("utf-8"))), **evidence.to_dict()}


@dataclass(frozen=True)
class SkillReferenceView:
    reference: dict

    def to_dict(self):
        return asdict(self)

    def with_content(self, content, evidence):
        return {**self.to_dict(), **asdict(_ReferenceText(content.decode("utf-8"))), **evidence.to_dict()}


@dataclass(frozen=True)
class _ReferenceText:
    text: str
