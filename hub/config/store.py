"""Shared atomic JSON persistence for Hub-owned workspace preferences/state."""

import json
import os
from pathlib import Path
import tempfile


class WorkspaceJSONStore:
    """Subclasses own validation; all Hub state lives below workspace/.hub."""

    def __init__(self, workspace, filename: str):
        self.path = Path(workspace).expanduser() / ".hub" / filename

    def _read_json(self, default):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default

    def _write_json(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=self.path.stem + "-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(value, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
