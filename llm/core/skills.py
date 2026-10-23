"""Agent Skills document contract. No storage, execution or authority policy."""

from dataclasses import dataclass
from copy import deepcopy
from typing import ClassVar

import yaml


class SkillName:
    MIN_LENGTH = 1
    MAX_LENGTH = 64
    SEPARATOR = "-"
    REPEATED_SEPARATOR = SEPARATOR * 2
    INVALID = "Invalid Agent Skill name"
    SCHEMA = {"type": "string", "minLength": MIN_LENGTH, "maxLength": MAX_LENGTH,
              "pattern": r"^[^\W_]+(?:-[^\W_]+)*$"}

    @classmethod
    def validate(cls, value):
        if (not isinstance(value, str) or not cls.MIN_LENGTH <= len(value) <= cls.MAX_LENGTH
                or value != value.lower() or value.startswith(cls.SEPARATOR) or value.endswith(cls.SEPARATOR)
                or cls.REPEATED_SEPARATOR in value or not all(c.isalnum() or c == cls.SEPARATOR for c in value)):
            raise ValueError(cls.INVALID)
        return value


class _FrontmatterLoader(yaml.SafeLoader):
    """Reject ambiguous YAML instead of silently overwriting a supplied field."""

    DEFAULT_DEEP = False
    INVALID_KEYS = "Skill frontmatter requires unique string keys"

    def construct_mapping(self, node, deep=DEFAULT_DEEP):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in mapping:
                raise ValueError(self.INVALID_KEYS)
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


@dataclass(frozen=True)
class SkillDocument:
    FILENAME: ClassVar[str] = "SKILL.md"
    NAME: ClassVar[str] = "name"
    DESCRIPTION: ClassVar[str] = "description"
    LICENSE: ClassVar[str] = "license"
    COMPATIBILITY: ClassVar[str] = "compatibility"
    METADATA: ClassVar[str] = "metadata"
    ALLOWED_TOOLS: ClassVar[str] = "allowed-tools"
    DESCRIPTION_LIMIT: ClassVar[int] = 1024
    COMPATIBILITY_LIMIT: ClassVar[int] = 500
    FIELDS: ClassVar[frozenset] = frozenset((NAME, DESCRIPTION, LICENSE, COMPATIBILITY, METADATA, ALLOWED_TOOLS))
    REQUIRED: ClassVar[frozenset] = frozenset((NAME, DESCRIPTION))
    TEXT_ENCODING: ClassVar[str] = "utf-8"
    DELIMITER: ClassVar[str] = "---"
    LINE_ENDINGS: ClassVar[str] = "\r\n"
    LINE_BREAK: ClassVar[str] = "\n"
    PRESERVE_LINE_ENDINGS: ClassVar[bool] = True
    ALLOW_UNICODE: ClassVar[bool] = True
    SORT_KEYS: ClassVar[bool] = False
    INVALID_TEXT: ClassVar[str] = "Skill markdown must be UTF-8 text"
    REQUIRED_FRONTMATTER: ClassVar[str] = "SKILL.md requires YAML frontmatter"
    MISSING_DELIMITER: ClassVar[str] = "Skill frontmatter closing delimiter is missing"
    INVALID_FRONTMATTER: ClassVar[str] = "Invalid Skill frontmatter"
    INVALID_FIELDS: ClassVar[str] = "Skill frontmatter requires name/description and only standard fields"
    WRONG_IDENTIFIER: ClassVar[str] = "Skill name must match its directory identifier"
    INVALID_FIELD: ClassVar[str] = "Invalid Skill {key}"
    NON_TEXT_FIELD: ClassVar[str] = "Skill {key} must be text"
    INVALID_METADATA: ClassVar[str] = "Skill metadata must map strings to strings"
    INVALID_BODY: ClassVar[str] = "Skill body must be text"

    text: str
    frontmatter: dict
    body: str

    @classmethod
    def parse(cls, text, *, identifier=None):
        if not isinstance(text, str):
            raise ValueError(cls.INVALID_TEXT)
        text.encode(cls.TEXT_ENCODING)
        lines = text.splitlines(keepends=cls.PRESERVE_LINE_ENDINGS)
        if not lines or lines[0].rstrip(cls.LINE_ENDINGS) != cls.DELIMITER:
            raise ValueError(cls.REQUIRED_FRONTMATTER)
        end = next((i for i in range(1, len(lines)) if lines[i].rstrip(cls.LINE_ENDINGS) == cls.DELIMITER), None)
        if end is None:
            raise ValueError(cls.MISSING_DELIMITER)
        try:
            fields = yaml.load("".join(lines[1:end]), Loader=_FrontmatterLoader)
        except yaml.YAMLError as error:
            raise ValueError(cls.INVALID_FRONTMATTER) from error
        cls.validate_frontmatter(fields, identifier=identifier)
        return cls(text, deepcopy(fields), "".join(lines[end + 1:]))

    @classmethod
    def validate_frontmatter(cls, value, *, identifier=None):
        if not isinstance(value, dict) or not cls.REQUIRED <= value.keys() or value.keys() - cls.FIELDS:
            raise ValueError(cls.INVALID_FIELDS)
        name = SkillName.validate(value[cls.NAME])
        if identifier is not None and name != SkillName.validate(identifier):
            raise ValueError(cls.WRONG_IDENTIFIER)
        for key, maximum in ((cls.DESCRIPTION, cls.DESCRIPTION_LIMIT),
                             (cls.COMPATIBILITY, cls.COMPATIBILITY_LIMIT)):
            if key in value and (not isinstance(value[key], str) or not value[key].strip()
                                 or len(value[key]) > maximum):
                raise ValueError(cls.INVALID_FIELD.format(key=key))
        for key in (cls.LICENSE, cls.ALLOWED_TOOLS):
            if key in value and not isinstance(value[key], str):
                raise ValueError(cls.NON_TEXT_FIELD.format(key=key))
        if cls.METADATA in value:
            metadata = value[cls.METADATA]
            if not isinstance(metadata, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                     for k, v in metadata.items()):
                raise ValueError(cls.INVALID_METADATA)

    @property
    def name(self):
        return self.frontmatter[self.NAME]

    @property
    def description(self):
        return self.frontmatter[self.DESCRIPTION]

    def summary(self):
        return {key: self.frontmatter[key] for key in (self.NAME, self.DESCRIPTION)}

    def renamed(self, identifier):
        """Explicit fork authoring; preserve every optional field and the body."""
        fields = deepcopy(self.frontmatter)
        fields[self.NAME] = identifier
        name = fields.pop(self.NAME)
        description = fields.pop(self.DESCRIPTION)
        return self.compose(name=name, description=description, body=self.body, **fields)

    @classmethod
    def compose(cls, *, name, description, body, **optional):
        """Author an explicitly supplied standard document; never infer missing fields."""
        fields = {cls.NAME: name, cls.DESCRIPTION: description, **optional}
        cls.validate_frontmatter(fields)
        if not isinstance(body, str):
            raise ValueError(cls.INVALID_BODY)
        boundary = cls.DELIMITER + cls.LINE_BREAK
        return cls.parse(boundary + yaml.safe_dump(fields, allow_unicode=cls.ALLOW_UNICODE,
                                                  sort_keys=cls.SORT_KEYS) + boundary + body)
