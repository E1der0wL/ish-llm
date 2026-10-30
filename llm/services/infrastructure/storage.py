"""원자적 JSON 교체, 경로 검증, 안전한 삭제와 비동기 저장 실행을 제공한다. 취소되더라도 수락한 파일 작업은 끝까지 정리한다.

Shared filesystem operations and ordered background storage execution."""

import asyncio
import json
import hashlib
import os
import re
import shutil
import stat
import tempfile
from functools import wraps
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from llm.core.models import DomainRecord, DOMAIN_STORAGE_VERSION, validate_domain_version
from .durability import sync_directory as durable_directory
from llm.services.infrastructure.locking import WorkspaceOwnership
from llm.services.infrastructure.transactions import NO_TRANSACTION, current_transaction, watch


_Result = TypeVar("_Result")


class StorageFormat:
    ENCODING = "utf-8"
    BINARY_READ = "rb"
    BINARY_WRITE = "wb"
    BINARY_APPEND = "ab"
    HASH_NAME = "sha256"
    OBJECT_ID = r"[a-f0-9]{32}"
    INVALID_ID = "Invalid object ID"
    REVISION_CONFLICT = "edit_conflict: stored data changed; reload before saving"
    INVALID_SNAPSHOT = "Snapshot requires regular files"
    INVALID_BYTES = "Atomic byte storage requires bytes"
    LINKED_PATH = "Storage paths cannot follow linked paths"
    INVALID_DIRECTORY = "Invalid component directory"
    MISMATCHED_TARGET = "Deletion target does not match the owned object"
    LINKED_DELETION = "Deletion through linked paths is not allowed"
    ESCAPED_DELETION = "Deletion target escapes its owner"
    NON_DIRECTORY_DELETION = "Deletion target must be a directory"
    LINKED_CONTENTS = "Remove linked contents before permanent deletion"
    LINKED_JOURNAL = "Deletion journal cannot follow links"
    LINKED_INTENT = "Deletion intent cannot follow links"
    LINKED_RECOVERY = "Deletion recovery cannot follow links"
    PROTECTED_TARGET = "Deletion intent targets a protected object"
    UNAPPROVED_TARGET = "Deletion intent is outside the approved operation"
    TEMPORARY_PREFIX = ".{name}."
    UNSUPPORTED_DOMAIN = (
        "Unsupported domain storage_version: {name}; expected {version}. "
        "Use a new workspace or an explicitly upgraded backup"
    )
    CREATE_PARENTS = True
    EXIST_OK = True
    MISSING_OK = True
    STRICT_PATHS = True
    SORT_KEYS = True
    ENSURE_ASCII = False
    ALLOW_NAN = False
    FOLLOW_LINKS = False
    NO_REVISION = None
    UNRESTRICTED_TARGETS = None


class StorageDrain:
    PENDING = False
    CANCELLED = True


class SnapshotFile:
    BYTES = "bytes"
    DIGEST = "digest"

    @classmethod
    def create(cls, size, digest):
        return {cls.BYTES: size, cls.DIGEST: digest}

    @classmethod
    def total_bytes(cls, snapshots):
        return sum(value[cls.BYTES] for value in snapshots.values())


class DeletionJournal:
    DIRECTORY = ".deletions"
    SUFFIX = ".json"
    RECORDS = "*.json"

    @classmethod
    def intent_path(cls, journal, name):
        return journal / (DeletionIntent.identity(name) + cls.SUFFIX)


class DeletionIntent:
    NAME = "name"
    OPERATION = "operation"
    DELETE = "delete"
    NAME_PATTERN = r"[A-Za-z0-9_-]{1,64}"
    INVALID = "Invalid deletion intent"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, name):
        return cls({cls.NAME: name, cls.OPERATION: cls.DELETE})

    @staticmethod
    def identity(name):
        return hashlib.sha256(name.encode()).hexdigest()

    @classmethod
    def read(cls, value, identity):
        name = value[cls.NAME]
        if (
            not re.fullmatch(cls.NAME_PATTERN, name)
            or value.get(cls.OPERATION) != cls.DELETE
            or identity != cls.identity(name)
        ):
            raise ValueError(cls.INVALID)
        return cls(value)

    @property
    def name(self):
        return self._value[self.NAME]

    @property
    def record(self):
        return self._value


# Metadata serialization and durable filesystem primitives.


def revision_token(value: dict) -> str:
    """열린 JSON을 바꾸지 않는 낙관적 동시 편집 토큰. 확인과 쓰기는 같은 소유권 잠금 안에서 한다."""
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=StorageFormat.SORT_KEYS,
            ensure_ascii=StorageFormat.ENSURE_ASCII,
            allow_nan=StorageFormat.ALLOW_NAN,
        ).encode()
    ).hexdigest()


def check_revision(value: dict, expected_version) -> None:
    if expected_version is not StorageFormat.NO_REVISION and expected_version != revision_token(value):
        raise ValueError(StorageFormat.REVISION_CONFLICT)


def tree_snapshot(root: Path, *, exclude=()) -> dict:
    """명시적 정리의 CAS 입력. 잠금 아래 읽으며 링크를 거부하고 파일 내용을 해시한다."""
    reject_links(root)
    if not root.exists():
        return {}
    result = {}

    def fail(error):
        raise error

    for directory, names, files in os.walk(root, onerror=fail, followlinks=StorageFormat.FOLLOW_LINKS):
        for name in (*names, *files):
            reject_links(Path(directory) / name)
        for name in files:
            path = Path(directory) / name
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError(StorageFormat.INVALID_SNAPSHOT)
            relative = path.relative_to(root).as_posix()
            if relative in exclude:
                continue
            with path.open(StorageFormat.BINARY_READ) as stream:
                digest = hashlib.file_digest(stream, StorageFormat.HASH_NAME).hexdigest()
                size = path.stat().st_size
            result[relative] = SnapshotFile.create(size, digest)
    return result


def child(root: Path, identifier: str) -> Path:
    if not re.fullmatch(StorageFormat.OBJECT_ID, identifier):
        raise ValueError(StorageFormat.INVALID_ID)
    return root / identifier


def sync_directory(path: Path) -> None:
    durable_directory(path)


def atomic_json(path: Path, value: dict) -> None:
    # Serialize before touching the destination, including on invalid metadata.
    data = json.dumps(
        value, ensure_ascii=StorageFormat.ENSURE_ASCII, allow_nan=StorageFormat.ALLOW_NAN
    ).encode(StorageFormat.ENCODING)
    atomic_bytes(path, data)


def atomic_bytes(path: Path, data: bytes) -> None:
    """Replace owned bytes with the same transaction/fsync contract as JSON."""
    if not isinstance(data, bytes):
        raise TypeError(StorageFormat.INVALID_BYTES)
    batch = record_transaction(path)
    if batch is not None:
        batch.write(path, data, append=False)
        return
    prepare_replace(path)
    transaction = current_transaction()
    owned = transaction is not NO_TRANSACTION and transaction.owns(path)
    path.parent.mkdir(parents=StorageFormat.CREATE_PARENTS, exist_ok=StorageFormat.EXIST_OK)
    fd, temporary = temporary_file(path)
    try:
        with os.fdopen(fd, StorageFormat.BINARY_WRITE) as stream:
            stream.write(data)
            stream.flush()
            if not owned:
                os.fsync(stream.fileno())
        os.replace(temporary, path)
        if owned:
            transaction.defer_sync(path, directory=True)
        else:
            sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=StorageFormat.MISSING_OK)


def temporary_file(destination: Path):
    transaction = current_transaction()
    if transaction is not NO_TRANSACTION and transaction.owns(destination):
        return transaction.temporary_file()
    return tempfile.mkstemp(
        prefix=StorageFormat.TEMPORARY_PREFIX.format(name=destination.name), dir=destination.parent
    )


def prepare_replace(path: Path) -> None:
    """원자 교체/삭제 전 기존 파일을 공통 트랜잭션에 참여시킨다."""
    transaction = current_transaction()
    if transaction is not NO_TRANSACTION and transaction.owns(path):
        transaction.before_replace(path)


def make_directory(path: Path) -> None:
    transaction = current_transaction()
    if transaction is not NO_TRANSACTION and transaction.owns(path):
        transaction.mkdir(path)
    else:
        path.mkdir(parents=StorageFormat.CREATE_PARENTS, exist_ok=StorageFormat.EXIST_OK)


def prepare_create(path: Path) -> None:
    """호출자가 생성/복사할 신규 트리를 확정 전에는 되돌릴 수 있게 한다."""
    transaction = current_transaction()
    if transaction is not NO_TRANSACTION and transaction.owns(path):
        transaction.prepare_create(path)


def append_bytes(path: Path, data: bytes) -> None:
    if not data:
        return
    batch = record_transaction(path)
    if batch is not None:
        batch.write(path, data, append=True)
        return
    transaction = current_transaction()
    owned = transaction is not NO_TRANSACTION and transaction.owns(path)
    if owned:
        transaction.before_append(path)
    first = not path.exists()
    path.parent.mkdir(parents=StorageFormat.CREATE_PARENTS, exist_ok=StorageFormat.EXIST_OK)
    with path.open(StorageFormat.BINARY_APPEND) as stream:
        stream.write(data)
        stream.flush()
        if not owned:
            os.fsync(stream.fileno())
    if owned:
        transaction.defer_sync(path, directory=first)
    elif first:
        sync_directory(path.parent)


def unlink_file(path: Path) -> None:
    prepare_replace(path)
    path.unlink()
    sync_directory(path.parent)


def read_json(path: Path) -> dict:
    with open_record(path) as stream:
        return json.loads(stream.read().decode(StorageFormat.ENCODING))


def record_transaction(path):
    transaction = current_transaction()
    if transaction is not None and transaction.record_batch and transaction.owns(path):
        return transaction
    return None


def open_record(path):
    """Read current transaction bytes, including staged appends, without copying history."""
    batch = record_transaction(path)
    return batch.open(path) if batch is not None else path.open(StorageFormat.BINARY_READ)


def record_stat(path):
    batch = record_transaction(path)
    return batch.stat(path) if batch is not None else path.stat()


def record_exists(path):
    batch = record_transaction(path)
    return batch.exists(path) if batch is not None else path.exists()


def record_children(path):
    batch = record_transaction(path)
    return batch.children(path) if batch is not None else path.iterdir()


def record_directory(path):
    batch = record_transaction(path)
    if batch is not None:
        return path.absolute() in batch.directories or path.is_dir()
    return path.is_dir()


def reject_links(path: Path) -> Path:
    """소유권 잠금 아래에서 사용하는 경로 사전 검사. OS 격리 경계를 대체하지 않는다."""
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError(StorageFormat.LINKED_PATH)
    return path


def read_domain_record(path: Path) -> dict:
    """지원하지 않는 저장 형식은 로드/수정 전에 거부한다. 암묵적인 변환은 하지 않는다."""
    data = read_json(path)
    try:
        validate_domain_version(data)
    except ValueError:
        raise ValueError(
            StorageFormat.UNSUPPORTED_DOMAIN.format(name=path.name, version=DOMAIN_STORAGE_VERSION)
        ) from None
    return data


def atomic_domain_json(path: Path, data: dict) -> None:
    """오래된 핸들도 새 저장 버전의 파일을 덮어쓰지 못하게 한다."""
    validate_domain_version(data)
    if record_exists(path):
        read_domain_record(path)
    atomic_json(path, data)


def record(model: object) -> dict:
    return DomainRecord.serialize(model)


# Validated permanent removal of an owned domain directory.


def remove_owned_tree(owner: Path, target: Path, identifier: str) -> None:
    """Delete one identified child, rejecting path escapes and linked contents.

    This preflight assumes exclusive workspace ownership; it is not a defense
    against another process swapping paths between validation and removal.
    """
    expected = child(owner, identifier).absolute()
    _remove_tree(owner, target, expected)


def remove_named_tree(owner: Path, target: Path, directory: str) -> None:
    """Remove a component's named direct child using domain deletion checks."""
    if not re.fullmatch(DeletionIntent.NAME_PATTERN, directory):
        raise ValueError(StorageFormat.INVALID_DIRECTORY)
    _remove_tree(owner, target, (owner / directory).absolute())


def _remove_tree(owner: Path, target: Path, expected: Path) -> None:
    if target.absolute() != expected:
        raise ValueError(StorageFormat.MISMATCHED_TARGET)
    # Reject symlink ancestors before touching the owned tree.
    for path in (target, *target.parents):
        if path.is_symlink():
            raise ValueError(StorageFormat.LINKED_DELETION)
    resolved_owner = owner.resolve(strict=StorageFormat.STRICT_PATHS)
    resolved_target = target.resolve(strict=StorageFormat.STRICT_PATHS)
    if resolved_target.parent != resolved_owner or resolved_target == resolved_owner:
        raise ValueError(StorageFormat.ESCAPED_DELETION)
    if not resolved_target.is_dir():
        raise ValueError(StorageFormat.NON_DIRECTORY_DELETION)

    def fail(error: OSError) -> None:
        raise error

    for directory, names, files in os.walk(
        resolved_target, followlinks=StorageFormat.FOLLOW_LINKS, onerror=fail
    ):
        for name in (*names, *files):
            entry = Path(directory) / name
            if entry.is_symlink():
                raise ValueError(StorageFormat.LINKED_CONTENTS)
    transaction = current_transaction()
    if transaction is not NO_TRANSACTION and transaction.owns(resolved_target):
        transaction.remove(resolved_target)
        return
    # Absolute target and containment have been verified above.
    journal = resolved_owner / DeletionJournal.DIRECTORY
    if journal.is_symlink():
        raise ValueError(StorageFormat.LINKED_JOURNAL)
    intent = DeletionJournal.intent_path(journal, resolved_target.name)
    if intent.is_symlink():
        raise ValueError(StorageFormat.LINKED_INTENT)
    atomic_json(intent, DeletionIntent.create(resolved_target.name).record)
    shutil.rmtree(resolved_target)
    sync_directory(resolved_owner)
    intent.unlink()
    sync_directory(journal)
    if not any(journal.iterdir()):
        journal.rmdir()
        sync_directory(resolved_owner)


def recover_deletions(owner: Path, *, protected=(), allowed=StorageFormat.UNRESTRICTED_TARGETS) -> list[str]:
    """이미 승인·시작된 삭제만 완료한다. 새 경로나 외부 경로를 삭제 대상으로 받지 않는다."""
    for path in (owner, *owner.parents, owner / DeletionJournal.DIRECTORY):
        if path.is_symlink():
            raise ValueError(StorageFormat.LINKED_RECOVERY)
    recovered = []
    for intent in list((owner / DeletionJournal.DIRECTORY).glob(DeletionJournal.RECORDS)):
        if intent.is_symlink():
            raise ValueError(StorageFormat.LINKED_RECOVERY)
        entry = DeletionIntent.read(read_json(intent), intent.stem)
        name = entry.name
        if name in protected:
            raise ValueError(StorageFormat.PROTECTED_TARGET)
        if allowed is not StorageFormat.UNRESTRICTED_TARGETS and name not in allowed:
            raise ValueError(StorageFormat.UNAPPROVED_TARGET)
        target = owner / name
        if target.exists() or target.is_symlink():
            remove_named_tree(owner, target, name)
        if intent.exists():
            unlink_file(intent)
        recovered.append(name)
    journal = owner / DeletionJournal.DIRECTORY
    if journal.exists() and not any(journal.iterdir()):
        transaction = current_transaction()
        if transaction is not NO_TRANSACTION and transaction.owns(journal):
            transaction.remove(journal)
        else:
            journal.rmdir()
            sync_directory(owner)
    return recovered


# Ordered background storage and cancellation draining.


def async_method(method: Callable[..., _Result]) -> Callable[..., Awaitable[_Result]]:
    """Expose an off-loop variant, preserving the synchronous method signature.

    The owner supplies _async_call to enforce lifecycle, loop and drain rules.
    Resolve by name at invocation so subclass overrides remain effective.
    """

    @wraps(method)
    async def call(self, *args, **kwargs):
        return await self._async_call(getattr(self, method.__name__), *args, **kwargs)

    return call


# 동기 파일 트랜잭션을 스레드로 옮기고 취소 시 정리 완료를 보장한다.
class StorageIO:
    """One in-flight transaction per manager, no unbounded executor backlog.

    Cancellation drains the submitted operation before propagating. A worker
    thread cannot be forcibly stopped midway through a durable transaction.
    """

    UNBOUND = None

    def __init__(self, ownership: WorkspaceOwnership) -> None:
        self.ownership = ownership
        self._serial = self.UNBOUND

    async def run(self, operation, *args, **kwargs):
        return await self._run(getattr(operation, '__record_batch__', False), operation, args, kwargs)

    async def run_records(self, operation, *args, **kwargs):
        """Commit record writes as one durable bundle; no raw filesystem mutations."""
        return await self._run(True, operation, args, kwargs)

    async def _run(self, records, operation, args, kwargs):
        if self._serial is self.UNBOUND:
            self._serial = asyncio.Lock()
        async with self._serial:
            from .processes import ProcessCancellation, current_process_cancellation

            cancellation = current_process_cancellation() or ProcessCancellation()

            def work():
                with cancellation.scope(), self.ownership.scope(records=records):
                    for value in (*args, *kwargs.values()):
                        watch(value)
                    return operation(*args, **kwargs)

            pending = asyncio.ensure_future(asyncio.to_thread(work))
            cancelled = StorageDrain.PENDING
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    cancelled = StorageDrain.CANCELLED
                    cancellation.cancel()
            # Surface storage failures even if cancellation arrived meanwhile.
            result = pending.result()
            if cancelled:
                raise asyncio.CancelledError
            return result


async def drain_on_cancel(awaitable):
    """Complete an accepted orchestration action before forwarding cancellation."""
    pending = asyncio.ensure_future(awaitable)
    cancelled = StorageDrain.PENDING
    while not pending.done():
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            cancelled = StorageDrain.CANCELLED
    result = pending.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
