"""Vision image identity, immutable record, tombstone and provenance projections."""

from copy import deepcopy
import hashlib
import re
from llm.core.models import now
from .image_contracts import ImageInfo, ImageEncoding, ImageOperation


class VisionImageIdentity:
    COMPONENT = "vision"
    ID = "id"
    IMAGE_ID = "image_id"
    DIGEST_PATTERN = "[0-9a-f]{64}"
    INVALID_DIGEST = "Invalid image SHA-256"
    ASSET_MISMATCH = "Image asset integrity mismatch"

    @classmethod
    def matches_digest(cls, value):
        return re.fullmatch(cls.DIGEST_PATTERN, value)

    @classmethod
    def valid_digest(cls, value):
        return isinstance(value, str) and cls.matches_digest(value)


class PreparedImage:
    """Required preparation facts without dropping extra input before record validation."""

    INVALID = "Prepared image integrity mismatch"

    def __init__(self, value):
        self._value = value

    @property
    def sha256(self):
        return self._value[ImageInfo.sha256.__name__]

    @property
    def bytes(self):
        return self._value[ImageInfo.bytes.__name__]

    def verify(self, raw):
        if hashlib.sha256(raw).hexdigest() != self.sha256 or len(raw) != self.bytes:
            raise ValueError(self.INVALID)


class VisionImageRecord:
    FORMAT_VERSION = 1
    MINIMUM_SIZE = 1
    ABSENT = None
    LIVE = False
    DELETED = True
    UNKNOWN_FIELDS = "vision/{identifier}: unsupported fields {fields}"
    INVALID = "Invalid Vision image record"
    IMMUTABLE = "Image content/provenance is immutable; edit title/metadata or register a new image"
    ASSET_MISMATCH = VisionImageIdentity.ASSET_MISMATCH

    class Field:
        ID = VisionImageIdentity.ID
        FORMAT_VERSION = "format_version"
        MIME_TYPE = ImageInfo.mime_type.__name__
        WIDTH = ImageInfo.width.__name__
        HEIGHT = ImageInfo.height.__name__
        BYTES = ImageInfo.bytes.__name__
        SHA256 = ImageInfo.sha256.__name__
        TITLE = "title"
        METADATA = "metadata"
        CREATED_AT = "created_at"
        DELETED = "deleted"
        DELETED_AT = "deleted_at"
        ORIGIN = "origin"

    FIELDS = {
        Field.ID,
        Field.FORMAT_VERSION,
        Field.MIME_TYPE,
        Field.WIDTH,
        Field.HEIGHT,
        Field.BYTES,
        Field.SHA256,
        Field.TITLE,
        Field.METADATA,
        Field.CREATED_AT,
        Field.DELETED,
        Field.DELETED_AT,
        Field.ORIGIN,
    }
    SIZE_FIELDS = (Field.WIDTH, Field.HEIGHT, Field.BYTES)
    EDITABLE = {Field.TITLE, Field.METADATA}
    REFERENCE_FIELDS = (Field.ID, Field.SHA256, Field.MIME_TYPE, Field.WIDTH, Field.HEIGHT, Field.ORIGIN)

    def __init__(self, value):
        # The selected Component's validate_record is the persisted record Reader.
        self._value = value

    @classmethod
    def validate(cls, identifier, data, format_version):
        unknown = set(data) - cls.FIELDS
        if unknown:
            raise ValueError(cls.UNKNOWN_FIELDS.format(identifier=identifier, fields=sorted(unknown)))
        if (
            type(data.get(cls.Field.FORMAT_VERSION)) is not int
            or data[cls.Field.FORMAT_VERSION] != format_version
            or data.get(cls.Field.ID) != identifier
            or data.get(cls.Field.MIME_TYPE) not in ImageEncoding.MIME_TYPES
            or any(type(data.get(key)) is not int or data[key] < cls.MINIMUM_SIZE for key in cls.SIZE_FIELDS)
            or not isinstance(data.get(cls.Field.SHA256), str)
            or not VisionImageIdentity.matches_digest(data[cls.Field.SHA256])
            or not isinstance(data.get(cls.Field.TITLE), str)
            or not isinstance(data.get(cls.Field.METADATA), dict)
            or type(data.get(cls.Field.DELETED)) is not bool
        ):
            raise ValueError(cls.INVALID)

    @classmethod
    def create(cls, identifier, format_version, info, title, metadata, origin):
        record = {
            cls.Field.ID: identifier,
            cls.Field.FORMAT_VERSION: format_version,
            **info,
            cls.Field.TITLE: title,
            cls.Field.METADATA: deepcopy(metadata),
            cls.Field.CREATED_AT: now(),
            cls.Field.DELETED: cls.LIVE,
        }
        if origin is not cls.ABSENT:
            record[cls.Field.ORIGIN] = deepcopy(origin)
        return record

    @property
    def identifier(self):
        return self._value[self.Field.ID]

    @property
    def deleted(self):
        return self._value[self.Field.DELETED]

    @property
    def sha256(self):
        return self._value[self.Field.SHA256]

    @property
    def bytes(self):
        return self._value[self.Field.BYTES]

    @property
    def width(self):
        return self._value[self.Field.WIDTH]

    @property
    def height(self):
        return self._value[self.Field.HEIGHT]

    @property
    def mime_type(self):
        return self._value[self.Field.MIME_TYPE]

    @property
    def title(self):
        return self._value[self.Field.TITLE]

    @property
    def metadata(self):
        return self._value[self.Field.METADATA]

    def check_edit(self, data):
        if {k: v for k, v in self._value.items() if k not in self.EDITABLE} != {
            k: v for k, v in data.items() if k not in self.EDITABLE
        }:
            raise ValueError(self.IMMUTABLE)

    def mark_deleted(self):
        self._value.update({self.Field.DELETED: self.DELETED, self.Field.DELETED_AT: now()})

    def verify_asset(self, raw):
        if len(raw) != self.bytes or hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError(self.ASSET_MISMATCH)

    def reference(self):
        return {key: deepcopy(self._value[key]) for key in self.REFERENCE_FIELDS if key in self._value}

    def to_dict(self):
        return self._value


class ImageOrigin:
    IMAGE_ID = VisionImageIdentity.IMAGE_ID
    SHA256 = VisionImageRecord.Field.SHA256
    OPERATIONS = ImageOperation.OPERATIONS
    COORDINATE_CONTRACT = "coordinate_contract"
    SEQUENTIAL_PIXELS = "sequential-input-pixels-ltrb"

    @classmethod
    def create(cls, source, operations):
        return {
            cls.IMAGE_ID: source.identifier,
            cls.SHA256: source.sha256,
            cls.OPERATIONS: deepcopy(operations),
            cls.COORDINATE_CONTRACT: cls.SEQUENTIAL_PIXELS,
        }
