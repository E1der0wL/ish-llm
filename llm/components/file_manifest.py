"""The package definition's managed file inventory; no filesystem or policy authority."""

from copy import deepcopy
from .file_evidence import FileEvidence


class FileManifest:
    FIELD = "files"
    ABSENT = None

    def __init__(self, definition):
        self._definition = definition

    @classmethod
    def schema(cls):
        return FileEvidence.manifest_schema()

    @property
    def present(self):
        return self.FIELD in self._definition

    @property
    def optional(self):
        return self._definition.get(self.FIELD)

    @property
    def entries(self):
        return self._definition.get(self.FIELD, {})

    def lookup(self, path):
        return self.entries.get(path)

    def require(self, path):
        return self._definition[self.FIELD][path]

    def store(self, path, evidence):
        self._definition.setdefault(self.FIELD, {})[path] = evidence

    def remove(self, path):
        del self._definition[self.FIELD][path]
        if not self._definition[self.FIELD]:
            del self._definition[self.FIELD]

    def discard(self):
        self._definition.pop(self.FIELD, self.ABSENT)

    def copy_entries(self):
        return deepcopy(self.entries)
