"""Builtin file Tool argument declarations and observation result projections."""

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import re

from llm.core.configuration import UNSET
from llm.core.schema import SchemaKeyword
from .arguments import BuiltinArguments


class FileArgumentFormats:
    SHA256 = "^[0-9a-f]{64}$"


class FileTrashIdentifier:
    PATTERN = "^[0-9a-f]{32}$"
    INVALID = "Invalid trash ID"

    @classmethod
    def validate(cls, value):
        if re.fullmatch(cls.PATTERN, value) is None:
            raise ValueError(cls.INVALID)


@dataclass(frozen=True, slots=True, kw_only=True)
class FileLimits(BuiltinArguments):
    max_file_bytes: int | None = field(default=None, metadata={BuiltinArguments.SCHEMA: {"type": "integer", "minimum": 1}})


@dataclass(frozen=True, slots=True, kw_only=True)
class FilePathArguments(FileLimits):
    path: str = field(metadata={BuiltinArguments.SCHEMA: {'type': 'string', 'minLength': 1}})

    @classmethod
    def optional_path(cls, arguments):
        return arguments.get(cls.path.__name__)

    @classmethod
    def with_path(cls, arguments, path):
        return {**arguments, cls.path.__name__: path}

    @classmethod
    def provided_path(cls, arguments):
        """Explicit path projection for the workspace's special-file guard."""
        return arguments.get(cls.path.__name__, UNSET)


@dataclass(frozen=True, slots=True, kw_only=True)
class FileReadArguments(FilePathArguments):
    NAME = 'file_read'
    DESCRIPTION = 'Read UTF-8 source or logs within the configured read roots. Use line ranges, tail_lines, or offset/max_chars (mutually exclusive). expected_sha256 checks the whole-file hash; character pages return next_offset.'

    start_line: object = field(default=UNSET, metadata={BuiltinArguments.SCHEMA: {'type': 'integer', 'minimum': 1}})
    max_lines: object = field(default=UNSET, metadata={BuiltinArguments.SCHEMA: {'type': 'integer', 'minimum': 1}})
    tail_lines: object = field(default=UNSET, metadata={BuiltinArguments.SCHEMA: {'type': 'integer', 'minimum': 1}})
    expected_sha256: object = field(default=UNSET, metadata={BuiltinArguments.SCHEMA: {'type': 'string', 'pattern': FileArgumentFormats.SHA256}})
    offset: object = field(default=UNSET, metadata={BuiltinArguments.SCHEMA: {'type': 'integer', 'minimum': 0}})
    max_chars: object = field(default=UNSET, metadata={BuiltinArguments.SCHEMA: {'type': 'integer', 'minimum': 1,
        'description': 'Character page size. Cannot be combined with line selection; use returned next_offset.'}})


@dataclass(frozen=True, slots=True, kw_only=True)
class FileTraversalArguments(FileLimits):
    path: str = field(default='.', metadata={BuiltinArguments.SCHEMA: {'type': 'string', 'minLength': 1}})
    recursive: bool = field(metadata={BuiltinArguments.SCHEMA: {'type': 'boolean'}})
    limit: int | None = field(default=None, metadata={BuiltinArguments.SCHEMA: {'type': 'integer', 'minimum': 1}})
    include: object = field(default=UNSET, metadata={BuiltinArguments.SCHEMA: {'type': 'array', 'items': {'type': 'string', 'minLength': 1}, 'minItems': 1}})
    exclude: list[str] = field(default_factory=list, metadata={BuiltinArguments.SCHEMA: {'type': 'array', 'items': {'type': 'string', 'minLength': 1}, 'minItems': 1}})


@dataclass(frozen=True, slots=True, kw_only=True)
class FileListArguments(FileTraversalArguments):
    NAME = 'file_list'
    DESCRIPTION = 'List entries within configured read roots. Symbolic links require explicit read permission. include/exclude are pathlib path glob patterns; excluded directories are not traversed.'



@dataclass(frozen=True, slots=True, kw_only=True)
class FileSearchArguments(FileTraversalArguments):
    NAME = 'file_search'
    DESCRIPTION = 'Find literal text in UTF-8 sources or logs, with line numbers, file hashes and optional surrounding lines. include/exclude use pathlib globs. offset skips matches; paging rescans current files, so restart after edits.'

    query: str = field(metadata={BuiltinArguments.SCHEMA: {'type': 'string', 'minLength': 1}})
    case_sensitive: bool = field(metadata={BuiltinArguments.SCHEMA: {'type': 'boolean'}})
    offset: int = field(default=0, metadata={BuiltinArguments.SCHEMA: {'type': 'integer', 'minimum': 0}})
    context_lines: int = field(default=0, metadata={BuiltinArguments.SCHEMA: {'type': 'integer', 'minimum': 0}})
    max_chars: object = field(default=UNSET, metadata={BuiltinArguments.SCHEMA: {'type': 'integer', 'minimum': 1,
        'description': 'Maximum returned characters per matching/context line; search still scans the whole line.'}})

    @classmethod
    def schema(cls):
        value = super(FileSearchArguments, cls).schema()
        value[SchemaKeyword.REQUIRED] = [cls.query.__name__, cls.case_sensitive.__name__, cls.recursive.__name__]
        return value



@dataclass(frozen=True, slots=True, kw_only=True)
class FileCreateArguments(FilePathArguments):
    NAME = 'file_create'
    DESCRIPTION = 'Create a new UTF-8 file; never overwrite an existing file.'

    content: str = field(metadata={BuiltinArguments.SCHEMA: {'type': 'string'}})


@dataclass(frozen=True, slots=True, kw_only=True)
class FilePatchArguments(FilePathArguments):
    NAME = 'file_patch'
    DESCRIPTION = 'Replace exactly one matching text after checking the file version.'

    expected_sha256: str = field(metadata={BuiltinArguments.SCHEMA: {'type': 'string', 'pattern': FileArgumentFormats.SHA256}})
    old_text: str = field(metadata={BuiltinArguments.SCHEMA: {'type': 'string', 'minLength': 1}})
    new_text: str = field(metadata={BuiltinArguments.SCHEMA: {'type': 'string'}})


@dataclass(frozen=True, slots=True, kw_only=True)
class FileMoveArguments(FilePathArguments):
    NAME = 'file_move'
    DESCRIPTION = 'Move a version-checked file without overwriting the destination.'

    destination: str = field(metadata={BuiltinArguments.SCHEMA: {'type': 'string', 'minLength': 1}})
    expected_sha256: str = field(metadata={BuiltinArguments.SCHEMA: {'type': 'string', 'pattern': FileArgumentFormats.SHA256}})


@dataclass(frozen=True, slots=True, kw_only=True)
class FileDeleteArguments(FilePathArguments):
    NAME = 'file_delete'
    DESCRIPTION = 'Move a version-checked file to recoverable trash.'

    expected_sha256: str = field(metadata={BuiltinArguments.SCHEMA: {'type': 'string', 'pattern': FileArgumentFormats.SHA256}})


@dataclass(frozen=True, slots=True, kw_only=True)
class FileRestoreArguments(FilePathArguments):
    NAME = 'file_restore'
    DESCRIPTION = 'Restore a trash item to an unused path within the configured write roots.'

    trash_id: str = field(metadata={BuiltinArguments.SCHEMA: {'type': 'string', 'pattern': FileTrashIdentifier.PATTERN}})


class FileEntryKind(StrEnum):
    DIRECTORY = "directory"
    FILE = "file"


class FileObservation:
    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FileReadResult(FileObservation):
    path: str
    text: str
    sha256: str
    total_lines: int
    start_line: int
    next_line: int | None
    truncated: bool


@dataclass(frozen=True, slots=True)
class FileEntry(FileObservation):
    path: str
    type: str


@dataclass(frozen=True, slots=True)
class FileListResult(FileObservation):
    entries: list[FileEntry]
    truncated: bool


@dataclass(frozen=True, slots=True)
class FileSearchMatch(FileObservation):
    path: str
    line: int
    text: str
    sha256: str
    context_start_line: int
    context: list[str]


@dataclass(frozen=True, slots=True)
class FileSearchResult(FileObservation):
    matches: list[FileSearchMatch]
    skipped: int
    scanned: int
    truncated: bool
    next_offset: int | None = None
    max_chars_per_line: int | None = None

    def to_dict(self):
        value = asdict(self)
        if self.max_chars_per_line is None:
            value.pop("max_chars_per_line")
        else:
            value["line_previews"] = True
        return value


@dataclass(frozen=True, slots=True)
class FileCreated(FileObservation):
    path: str
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class FilePatched(FileObservation):
    path: str
    before_sha256: str
    sha256: str


@dataclass(frozen=True, slots=True)
class FileDeleted(FileObservation):
    path: str
    trash_id: str
    sha256: str


@dataclass(frozen=True, slots=True)
class FileRestored(FileObservation):
    path: str
    restored: bool = True


@dataclass(frozen=True, slots=True)
class FileMoved(FileObservation):
    path: str
    sha256: str
