"""지정한 작업 루트 안의 텍스트 파일 탐색과 원자적 변경을 제공한다."""

import hashlib
import os
import stat
from pathlib import Path
import tempfile
import threading
from copy import copy
from uuid import uuid4
from llm.core.configuration import UNSET
from llm.core.schema import SchemaKeyword, validate_declared_fields
from .file_values import (FileLimits, FileReadArguments, FileListArguments, FileSearchArguments,
    FileCreateArguments, FilePatchArguments, FileMoveArguments, FileDeleteArguments, FileRestoreArguments,
    FileEntryKind, FileReadResult, FileEntry, FileListResult, FileSearchMatch, FileSearchResult,
    FileCreated, FilePatched, FileDeleted, FileRestored, FileMoved, FileTrashIdentifier)



class FileTools:
    """실행 루트는 개발자가 지정한다. 심볼릭 링크과 상위 경로는 따라가지 않는다."""

    MAX_BYTES_FIELD = FileLimits.max_file_bytes.__name__
    ARGUMENTS = (FileReadArguments, FileListArguments, FileSearchArguments, FileCreateArguments,
                 FilePatchArguments, FileMoveArguments, FileDeleteArguments, FileRestoreArguments)
    ROOT_PATH = "."
    PARENT_PATH = ".."
    RESERVED_PREFIX = ".llm-"
    WRITE_PREFIX = ".llm-write-"
    TRASH_ROOT = ".llm-trash"
    TRASH_CONTENT = "content"
    TEXT_ENCODING = "utf-8"
    NUL = "\x00"
    BINARY_MODE = "rb"
    READ_ALL = -1
    FIRST_LINE = 1
    END_OF_LINES = None
    UNIQUE_MATCH = 1
    COMPLETE = False
    TRUNCATED = True
    EXTERNAL_PATH = False
    INTERNAL_PATH = True
    KEEP_LINES = True
    RECOVERABLE_MISSING = True
    CREATE_PARENTS = True
    EXISTING_PARENT = True
    RETAIN_TEMPORARY = False
    NO_BYTE_LIMIT = None
    INVALID_ROOT = "Tool working root must be an existing directory"
    INVALID_PATH = "Expected a relative path"
    ESCAPING_PATH = "Path must stay inside the working root"
    RESERVED_PATH = "Path is reserved for Tool storage"
    LINKED_PATH = "Tool paths cannot follow symlinks"
    REGULAR_FILE = "Expected a regular file"
    TOO_LARGE = "File exceeds Tool byte limit"
    CHANGED_FILE = "File changed; read it again before editing"
    MISSING_FILE = "File not found: {path}"
    MIXED_RANGE = "tail_lines cannot be combined with start_line/max_lines"
    DIRECTORY_REQUIRED = "Expected a directory"
    BINARY_FILE = "Binary file"
    EXISTS = "File already exists"
    AMBIGUOUS_PATCH = "Patch text must match exactly once"
    RESTORE_EXISTS = "Restore destination already exists"
    MOVE_EXISTS = "Move destination already exists"

    @classmethod
    def argument_schema(cls):
        return FileLimits.schema()[SchemaKeyword.PROPERTIES]

    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        self.max_bytes = self.NO_BYTE_LIMIT
        self.lock = threading.RLock()
        self.path(self.ROOT_PATH)
        if not self.root.is_dir():
            raise ValueError(self.INVALID_ROOT)

    def for_arguments(self, arguments):
        """호출별 Project argument constraint를 적용하며 파일 잠금은 공유한다."""
        selected = copy(self)
        selected.max_bytes = FileLimits.read(arguments).max_file_bytes
        if selected.max_bytes is not None:
            validate_declared_fields({self.MAX_BYTES_FIELD: selected.max_bytes}, self.argument_schema())
        return selected

    def path(self, value: str, *, internal=EXTERNAL_PATH) -> Path:
        if not isinstance(value, str) or not value or self.NUL in value:
            raise ValueError(self.INVALID_PATH)
        relative = Path(value)
        if relative.is_absolute() or self.PARENT_PATH in relative.parts:
            raise ValueError(self.ESCAPING_PATH)
        if not internal and any(part.startswith(self.RESERVED_PREFIX) for part in relative.parts):
            raise ValueError(self.RESERVED_PATH)
        target = self.root / relative
        for entry in (target, *target.parents):
            if entry.is_symlink():
                raise ValueError(self.LINKED_PATH)
        target.resolve().relative_to(self.root.resolve())
        return target

    @staticmethod
    def digest(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    def read_bytes(self, path: Path) -> bytes:
        # 샌드박스 산출물에도 사용한다. FIFO/device 또는 링크를 열어 block하지 않는다.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, self.BINARY_MODE) as stream:
            mode = os.fstat(stream.fileno()).st_mode
            if stat.S_ISDIR(mode):
                raise IsADirectoryError(str(path))
            if not stat.S_ISREG(mode):
                raise ValueError(self.REGULAR_FILE)
            value = stream.read(self.READ_ALL if self.max_bytes is None else self.max_bytes + 1)
        if self.max_bytes is not None and len(value) > self.max_bytes:
            raise ValueError(self.TOO_LARGE)
        return value

    def check_version(self, value: bytes, expected: str) -> None:
        if self.digest(value) != expected:
            raise ValueError(self.CHANGED_FILE)

    def write(self, path: Path, content: bytes) -> None:
        if self.max_bytes is not None and len(content) > self.max_bytes:
            raise ValueError(self.TOO_LARGE)
        path.parent.mkdir(parents=self.CREATE_PARENTS, exist_ok=self.EXISTING_PARENT)
        mode = path.stat().st_mode if path.exists() else None
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=self.WRITE_PREFIX, delete=self.RETAIN_TEMPORARY) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if mode is not None:
                os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def file_read(self, args):
        args = FileReadArguments.read(args)
        with self.lock:
            target = self.path(args.path)
            try:
                value = self.read_bytes(target)
            except FileNotFoundError as error:
                # 경로/링크 검증을 통과한 읽기만 분류한다. 보안/버전/일반 I/O 오류는 삼키지 않는다.
                from llm.services.runtime.tools import ToolExecutionError
                raise ToolExecutionError(self.MISSING_FILE.format(path=args.path), effect=ToolExecutionError.NO_EFFECT,
                                         recoverable=self.RECOVERABLE_MISSING) from error
            if args.expected_sha256 is not UNSET:
                self.check_version(value, args.expected_sha256)
            lines = value.decode(self.TEXT_ENCODING).splitlines(keepends=self.KEEP_LINES)
            start = self.FIRST_LINE if args.start_line is UNSET else args.start_line
            count = len(lines) if args.max_lines is UNSET else args.max_lines
            if args.tail_lines is not UNSET:
                if args.start_line is not UNSET or args.max_lines is not UNSET:
                    raise ValueError(self.MIXED_RANGE)
                count = args.tail_lines
                start = max(self.FIRST_LINE, len(lines) - count + 1)
            end = min(len(lines), start - 1 + count)
            return FileReadResult(args.path, "".join(lines[start - 1:start - 1 + count]),
                self.digest(value), len(lines), start, end + 1 if end < len(lines) else self.END_OF_LINES,
                start > self.FIRST_LINE or end < len(lines)).to_dict()

    def _matches(self, path, patterns):
        """작업 루트 상대 경로에 pathlib glob 규칙을 적용한다."""
        relative = path.relative_to(self.root)
        return any(relative.match(pattern) for pattern in patterns)

    def entries(self, args):
        root = self.path(args.path)
        if not root.is_dir():
            raise ValueError(self.DIRECTORY_REQUIRED)
        pending = [root]
        while pending:
            directory = pending.pop()
            for path in sorted(directory.iterdir()):
                if path.name.startswith(self.RESERVED_PREFIX) or path.is_symlink():
                    continue
                if self._matches(path, args.exclude):
                    continue
                yield path
                if args.recursive and path.is_dir():
                    pending.append(path)

    def file_list(self, args):
        args = FileListArguments.read(args)
        with self.lock:
            limit = args.limit
            values = []
            for path in self.entries(args):
                if args.include is not UNSET and not self._matches(path, args.include):
                    continue
                if len(values) == limit:
                    return FileListResult(values, self.TRUNCATED).to_dict()
                values.append(FileEntry(path.relative_to(self.root).as_posix(),
                    FileEntryKind.DIRECTORY.value if path.is_dir() else FileEntryKind.FILE.value))
            return FileListResult(values, self.COMPLETE).to_dict()

    def file_search(self, args):
        args = FileSearchArguments.read(args)
        with self.lock:
            query, values, skipped = args.query, [], 0
            insensitive = not args.case_sensitive
            needle = query.casefold() if insensitive else query
            matched, scanned = 0, 0
            offset, context = args.offset, args.context_lines
            for path in self.entries(args):
                if not path.is_file():
                    continue
                if args.include is not UNSET and not self._matches(path, args.include):
                    continue
                scanned += 1
                try:
                    raw = self.read_bytes(path)
                    text = raw.decode(self.TEXT_ENCODING)
                    if self.NUL in text:
                        raise ValueError(self.BINARY_FILE)
                except (ValueError, UnicodeError):
                    skipped += 1
                    continue
                lines = text.splitlines()
                digest = self.digest(raw)
                for line_number, line in enumerate(lines, self.FIRST_LINE):
                    if needle in (line.casefold() if insensitive else line):
                        matched += 1
                        if matched <= offset:
                            continue
                        if args.limit is not None and len(values) >= args.limit:
                            return FileSearchResult(values, skipped, scanned, self.TRUNCATED, offset + len(values)).to_dict()
                        values.append(FileSearchMatch(path.relative_to(self.root).as_posix(), line_number, line, digest,
                            max(self.FIRST_LINE, line_number - context),
                            lines[max(0, line_number - 1 - context):line_number + context]))
            return FileSearchResult(values, skipped, scanned, self.COMPLETE).to_dict()

    def file_create(self, args):
        args = FileCreateArguments.read(args)
        with self.lock:
            path = self.path(args.path)
            if path.exists():
                raise FileExistsError(self.EXISTS)
            value = args.content.encode(self.TEXT_ENCODING)
            self.write(path, value)
            return FileCreated(args.path, self.digest(value), len(value)).to_dict()

    def file_patch(self, args):
        args = FilePatchArguments.read(args)
        with self.lock:
            path = self.path(args.path)
            original = self.read_bytes(path)
            self.check_version(original, args.expected_sha256)
            text = original.decode(self.TEXT_ENCODING)
            if text.count(args.old_text) != self.UNIQUE_MATCH:
                raise ValueError(self.AMBIGUOUS_PATCH)
            value = text.replace(args.old_text, args.new_text, self.UNIQUE_MATCH).encode(self.TEXT_ENCODING)
            self.write(path, value)
            return FilePatched(args.path, self.digest(original), self.digest(value)).to_dict()

    def file_delete(self, args):
        """삭제는 휴지통 이동으로 처리한다. 디렉토리 재귀 삭제는 제공하지 않는다."""
        args = FileDeleteArguments.read(args)
        with self.lock:
            source = self.path(args.path)
            original = self.read_bytes(source)
            self.check_version(original, args.expected_sha256)
            identifier = uuid4().hex
            destination = self.trash_path(identifier)
            destination.parent.mkdir(parents=self.CREATE_PARENTS)
            source.rename(destination)
            return FileDeleted(args.path, identifier, self.digest(original)).to_dict()

    def trash_path(self, identifier):
        return self.path((Path(self.TRASH_ROOT) / identifier / self.TRASH_CONTENT).as_posix(), internal=self.INTERNAL_PATH)

    def file_restore(self, args):
        args = FileRestoreArguments.read(args)
        with self.lock:
            identifier = args.trash_id
            FileTrashIdentifier.validate(identifier)
            source = self.trash_path(identifier)
            destination = self.path(args.path)
            if destination.exists():
                raise FileExistsError(self.RESTORE_EXISTS)
            destination.parent.mkdir(parents=self.CREATE_PARENTS, exist_ok=self.EXISTING_PARENT)
            source.rename(destination)
            return FileRestored(args.path).to_dict()

    def file_move(self, args):
        args = FileMoveArguments.read(args)
        with self.lock:
            source, destination = self.path(args.path), self.path(args.destination)
            original = self.read_bytes(source)
            self.check_version(original, args.expected_sha256)
            if destination.exists():
                raise FileExistsError(self.MOVE_EXISTS)
            destination.parent.mkdir(parents=self.CREATE_PARENTS, exist_ok=self.EXISTING_PARENT)
            source.rename(destination)
            return FileMoved(args.destination, self.digest(original)).to_dict()
