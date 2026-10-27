"""Storage, copying and leasing for Host-owned Project test spaces."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
from uuid import uuid4

from llm.services.infrastructure.storage import atomic_json, sync_directory
from llm.core.configuration import UNSET
from .files import FileTools
from .file_values import FilePathArguments
from .workspace_records import (WorkspaceIdentity, WorkspaceEvidence, WorkspaceRecord, WorkspaceOrigin,
    MissingWorkspaceRecord, WorkspaceInventory, WorkspaceDeleted)


class WorkspaceRepository:
    """One physical Owner; execution/approval policy stays with the Tool runtime."""

    LOCKS = '.locks'
    LOCK_SUFFIX = '.lock'
    RECORD_FILE = 'record.json'
    FILES_DIRECTORY = 'files'
    ENCODING = 'utf-8'
    READ_MODE = 'rb'
    WRITE_MODE = 'xb'
    LINKED_PATH = 'Test workspace paths cannot follow symlinks'
    CONTAINS_SOURCE = 'Test storage cannot contain the source root'
    BUSY = 'Test workspace is busy'
    NOT_FOUND = 'Test workspace does not exist'
    INVALID_COPY_KIND = 'Only regular files and directories can be copied'
    PREPARATION_RACE = 'Source changed while preparing test workspace'
    COPY_RACE = 'Source changed while copying test workspace'
    RESERVED_SELECTION = 'Selected directory includes reserved Tool storage'
    INVALID_SELECTION = 'source_paths must be a list of relative paths'
    STORAGE_SELECTION = 'Cannot copy test workspace storage'
    OVERLAP = 'Selected source paths overlap'
    UNSAFE_DELETE = 'Safe recursive deletion is unavailable'
    INVALID_OUTPUT = 'Test output must be a regular file or directory'
    OWNER_MODE = 0o600
    EXECUTABLE_BITS = 0o111
    CREATE_PARENTS = True
    ALLOW_EXISTING = True
    FOLLOW_LINKS = False
    TOP_DOWN = False
    REQUIRE_READY = True
    OBSERVE_ONLY = False

    def __init__(self, root, source_root):
        self.root = Path(root).absolute()
        self.source = FileTools(Path(source_root))
        self.check_path(self.root)
        if self.root == self.source.root or self.root in self.source.root.parents:
            raise ValueError(self.CONTAINS_SOURCE)

    @staticmethod
    def check_path(path):
        for part in (path, *path.parents):
            if part.is_symlink():
                raise ValueError(WorkspaceRepository.LINKED_PATH)

    def paths(self, project_id, identifier):
        WorkspaceIdentity.validate_project(project_id)
        WorkspaceIdentity.validate(identifier)
        project = self.root / project_id
        self.check_path(project)
        directory = project / identifier
        self.check_path(directory)
        return project, directory

    @contextmanager
    def lease(self, project_id, identifier):
        project, directory = self.paths(project_id, identifier)
        locks = project / self.LOCKS
        self.check_path(locks)
        locks.mkdir(parents=self.CREATE_PARENTS, exist_ok=self.ALLOW_EXISTING)
        fd = os.open(locks / (identifier + self.LOCK_SUFFIX), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, self.OWNER_MODE)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError(self.BUSY) from None
            yield directory, fd
        finally:
            # close만 사용: 시작 게이트가 상속한 동일 잠금의 수명도 보존한다.
            os.close(fd)

    def record(self, directory, project_id, *, require_ready=OBSERVE_ONLY):
        path = directory / self.RECORD_FILE
        self.check_path(path)
        if not directory.is_dir():
            raise FileNotFoundError(self.NOT_FOUND)
        if not path.exists():
            # mkdir→atomic record, 혹은 delete의 마지막 구간에서 중단된 자원이다.
            # 신뢰된 Project 저장 경계 안의 ID만 관측/삭제하며 실행 권한은 만들지 않는다.
            value = MissingWorkspaceRecord(directory.name, project_id)
        else:
            value = WorkspaceRecord.read(json.loads(path.read_text(encoding=self.ENCODING)),
                project_id=project_id, workspace_id=directory.name)
        if require_ready:
            value.require_ready()
        return value

    def _copy(self, parent_fd, name, target):
        """정규 파일과 디렉터리만 복사한다. 링크·FIFO·device는 열지 않는다."""
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=self.FOLLOW_LINKS)
        if not (stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode)):
            raise ValueError(self.INVALID_COPY_KIND)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        try:
            opened = os.fstat(fd)
            if (before.st_dev, before.st_ino, before.st_mode) != (opened.st_dev, opened.st_ino, opened.st_mode):
                raise ValueError(self.PREPARATION_RACE)
            if stat.S_ISDIR(opened.st_mode):
                target.mkdir()
                for child in sorted(os.listdir(fd)):
                    if child.startswith(FileTools.RESERVED_PREFIX):
                        raise ValueError(self.RESERVED_SELECTION)
                    self._copy(fd, child, target / child)
                sync_directory(target)
            else:
                with os.fdopen(os.dup(fd), self.READ_MODE) as source, target.open(self.WRITE_MODE) as output:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fchmod(output.fileno(), self.OWNER_MODE | (opened.st_mode & self.EXECUTABLE_BITS))
                    os.fsync(output.fileno())
            after = os.fstat(fd)
            if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError(self.COPY_RACE)
        finally:
            os.close(fd)

    def create(self, call, paths):
        selected = []
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            raise ValueError(self.INVALID_SELECTION)
        for value in paths:
            source = self.source.path(value)
            if source == self.root or self.root in source.parents or source in self.root.parents:
                raise ValueError(self.STORAGE_SELECTION)
            if any(source == other or source in other.parents or other in source.parents for other in selected):
                raise ValueError(self.OVERLAP)
            selected.append(source)
        identifier = uuid4().hex
        with self.lease(call.project_id, identifier) as (directory, _):
            directory.mkdir()
            try:
                record = WorkspaceRecord(WorkspaceRecord.VERSION, identifier, call.project_id,
                    datetime.now(timezone.utc).isoformat(), WorkspaceOrigin.from_call(call),
                    [path.relative_to(self.source.root).as_posix() for path in selected])
                atomic_json(directory / self.RECORD_FILE, record.to_dict())
                files = directory / self.FILES_DIRECTORY
                if self.source.root not in selected:
                    files.mkdir()
                for source in selected:
                    relative = source.relative_to(self.source.root)
                    target = files / relative
                    target.parent.mkdir(parents=self.CREATE_PARENTS, exist_ok=self.ALLOW_EXISTING)
                    base = self.source.root if relative.parts else self.source.root.parent
                    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        for part in relative.parts[:-1]:
                            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                            os.close(fd)
                            fd = child
                        self._copy(fd, relative.name if relative.parts else self.source.root.name, target)
                    finally:
                        os.close(fd)
                # 파일과 중간 디렉터리가 durable해진 뒤에만 실행 가능한 공간으로 게시한다.
                for current, _, _ in os.walk(files, topdown=self.TOP_DOWN):
                    sync_directory(Path(current))
                record = record.published()
                atomic_json(directory / self.RECORD_FILE, record.to_dict())
                sync_directory(directory.parent)
                return record
            except BaseException:
                shutil.rmtree(directory)
                sync_directory(directory.parent)
                raise

    def list(self, project_id):
        WorkspaceIdentity.validate_project(project_id)
        project = self.root / project_id
        self.check_path(project)
        if not project.exists():
            return WorkspaceInventory([])
        values = []
        for directory in sorted(project.iterdir()):
            if not WorkspaceIdentity.valid(directory.name):
                continue
            try:
                values.append(self.record(directory, project_id))
            except FileNotFoundError:
                # 생성/삭제 중일 수 있다. 목록은 실행 상태의 source of truth가 아니다.
                continue
        return WorkspaceInventory(values)

    def delete(self, project_id, identifier):
        with self.lease(project_id, identifier) as (directory, _):
            self.record(directory, project_id)
            # Linux의 fd 기반 rmtree는 산출물 symlink를 따라가지 않는다.
            if not shutil.rmtree.avoids_symlink_attacks:
                raise RuntimeError(self.UNSAFE_DELETE)
            shutil.rmtree(directory)
            sync_directory(directory.parent)
        return WorkspaceDeleted(identifier)

    def files(self, project_id, identifier, operation, args):
        with self.lease(project_id, identifier) as (directory, _):
            self.record(directory, project_id, require_ready=self.REQUIRE_READY)
            files = self.file_tools(directory).for_arguments(args)
            # 임의 명령이 만든 FIFO/device를 file_read가 열어 대기하지 않게 한다.
            supplied_path = FilePathArguments.provided_path(args)
            if supplied_path is not UNSET:
                target = files.path(supplied_path)
                if target.exists() and not (target.is_file() or target.is_dir()):
                    raise ValueError(self.INVALID_OUTPUT)
            return getattr(files, operation)(args)

    def file_tools(self, directory):
        return FileTools(directory / self.FILES_DIRECTORY)
