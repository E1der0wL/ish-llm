"""잠금 안의 동기 저장 단위를 undo 저널로 묶는다. 실행 효과는 재생하지 않는다.

쓰기 전에 복구 정보를 fsync하고 확정 마커 전 장애는 역순으로 되돌린다.
JSON 교체는 이전 inode, JSONL 추가는 이전 길이만 보관한다. 외부 독자는 반드시
같은 WorkspaceOwnership을 사용해야 한다. 이 계층은 DB/네트워크 트랜잭션이 아니다.
"""

import json
import hashlib
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


_current = ContextVar("llm_storage_transaction", default=None)


def current_transaction() -> Optional["Transaction"]:
    return _current.get()


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _checked(path):
    path = Path(path).absolute()
    if ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Transaction paths cannot escape or follow links")
    return path


def _write(path, data):
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        _sync(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def after_commit(callback: Callable[[], None]) -> None:
    """관찰/캐시 해제는 저장 확정 후에만 수행한다."""
    transaction = current_transaction()
    if transaction is None:
        callback()
    else:
        transaction.after_commit(callback)


def watch(model: object) -> None:
    """실패한 저장이 호출자가 보유한 도메인 객체까지 변경하지 않도록 한다."""
    transaction = current_transaction()
    if transaction is not None:
        transaction.watch(model)


class Transaction:
    def __init__(self, manager: "TransactionManager") -> None:
        self.manager = manager
        self.path = manager.journal / uuid4().hex
        self.entries = []
        self.created = []
        self.removed = []
        self._rollback = {}
        self._errors = {}
        self._committed = []
        self.failed = False
        self.writes = 0

    def _target(self, path):
        path = _checked(path)
        relative = path.relative_to(self.manager.root)
        if not relative.parts or relative.parts[0] in (".transactions", ".ish.lock"):
            raise ValueError("Reserved transaction target")
        return path, relative.as_posix()

    def _ensure(self):
        if self.path.exists():
            return
        self.manager.journal.mkdir(exist_ok=True, mode=0o700)
        _sync(self.manager.root)
        self.path.mkdir(mode=0o700)
        _sync(self.manager.journal)

    def _entry(self, value):
        self._ensure()
        name = f"{len(self.entries):08d}.json"
        payload = json.dumps(value, sort_keys=True, allow_nan=False).encode()
        envelope = {"format_version": 1, "operation": value,
                    "checksum": hashlib.sha256(payload).hexdigest()}
        _write(self.path / name, json.dumps(envelope, allow_nan=False).encode())
        self.entries.append(value)

    def _new(self, path):
        # 격리 후 같은 이름을 재생성하면 새 undo가 필요하다. 이전 신규 트리의
        # 제거 기록보다 재생성 제거가 먼저 실행되어야 원본 격리를 되돌릴 수 있다.
        return (any(root == path or root in path.parents for root in self.created)
                and not any(root == path or root in path.parents for root in self.removed))

    def _sync_created(self):
        # 컴포넌트 초기화/clone이 복사한 파일도 마커 전에 내구성을 확보한다.
        # 기존 트리는 순회하지 않는다. 새 트리/파일만 대상이다.
        for root in self.created:
            if not root.exists():
                continue
            paths = [root, *root.rglob("*")] if root.is_dir() else [root]
            for path in paths:
                mode = _checked(path).stat().st_mode
                if stat.S_ISREG(mode):
                    with path.open("rb") as stream:
                        os.fsync(stream.fileno())
                elif not stat.S_ISDIR(mode):
                    raise ValueError("Transaction creation supports regular files and directories only")
            for path in reversed(paths):
                if path.is_dir():
                    _sync(path)
            _sync(root.parent)

    def _undo(self):
        # 매번 전체 저널을 검증한 후 수정한다. 복구 중 실패해도 같은 정보로 재시도한다.
        try:
            self.manager._rollback(self.path)
        finally:
            for callback in reversed(tuple(self._rollback.values())):
                callback()

    def owns(self, path: Path) -> bool:
        path = Path(path).absolute()
        return self.manager.root in path.parents and self.manager.journal not in path.parents

    def on_rollback(self, key, callback: Callable[[], None]) -> None:
        self._rollback.setdefault(key, callback)

    def on_error(self, key, callback: Callable[[], None]) -> None:
        """확정 여부가 불명확해도 반납해야 할 프로세스 자원에 사용한다."""
        self._errors.setdefault(key, callback)

    def changed(self) -> None:
        """파일 밖 참여자도 중첩 작업 실패를 바깥 확정에 전파한다."""
        self.writes += 1

    def after_commit(self, callback: Callable[[], None]) -> None:
        self._committed.append(callback)

    def staging_directory(self) -> Path:
        """큰 복원 사본은 도메인 목록에서 보이지 않는 저널 아래에 준비한다."""
        self._ensure()
        path = Path(tempfile.mkdtemp(prefix="stage-", dir=self.path))
        _sync(self.path)
        return path

    def temporary_file(self) -> tuple[int, str]:
        """원자 교체의 임시 파일도 저널과 같이 회수되도록 배치한다."""
        self._ensure()
        return tempfile.mkstemp(prefix="value-", dir=self.path)

    def watch(self, model: object) -> None:
        if not is_dataclass(model) or isinstance(model, type) or getattr(type(model), "__dataclass_params__").frozen:
            return
        key = ("model", id(model))
        if key in self._rollback:
            return
        # TaskRuntime 등 asyncio 객체를 가진 실행 컨테이너는 대상이 아니다.
        if not hasattr(model, "paths"):
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
            _sync(directory.parent)

    def prepare_create(self, path: Path) -> None:
        """신규 디렉토리 복사/초기화도 하나의 공개 단위에 참여시킨다."""
        path, name = self._target(path)
        if path.exists():
            raise FileExistsError(path)
        self.changed()
        if not self._new(path):
            self._entry({"kind": "create", "path": name})
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
            raise ValueError("Transaction replacement requires a regular file")
        self._ensure()
        backup = f"{len(self.entries):08d}.data"
        os.link(path, self.path / backup)
        _sync(self.path)
        self._entry({"kind": "replace", "path": name, "backup": backup})

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
                raise ValueError("Transaction append requires a regular file")
            self._entry({"kind": "append", "path": name, "size": path.stat().st_size})

    def before_truncate(self, path: Path, size: int) -> None:
        """깨진 JSONL 끝을 수리할 때도 제거한 꼬리만 보관한다."""
        path, name = self._target(path)
        self.changed()
        if self._new(path):
            return
        self._ensure()
        backup = f"{len(self.entries):08d}.tail"
        with path.open("rb") as stream:
            stream.seek(size)
            data = stream.read()
        _write(self.path / backup, data)
        self._entry({"kind": "truncate", "path": name, "size": size, "backup": backup})

    def remove(self, path: Path) -> None:
        """큰 트리는 복사 없이 격리한다. 실제 제거는 확정 후 재시도 가능한 GC다."""
        path, name = self._target(path)
        self.changed()
        self._ensure()
        backup = f"{len(self.entries):08d}.removed"
        self._entry({"kind": "remove", "path": name, "backup": backup})
        self.removed.append(path)
        os.rename(path, self.path / backup)
        # 목적지 엔트리를 먼저 보존한 뒤 원래 위치의 제거를 확정한다.
        _sync(self.path)
        _sync(path.parent)


class TransactionManager:
    """WorkspaceOwnership 한 개가 소유한다. context는 동기이며 Engine 실행을 감싸지 않는다."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).absolute()
        self.journal = self.root / ".transactions"

    def _entries(self, directory):
        _checked(directory)
        values = []
        for index, path in enumerate(sorted(directory.glob("*.json"))):
            if path.name != f"{index:08d}.json":
                raise ValueError("Invalid transaction journal sequence")
            envelope = json.loads(_checked(path).read_text())
            if type(envelope.get("format_version")) is not int or envelope["format_version"] != 1:
                raise ValueError("Unsupported transaction journal format")
            value = envelope["operation"]
            payload = json.dumps(value, sort_keys=True, allow_nan=False).encode()
            if envelope.get("checksum") != hashlib.sha256(payload).hexdigest():
                raise ValueError("Transaction journal checksum mismatch")
            name, kind = value.get("path"), value.get("kind")
            if not isinstance(name, str) or Path(name).is_absolute():
                raise ValueError("Invalid transaction target")
            target = _checked(self.root / name)
            relative = target.relative_to(self.root)
            if not relative.parts or relative.parts[0] in (".transactions", ".ish.lock"):
                raise ValueError("Reserved transaction target")
            if kind not in ("create", "replace", "append", "remove", "truncate"):
                raise ValueError("Invalid transaction operation")
            if kind in ("append", "truncate") and (type(value.get("size")) is not int or value["size"] < 0):
                raise ValueError("Invalid append recovery offset")
            if kind in ("replace", "remove", "truncate"):
                suffix = {"replace": "data", "remove": "removed", "truncate": "tail"}[kind]
                if value.get("backup") != f"{index:08d}.{suffix}":
                    raise ValueError("Invalid transaction backup")
                _checked(directory / value["backup"])
            if kind in ("replace", "truncate") and not (directory / value["backup"]).is_file():
                raise ValueError("Missing transaction backup")
            values.append((value, target))
        return values

    def _rollback(self, directory):
        if not directory.exists():
            return
        entries = self._entries(directory)
        for index in reversed(range(len(entries))):
            value, target = entries[index]
            done = directory / f"undone-{index:08d}"
            _checked(done)
            if done.exists():
                if done.read_bytes() != b"1\n":
                    raise ValueError("Invalid transaction rollback marker")
                continue
            kind = value["kind"]
            if kind == "create":
                if target.is_dir():
                    self._remove(target)
                else:
                    target.unlink(missing_ok=True)
            elif kind == "replace":
                # 백업은 복구 완료 때까지 유지한다. 복구 자체가 중단되어도 재시도 가능하다.
                fd, name = tempfile.mkstemp(prefix=".restore-", dir=directory)
                os.close(fd)
                temporary = Path(name)
                try:
                    temporary.unlink()
                    os.link(directory / value["backup"], temporary)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            elif kind == "truncate":
                with target.open("r+b") as stream:
                    stream.seek(value["size"])
                    stream.write((directory / value["backup"]).read_bytes())
                    stream.truncate()
                    stream.flush()
                    os.fsync(stream.fileno())
            elif kind == "append":
                if not target.exists() or target.stat().st_size < value["size"]:
                    raise ValueError("Transaction append source is missing or shortened")
                with target.open("r+b") as stream:
                    stream.truncate(value["size"])
                    stream.flush()
                    os.fsync(stream.fileno())
            else:
                backup = directory / value["backup"]
                if backup.exists():
                    if target.exists():
                        raise ValueError("Transaction removal destination changed")
                    os.rename(backup, target)
                    _sync(target.parent)
                    _sync(directory)
            if target.parent.exists():
                _sync(target.parent)
            _write(done, b"1\n")

    @staticmethod
    def _remove(path):
        _checked(path)
        # 같은 사용자에 의한 경로 교체를 격리하는 보안 경계는 아니다.
        if path.is_dir():
            if any(p.is_symlink() for p in path.rglob("*")):
                raise ValueError("Linked contents in transaction cleanup")
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def _retire(self, path):
        if not path.exists():
            return
        garbage = path.with_name("gc-" + path.name)
        os.rename(path, garbage)
        _sync(self.journal)
        try:
            self._remove(garbage)
            _sync(self.journal)
        except OSError:
            warnings.warn("Committed transaction cleanup will be retried", RuntimeWarning)

    def recover(self) -> None:
        """읽기 전 OS 잠금을 가진 호출자만 사용한다. LLM/Tool을 호출하지 않는다."""
        _checked(self.journal)
        if not self.journal.exists():
            return
        for directory in sorted(self.journal.iterdir()):
            if not re.fullmatch(r"(?:gc-)?[a-f0-9]{32}", directory.name) or not directory.is_dir():
                raise ValueError("Invalid transaction journal directory")
            _checked(directory)
            if directory.name.startswith("gc-"):
                try:
                    self._remove(directory)
                    _sync(self.journal)
                except OSError:
                    warnings.warn("Committed transaction cleanup will be retried", RuntimeWarning)
                continue
            marker = directory / "COMMITTED"
            _checked(marker)
            if marker.exists():
                if marker.read_bytes() != b"1\n":
                    raise ValueError("Invalid transaction commit marker")
            else:
                self._rollback(directory)
            self._retire(directory)

    @contextmanager
    def scope(self) -> Iterator[Transaction]:
        parent = current_transaction()
        if parent is not None:
            if parent.manager is not self:
                raise RuntimeError("Cross-workspace nested transactions are not supported")
            count = parent.writes
            try:
                yield parent
            except BaseException:
                if parent.writes != count:
                    parent.failed = True
                raise
            return
        self.recover()
        transaction = Transaction(self)
        token = _current.set(transaction)
        committed = False
        try:
            yield transaction
            if transaction.failed:
                raise RuntimeError("Nested storage operation failed; transaction aborted")
            if transaction.path.exists():
                transaction._sync_created()
                _write(transaction.path / "COMMITTED", b"1\n")
            committed = True
        except BaseException:
            # rename 이후 fsync 오류는 확정 여부가 불명확하다. 이미 공개된 마커를
            # 되돌리지 않고 다음 소유권 진입에서 판정한다. 실행 효과는 재시도하지 않는다.
            try:
                if not (transaction.path / "COMMITTED").exists():
                    transaction._undo()
                    self._retire(transaction.path)
            finally:
                for callback in reversed(tuple(transaction._errors.values())):
                    callback()
            raise
        finally:
            _current.reset(token)
        if committed:
            try:
                self._retire(transaction.path)
            except OSError:
                warnings.warn("Committed transaction cleanup will be retried", RuntimeWarning)
            for callback in transaction._committed:
                try:
                    callback()
                except Exception:
                    warnings.warn("Post-commit observer failed", RuntimeWarning)
