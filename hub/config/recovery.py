"""Explicit quarantine of one Hub-owned settings file, retaining original bytes."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from datetime import datetime, timezone
from uuid import uuid4

from ..errors.startup import StartupLoadError
from .store import WorkspaceJSONStore


@dataclass(frozen=True, slots=True)
class FileReset:
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
            raise ValueError("Settings changed since the failure; retry loading before resetting")
        directory = self.root / "recovery-backups"
        if directory.is_symlink():
            raise ValueError("Recovery backup directory must not be a symbolic link")
        directory.mkdir(mode=0o700, exist_ok=True)
        backup = directory / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex)
        backup.mkdir(mode=0o700)
        saved = backup / self.path.name
        metadata = {"source": str(self.path), "sha256": self.digest,
                    "created_at": datetime.now(timezone.utc).isoformat()}
        with (backup / "manifest.json").open("x", encoding="utf-8") as stream:
            os.chmod(stream.name, 0o600)
            json.dump(metadata, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        # Rename within .hub atomically preserves the actual bytes. Never remove
        # the original before a backup exists, and never overwrite another file.
        os.rename(self.path, saved)
        try:
            if saved.is_symlink() or hashlib.sha256(saved.read_bytes()).hexdigest() != self.digest:
                raise ValueError("Settings changed during backup; loading must be retried")
            os.chmod(saved, 0o600)
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
