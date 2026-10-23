"""Python Tool package text and manifest records; filesystem effects stay in the repository."""

from copy import deepcopy
from dataclasses import asdict, dataclass, fields, replace
import hashlib
import re

from packaging.requirements import Requirement, InvalidRequirement


class ToolRequirements:
    COMMENT_PREFIX = "#"
    OPTION_PREFIX = "-"
    OPTIONS_FORBIDDEN = "Tool requirements only support PEP 508 requirement lines"
    INVALID = "Invalid Tool requirement"
    URL_FORBIDDEN = "Tool requirements do not support URL/path installations"

    @classmethod
    def parse(cls, text):
        result = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(cls.COMMENT_PREFIX):
                continue
            if line.startswith(cls.OPTION_PREFIX):
                raise ValueError(cls.OPTIONS_FORBIDDEN)
            try:
                item = Requirement(line)
            except InvalidRequirement as error:
                raise ValueError(cls.INVALID) from error
            if item.url:
                raise ValueError(cls.URL_FORBIDDEN)
            result.append(item)
        return result


@dataclass(frozen=True, slots=True)
class ToolPackageSource:
    ENCODING = "utf-8"
    ABSENT_REQUIREMENTS = None
    EMPTY_REQUIREMENTS = ""
    REQUIRED_FIELDS = "Tool package requires source and optional requirements"
    INVALID_TEXT = "Tool package contents must be UTF-8 text with nonempty source"

    source: str
    requirements: str | None = ABSENT_REQUIREMENTS

    def __post_init__(self):
        if (not isinstance(self.source, str) or not self.source.strip()
                or self.requirements is not self.ABSENT_REQUIREMENTS and not isinstance(self.requirements, str)):
            raise ValueError(self.INVALID_TEXT)
        self.source.encode(self.ENCODING)
        if self.has_requirements:
            self.requirements.encode(self.ENCODING)
        ToolRequirements.parse(self.requirements_text)

    @classmethod
    def read(cls, value):
        if (not isinstance(value, dict) or value.keys() - {f.name for f in fields(cls)}
                or cls.source.__name__ not in value):
            raise ValueError(cls.REQUIRED_FIELDS)
        if any(not isinstance(item, str) for item in value.values()):
            raise ValueError(cls.INVALID_TEXT)
        return cls(**value)

    @property
    def has_requirements(self):
        return self.requirements is not self.ABSENT_REQUIREMENTS

    @property
    def requirements_text(self):
        return self.requirements if self.has_requirements else self.EMPTY_REQUIREMENTS

    def to_dict(self):
        value = asdict(self)
        if not self.has_requirements:
            del value[type(self).requirements.__name__]
        return value

    def fingerprints(self):
        return ToolPackageDigests.for_text(self.source, self.requirements)


@dataclass(frozen=True, slots=True)
class ToolPackageDigests:
    PATTERN = r"[a-f0-9]{64}"
    ABSENT_REQUIREMENTS = None
    INVALID = "Invalid Tool package content declaration"

    source: str
    requirements: str | None

    def __post_init__(self):
        if (not isinstance(self.source, str) or not re.fullmatch(self.PATTERN, self.source)
                or self.requirements is not self.ABSENT_REQUIREMENTS and
                (not isinstance(self.requirements, str) or not re.fullmatch(self.PATTERN, self.requirements))):
            raise ValueError(self.INVALID)

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or value.keys() != {f.name for f in fields(cls)}:
            raise ValueError(cls.INVALID)
        return cls(**value)

    @staticmethod
    def digest(text):
        return hashlib.sha256(text.encode(ToolPackageSource.ENCODING)).hexdigest()

    @classmethod
    def fingerprint_record(cls, value):
        """Checksum projection only: valid hashes do not prove a valid definition."""
        source = cls.digest(value[ToolPackageSource.source.__name__])
        requirement_key = ToolPackageSource.requirements.__name__
        requirements = cls.digest(value[requirement_key]) if requirement_key in value else cls.ABSENT_REQUIREMENTS
        return cls(source, requirements)

    @classmethod
    def for_text(cls, source, requirements):
        return cls(cls.digest(source), cls.digest(requirements)
            if requirements is not ToolPackageSource.ABSENT_REQUIREMENTS else cls.ABSENT_REQUIREMENTS)

    @property
    def has_requirements(self):
        return self.requirements is not self.ABSENT_REQUIREMENTS

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ToolPackageManifest:
    VERSION = 1
    INVALID = "Invalid Tool package manifest"

    schema_version: int
    project_id: str
    component: str
    packages: dict[str, ToolPackageDigests]

    @classmethod
    def create(cls, project_id, component):
        return cls(cls.VERSION, project_id, component, {})

    @classmethod
    def read(cls, value, *, project_id, component):
        if not isinstance(value, dict) or value.keys() != {f.name for f in fields(cls)}:
            raise ValueError(cls.INVALID)
        header = cls(**value)
        if (type(header.schema_version) is not int or header.schema_version != cls.VERSION
                or header.project_id != project_id or header.component != component
                or not isinstance(header.packages, dict)):
            raise ValueError(cls.INVALID)
        return replace(header, packages={name: ToolPackageDigests.read(entry) for name, entry in header.packages.items()})

    def to_dict(self):
        return asdict(self)

    def with_package(self, name, source):
        packages = deepcopy(self.packages)
        packages[name] = source.fingerprints()
        return replace(self, packages=packages)

    def without_package(self, name):
        packages = deepcopy(self.packages)
        del packages[name]
        return replace(self, packages=packages)
