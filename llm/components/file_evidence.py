"""File integrity values shared by package Owners, without storage or business policy."""

from dataclasses import asdict, dataclass, field, fields
from typing import ClassVar
import hashlib
import io

from jsonschema import Draft202012Validator

from llm.core.schema import object_schema, open_schema, SchemaKeyword, OpenSchemaKind


@dataclass(frozen=True)
class FileEvidence:
    MANIFEST_OWNER: ClassVar[str] = "component-owned file manifest"
    MANIFEST_READ_ONLY: ClassVar[bool] = True
    BUFFER_BYTES: ClassVar[int] = 1024 * 1024
    EMPTY_BYTES: ClassVar[int] = 0
    INVALID_EVIDENCE: ClassVar[str] = "Invalid file integrity evidence"
    INVALID_FIELDS: ClassVar[str] = "Invalid file integrity fields"

    sha256: str = field(metadata={"type": "string", "pattern": "^[a-f0-9]{64}$"})
    bytes: int = field(metadata={"type": "integer", "minimum": 0})

    @classmethod
    def schema(cls):
        properties = {item.name: dict(item.metadata) for item in fields(cls)}
        return object_schema(properties, required=list(properties))

    @classmethod
    def manifest_schema(cls):
        result = open_schema(cls.MANIFEST_OWNER, category=OpenSchemaKind.DATA, readOnly=cls.MANIFEST_READ_ONLY)
        result[SchemaKeyword.ADDITIONAL_PROPERTIES] = cls.schema()
        return result

    def __post_init__(self):
        if not Draft202012Validator(self.schema()).is_valid(self.to_dict()):
            raise ValueError(self.INVALID_EVIDENCE)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.INVALID_FIELDS)
        return cls(**value)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def measure(cls, stream, output=None, **extra):
        digest, size = hashlib.sha256(), cls.EMPTY_BYTES
        # I/O buffer size only; no content/retention limit.
        while block := stream.read(cls.BUFFER_BYTES):
            digest.update(block)
            size += len(block)
            if output is not None:
                output.write(block)
        return cls(digest.hexdigest(), size, **extra)

    def matches(self, content):
        actual = FileEvidence.measure(io.BytesIO(content))
        return self.sha256 == actual.sha256 and self.bytes == actual.bytes
