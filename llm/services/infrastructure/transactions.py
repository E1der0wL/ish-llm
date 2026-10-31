"""잠금 안의 동기 저장 단위를 undo 저널로 묶는다. 실행 효과는 재생하지 않는다.

쓰기 전에 복구 정보를 fsync하고 확정 마커 전 장애는 역순으로 되돌린다.
JSON 교체는 이전 inode, JSONL 추가는 이전 길이만 보관한다. 외부 독자는 반드시
같은 WorkspaceOwnership을 사용해야 한다. 이 계층은 DB/네트워크 트랜잭션이 아니다.
"""

import json
import os
import re
import shutil
import stat
import tempfile
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional
from uuid import uuid4

from llm.core.paths import WorkspacePaths
from .durability import sync_directory as durable_directory
from .transaction_records import TransactionJournal, UndoEnvelope, UndoKind, UndoOperation


NO_TRANSACTION = None
TRANSACTION_CONTEXT = "llm_storage_transaction"
DATACLASS_PARAMETERS = "__dataclass_params__"

_current = ContextVar(TRANSACTION_CONTEXT, default=NO_TRANSACTION)


class TransactionStorage:
    INVALID_PATH = "Transaction paths cannot escape or follow links"
    INVALID_CREATION = "Transaction creation supports regular files and directories only"
    INVALID_REPLACEMENT = "Transaction replacement requires a regular file"
    INVALID_APPEND = "Transaction append requires a regular file"


def current_transaction() -> Optional["Transaction"]:
    return _current.get()


def _sync(path):
    durable_directory(path)


def _checked(path):
    path = Path(path).absolute()
    if TransactionJournal.PARENT in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(TransactionStorage.INVALID_PATH)
    return path


def _relative(path, root):
    # These are lexical paths, not cached filesystem authority. Avoid building
    # every ancestor Path merely to remove a known absolute root prefix.
    parts, prefix = path.parts, root.parts
    if parts[:len(prefix)] == prefix:
        return Path(*parts[len(prefix):])
    return path.relative_to(root)


def _write(path, data):
    fd, name = tempfile.mkstemp(prefix=TransactionJournal.WRITE_PREFIX, dir=path.parent)
    try:
        with os.fdopen(fd, TransactionJournal.BINARY_WRITE) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        _sync(path.parent)
    finally:
        Path(name).unlink(missing_ok=TransactionJournal.MISSING_OK)


def after_commit(callback: Callable[[], None]) -> None:
    """관찰/캐시 해제는 저장 확정 후에만 수행한다."""
    transaction = current_transaction()
    if transaction is NO_TRANSACTION:
        callback()
    else:
        transaction.after_commit(callback)


def watch(model: object) -> None:
    """실패한 저장이 호출자가 보유한 도메인 객체까지 변경하지 않도록 한다."""
    transaction = current_transaction()
    if transaction is not NO_TRANSACTION:
        transaction.watch(model)


class Transaction:
    record_batch = False
    CLEAN = False
    FAILED = True
    NO_WRITES = 0
    WRITE = 1
    MODEL_PARTICIPANT = "model"

    def __init__(self, manager: "TransactionManager") -> None:
        self.manager = manager
        self.path = manager.journal / uuid4().hex
        self.entries = []
        self.created = []
        self.removed = []
        self._rollback = {}
        self._errors = {}
        self._committed = []
        self._prepared = {}
        self._dirty_files = set()
        self._dirty_directories = set()
        self.failed = self.CLEAN
        self.writes = self.NO_WRITES

    def _target(self, path):
        path = _checked(path)
        relative = _relative(path, self.manager.root)
        if WorkspacePaths.reserved(relative):
            raise ValueError(UndoOperation.RESERVED_TARGET)
        return path, relative.as_posix()

    def _ensure(self):
        if self.path.exists():
            return
        # Undo backups may depend on record projections. Make those projections
        # independently durable before the first general filesystem mutation.
        self.manager.records.checkpoint()
        self.manager._ensure_journal()
        self.path.mkdir(mode=TransactionJournal.DIRECTORY_MODE)
        _sync(self.manager.journal)

    def _entry(self, value):
        self._ensure()
        name = TransactionJournal.entry(len(self.entries))
        _write(self.path / name, UndoEnvelope.encode(value, self.manager.FORMAT_VERSION))
        self.entries.append(value)

    def _new(self, path):
        # 격리 후 같은 이름을 재생성하면 새 undo가 필요하다. 이전 신규 트리의
        # 제거 기록보다 재생성 제거가 먼저 실행되어야 원본 격리를 되돌릴 수 있다.
        return any(root == path or root in path.parents for root in self.created) and not any(
            root == path or root in path.parents for root in self.removed
        )

    def _sync_changes(self):
        # Undo is durable before mutation. Data/rename durability is needed at
        # commit, before COMMITTED and observers, not after each in-scope write.
        # Include raw component copies beneath newly created trees; existing
        # trees are never scanned. Flush each final file/directory once.
        files = set(self._dirty_files)
        directories = set(self._dirty_directories)
        for root in self.created:
            if not root.exists():
                continue
            paths = [root, *root.rglob(TransactionJournal.ALL_FILES)] if root.is_dir() else [root]
            for path in paths:
                mode = _checked(path).stat().st_mode
                if stat.S_ISREG(mode):
                    files.add(path)
                elif stat.S_ISDIR(mode):
                    directories.add(path)
                else:
                    raise ValueError(TransactionStorage.INVALID_CREATION)
            directories.add(root.parent)
        for path in sorted(files):
            if not path.exists():
                continue  # A later journalled removal superseded this write.
            mode = _checked(path).stat().st_mode
            if not stat.S_ISREG(mode):
                if path in directories and stat.S_ISDIR(mode):
                    continue
                raise ValueError(TransactionStorage.INVALID_CREATION)
            with path.open(TransactionJournal.BINARY_READ) as stream:
                os.fsync(stream.fileno())
        for path in sorted(directories, key=lambda p: (-len(p.parts), str(p))):
            if path.exists():
                path = _checked(path)
                if path.is_dir():
                    _sync(path)

    def defer_sync(self, path, *, directory=False):
        """Track successful storage writes until this transaction's commit."""
        path, _ = self._target(path)
        self._dirty_files.add(path)
        if directory:
            self._dirty_directories.add(path.parent)

    def _flush_file(self, path):
        # A later undo entry may depend on this intermediate inode/length.
        # Persist it before making a backup or changing its interpretation.
        if path in self._dirty_files:
            with path.open(TransactionJournal.BINARY_READ) as stream:
                os.fsync(stream.fileno())
            self._dirty_files.remove(path)
        self._flush_directories(path.parent)

    def _flush_directories(self, path):
        # A dependent undo may need to restore into a newly created ancestor.
        # That destination must survive a crash even before the final commit.
        # Include copied directories between it and the pending ancestor entry.
        pending = []
        for directory in (path, *path.parents):
            pending.append(directory)
            if directory in self._dirty_directories:
                for parent in pending:
                    _sync(parent)
                    self._dirty_directories.discard(parent)
                pending.clear()

    def _forget_prepared(self, path):
        # A removal/recreation changes inode identity, including descendants.
        self._prepared = {p: kind for p, kind in self._prepared.items()
                          if p != path and path not in p.parents}

    def _undo(self):
        # 매번 전체 저널을 검증한 후 수정한다. 복구 중 실패해도 같은 정보로 재시도한다.
        try:
            self.manager._rollback(self.path)
        finally:
            for callback in reversed(tuple(self._rollback.values())):
                callback()

    def commit(self):
        if self.path.exists():
            self._sync_changes()
            _write(self.path / TransactionJournal.COMMITTED, TransactionJournal.COMPLETE_MARKER)

    def is_committed(self):
        return (self.path / TransactionJournal.COMMITTED).exists()

    def discard(self):
        self._undo()
        self.manager._retire(self.path)

    def finish(self):
        self.manager._retire(self.path)

    def close(self):
        pass

    def owns(self, path: Path) -> bool:
        path = Path(path).absolute()
        parts = path.parts
        root, journal = self.manager.root.parts, self.manager.journal.parts
        # Strict descendants, just like membership in Path.parents. Filesystem
        # link checks still run in _target before any record is read or written.
        return (len(parts) > len(root) and parts[:len(root)] == root
                and not (len(parts) > len(journal) and parts[:len(journal)] == journal))

    def on_rollback(self, key, callback: Callable[[], None]) -> None:
        self._rollback.setdefault(key, callback)

    def on_error(self, key, callback: Callable[[], None]) -> None:
        """확정 여부가 불명확해도 반납해야 할 프로세스 자원에 사용한다."""
        self._errors.setdefault(key, callback)

    def changed(self) -> None:
        """파일 밖 참여자도 중첩 작업 실패를 바깥 확정에 전파한다."""
        self.writes += self.WRITE

    def after_commit(self, callback: Callable[[], None]) -> None:
        self._committed.append(callback)

    def staging_directory(self) -> Path:
        """큰 복원 사본은 도메인 목록에서 보이지 않는 저널 아래에 준비한다."""
        self._ensure()
        path = Path(tempfile.mkdtemp(prefix=TransactionJournal.STAGE_PREFIX, dir=self.path))
        _sync(self.path)
        return path

    def temporary_file(self) -> tuple[int, str]:
        """원자 교체의 임시 파일도 저널과 같이 회수되도록 배치한다."""
        self._ensure()
        return tempfile.mkstemp(prefix=TransactionJournal.VALUE_PREFIX, dir=self.path)

    def watch(self, model: object) -> None:
        from llm.core.models import Project

        if (
            not is_dataclass(model)
            or isinstance(model, type)
            or getattr(type(model), DATACLASS_PARAMETERS).frozen
        ):
            return
        key = (self.MODEL_PARTICIPANT, id(model))
        if key in self._rollback:
            return
        # SessionRuntime 등 asyncio 객체를 가진 실행 컨테이너는 대상이 아니다.
        if not hasattr(model, Project.paths.__name__):
            return
        snapshot = {field.name: deepcopy(getattr(model, field.name)) for field in fields(model)}

        def restore():
            for name, value in snapshot.items():
                setattr(model, name, value)

        self.on_rollback(key, restore)

    def mkdir(self, path: Path) -> None:
        path, _ = self._target(path)
        missing = []
        cursor = path
        while not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        for directory in reversed(missing):
            self.prepare_create(directory)
            directory.mkdir()
            # The CREATE undo is already durable. Until commit, losing a new
            # directory is equivalent to rollback; publish its directory entry
            # with the rest of the final tree, before COMMITTED/observers.
            self._dirty_directories.add(directory.parent)

    def prepare_create(self, path: Path) -> None:
        """신규 디렉토리 복사/초기화도 하나의 공개 단위에 참여시킨다."""
        path, name = self._target(path)
        if path.exists():
            raise FileExistsError(path)
        self._forget_prepared(path)
        self.changed()
        if not self._new(path):
            self._entry(UndoOperation.create(name))
            self.created.append(path)

    def before_replace(self, path: Path) -> None:
        path, name = self._target(path)
        self.changed()
        if path.parent != self.manager.root:
            self.mkdir(path.parent)
        if self._new(path):
            return
        if not path.exists():
            self.prepare_create(path)
            return
        if not path.is_file():
            raise ValueError(TransactionStorage.INVALID_REPLACEMENT)
        if self._prepared.get(path) == UndoKind.REPLACE:
            return
        self._flush_file(path)
        self._ensure()
        backup = TransactionJournal.backup(len(self.entries), UndoKind.REPLACE)
        os.link(path, self.path / backup)
        # _entry syncs both the backup link and undo entry before replacement.
        self._entry(UndoOperation.replace(name, backup))
        self._prepared[path] = UndoKind.REPLACE

    def before_append(self, path: Path) -> None:
        path, name = self._target(path)
        self.changed()
        if path.parent != self.manager.root:
            self.mkdir(path.parent)
        if self._new(path):
            return
        if not path.exists():
            self.prepare_create(path)
        else:
            if not path.is_file():
                raise ValueError(TransactionStorage.INVALID_APPEND)
            if self._prepared.get(path) == UndoKind.APPEND:
                return
            self._flush_file(path)
            self._entry(UndoOperation.append(name, path.stat().st_size))
            self._prepared[path] = UndoKind.APPEND

    def before_truncate(self, path: Path, size: int) -> None:
        """깨진 JSONL 끝을 수리할 때도 제거한 꼬리만 보관한다."""
        path, name = self._target(path)
        self.changed()
        self._forget_prepared(path)
        if self._new(path):
            return
        self._flush_file(path)
        self._ensure()
        backup = TransactionJournal.backup(len(self.entries), UndoKind.TRUNCATE)
        with path.open(TransactionJournal.BINARY_READ) as stream:
            stream.seek(size)
            data = stream.read()
        _write(self.path / backup, data)
        self._entry(UndoOperation.truncate(name, size, backup))

    def remove(self, path: Path) -> None:
        """큰 트리는 복사 없이 격리한다. 실제 제거는 확정 후 재시도 가능한 GC다."""
        path, name = self._target(path)
        self.changed()
        self._forget_prepared(path)
        for dirty in tuple(self._dirty_files):
            if dirty == path or path in dirty.parents:
                self._flush_file(dirty)
        self._flush_directories(path.parent)
        self._ensure()
        backup = TransactionJournal.backup(len(self.entries), UndoKind.REMOVE)
        self._entry(UndoOperation.remove(name, backup))
        self.removed.append(path)
        os.rename(path, self.path / backup)
        # 목적지 엔트리를 먼저 보존한 뒤 원래 위치의 제거를 확정한다.
        _sync(self.path)
        _sync(path.parent)


class TransactionManager:
    """WorkspaceOwnership 한 개가 소유한다. context는 동기이며 Engine 실행을 감싸지 않는다."""

    FORMAT_VERSION = UndoEnvelope.FORMAT_VERSION
    INVALID_SEQUENCE = "Invalid transaction journal sequence"
    INVALID_UNDO_MARKER = "Invalid transaction rollback marker"
    MISSING_APPEND_SOURCE = "Transaction append source is missing or shortened"
    REMOVAL_DESTINATION_CHANGED = "Transaction removal destination changed"
    LINKED_CLEANUP = "Linked contents in transaction cleanup"
    CLEANUP_WARNING = "Committed transaction cleanup will be retried"
    INVALID_DIRECTORY = "Invalid transaction journal directory"
    INVALID_COMMIT_MARKER = "Invalid transaction commit marker"
    CROSS_WORKSPACE = "Cross-workspace nested transactions are not supported"
    NESTED_FAILURE = "Nested storage operation failed; transaction aborted"
    OBSERVER_WARNING = "Post-commit observer failed"
    PENDING_COMMIT = False
    COMMITTED = True

    def __init__(self, root: Path) -> None:
        self.root = Path(root).absolute()
        self.journal = WorkspacePaths(self.root).transactions
        self._journal_ready = False
        from .record_journal import RecordJournal
        self.records = RecordJournal(self)

    def _ensure_journal(self):
        if not self._journal_ready:
            self.journal.mkdir(exist_ok=TransactionJournal.EXIST_OK,
                               mode=TransactionJournal.DIRECTORY_MODE)
            _sync(self.root)
            self._journal_ready = True

    def _entries(self, directory):
        _checked(directory)
        values = []
        for index, path in enumerate(sorted(directory.glob(TransactionJournal.RECORDS))):
            if path.name != TransactionJournal.entry(index):
                raise ValueError(TransactionManager.INVALID_SEQUENCE)
            envelope = json.loads(_checked(path).read_text())
            value = UndoEnvelope.read(envelope, self.FORMAT_VERSION)
            values.append(UndoOperation.read(value, self.root, directory, index, _checked))
        return values

    def _rollback(self, directory):
        if not directory.exists():
            return
        entries = self._entries(directory)
        for index in reversed(range(len(entries))):
            value, target = entries[index]
            done = directory / TransactionJournal.undone(index)
            _checked(done)
            if done.exists():
                if done.read_bytes() != TransactionJournal.COMPLETE_MARKER:
                    raise ValueError(TransactionManager.INVALID_UNDO_MARKER)
                continue
            kind = value.kind
            if kind == UndoKind.CREATE:
                if target.is_dir():
                    self._remove(target)
                else:
                    target.unlink(missing_ok=TransactionJournal.MISSING_OK)
            elif kind == UndoKind.REPLACE:
                # 백업은 복구 완료 때까지 유지한다. 복구 자체가 중단되어도 재시도 가능하다.
                fd, name = tempfile.mkstemp(prefix=TransactionJournal.RESTORE_PREFIX, dir=directory)
                os.close(fd)
                temporary = Path(name)
                try:
                    temporary.unlink()
                    os.link(directory / value.backup, temporary)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=TransactionJournal.MISSING_OK)
            elif kind == UndoKind.TRUNCATE:
                with target.open(TransactionJournal.BINARY_UPDATE) as stream:
                    stream.seek(value.size)
                    stream.write((directory / value.backup).read_bytes())
                    stream.truncate()
                    stream.flush()
                    os.fsync(stream.fileno())
            elif kind == UndoKind.APPEND:
                if not target.exists() or target.stat().st_size < value.size:
                    raise ValueError(TransactionManager.MISSING_APPEND_SOURCE)
                with target.open(TransactionJournal.BINARY_UPDATE) as stream:
                    stream.truncate(value.size)
                    stream.flush()
                    os.fsync(stream.fileno())
            else:
                backup = directory / value.backup
                if backup.exists():
                    if target.exists():
                        raise ValueError(TransactionManager.REMOVAL_DESTINATION_CHANGED)
                    os.rename(backup, target)
                    _sync(target.parent)
                    _sync(directory)
            if target.parent.exists():
                _sync(target.parent)
            _write(done, TransactionJournal.COMPLETE_MARKER)

    @staticmethod
    def _remove(path):
        _checked(path)
        # 같은 사용자에 의한 경로 교체를 격리하는 보안 경계는 아니다.
        if path.is_dir():
            if any(p.is_symlink() for p in path.rglob(TransactionJournal.ALL_FILES)):
                raise ValueError(TransactionManager.LINKED_CLEANUP)
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=TransactionJournal.MISSING_OK)

    def _retire(self, path):
        if not path.exists():
            return
        garbage = path.with_name(TransactionJournal.GC_PREFIX + path.name)
        os.rename(path, garbage)
        _sync(self.journal)
        try:
            self._remove(garbage)
            # The GC rename is already durable. A crash can resurrect only a
            # gc-* directory, which recover reaps without undoing committed data.
        except OSError:
            warnings.warn(TransactionManager.CLEANUP_WARNING, RuntimeWarning)

    def recover(self) -> None:
        """읽기 전 OS 잠금을 가진 호출자만 사용한다. LLM/Tool을 호출하지 않는다."""
        _checked(self.journal)
        self.records.recover()
        if not self.journal.exists():
            return
        for directory in sorted(self.journal.iterdir()):
            if self.records.owns(directory):
                continue
            if (
                not re.fullmatch(TransactionJournal.DIRECTORY_PATTERN, directory.name)
                or not directory.is_dir()
            ):
                raise ValueError(TransactionManager.INVALID_DIRECTORY)
            _checked(directory)
            if directory.name.startswith(TransactionJournal.GC_PREFIX):
                try:
                    self._remove(directory)
                    _sync(self.journal)
                except OSError:
                    warnings.warn(TransactionManager.CLEANUP_WARNING, RuntimeWarning)
                continue
            marker = directory / TransactionJournal.COMMITTED
            _checked(marker)
            if marker.exists():
                if marker.read_bytes() != TransactionJournal.COMPLETE_MARKER:
                    raise ValueError(TransactionManager.INVALID_COMMIT_MARKER)
            else:
                self._rollback(directory)
            self._retire(directory)

    @contextmanager
    def scope(self, *, records=False) -> Iterator[Transaction]:
        parent = current_transaction()
        if parent is not NO_TRANSACTION:
            if parent.manager is not self:
                raise RuntimeError(TransactionManager.CROSS_WORKSPACE)
            count = parent.writes
            try:
                yield parent
            except BaseException:
                if parent.writes != count:
                    parent.failed = Transaction.FAILED
                raise
            return
        self.recover()
        if records:
            from .record_transactions import RecordTransaction
            transaction = RecordTransaction(self)
        else:
            transaction = Transaction(self)
        token = _current.set(transaction)
        committed = self.PENDING_COMMIT
        try:
            yield transaction
            if transaction.failed:
                raise RuntimeError(TransactionManager.NESTED_FAILURE)
            transaction.commit()
            committed = self.COMMITTED
        except BaseException:
            # rename 이후 fsync 오류는 확정 여부가 불명확하다. 이미 공개된 마커를
            # 되돌리지 않고 다음 소유권 진입에서 판정한다. 실행 효과는 재시도하지 않는다.
            try:
                if not transaction.is_committed():
                    transaction.discard()
            finally:
                for callback in reversed(tuple(transaction._errors.values())):
                    callback()
            raise
        finally:
            _current.reset(token)
            transaction.close()
        if committed:
            try:
                transaction.finish()
            except OSError:
                warnings.warn(TransactionManager.CLEANUP_WARNING, RuntimeWarning)
            for callback in transaction._committed:
                try:
                    callback()
                except Exception:
                    warnings.warn(TransactionManager.OBSERVER_WARNING, RuntimeWarning)
