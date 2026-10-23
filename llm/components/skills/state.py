"""Required Skill operational evidence; never a second Markdown definition."""

from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from typing import ClassVar

from llm.core.skills import SkillName
from llm.components.files import file_path
from .records import SkillFileEvidence


@dataclass(frozen=True, slots=True)
class SkillStateIdentity:
    VERSION: ClassVar[int] = 1
    INVALID: ClassVar[str] = "Skill operational record identity or version mismatch"

    schema_version: int
    project_id: str
    component: str

    def require_identity(self, expected):
        if (type(self.schema_version) is not int
                or any(getattr(self, item.name) != getattr(expected, item.name)
                       for item in fields(SkillStateIdentity))):
            raise ValueError(self.INVALID)

    @classmethod
    def field_names(cls):
        return frozenset(item.name for item in fields(cls))

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SkillCatalog(SkillStateIdentity):
    INVALID_FIELDS: ClassVar[str] = "Invalid Skill operational inventory"
    INVALID_RECORDS: ClassVar[str] = "Invalid Skill operational record set"
    INVALID_DIGEST: ClassVar[str] = "Invalid Skill operational digest"
    DIGEST_LENGTH: ClassVar[int] = 64
    DIGEST_ALPHABET: ClassVar[str] = "0123456789abcdef"

    records: dict[str, str]

    @classmethod
    def empty(cls, identity):
        return cls(**identity.to_dict(), records={})

    @classmethod
    def read(cls, value, *, identity):
        if not isinstance(value, dict) or value.keys() != cls.field_names():
            raise ValueError(cls.INVALID_FIELDS)
        result = cls(**deepcopy(value))
        result.require_identity(identity)
        if not isinstance(result.records, dict):
            raise ValueError(cls.INVALID_RECORDS)
        for name, digest in result.records.items():
            SkillName.validate(name)
            if (not isinstance(digest, str) or len(digest) != cls.DIGEST_LENGTH
                    or any(character not in cls.DIGEST_ALPHABET for character in digest)):
                raise ValueError(cls.INVALID_DIGEST)
        return result


@dataclass(frozen=True, slots=True)
class SkillOperationalRecord(SkillStateIdentity):
    INVALID_FIELDS: ClassVar[str] = "Invalid Skill operational record fields"
    INVALID_DIRECTORIES: ClassVar[str] = "Invalid Skill directory inventory"
    MISSING_CONTAINERS: ClassVar[str] = "Skill requires stored file and reference containers"

    id: str
    document: dict
    directories: list[str]
    files: dict
    resources: list
    lineage: dict | None
    source: dict | None

    @classmethod
    def read(cls, value, *, identity, identifier):
        if not isinstance(value, dict) or value.keys() != cls.field_names() or value[cls.id.__name__] != identifier:
            raise ValueError(cls.INVALID_FIELDS)
        result = cls(**deepcopy(value))
        result.require_identity(identity)
        if not isinstance(result.directories, list) or result.directories != sorted(set(result.directories)):
            raise ValueError(cls.INVALID_DIRECTORIES)
        for path in result.directories:
            file_path(path)
        SkillFileEvidence.from_dict(result.document)
        if not isinstance(result.files, dict) or not isinstance(result.resources, list):
            raise ValueError(cls.MISSING_CONTAINERS)
        return result

    @classmethod
    def create(cls, identity, identifier, *, document, directories, files, record):
        return cls(**identity.to_dict(), id=identifier, document=document.to_dict(),
                   directories=directories, files={path: value.to_dict() for path, value in files.items()},
                   resources=[reference.to_dict() for reference in record.resources],
                   lineage=record.lineage.to_dict() if record.lineage is not None else None,
                   source=record.source)
