"""Validated configuration loading, atomic saving and first-run creation."""
from __future__ import annotations

from pathlib import Path
import json
import os
import tempfile

from ..core.records import SettingsError
from .config import DockConfig
from .files import ConfigFiles
from .jsonc import Jsonc


class ConfigStore:
    ENCODING = "utf-8"

    def __init__(self, path: Path | str):
        self.path = Path(path)

    @classmethod
    def for_home(cls, home: Path | str) -> ConfigStore:
        return cls(ConfigFiles.for_home(home))

    @staticmethod
    def _object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SettingsError(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    def load(self) -> DockConfig:
        try:
            text = self.path.read_text(encoding=self.ENCODING)
        except FileNotFoundError:
            return DockConfig()
        try:
            data = Jsonc.loads(text, object_pairs_hook=self._object)
        except json.JSONDecodeError as exc:
            raise SettingsError(f"Invalid config {self.path}: {exc}") from exc
        return DockConfig.from_dict(data)

    def save(self, config: DockConfig) -> None:
        self._write(config, overwrite=True)

    def create(self, config: DockConfig) -> bool:
        """Publish a complete file only when absent, including concurrent startup."""
        return self._write(config, overwrite=False)

    def _write(self, config: DockConfig, *, overwrite: bool) -> bool:
        if not isinstance(config, DockConfig):
            raise TypeError("save expects a DockConfig")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding=self.ENCODING, dir=self.path.parent,
                                             prefix=f".{self.path.name}.", delete=False) as output:
                name = output.name
                output.write(Jsonc.dumps(config))
                output.flush()
                os.fsync(output.fileno())
            if overwrite:
                os.replace(name, self.path)
                name = None
            else:
                try:
                    os.link(name, self.path)
                except FileExistsError:
                    return False
                Path(name).unlink()
                name = None
            descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return True
        finally:
            if name is not None:
                Path(name).unlink(missing_ok=True)
