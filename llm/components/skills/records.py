"""Skill snapshot and Project-local relations. Never a second Skill definition."""

from copy import deepcopy

from jsonschema import Draft202012Validator

from llm.core.skills import SkillDocument, SkillName
from llm.core.schema import object_schema, metadata_schema
from llm.components.files import FILE_MANIFEST, file_path


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
    FILE_MODE = "mode"
    DIGEST_SCHEMA = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
    TARGET_SCHEMA = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"}
    FILE_SCHEMA = deepcopy(FILE_MANIFEST)
    FILE_SCHEMA["additionalProperties"]["properties"][FILE_MODE] = {"type": "integer", "minimum": 0, "maximum": 0o777}
    FILE_SCHEMA["additionalProperties"]["required"].append(FILE_MODE)
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
