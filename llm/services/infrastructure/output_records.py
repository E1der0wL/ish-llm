"""Current output journal records, required source evidence and derived seek index."""

import hashlib
import json
import re
from bisect import bisect_right

from llm.core.results import EngineDelta, EngineOutput


class OutputEventRecord:
    TYPE = "type"
    VALUE = "value"
    DELTA = "delta"
    OUTPUT = "output"
    FIELDS = frozenset((TYPE, VALUE))
    KINDS = (DELTA, OUTPUT)
    INVALID = "Invalid output journal record"

    @classmethod
    def read(cls, item):
        if not isinstance(item, dict) or item.keys() != cls.FIELDS or item[cls.TYPE] not in cls.KINDS:
            raise ValueError(cls.INVALID)
        return (EngineDelta if item[cls.TYPE] == cls.DELTA else EngineOutput).from_dict(item[cls.VALUE])

    @classmethod
    def create(cls, value):
        return {
            cls.TYPE: cls.DELTA if isinstance(value, EngineDelta) else cls.OUTPUT,
            cls.VALUE: value.to_dict(),
        }


class OutputSource:
    SCHEMA_VERSION = 1
    SUFFIX = ".manifest.json"
    VERSION = "schema_version"
    BYTES = "bytes"
    SEQUENCE = "sequence"
    SHA256 = "sha256"
    FIELDS = frozenset((VERSION, BYTES, SEQUENCE, SHA256))
    EMPTY_BYTES = b""
    ZERO = 0
    DIGEST_PATTERN = r"[a-f0-9]{64}"
    INVALID_FIELDS = "Output manifest requires all current fields"
    INVALID_VERSION = "Invalid output manifest version"
    INVALID_EVIDENCE = "Invalid output manifest source evidence"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, version, byte_count, sequence, digest):
        return cls({cls.VERSION: version, cls.BYTES: byte_count, cls.SEQUENCE: sequence, cls.SHA256: digest})

    @classmethod
    def empty(cls, version):
        return cls.create(version, cls.ZERO, cls.ZERO, hashlib.sha256(cls.EMPTY_BYTES).hexdigest())

    @classmethod
    def read(cls, value, version):
        if not isinstance(value, dict) or value.keys() != cls.FIELDS:
            raise ValueError(cls.INVALID_FIELDS)
        if type(value[cls.VERSION]) is not int or value[cls.VERSION] != version:
            raise ValueError(cls.INVALID_VERSION)
        if (
            type(value[cls.BYTES]) is not int
            or value[cls.BYTES] < cls.ZERO
            or type(value[cls.SEQUENCE]) is not int
            or value[cls.SEQUENCE] < cls.ZERO
            or not isinstance(value[cls.SHA256], str)
            or not re.fullmatch(cls.DIGEST_PATTERN, value[cls.SHA256])
        ):
            raise ValueError(cls.INVALID_EVIDENCE)
        return cls(value)

    @property
    def byte_count(self):
        return self._value[self.BYTES]

    @property
    def sequence(self):
        return self._value[self.SEQUENCE]

    @property
    def digest(self):
        return self._value[self.SHA256]

    @property
    def record(self):
        return self._value


class OutputAnchor:
    SEQUENCE = 0
    OFFSET = 1
    WIDTH = 2
    BEFORE_FIRST = (0, -1)
    INVALID = "Invalid output index anchor"

    @classmethod
    def validate(cls, anchor, previous, sequence, size):
        if (
            not isinstance(anchor, list)
            or len(anchor) != cls.WIDTH
            or any(type(v) is not int for v in anchor)
            or not previous[cls.SEQUENCE] < anchor[cls.SEQUENCE] <= sequence
            or not previous[cls.OFFSET] < anchor[cls.OFFSET] < size
        ):
            raise ValueError(cls.INVALID)

    @staticmethod
    def create(sequence, offset):
        return [sequence, offset]


class OutputIndex:
    VERSION = 1
    FILE = "outputs.index.json"
    VERSION_FIELD = "version"
    IDENTITY = "identity"
    STRIDE = "stride"
    SIZE = "size"
    SEQUENCE = OutputSource.SEQUENCE
    ANCHORS = "anchors"
    SOURCE_DIGEST = "source_digest"
    MTIME = "mtime"
    FIELDS = frozenset((VERSION_FIELD, IDENTITY, STRIDE, SIZE, SEQUENCE, ANCHORS, SOURCE_DIGEST, MTIME))
    COUNTERS = (STRIDE, SIZE, SEQUENCE, MTIME)
    ZERO = 0
    ONE = 1
    INVALID_FIELDS = "Invalid output index fields"
    INVALID_VERSION = "Invalid output index version"
    INVALID_COUNTERS = "Invalid output index counters"
    INVALID_ANCHORS = "Invalid output index anchors"

    def __init__(self, value):
        self._value = value

    @classmethod
    def start(cls, version, identity, stride):
        return cls(
            {
                cls.VERSION_FIELD: version,
                cls.IDENTITY: identity,
                cls.STRIDE: stride,
                cls.SIZE: cls.ZERO,
                cls.SEQUENCE: cls.ZERO,
                cls.ANCHORS: [],
            }
        )

    @classmethod
    def read(cls, value, version):
        if not isinstance(value, dict) or value.keys() != cls.FIELDS:
            raise ValueError(cls.INVALID_FIELDS)
        if type(value[cls.VERSION_FIELD]) is not int or value[cls.VERSION_FIELD] != version:
            raise ValueError(cls.INVALID_VERSION)
        if any(type(value[key]) is not int or value[key] < cls.ZERO for key in cls.COUNTERS):
            raise ValueError(cls.INVALID_COUNTERS)
        if not isinstance(value[cls.ANCHORS], list):
            raise ValueError(cls.INVALID_ANCHORS)
        previous = OutputAnchor.BEFORE_FIRST
        for anchor in value[cls.ANCHORS]:
            OutputAnchor.validate(anchor, previous, value[cls.SEQUENCE], value[cls.SIZE])
            previous = anchor
        return cls(value)

    def matches(self, checksum, digest, identity, stride, stat):
        return (
            checksum == digest(self._value)
            and self._value[self.IDENTITY] == identity
            and self._value[self.STRIDE] == stride
            and self.ZERO <= self.size <= stat.st_size
            and (self.size < stat.st_size or self._value[self.MTIME] == stat.st_mtime_ns)
        )

    @property
    def size(self):
        return self._value[self.SIZE]

    @property
    def sequence(self):
        return self._value[self.SEQUENCE]

    @property
    def source_digest(self):
        return self._value[self.SOURCE_DIGEST]

    def anchor(self, sequence, offset):
        self._value[self.ANCHORS].append(OutputAnchor.create(sequence, offset))

    def advance(self, sequence, size):
        self._value[self.SEQUENCE], self._value[self.SIZE] = sequence, size

    def seal(self, source, mtime):
        self._value[self.SOURCE_DIGEST] = source.digest
        self._value[self.MTIME] = mtime

    def cursor(self, after):
        # The second key includes anchors at exactly the requested sequence.
        index = bisect_right(self._value[self.ANCHORS], [after + self.ONE, self.size]) - self.ONE
        if index >= self.ZERO:
            number, offset = self._value[self.ANCHORS][index]
            return offset, number - self.ONE
        return self.ZERO, self.ZERO

    @property
    def record(self):
        return self._value


class OutputIndexEnvelope:
    INDEX = "index"
    CHECKSUM = "checksum"
    SORT_KEYS = True

    def __init__(self, value):
        self._value = value

    @property
    def index(self):
        return self._value[self.INDEX]

    @property
    def checksum(self):
        return self._value[self.CHECKSUM]

    @classmethod
    def create(cls, index, checksum):
        return {cls.INDEX: index, cls.CHECKSUM: checksum}

    @classmethod
    def digest(cls, value):
        return hashlib.sha256(json.dumps(value, sort_keys=cls.SORT_KEYS).encode()).hexdigest()
