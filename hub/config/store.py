"""Shared atomic JSON persistence for Hub-owned workspace preferences/state."""

import json
import os
from pathlib import Path
import tempfile


class WorkspaceJSONStore:
    """Subclasses own validation; all Hub state lives below workspace/.hub."""

    DIRECTORY = ".hub"
    ENCODING = "utf-8"
    WRITE_MODE = "w"
    TEMPORARY_SEPARATOR = "-"
    TEMPORARY_SUFFIX = ".tmp"
    DELETE_ON_CLOSE = False
    ENSURE_ASCII = False
    ALLOW_NAN = False
    CREATE_PARENTS = True
    EXISTING_DIRECTORY_OK = True
    MISSING_TEMPORARY_OK = True

    def __init__(self, workspace, filename: str):
        self.path = Path(workspace).expanduser() / self.DIRECTORY / filename

    @property
    def workspace(self):
        return self.path.parent.parent

    def _read_json(self, default):
        try:
            return json.loads(self.path.read_text(encoding=self.ENCODING))
        except FileNotFoundError:
            return default

    def _write_json(self, value):
        self.path.parent.mkdir(
            parents=self.CREATE_PARENTS, exist_ok=self.EXISTING_DIRECTORY_OK
        )
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode=self.WRITE_MODE,
                encoding=self.ENCODING,
                dir=self.path.parent,
                prefix=self.path.stem + self.TEMPORARY_SEPARATOR,
                suffix=self.TEMPORARY_SUFFIX,
                delete=self.DELETE_ON_CLOSE,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(
                    value,
                    stream,
                    ensure_ascii=self.ENSURE_ASCII,
                    allow_nan=self.ALLOW_NAN,
                )
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=self.MISSING_TEMPORARY_OK)
