"""원자적 JSON 교체, 경로 검증, 안전한 삭제와 비동기 저장 실행을 제공한다. 취소되더라도 수락한 파일 작업은 끝까지 정리한다.

Shared filesystem operations and ordered background storage execution."""

import asyncio
import json
import hashlib
import os
import re
import shutil
import tempfile
from functools import wraps
from dataclasses import fields
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from llm.services.infrastructure.locking import WorkspaceOwnership
from llm.services.infrastructure.transactions import current_transaction, watch


# Metadata serialization and durable filesystem primitives.


def revision_token(value: dict) -> str:
    """열린 JSON을 바꾸지 않는 낙관적 동시 편집 토큰. 확인과 쓰기는 같은 소유권 잠금 안에서 한다."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def check_revision(value: dict, expected_version) -> None:
    if expected_version is not None and expected_version != revision_token(value):
        raise ValueError("edit_conflict: stored data changed; reload before saving")

def child(root: Path, identifier: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise ValueError("Invalid object ID")
    return root / identifier


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path: Path, value: dict) -> None:
    # Serialize before touching the destination, including on invalid metadata.
    data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    prepare_replace(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = temporary_file(path)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def temporary_file(destination: Path):
    transaction = current_transaction()
    if transaction is not None and transaction.owns(destination):
        return transaction.temporary_file()
    return tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)


def prepare_replace(path: Path) -> None:
    """원자 교체/삭제 전 기존 파일을 공통 트랜잭션에 참여시킨다."""
    transaction = current_transaction()
    if transaction is not None and transaction.owns(path):
        transaction.before_replace(path)


def make_directory(path: Path) -> None:
    transaction = current_transaction()
    if transaction is not None and transaction.owns(path):
        transaction.mkdir(path)
    else:
        path.mkdir(parents=True, exist_ok=True)


def prepare_create(path: Path) -> None:
    """호출자가 생성/복사할 신규 트리를 확정 전에는 되돌릴 수 있게 한다."""
    transaction = current_transaction()
    if transaction is not None and transaction.owns(path):
        transaction.prepare_create(path)


def append_bytes(path: Path, data: bytes) -> None:
    if not data:
        return
    transaction = current_transaction()
    if transaction is not None and transaction.owns(path):
        transaction.before_append(path)
    first = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if first:
        sync_directory(path.parent)


def unlink_file(path: Path) -> None:
    prepare_replace(path)
    path.unlink()
    sync_directory(path.parent)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def reject_links(path: Path) -> Path:
    """소유권 잠금 아래에서 사용하는 경로 사전 검사. OS 격리 경계를 대체하지 않는다."""
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Storage paths cannot follow linked paths")
    return path


def read_domain_record(path: Path) -> dict:
    """지원하지 않는 저장 형식은 로드/수정 전에 거부한다. 암묵적인 변환은 하지 않는다."""
    data = read_json(path)
    if type(data.get("storage_version")) is not int or data["storage_version"] != 1:
        raise ValueError(f"Unsupported domain storage_version: {path.name}; explicit backup upgrade required")
    return data


def atomic_domain_json(path: Path, data: dict) -> None:
    """오래된 핸들도 새 저장 버전의 파일을 덮어쓰지 못하게 한다."""
    if type(data.get("storage_version")) is not int or data["storage_version"] != 1:
        raise ValueError("Unsupported domain storage_version")
    if path.exists():
        read_domain_record(path)
    atomic_json(path, data)


def record(model: object) -> dict:
    return {item.name: getattr(model, item.name) for item in fields(model)
            if item.name != "paths"}


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
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", directory):
        raise ValueError("Invalid component directory")
    _remove_tree(owner, target, (owner / directory).absolute())


def _remove_tree(owner: Path, target: Path, expected: Path) -> None:
    if target.absolute() != expected:
        raise ValueError("Deletion target does not match the owned object")
    # Reject symlink ancestors before touching the owned tree.
    for path in (target, *target.parents):
        if path.is_symlink():
            raise ValueError("Deletion through linked paths is not allowed")
    resolved_owner = owner.resolve(strict=True)
    resolved_target = target.resolve(strict=True)
    if resolved_target.parent != resolved_owner or resolved_target == resolved_owner:
        raise ValueError("Deletion target escapes its owner")
    if not resolved_target.is_dir():
        raise ValueError("Deletion target must be a directory")

    def fail(error: OSError) -> None:
        raise error

    for directory, names, files in os.walk(resolved_target, followlinks=False, onerror=fail):
        for name in (*names, *files):
            entry = Path(directory) / name
            if entry.is_symlink():
                raise ValueError("Remove linked contents before permanent deletion")
    transaction = current_transaction()
    if transaction is not None and transaction.owns(resolved_target):
        transaction.remove(resolved_target)
        return
    # Absolute target and containment have been verified above.
    journal = resolved_owner / ".deletions"
    if journal.is_symlink():
        raise ValueError("Deletion journal cannot follow links")
    intent = journal / (hashlib.sha256(resolved_target.name.encode()).hexdigest() + ".json")
    if intent.is_symlink():
        raise ValueError("Deletion intent cannot follow links")
    atomic_json(intent, {"name": resolved_target.name, "operation": "delete"})
    shutil.rmtree(resolved_target)
    sync_directory(resolved_owner)
    intent.unlink()
    sync_directory(journal)
    if not any(journal.iterdir()):
        journal.rmdir()
        sync_directory(resolved_owner)


def recover_deletions(owner: Path, *, protected=(), allowed=None) -> list[str]:
    """이미 승인·시작된 삭제만 완료한다. 새 경로나 외부 경로를 삭제 대상으로 받지 않는다."""
    for path in (owner, *owner.parents, owner / ".deletions"):
        if path.is_symlink():
            raise ValueError("Deletion recovery cannot follow links")
    recovered = []
    for intent in list((owner / ".deletions").glob("*.json")):
        if intent.is_symlink():
            raise ValueError("Deletion recovery cannot follow links")
        entry = read_json(intent)
        name = entry["name"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or entry.get("operation") != "delete" or intent.stem != hashlib.sha256(name.encode()).hexdigest():
            raise ValueError("Invalid deletion intent")
        if name in protected:
            raise ValueError("Deletion intent targets a protected object")
        if allowed is not None and name not in allowed:
            raise ValueError("Deletion intent is outside the approved operation")
        target = owner / name
        if target.exists() or target.is_symlink():
            remove_named_tree(owner, target, name)
        if intent.exists():
            unlink_file(intent)
        recovered.append(name)
    journal = owner / ".deletions"
    if journal.exists() and not any(journal.iterdir()):
        transaction = current_transaction()
        if transaction is not None and transaction.owns(journal):
            transaction.remove(journal)
        else:
            journal.rmdir()
            sync_directory(owner)
    return recovered


# Ordered background storage and cancellation draining.

_Result = TypeVar("_Result")


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

    def __init__(self, ownership: WorkspaceOwnership) -> None:
        self.ownership = ownership
        self._serial = None

    async def run(self, operation, *args, **kwargs):
        if self._serial is None:
            self._serial = asyncio.Lock()
        async with self._serial:
            def work():
                with self.ownership.scope():
                    for value in (*args, *kwargs.values()):
                        watch(value)
                    return operation(*args, **kwargs)

            pending = asyncio.ensure_future(asyncio.to_thread(work))
            cancelled = False
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    cancelled = True
            # Surface storage failures even if cancellation arrived meanwhile.
            result = pending.result()
            if cancelled:
                raise asyncio.CancelledError
            return result


async def drain_on_cancel(awaitable):
    """Complete an accepted orchestration action before forwarding cancellation."""
    pending = asyncio.ensure_future(awaitable)
    cancelled = False
    while not pending.done():
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            cancelled = True
    result = pending.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
