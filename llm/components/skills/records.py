"""Skill snapshot and Project-local relations. Never a second Skill definition."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from typing import ClassVar

from jsonschema import Draft202012Validator

from llm.core.skills import SkillDocument, SkillName
from llm.core.schema import object_schema, metadata_schema, SchemaKeyword
from llm.components.files import file_path
from llm.components.file_evidence import FileEvidence
from llm.components.data import DataComponent
from llm.components.tools.packages import ToolPaths


@dataclass(frozen=True)
class SkillFileEvidence(FileEvidence):
    mode: int = field(metadata={"type": "integer", "minimum": 0, "maximum": 0o777})


@dataclass(frozen=True, slots=True)
class SkillLineage:
    REVISION_SCHEMA: ClassVar[dict] = {"type": "string", "pattern": "^[a-f0-9]{64}$"}

    parent: str = field(metadata=SkillName.SCHEMA)
    parent_revision: str = field(metadata=REVISION_SCHEMA)

    @classmethod
    def schema(cls):
        properties = {item.name: dict(item.metadata) for item in fields(cls)}
        return object_schema(properties, required=list(properties))

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True, init=False)
class SkillResource:
    """Detached projection of a resource validated by the Skill record Owner."""

    _value: dict

    @classmethod
    def read(cls, value):
        error = next(Draft202012Validator(SkillRecord.RESOURCE_SCHEMA[SchemaKeyword.ITEMS]).iter_errors(value), None)
        if error:
            raise ValueError(SkillRecord.INVALID_RECORD.format(message=error.message))
        result = object.__new__(cls)
        object.__setattr__(result, cls._value.__name__, deepcopy(value))
        return result

    @property
    def component(self):
        return self._value.get(SkillRecord.COMPONENT)

    @property
    def identifier(self):
        return self._value.get(SkillRecord.IDENTIFIER)

    @property
    def revision(self):
        return self._value.get(SkillRecord.REVISION)

    @property
    def path(self):
        return self._value.get(SkillRecord.PATH)

    @property
    def external(self):
        return SkillRecord.COMPONENT in self._value

    @property
    def tool_reference(self):
        return self.component == SkillRecord.TOOLS

    @property
    def data_reference(self):
        return self.component == SkillRecord.DATA

    def to_dict(self):
        return deepcopy(self._value)


@dataclass(frozen=True, slots=True, init=False)
class SkillRecord:
    CAPABILITY = "skills"
    MARKDOWN = "markdown"
    FILES = "files"
    RESOURCES = "resources"
    LINEAGE = "lineage"
    SOURCE = "source"
    PARENT = SkillLineage.parent.__name__
    PARENT_REVISION = SkillLineage.parent_revision.__name__
    COMPONENT = "component"
    IDENTIFIER = "identifier"
    REVISION = "revision"
    PATH = "path"
    URI = "uri"
    DATA = DataComponent.name
    TOOLS = ToolPaths.COMPONENT
    TEXT_ENCODING = SkillDocument.TEXT_ENCODING
    DIGEST_SCHEMA = SkillLineage.REVISION_SCHEMA
    TARGET_SCHEMA = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"}
    FILE_SCHEMA = SkillFileEvidence.manifest_schema()
    RESOURCE_SCHEMA = {"type": "array", "items": {"oneOf": [object_schema({
        URI: {"type": "string", "minLength": 1}, "description": {"type": "string"},
        "metadata": metadata_schema()}, required=[URI]), object_schema({
        PATH: {"type": "string", "minLength": 1}, "description": {"type": "string"},
        "metadata": metadata_schema()}, required=[PATH]), object_schema({
        COMPONENT: {"const": DATA}, IDENTIFIER: TARGET_SCHEMA, REVISION: DIGEST_SCHEMA,
        PATH: {"type": "string", "minLength": 1}, "description": {"type": "string"},
        "metadata": metadata_schema()}, required=[COMPONENT, IDENTIFIER, REVISION, PATH]), object_schema({
        COMPONENT: {"const": TOOLS}, IDENTIFIER: TARGET_SCHEMA, REVISION: DIGEST_SCHEMA,
        "description": {"type": "string"}, "metadata": metadata_schema()},
        required=[COMPONENT, IDENTIFIER, REVISION])]}}
    LINEAGE_SCHEMA = SkillLineage.schema()
    SCHEMA = object_schema({MARKDOWN: {"type": "string", "description": "Complete standard SKILL.md, including YAML name and description."},
                           FILES: FILE_SCHEMA, RESOURCES: RESOURCE_SCHEMA, LINEAGE: LINEAGE_SCHEMA,
                           SOURCE: metadata_schema()}, required=[MARKDOWN])
    EDITABLE = frozenset((MARKDOWN, RESOURCES))
    INVALID_RECORD = "Invalid Skill record: {message}"
    OWN_PARENT = "Skill cannot be its own parent"
    MARKDOWN_EDIT = "Use Skill markdown editing for SKILL.md"
    UNAVAILABLE_FILE = "Skill file is unavailable in this Run"
    INVALID_RESOURCE_INDEX = "Invalid Skill resource index"
    FIRST_RESOURCE_INDEX = 0

    _value: dict
    _document: SkillDocument

    @classmethod
    def validate(cls, identifier, value):
        error = next(Draft202012Validator(cls.SCHEMA).iter_errors(value), None)
        if error:
            raise ValueError(cls.INVALID_RECORD.format(message=error.message))
        document = SkillDocument.parse(value[cls.MARKDOWN], identifier=identifier)
        for path in value.get(cls.FILES, {}):
            cls.resource_path(path)
        if cls.LINEAGE in value:
            parent = SkillName.validate(value[cls.LINEAGE][cls.PARENT])
            if parent == document.name:
                raise ValueError(cls.OWN_PARENT)
        for resource in value.get(cls.RESOURCES, []):
            if cls.PATH in resource:
                file_path(resource[cls.PATH])
        return document

    @classmethod
    def document(cls, value):
        return SkillDocument.parse(value[cls.MARKDOWN])

    @classmethod
    def read(cls, value, *, identifier=None):
        value = deepcopy(value)
        document = cls.validate(identifier, value)
        result = object.__new__(cls)
        object.__setattr__(result, cls._value.__name__, value)
        object.__setattr__(result, cls._document.__name__, document)
        return result

    @property
    def markdown(self):
        return self._document.text

    @property
    def parsed_document(self):
        return deepcopy(self._document)

    @property
    def resources(self):
        return tuple(SkillResource.read(value) for value in self._value.get(self.RESOURCES, []))

    @property
    def lineage(self):
        return SkillLineage(**deepcopy(self._value[self.LINEAGE])) if self.LINEAGE in self._value else None

    @property
    def files(self):
        return {path: SkillFileEvidence.from_dict(value) for path, value in self._value.get(self.FILES, {}).items()}

    @property
    def source(self):
        return deepcopy(self._value.get(self.SOURCE))

    @property
    def has_files(self):
        return self.FILES in self._value

    def resource(self, index):
        resources = self.resources
        if type(index) is not int or index < self.FIRST_RESOURCE_INDEX or index >= len(resources):
            raise ValueError(self.INVALID_RESOURCE_INDEX)
        return resources[index]

    def same_files(self, other):
        """Omitted and explicit empty manifests retain their distinct edit meaning."""
        return self._value.get(self.FILES) == other._value.get(self.FILES)

    def same_authorship(self, other):
        return all(self._value.get(key) == other._value.get(key) for key in (self.LINEAGE, self.SOURCE))

    def with_lineage(self, parent, parent_revision):
        return {**self.to_dict(), self.LINEAGE: SkillLineage(parent, parent_revision).to_dict()}

    @classmethod
    def markdown_change(cls, markdown):
        return {cls.MARKDOWN: markdown}

    @classmethod
    def from_storage(cls, markdown, *, files, resources, lineage, source, identifier):
        value = cls.markdown_change(markdown)
        if files:
            value[cls.FILES] = deepcopy(files)
        if resources:
            value[cls.RESOURCES] = deepcopy(resources)
        for key, selected in ((cls.LINEAGE, lineage), (cls.SOURCE, source)):
            if selected is not None:
                value[key] = deepcopy(selected)
        return cls.read(value, identifier=identifier)

    def with_file(self, path, evidence):
        value = self.to_dict()
        value.setdefault(self.FILES, {})[path] = evidence.to_dict()
        return value

    def without_file(self, path):
        value = self.to_dict()
        del value[self.FILES][path]
        return value

    @classmethod
    def without_files(cls, value):
        value = deepcopy(value)
        value.pop(cls.FILES, None)
        return value

    @classmethod
    def with_files(cls, value, files):
        return {**deepcopy(value), cls.FILES: {path: evidence.to_dict() for path, evidence in files.items()}}

    def to_dict(self):
        return deepcopy(self._value)

    @classmethod
    def resource_path(cls, path):
        path = file_path(path)
        if path == SkillDocument.FILENAME:
            raise ValueError(cls.MARKDOWN_EDIT)
        return path

    @classmethod
    def instructions(cls, value):
        return cls.document(value).body

    @classmethod
    def renamed(cls, value, identifier):
        """Explicit fork authoring; only the name changes in the proposed document."""
        return {**deepcopy(value), cls.MARKDOWN: cls.document(value).renamed(identifier).text}


@dataclass(frozen=True, slots=True, init=False)
class SkillSnapshot:
    """Validated detached runtime view. The standard Markdown remains the only definition."""

    id: str
    revision: str
    _record: SkillRecord

    def __init__(self, identifier, value):
        from llm.services.infrastructure.storage import revision_token
        record = SkillRecord.read(value, identifier=identifier)
        object.__setattr__(self, type(self)._record.__name__, record)
        object.__setattr__(self, type(self).id.__name__, identifier)
        object.__setattr__(self, type(self).revision.__name__, revision_token(record.to_dict()))

    @property
    def name(self):
        return self._record.parsed_document.name

    @property
    def description(self):
        return self._record.parsed_document.description

    def to_dict(self):
        return self._record.to_dict()

    def file(self, path):
        path = SkillRecord.resource_path(path)
        files = self._record.files
        if path not in files:
            raise ValueError(SkillRecord.UNAVAILABLE_FILE)
        return files[path]
