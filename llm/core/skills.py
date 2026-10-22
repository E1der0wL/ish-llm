"""Agent Skills document contract. No storage, execution or authority policy."""

from dataclasses import dataclass
from copy import deepcopy
from typing import ClassVar

import yaml


class SkillName:
    MAX_LENGTH = 64
    SCHEMA = {"type": "string", "minLength": 1, "maxLength": MAX_LENGTH,
              "pattern": r"^[^\W_]+(?:-[^\W_]+)*$"}

    @classmethod
    def validate(cls, value):
        if (not isinstance(value, str) or not 1 <= len(value) <= cls.MAX_LENGTH
                or value != value.lower() or value.startswith("-") or value.endswith("-")
                or "--" in value or not all(c.isalnum() or c == "-" for c in value)):
            raise ValueError("Invalid Agent Skill name")
        return value


class _FrontmatterLoader(yaml.SafeLoader):
    """Reject ambiguous YAML instead of silently overwriting a supplied field."""

    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in mapping:
                raise ValueError("Skill frontmatter requires unique string keys")
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

    text: str
    frontmatter: dict
    body: str

    @classmethod
    def parse(cls, text, *, identifier=None):
        if not isinstance(text, str):
            raise ValueError("Skill markdown must be UTF-8 text")
        text.encode("utf-8")
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].rstrip("\r\n") != "---":
            raise ValueError("SKILL.md requires YAML frontmatter")
        end = next((i for i in range(1, len(lines)) if lines[i].rstrip("\r\n") == "---"), None)
        if end is None:
            raise ValueError("Skill frontmatter closing delimiter is missing")
        try:
            fields = yaml.load("".join(lines[1:end]), Loader=_FrontmatterLoader)
        except yaml.YAMLError as error:
            raise ValueError("Invalid Skill frontmatter") from error
        cls.validate_frontmatter(fields, identifier=identifier)
        return cls(text, deepcopy(fields), "".join(lines[end + 1:]))

    @classmethod
    def validate_frontmatter(cls, value, *, identifier=None):
        if not isinstance(value, dict) or not cls.REQUIRED <= value.keys() or value.keys() - cls.FIELDS:
            raise ValueError("Skill frontmatter requires name/description and only standard fields")
        name = SkillName.validate(value[cls.NAME])
        if identifier is not None and name != SkillName.validate(identifier):
            raise ValueError("Skill name must match its directory identifier")
        for key, maximum in ((cls.DESCRIPTION, cls.DESCRIPTION_LIMIT),
                             (cls.COMPATIBILITY, cls.COMPATIBILITY_LIMIT)):
            if key in value and (not isinstance(value[key], str) or not value[key].strip()
                                 or len(value[key]) > maximum):
                raise ValueError(f"Invalid Skill {key}")
        for key in (cls.LICENSE, cls.ALLOWED_TOOLS):
            if key in value and not isinstance(value[key], str):
                raise ValueError(f"Skill {key} must be text")
        if cls.METADATA in value:
            metadata = value[cls.METADATA]
            if not isinstance(metadata, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                     for k, v in metadata.items()):
                raise ValueError("Skill metadata must map strings to strings")

    @property
    def name(self):
        return self.frontmatter[self.NAME]

    def summary(self):
        return {key: self.frontmatter[key] for key in (self.NAME, self.DESCRIPTION)}

    @classmethod
    def compose(cls, *, name, description, body, **optional):
        """Author an explicitly supplied standard document; never infer missing fields."""
        fields = {cls.NAME: name, cls.DESCRIPTION: description, **optional}
        cls.validate_frontmatter(fields)
        if not isinstance(body, str):
            raise ValueError("Skill body must be text")
        return cls.parse("---\n" + yaml.safe_dump(fields, allow_unicode=True, sort_keys=False) + "---\n" + body)
