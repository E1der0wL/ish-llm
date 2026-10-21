"""Memory-owned evidence for the presence of each Session summary."""

import re

from llm.components.base import validate_name
from llm.services.infrastructure.storage import atomic_json, make_directory, revision_token, unlink_file


class SummaryStorage:
    SCHEMA_VERSION = 1
    MANIFEST = "contexts.manifest.json"
    DIRECTORY = "contexts"

    def __init__(self, project, component):
        self.project_id, self.component = project.id, component
        self.checked = component._checked
        root = self.checked(component.root(project))
        self.directory = self.checked(root / self.DIRECTORY)
        self.manifest = self.checked(root / self.MANIFEST)

    def path(self, session_id):
        return self.checked(self.directory / (validate_name(session_id) + ".json"))

    def initialize(self):
        if self.manifest.exists() or self.directory.exists():
            raise ValueError("Cannot adopt existing Memory summary storage")
        make_directory(self.directory)
        atomic_json(self.manifest, {"schema_version": self.SCHEMA_VERSION,
            "project_id": self.project_id, "component": self.component.name, "summaries": {}})

    def _json(self, path):
        path = self.checked(path)
        if not path.is_file():
            raise ValueError("Memory summary evidence must be a regular file")
        try:
            return self.component.deserialize(path.read_text(encoding="utf-8"))
        except TypeError as error:
            raise ValueError("Invalid Memory summary JSON") from error

    def inventory(self):
        manifest = self._json(self.manifest)
        if (not isinstance(manifest, dict)
                or manifest.keys() != {"schema_version", "project_id", "component", "summaries"}
                or type(manifest["schema_version"]) is not int
                or manifest["schema_version"] != self.SCHEMA_VERSION
                or manifest["project_id"] != self.project_id
                or manifest["component"] != self.component.name
                or not isinstance(manifest["summaries"], dict)):
            raise ValueError("Invalid Memory summary manifest")
        if not self.directory.is_dir():
            raise ValueError("Memory summary directory is missing")
        paths = {}
        for path in self.directory.iterdir():
            self.checked(path)
            if path.suffix != ".json" or not path.is_file():
                raise ValueError("Invalid Memory summary entry")
            paths[validate_name(path.stem)] = path
        if paths.keys() != manifest["summaries"].keys():
            raise ValueError("Memory summaries are missing or undeclared")
        records = {}
        for session_id, revision in manifest["summaries"].items():
            if not isinstance(revision, str) or re.fullmatch(r"[a-f0-9]{64}", revision) is None:
                raise ValueError("Invalid Memory summary revision")
            value = self._json(paths[session_id])
            self.component._validate_summary(session_id, value)
            if revision_token(value) != revision:
                raise ValueError("Memory summary revision changed")
            records[session_id] = value
        return manifest, records

    def read(self, session_id):
        validate_name(session_id)
        return self.inventory()[1].get(session_id)

    def write(self, session_id, value):
        manifest, _ = self.inventory()
        self.component._validate_summary(session_id, value)
        manifest["summaries"][session_id] = revision_token(value)
        atomic_json(self.path(session_id), value)
        atomic_json(self.manifest, manifest)

    def clear(self, session_id):
        manifest, records = self.inventory()
        if session_id not in records:
            raise FileNotFoundError("Memory summary does not exist")
        del manifest["summaries"][session_id]
        unlink_file(self.path(session_id))
        atomic_json(self.manifest, manifest)
