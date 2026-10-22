"""Skill snapshot and Project-local relations. Never a second Skill definition."""

from copy import deepcopy
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator

from llm.core.skills import SkillDocument, SkillName
from llm.core.schema import object_schema, metadata_schema
from llm.components.files import file_path
from llm.components.file_evidence import FileEvidence


@dataclass(frozen=True)
class SkillFileEvidence(FileEvidence):
    mode: int = field(metadata={"type": "integer", "minimum": 0, "maximum": 0o777})


class SkillRecord:
    MARKDOWN = "markdown"
    FILES = "files"
    RESOURCES = "resources"
    LINEAGE = "lineage"
    SOURCE = "source"
    PARENT = "parent"
    PARENT_REVISION = "parent_revision"
    COMPONENT = "component"
    IDENTIFIER = "identifier"
    REVISION = "revision"
    PATH = "path"
    URI = "uri"
    DATA = "data"
    TOOLS = "tools"
    DIGEST_SCHEMA = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
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
    LINEAGE_SCHEMA = object_schema({PARENT: SkillName.SCHEMA, PARENT_REVISION: DIGEST_SCHEMA},
                                   required=[PARENT, PARENT_REVISION])
    SCHEMA = object_schema({MARKDOWN: {"type": "string", "description": "Complete standard SKILL.md, including YAML name and description."},
                           FILES: FILE_SCHEMA, RESOURCES: RESOURCE_SCHEMA, LINEAGE: LINEAGE_SCHEMA,
                           SOURCE: metadata_schema()}, required=[MARKDOWN])
    EDITABLE = frozenset((MARKDOWN, RESOURCES))

    @classmethod
    def validate(cls, identifier, value):
        error = next(Draft202012Validator(cls.SCHEMA).iter_errors(value), None)
        if error:
            raise ValueError(f"Invalid Skill record: {error.message}")
        document = SkillDocument.parse(value[cls.MARKDOWN], identifier=identifier)
        for path in value.get(cls.FILES, {}):
            cls.resource_path(path)
        if cls.LINEAGE in value:
            parent = SkillName.validate(value[cls.LINEAGE][cls.PARENT])
            if parent == document.name:
                raise ValueError("Skill cannot be its own parent")
        for resource in value.get(cls.RESOURCES, []):
            if cls.PATH in resource:
                file_path(resource[cls.PATH])
        return document

    @classmethod
    def document(cls, value):
        return SkillDocument.parse(value[cls.MARKDOWN])

    @classmethod
    def resource_path(cls, path):
        path = file_path(path)
        if path == SkillDocument.FILENAME:
            raise ValueError("Use Skill markdown editing for SKILL.md")
        return path

    @classmethod
    def instructions(cls, value):
        return cls.document(value).body

    @classmethod
    def renamed(cls, value, identifier):
        """Explicit fork authoring; only the name changes in the proposed document."""
        document = cls.document(value)
        fields = deepcopy(document.frontmatter)
        fields[SkillDocument.NAME] = identifier
        name = fields.pop(SkillDocument.NAME)
        description = fields.pop(SkillDocument.DESCRIPTION)
        return {**deepcopy(value), cls.MARKDOWN: SkillDocument.compose(
            name=name, description=description, body=document.body, **fields).text}


@dataclass(frozen=True, init=False)
class SkillSnapshot:
    """Validated detached runtime view. The standard Markdown remains the only definition."""

    id: str
    revision: str
    _value: dict
    _document: SkillDocument

    def __init__(self, identifier, value):
        from llm.services.infrastructure.storage import revision_token
        value = deepcopy(value)
        object.__setattr__(self, "_document", SkillRecord.validate(identifier, value))
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "id", identifier)
        object.__setattr__(self, "revision", revision_token(value))

    @property
    def name(self):
        return self._document.name

    @property
    def description(self):
        return self._document.frontmatter[SkillDocument.DESCRIPTION]

    def to_dict(self):
        return deepcopy(self._value)

    def file(self, path):
        path = SkillRecord.resource_path(path)
        files = self._value.get(SkillRecord.FILES, {})
        if path not in files:
            raise ValueError("Skill file is unavailable in this Run")
        return SkillFileEvidence.from_dict(files[path])
