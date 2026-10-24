"""Current undo journal schema and filesystem evidence; no domain execution policy."""

import hashlib
import json
from pathlib import Path

from llm.core.paths import WorkspacePaths


class UndoKind:
    CREATE = "create"
    REPLACE = "replace"
    APPEND = "append"
    REMOVE = "remove"
    TRUNCATE = "truncate"
    ALL = (CREATE, REPLACE, APPEND, REMOVE, TRUNCATE)
    WITH_OFFSET = (APPEND, TRUNCATE)
    WITH_BACKUP = (REPLACE, REMOVE, TRUNCATE)
    REQUIRED_BACKUP_FILE = (REPLACE, TRUNCATE)


class TransactionJournal:
    RECORDS = "*.json"
    ENTRY_NAME = "{index:08d}.json"
    BACKUP_NAME = "{index:08d}.{suffix}"
    BACKUP_SUFFIXES = {UndoKind.REPLACE: "data", UndoKind.REMOVE: "removed", UndoKind.TRUNCATE: "tail"}
    UNDONE_NAME = "undone-{index:08d}"
    COMMITTED = "COMMITTED"
    COMPLETE_MARKER = b"1\n"
    GC_PREFIX = "gc-"
    DIRECTORY_PATTERN = rf"(?:{GC_PREFIX})?[a-f0-9]{{32}}"
    WRITE_PREFIX = ".write-"
    RESTORE_PREFIX = ".restore-"
    STAGE_PREFIX = "stage-"
    VALUE_PREFIX = "value-"
    PARENT = ".."
    ALL_FILES = "*"
    DIRECTORY_MODE = 0o700
    BINARY_READ = "rb"
    BINARY_WRITE = "wb"
    BINARY_UPDATE = "r+b"
    EXIST_OK = True
    MISSING_OK = True

    @classmethod
    def entry(cls, index):
        return cls.ENTRY_NAME.format(index=index)

    @classmethod
    def backup(cls, index, kind):
        return cls.BACKUP_NAME.format(index=index, suffix=cls.BACKUP_SUFFIXES[kind])

    @classmethod
    def undone(cls, index):
        return cls.UNDONE_NAME.format(index=index)


class UndoEnvelope:
    FORMAT_VERSION = 1
    VERSION = "format_version"
    OPERATION = "operation"
    CHECKSUM = "checksum"
    SORT_KEYS = True
    ALLOW_NAN = False
    UNSUPPORTED = "Unsupported transaction journal format"
    CHECKSUM_MISMATCH = "Transaction journal checksum mismatch"

    @classmethod
    def digest(cls, value):
        payload = json.dumps(value, sort_keys=cls.SORT_KEYS, allow_nan=cls.ALLOW_NAN).encode()
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def encode(cls, value, version):
        checksum = cls.digest(value)
        envelope = {cls.VERSION: version, cls.OPERATION: value, cls.CHECKSUM: checksum}
        return json.dumps(envelope, allow_nan=cls.ALLOW_NAN).encode()

    @classmethod
    def read(cls, envelope, version):
        if type(envelope.get(cls.VERSION)) is not int or envelope[cls.VERSION] != version:
            raise ValueError(cls.UNSUPPORTED)
        value = envelope[cls.OPERATION]
        checksum = cls.digest(value)
        if envelope.get(cls.CHECKSUM) != checksum:
            raise ValueError(cls.CHECKSUM_MISMATCH)
        return value


class UndoOperation:
    KIND = "kind"
    PATH = "path"
    BACKUP = "backup"
    SIZE = "size"
    MINIMUM_OFFSET = 0
    INVALID_TARGET = "Invalid transaction target"
    RESERVED_TARGET = "Reserved transaction target"
    INVALID_KIND = "Invalid transaction operation"
    INVALID_OFFSET = "Invalid append recovery offset"
    INVALID_BACKUP = "Invalid transaction backup"
    MISSING_BACKUP = "Missing transaction backup"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, path):
        return {cls.KIND: UndoKind.CREATE, cls.PATH: path}

    @classmethod
    def replace(cls, path, backup):
        return {cls.KIND: UndoKind.REPLACE, cls.PATH: path, cls.BACKUP: backup}

    @classmethod
    def append(cls, path, size):
        return {cls.KIND: UndoKind.APPEND, cls.PATH: path, cls.SIZE: size}

    @classmethod
    def truncate(cls, path, size, backup):
        return {cls.KIND: UndoKind.TRUNCATE, cls.PATH: path, cls.SIZE: size, cls.BACKUP: backup}

    @classmethod
    def remove(cls, path, backup):
        return {cls.KIND: UndoKind.REMOVE, cls.PATH: path, cls.BACKUP: backup}

    @classmethod
    def read(cls, value, root, directory, index, checked):
        name, kind = value.get(cls.PATH), value.get(cls.KIND)
        if not isinstance(name, str) or Path(name).is_absolute():
            raise ValueError(cls.INVALID_TARGET)
        target = checked(root / name)
        relative = target.relative_to(root)
        if WorkspacePaths.reserved(relative):
            raise ValueError(cls.RESERVED_TARGET)
        if kind not in UndoKind.ALL:
            raise ValueError(cls.INVALID_KIND)
        if kind in UndoKind.WITH_OFFSET and (
            type(value.get(cls.SIZE)) is not int or value[cls.SIZE] < cls.MINIMUM_OFFSET
        ):
            raise ValueError(cls.INVALID_OFFSET)
        if kind in UndoKind.WITH_BACKUP:
            if value.get(cls.BACKUP) != TransactionJournal.backup(index, kind):
                raise ValueError(cls.INVALID_BACKUP)
            checked(directory / value[cls.BACKUP])
        if kind in UndoKind.REQUIRED_BACKUP_FILE and not (directory / value[cls.BACKUP]).is_file():
            raise ValueError(cls.MISSING_BACKUP)
        return cls(value), target

    @property
    def kind(self):
        return self._value[self.KIND]

    @property
    def size(self):
        return self._value[self.SIZE]

    @property
    def backup(self):
        return self._value[self.BACKUP]

    @property
    def record(self):
        return self._value
