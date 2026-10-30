"""Application-selected file access and atomic regular-file replacement."""

import hashlib
import os
import stat
from pathlib import Path
import threading
from copy import copy
from uuid import uuid4
from .path_access import PathAccess
from .file_text import TextIndex
from contextlib import closing, contextmanager, ExitStack
from llm.services.infrastructure.text_stream import read_text_range
from llm.core.configuration import UNSET
from llm.services.runtime.tool_errors import ToolExecutionError
from llm.core.schema import SchemaKeyword, validate_declared_fields
from .file_values import (FileLimits, FileReadArguments, FileListArguments, FileSearchArguments,
    FileCreateArguments, FilePatchArguments, FileMoveArguments, FileDeleteArguments, FileRestoreArguments,
    FileEntryKind, FileReadResult, FileEntry, FileListResult, FileSearchMatch, FileSearchResult,
    FileCreated, FilePatched, FileDeleted, FileRestored, FileMoved, FileTrashIdentifier)



class FileTools:
    """Read, write and command-directory grants are independent of the base path."""

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

    def __init__(self, root: Path, *, read_access=None, write_access=None, cwd_access=None):
        self.root = Path(root).absolute()
        self.max_bytes = self.NO_BYTE_LIMIT
        self.lock = threading.RLock()
        self.path(self.ROOT_PATH)
        if not self.root.is_dir():
            raise ValueError(self.INVALID_ROOT)
        # The explicit working root grants the original capability when there is
        # no separate read grant. Never write this projection into user config.
        for name, value in (("read_access", read_access), ("write_access", write_access), ("cwd_access", cwd_access)):
            setattr(self, name, PathAccess((self.root,), False) if value is None else PathAccess.read(value))

    def with_access(self, values):
        selected = copy(self)
        for name, value in values.items():
            if name not in ("read_access", "write_access", "cwd_access"):
                raise ValueError("Unknown path access grant")
            setattr(selected, name, PathAccess.read(value))
        return selected

    def read_path(self, value):
        return self.read_access.resolve(self.root, value)

    def display_path(self, value):
        return str(value.relative_to(self.root)) if value.is_relative_to(self.root) else str(value)

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

    def read_bytes(self, path: Path, *, parent_fd=None) -> bytes:
        # 샌드박스 산출물에도 사용한다. FIFO/device 또는 링크를 열어 block하지 않는다.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent_fd)
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

    @contextmanager
    def mutation_path(self, value, *, create=False, internal=False):
        path = self.path(value, internal=True) if internal else self.write_access.resolve(self.root, value, missing=create)
        # Creating ancestors must not produce directories outside the grant.
        boundary = self.root if internal else min(
            (root for root in self.write_access.roots if path.is_relative_to(root)), key=lambda root: len(root.parts))
        with PathAccess.directory(path.parent, create=create, create_within=boundary) as parent:
            yield parent, path.name

    def write(self, parent, name, content: bytes, *, replace=False) -> None:
        if self.max_bytes is not None and len(content) > self.max_bytes:
            raise ValueError(self.TOO_LARGE)
        mode = os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode if replace else None
        if mode is not None and not stat.S_ISREG(mode):
            raise ValueError(self.REGULAR_FILE)
        temporary = self.WRITE_PREFIX + uuid4().hex
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                if mode is not None:
                    os.fchmod(stream.fileno(), stat.S_IMODE(mode))
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            else:
                # Atomic no-overwrite publication, including concurrent external creators.
                os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass

    def file_read(self, args):
        args = FileReadArguments.read(args)
        with self.lock:
            try:
                with self.read_access.open(self.root, args.path) as stream:
                    before = os.fstat(stream.fileno())
                    with closing(TextIndex(stream, max_bytes=self.max_bytes)) as index:
                        if args.expected_sha256 is not UNSET and args.expected_sha256 != index.sha256:
                            raise ValueError(self.CHANGED_FILE)
                        character_page = args.offset is not UNSET or args.max_chars is not UNSET
                        if character_page:
                            if args.max_chars is UNSET:
                                raise ValueError("Character pages require max_chars")
                            if any(value is not UNSET for value in (args.start_line, args.max_lines, args.tail_lines)):
                                raise ValueError("Character pages require max_chars and cannot be combined with line ranges")
                            offset = 0 if args.offset is UNSET else args.offset
                            stream.seek(0)
                            text = read_text_range(stream, offset=offset, limit=args.max_chars + 1)
                            more = len(text) > args.max_chars
                            text = text[:args.max_chars]
                            result = {"path": args.path, "text": text, "sha256": index.sha256,
                                "offset": offset, "next_offset": offset + len(text) if more else None,
                                "truncated": offset > 0 or more}
                        else:
                            start = self.FIRST_LINE if args.start_line is UNSET else args.start_line
                            count = index.count if args.max_lines is UNSET else args.max_lines
                            if args.tail_lines is not UNSET:
                                if args.start_line is not UNSET or args.max_lines is not UNSET:
                                    raise ValueError(self.MIXED_RANGE)
                                count = args.tail_lines
                                start = max(self.FIRST_LINE, index.count - count + 1)
                            end = min(index.count, start - 1 + count)
                            text = "".join(index.read(line) for line in range(start, end + 1))
                            result = FileReadResult(args.path, text, index.sha256, index.count, start,
                                end + 1 if end < index.count else self.END_OF_LINES,
                                start > self.FIRST_LINE or end < index.count).to_dict()
                    self.check_observation(stream, before)
                    return result
            except FileNotFoundError as error:
                # 경로/링크 검증을 통과한 읽기만 분류한다. 보안/버전/일반 I/O 오류는 삼키지 않는다.
                from llm.services.runtime.tools import ToolExecutionError
                raise ToolExecutionError(self.MISSING_FILE.format(path=args.path), effect=ToolExecutionError.NO_EFFECT,
                                         recoverable=self.RECOVERABLE_MISSING) from error

    @staticmethod
    def check_observation(stream, before):
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError(FileTools.CHANGED_FILE)

    def _matches(self, path, patterns):
        """작업 루트 상대 경로에 pathlib glob 규칙을 적용한다."""
        relative = Path(self.display_path(path))
        return any(relative.match(pattern) for pattern in patterns)

    def entries(self, args):
        root = self.read_path(args.path)
        if not root.is_dir():
            raise ValueError(self.DIRECTORY_REQUIRED)
        pending, seen = [root], set()
        while pending:
            directory = pending.pop()
            with self.read_access.open(self.root, str(directory), directory=True) as fd:
                identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
                if identity in seen:
                    continue
                seen.add(identity)
                names = sorted(os.listdir(fd))
            for name in names:
                path = directory / name
                if path.is_relative_to(self.root) and path.name.startswith(self.RESERVED_PREFIX):
                    continue
                try:
                    self.read_path(str(path))
                except (ValueError, OSError, RuntimeError):
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
                values.append(FileEntry(self.display_path(path),
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
            maximum = None if args.max_chars is UNSET else args.max_chars
            for path in self.entries(args):
                if not path.is_file():
                    continue
                if args.include is not UNSET and not self._matches(path, args.include):
                    continue
                scanned += 1
                prior_count, prior_matches = len(values), matched
                try:
                    with self.read_access.open(self.root, str(path)) as stream:
                        before = os.fstat(stream.fileno())
                        with closing(TextIndex(stream, max_bytes=self.max_bytes, reject_nul=True)) as index:
                            for line_number in range(1, index.count + 1):
                                if not index.contains(line_number, needle, case_sensitive=args.case_sensitive):
                                    continue
                                matched += 1
                                if matched <= offset:
                                    continue
                                if args.limit is not None and len(values) >= args.limit:
                                    self.check_observation(stream, before)
                                    return FileSearchResult(values, skipped, scanned, self.TRUNCATED,
                                        offset + len(values), maximum).to_dict()
                                start = max(1, line_number - context)
                                text = index.read(line_number, max_chars=maximum, keepends=False)
                                surrounding = [index.read(line, max_chars=maximum, keepends=False)
                                               for line in range(start, min(index.count, line_number + context) + 1)]
                                values.append(FileSearchMatch(self.display_path(path), line_number, text,
                                    index.sha256, start, surrounding))
                        self.check_observation(stream, before)
                except (ValueError, UnicodeError):
                    del values[prior_count:]
                    matched = prior_matches
                    skipped += 1
                    continue
            return FileSearchResult(values, skipped, scanned, self.COMPLETE, max_chars_per_line=maximum).to_dict()

    def file_create(self, args):
        args = FileCreateArguments.read(args)
        with self.lock, self.mutation_path(args.path, create=True) as (parent, name):
            value = args.content.encode(self.TEXT_ENCODING)
            self.write(parent, name, value)
            return FileCreated(args.path, self.digest(value), len(value)).to_dict()

    def file_patch(self, args):
        args = FilePatchArguments.read(args)
        with self.lock, ExitStack() as scope:
            # Only failures proved to precede write() may claim no effects.
            # A stale CAS token requires a fresh read/invocation, never a blind
            # retry that changes the expected version or target path.
            try:
                parent, name = scope.enter_context(self.mutation_path(args.path))
                original = self.read_bytes(name, parent_fd=parent)
            except FileNotFoundError as error:
                raise ToolExecutionError(self.MISSING_FILE.format(path=args.path),
                                         effect=ToolExecutionError.NO_EFFECT) from error
            if self.digest(original) != args.expected_sha256:
                raise ToolExecutionError(self.CHANGED_FILE, effect=ToolExecutionError.NO_EFFECT)
            text = original.decode(self.TEXT_ENCODING)
            if text.count(args.old_text) != self.UNIQUE_MATCH:
                raise ToolExecutionError(self.AMBIGUOUS_PATCH, effect=ToolExecutionError.NO_EFFECT,
                                         recoverable=True)
            value = text.replace(args.old_text, args.new_text, self.UNIQUE_MATCH).encode(self.TEXT_ENCODING)
            self.write(parent, name, value, replace=True)
            return FilePatched(args.path, self.digest(original), self.digest(value)).to_dict()

    def file_delete(self, args):
        """삭제는 휴지통 이동으로 처리한다. 디렉토리 재귀 삭제는 제공하지 않는다."""
        args = FileDeleteArguments.read(args)
        with self.lock, self.mutation_path(args.path) as (parent, name):
            original = self.read_bytes(name, parent_fd=parent)
            self.check_version(original, args.expected_sha256)
            identifier = uuid4().hex
            with self.mutation_path(self.trash_name(identifier), create=True, internal=True) as (trash, content):
                os.rename(name, content, src_dir_fd=parent, dst_dir_fd=trash)
                os.fsync(parent); os.fsync(trash)
            return FileDeleted(args.path, identifier, self.digest(original)).to_dict()

    def trash_path(self, identifier):
        return self.path(self.trash_name(identifier), internal=self.INTERNAL_PATH)

    def trash_name(self, identifier):
        return (Path(self.TRASH_ROOT) / identifier / self.TRASH_CONTENT).as_posix()

    def move_file(self, source, destination):
        parent, name = source
        target, following = destination
        if not stat.S_ISREG(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode):
            raise ValueError(self.REGULAR_FILE)
        try:
            os.stat(following, dir_fd=target, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(self.MOVE_EXISTS)
        os.rename(name, following, src_dir_fd=parent, dst_dir_fd=target)
        os.fsync(parent); os.fsync(target)

    def file_restore(self, args):
        args = FileRestoreArguments.read(args)
        with self.lock:
            identifier = args.trash_id
            FileTrashIdentifier.validate(identifier)
            with self.mutation_path(self.trash_name(identifier), internal=True) as source, \
                    self.mutation_path(args.path, create=True) as destination:
                self.move_file(source, destination)
            return FileRestored(args.path).to_dict()

    def file_move(self, args):
        args = FileMoveArguments.read(args)
        with self.lock, self.mutation_path(args.path) as source:
            original = self.read_bytes(source[1], parent_fd=source[0])
            self.check_version(original, args.expected_sha256)
            with self.mutation_path(args.destination, create=True) as destination:
                self.move_file(source, destination)
            return FileMoved(args.destination, self.digest(original)).to_dict()
