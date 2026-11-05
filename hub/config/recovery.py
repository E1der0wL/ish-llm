"""Explicit quarantine of one Hub-owned settings file, retaining original bytes."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from datetime import datetime, timezone
from uuid import uuid4
from llm.core.contracts import JsonValue

from ..errors.startup import StartupLoadError
from .store import WorkspaceJSONStore


@dataclass(frozen=True, slots=True)
class RecoveryManifest(JsonValue):
    source: str
    sha256: str
    created_at: str


@dataclass(frozen=True, slots=True)
class FileReset:
    CHANGED = 'Settings changed since the failure; retry loading before resetting'
    DIRECTORY = 'recovery-backups'
    SYMLINK = 'Recovery backup directory must not be a symbolic link'
    DIRECTORY_MODE = 0o700
    STAMP = '%Y%m%dT%H%M%SZ'
    SEPARATOR = '-'
    MANIFEST = 'manifest.json'
    CREATE_MODE = 'x'
    ENCODING = 'utf-8'
    FILE_MODE = 0o600
    RACE = 'Settings changed during backup; loading must be retried'
    path: Path
    root: Path
    digest: str

    @classmethod
    def capture(cls, store, *, workspace=None):
        path = store.path.absolute()
        root = WorkspaceJSONStore.directory(store.workspace if workspace is None else workspace).absolute()
        # Only the owning store supplies this path. No exception filename becomes
        # a deletion target and no backend/component storage is handled here.
        if not path.is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents)):
            return None
        try:
            if not stat.S_ISREG(path.stat().st_mode):
                return None
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None
        return cls(path, root, digest)

    def __call__(self):
        current = self.capture(WorkspaceJSONStore(
            self.root.parent, self.path.relative_to(self.root)), workspace=self.root.parent)
        if current != self:
            raise ValueError(FileReset.CHANGED)
        directory = self.root / FileReset.DIRECTORY
        if directory.is_symlink():
            raise ValueError(FileReset.SYMLINK)
        directory.mkdir(mode=FileReset.DIRECTORY_MODE, exist_ok=True)
        backup = directory / (datetime.now(timezone.utc).strftime(FileReset.STAMP) + FileReset.SEPARATOR + uuid4().hex)
        backup.mkdir(mode=FileReset.DIRECTORY_MODE)
        saved = backup / self.path.name
        metadata = RecoveryManifest(str(self.path), self.digest, datetime.now(timezone.utc).isoformat())
        with (backup / FileReset.MANIFEST).open(FileReset.CREATE_MODE, encoding=FileReset.ENCODING) as stream:
            os.chmod(stream.name, FileReset.FILE_MODE)
            json.dump(metadata.to_dict(), stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        # Rename within .hub atomically preserves the actual bytes. Never remove
        # the original before a backup exists, and never overwrite another file.
        os.rename(self.path, saved)
        try:
            if saved.is_symlink() or hashlib.sha256(saved.read_bytes()).hexdigest() != self.digest:
                raise ValueError(FileReset.RACE)
            os.chmod(saved, FileReset.FILE_MODE)
            # Persist both directory creation and removal from nested owners
            # such as presets/, not just the backup file's immediate parent.
            for path in dict.fromkeys((backup, directory, self.path.parent, self.root)):
                descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except BaseException:
            # Restore only if no concurrent writer has supplied a new value.
            try:
                os.link(saved, self.path, follow_symlinks=False)
            except FileExistsError:
                pass
            raise
        return saved


def read_for_startup(store, read, *, workspace=None):
    try:
        return read()
    except (OSError, ValueError, TypeError, KeyError) as error:
        reset = FileReset.capture(store, workspace=workspace)
        from ..locales.keys import MessageKey
        hint = MessageKey.STARTUP_RESET_HINT if reset else MessageKey.STARTUP_PERMISSION_HINT
        raise StartupLoadError(error, store.path, reset=reset, hint=hint) from error
