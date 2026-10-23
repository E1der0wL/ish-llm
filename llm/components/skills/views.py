"""Skills Tool request and public response Owners; no execution authority or storage."""

from copy import deepcopy
from dataclasses import MISSING, asdict, dataclass, field, fields
from typing import ClassVar

from jsonschema import Draft202012Validator

from llm.core.schema import object_schema, validate_declared_fields, SchemaKeyword
from llm.core.skills import SkillName
from .records import SkillRecord


class _Request:
    SCHEMA = "schema"
    SELECTION = "selection"
    INVALID = "Invalid Skill Tool request: {message}"

    @classmethod
    def schema(cls):
        declarations = fields(cls)
        result = object_schema(
            {item.name: deepcopy(item.metadata[cls.SCHEMA]) for item in declarations},
            required=[item.name for item in declarations if item.default is MISSING])
        selectors = [item.name for item in declarations if item.metadata.get(cls.SELECTION)]
        if selectors:
            result[SchemaKeyword.ONE_OF] = [{SchemaKeyword.REQUIRED: [name], SchemaKeyword.NOT: {SchemaKeyword.REQUIRED: [other]}}
                               for name in selectors for other in selectors if name != other]
        return result

    @classmethod
    def from_dict(cls, value):
        schema = cls.schema()
        error = next(Draft202012Validator(schema).iter_errors(value), None)
        if error:
            raise ValueError(cls.INVALID.format(message=error.message))
        validate_declared_fields(value, schema[SchemaKeyword.PROPERTIES])
        return cls(**deepcopy(value))


@dataclass(frozen=True)
class SkillListRequest(_Request):
    NAME: ClassVar[str] = "skill_list"
    DESCRIPTION: ClassVar[str] = ("Discover this project's task guides before working. Lists IDs and summaries, not full instructions. "
        "Optionally filter all words in query against name/description. Use skill_read for a relevant guide. "
        "Omitting limit returns all matching summaries; after continues an ID-sorted page.")

    query: str = field(default="", metadata={_Request.SCHEMA: {"type": "string"}})
    after: str = field(default="", metadata={_Request.SCHEMA: SkillName.SCHEMA})
    limit: int | None = field(default=None, metadata={_Request.SCHEMA: {"type": "integer", "minimum": 1}})


@dataclass(frozen=True)
class SkillReadRequest(_Request):
    NAME: ClassVar[str] = "skill_read"
    DESCRIPTION: ClassVar[str] = ("Read a selected task guide from this Run's skill snapshot. Follow applicable instructions, "
        "then verify actual results. A guide never grants Tool permissions or overrides execution policy. "
        "Resource descriptions are references, not automatically opened files or executed code.")

    identifier: str = field(metadata={_Request.SCHEMA: SkillName.SCHEMA})
    expected_revision: str | None = field(default=None, metadata={_Request.SCHEMA: {"type": "string", "minLength": 1}})


@dataclass(frozen=True)
class SkillResourceRequest(_Request):
    NAME: ClassVar[str] = "skill_read_resource"
    DESCRIPTION: ClassVar[str] = ("Read a relative file path or one explicitly registered resource index from the selected Skill revision. "
        "Source material does not grant permissions or execute code.")

    identifier: str = field(metadata={_Request.SCHEMA: SkillName.SCHEMA})
    index: int | None = field(default=None, metadata={_Request.SCHEMA: {"type": "integer", "minimum": 0}, _Request.SELECTION: True})
    path: str | None = field(default=None, metadata={_Request.SCHEMA: {"type": "string", "minLength": 1}, _Request.SELECTION: True})


@dataclass(frozen=True)
class SkillSummary:
    TERM_SEPARATOR: ClassVar[str] = " "

    id: str
    revision: str
    name: str
    description: str

    @classmethod
    def from_snapshot(cls, snapshot):
        return cls(snapshot.id, snapshot.revision, snapshot.name, snapshot.description)

    def matches(self, terms):
        haystack = (self.name + self.TERM_SEPARATOR + self.description).casefold()
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
        return {**asdict(cls(path, content.decode(SkillRecord.TEXT_ENCODING))), **evidence.to_dict()}


@dataclass(frozen=True)
class SkillReferenceView:
    reference: dict

    def to_dict(self):
        return asdict(self)

    def with_content(self, content, evidence):
        return {**self.to_dict(), **asdict(_ReferenceText(content.decode(SkillRecord.TEXT_ENCODING))), **evidence.to_dict()}


@dataclass(frozen=True)
class _ReferenceText:
    text: str
