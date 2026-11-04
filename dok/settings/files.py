"""Configuration filename and its location under the ish home."""
from __future__ import annotations

from pathlib import Path

from .jsonc import Jsonc


class ConfigFiles:
    DIRECTORY = "dok"
    FILENAME = "config" + Jsonc.SUFFIX

    @classmethod
    def for_home(cls, home: Path | str) -> Path:
        return Path(home) / cls.DIRECTORY / cls.FILENAME
