"""Atomic completion catalog persistence with optimistic concurrent-edit checks."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile

from .models import Catalog


class CompletionStore:
    FILENAME = "completions.json"

    def __init__(self, directory):
        self.path = Path(directory) / self.FILENAME

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _read(self):
        try:
            return Catalog.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return Catalog()

    def load(self):
        with self._locked():
            return self._read()

    def update(self, expected: Catalog, change):
        with self._locked():
            current = self._read()
            if current != expected:
                raise ValueError("Completion catalog changed; reload settings before registering again")
            result = change(current)
            if not isinstance(result, Catalog):
                raise TypeError("Expected a completion Catalog")
            name = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                 prefix=".completions-", delete=False) as output:
                    name = output.name
                    json.dump(result.to_dict(), output, ensure_ascii=False, indent=2)
                    output.write("\n")
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(name, self.path)
                name = None
                fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            finally:
                if name is not None:
                    Path(name).unlink(missing_ok=True)
            return result
