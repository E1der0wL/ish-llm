"""Native Skill directories and separate, mandatory Project-local evidence."""

from copy import deepcopy
import hashlib
import io
import os
from pathlib import Path
import shutil
import stat

from llm.core.skills import SkillDocument, SkillName
from llm.components.files import FileComponent, file_path
from llm.components.usage import ModelUsageRecord
from llm.services.infrastructure.storage import (
    atomic_bytes, atomic_json, read_json, make_directory, prepare_create, prepare_replace,
    remove_named_tree, temporary_file, sync_directory, unlink_file,
)
from .records import SkillRecord as Record


class SkillStorage:
    VERSION = 1
    STATE = "_state"
    CATALOG = "manifest.json"
    RECORDS = "records"
    VERSION_KEY = "schema_version"
    PROJECT = "project_id"
    COMPONENT = "component"
    ID = "id"
    DOCUMENT = "document"
    DIRECTORIES = "directories"
    CATALOG_FIELDS = frozenset((VERSION_KEY, PROJECT, COMPONENT, RECORDS))
    ENTRY_FIELDS = frozenset((VERSION_KEY, PROJECT, COMPONENT, ID, DOCUMENT, DIRECTORIES,
                             Record.FILES, Record.RESOURCES, Record.LINEAGE, Record.SOURCE))

    def __init__(self, project, component):
        self.project, self.component = project, component
        self.checked = component._checked
        self.root = component.root(project)
        self.state = self.checked(self.root / self.STATE)
        self.records = self.checked(self.state / self.RECORDS)
        self.catalog = self.checked(self.state / self.CATALOG)

    def package(self, identifier):
        return self.checked(self.root / SkillName.validate(identifier))

    def record_path(self, identifier):
        return self.checked(self.records / (SkillName.validate(identifier) + ".json"))

    def usage_root(self):
        return self.checked(self.state / ModelUsageRecord.DIRECTORY)

    def _identity(self):
        return {self.VERSION_KEY: self.VERSION, self.PROJECT: self.project.id, self.COMPONENT: self.component.name}

    def _validate_identity(self, value):
        if (type(value.get(self.VERSION_KEY)) is not int
                or any(value.get(key) != expected for key, expected in self._identity().items())):
            raise ValueError("Skill operational record identity or version mismatch")

    @staticmethod
    def _info(stream):
        info = FileComponent._digest(stream)
        mode = stat.S_IMODE(os.fstat(stream.fileno()).st_mode)
        if mode & ~0o777:
            raise ValueError("Skill files cannot carry special permission bits")
        return {**info, Record.FILE_MODE: mode}

    def _tree(self, package):
        self.checked(package)
        if not package.is_dir():
            raise ValueError("Skill directory is missing")
        files, directories = {}, []
        for path in sorted(package.rglob("*")):
            self.checked(path)
            name = file_path(path.relative_to(package).as_posix())
            if path.is_dir():
                directories.append(name)
            else:
                with FileComponent._stream(path) as stream:
                    files[name] = self._info(stream)
        if SkillDocument.FILENAME not in files:
            raise ValueError("Skill directory requires SKILL.md; legacy definitions are unsupported")
        return files, directories

    def initialize(self):
        if self.component.name in self.project.initialized_components:
            self.inventory()
            self.component._model_usage_records(self.project)
            return
        if self.root.exists():
            raise ValueError("Cannot initialize undeclared or legacy Skill storage")
        make_directory(self.records)
        atomic_json(self.catalog, {**self._identity(), self.RECORDS: {}})
        atomic_json(self.usage_root() / ModelUsageRecord.MANIFEST, {
            "schema_version": ModelUsageRecord.SCHEMA_VERSION, "project_id": self.project.id,
            "component": self.component.name, "records": {}})

    def _catalog_records(self):
        value = read_json(self.catalog)
        if not isinstance(value, dict) or value.keys() != self.CATALOG_FIELDS:
            raise ValueError("Invalid Skill operational inventory")
        self._validate_identity(value)
        expected = value[self.RECORDS]
        if not isinstance(expected, dict):
            raise ValueError("Invalid Skill operational record set")
        for name, digest in expected.items():
            SkillName.validate(name)
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Invalid Skill operational digest")
        if not self.records.is_dir():
            raise ValueError("Skill operational records are missing")
        if {self.checked(p).name for p in self.records.iterdir()} != {name + ".json" for name in expected}:
            raise ValueError("Skill operational records are missing or undeclared")
        if {self.checked(p).name for p in self.state.iterdir()} != {self.CATALOG, self.RECORDS, ModelUsageRecord.DIRECTORY}:
            raise ValueError("Skill operational storage is missing or undeclared")
        if {self.checked(p).name for p in self.root.iterdir()} != {self.STATE, *expected}:
            raise ValueError("Skill packages are missing or undeclared; legacy layouts are unsupported")
        entries = {}
        for name, digest in expected.items():
            with FileComponent._stream(self.record_path(name)) as stream:
                raw = stream.read()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("Skill operational record integrity mismatch")
            entry = self.component.deserialize(raw.decode("utf-8"))
            if entry.keys() != self.ENTRY_FIELDS or entry[self.ID] != name:
                raise ValueError("Invalid Skill operational record fields")
            self._validate_identity(entry)
            if (not isinstance(entry[self.DIRECTORIES], list)
                    or entry[self.DIRECTORIES] != sorted(set(entry[self.DIRECTORIES]))):
                raise ValueError("Invalid Skill directory inventory")
            for path in entry[self.DIRECTORIES]:
                file_path(path)
            for key in (self.DOCUMENT,):
                # The document uses the same digest/size/mode evidence as resource files.
                from jsonschema import Draft202012Validator
                if not Draft202012Validator(Record.FILE_SCHEMA["additionalProperties"]).is_valid(entry[key]):
                    raise ValueError("Invalid Skill document integrity evidence")
            if not isinstance(entry[Record.FILES], dict) or not isinstance(entry[Record.RESOURCES], list):
                raise ValueError("Skill requires stored file and reference containers")
            entries[name] = entry
        return value, entries

    def _read(self, name, entry):
        package = self.package(name)
        files, directories = self._tree(package)
        document_info = files.pop(SkillDocument.FILENAME)
        if (files != entry[Record.FILES] or document_info != entry[self.DOCUMENT]
                or directories != entry[self.DIRECTORIES]):
            raise ValueError("Skill package file integrity mismatch")
        with FileComponent._stream(self.checked(package / SkillDocument.FILENAME)) as stream:
            raw = stream.read()
        if hashlib.sha256(raw).hexdigest() != document_info["sha256"]:
            raise ValueError("Skill document changed during read")
        value = {Record.MARKDOWN: raw.decode("utf-8")}
        if entry[Record.FILES]:
            value[Record.FILES] = deepcopy(entry[Record.FILES])
        if entry[Record.RESOURCES]:
            value[Record.RESOURCES] = deepcopy(entry[Record.RESOURCES])
        for key in (Record.LINEAGE, Record.SOURCE):
            if entry[key] is not None:
                value[key] = deepcopy(entry[key])
        Record.validate(name, value)
        return value

    def inventory(self):
        catalog, entries = self._catalog_records()
        return catalog, {name: self._read(name, entry) for name, entry in entries.items()}

    def load(self, identifier):
        SkillName.validate(identifier)
        records = self.inventory()[1]
        if identifier not in records:
            raise FileNotFoundError(identifier)
        return records[identifier]

    def _commit(self, identifier, value, catalog):
        Record.validate(identifier, value)
        files, directories = self._tree(self.package(identifier))
        raw = value[Record.MARKDOWN].encode("utf-8")
        document = files[SkillDocument.FILENAME]
        if document["bytes"] != len(raw) or document["sha256"] != hashlib.sha256(raw).hexdigest():
            raise ValueError("Skill document changed before operational commit")
        entry = {**self._identity(), self.ID: identifier, self.DOCUMENT: files.pop(SkillDocument.FILENAME),
                 self.DIRECTORIES: directories, Record.FILES: files,
                 Record.RESOURCES: deepcopy(value.get(Record.RESOURCES, [])),
                 Record.LINEAGE: deepcopy(value.get(Record.LINEAGE)), Record.SOURCE: deepcopy(value.get(Record.SOURCE))}
        if files != value.get(Record.FILES, {}):
            raise ValueError("Skill files must be managed by file APIs")
        path = self.record_path(identifier)
        atomic_json(path, entry)
        catalog[self.RECORDS][identifier] = hashlib.sha256(path.read_bytes()).hexdigest()
        atomic_json(self.catalog, catalog)

    @staticmethod
    def _copy_stream(stream, destination, *, mode):
        make_directory(destination.parent)
        prepare_replace(destination)
        fd, temporary = temporary_file(destination)
        try:
            with os.fdopen(fd, "wb") as output:
                info = FileComponent._digest(stream, output)
                os.fchmod(output.fileno(), mode)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            sync_directory(destination.parent)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {**info, Record.FILE_MODE: mode}

    def create(self, value, identifier=None):
        document = Record.validate(identifier, value)
        identifier = document.name
        if Record.FILES in value:
            raise ValueError("File manifests are managed by file APIs")
        catalog, _ = self.inventory()
        package = self.package(identifier)
        if package.exists():
            raise FileExistsError("Skill package already exists")
        make_directory(package)
        atomic_bytes(package / SkillDocument.FILENAME, document.text.encode("utf-8"))
        self._commit(identifier, value, catalog)
        return identifier

    def save(self, identifier, value):
        catalog, records = self.inventory()
        old = records[identifier]
        document = Record.validate(identifier, value)
        if old.get(Record.FILES) != value.get(Record.FILES):
            raise ValueError("File manifests are managed by file APIs")
        if old.get(Record.LINEAGE) != value.get(Record.LINEAGE) or old.get(Record.SOURCE) != value.get(Record.SOURCE):
            raise ValueError("Skill lineage and source are immutable")
        path = self.package(identifier) / SkillDocument.FILENAME
        if old[Record.MARKDOWN] != document.text:
            self._copy_stream(io.BytesIO(document.text.encode("utf-8")), path, mode=stat.S_IMODE(path.stat().st_mode))
        self._commit(identifier, value, catalog)

    def delete(self, identifier):
        catalog, _ = self.inventory()
        self.load(identifier)
        remove_named_tree(self.root, self.package(identifier), identifier)
        unlink_file(self.record_path(identifier))
        del catalog[self.RECORDS][identifier]
        atomic_json(self.catalog, catalog)

    def write_file(self, identifier, path, stream, *, mode=None):
        path = Record.resource_path(path)
        catalog, records = self.inventory()
        value = records[identifier]
        destination = self.checked(self.package(identifier) / path)
        if mode is None:
            mode = value.get(Record.FILES, {}).get(path, {}).get(Record.FILE_MODE, 0o600)
        info = self._copy_stream(stream, destination, mode=mode)
        value.setdefault(Record.FILES, {})[path] = info
        self._commit(identifier, value, catalog)

    def read_file(self, identifier, path, *, offset=0, length=None):
        value = self.load(identifier)
        if Record.resource_path(path) not in value.get(Record.FILES, {}):
            raise FileNotFoundError(path)
        if type(offset) is not int or offset < 0 or length is not None and (type(length) is not int or length < 0):
            raise ValueError("File ranges must be nonnegative integers")
        with FileComponent._stream(self.checked(self.package(identifier) / path)) as stream:
            stream.seek(offset)
            return stream.read(-1 if length is None else length)

    def delete_file(self, identifier, path):
        path = Record.resource_path(path)
        catalog, records = self.inventory()
        value = records[identifier]
        if path not in value.get(Record.FILES, {}):
            raise FileNotFoundError(path)
        if any(Record.COMPONENT not in ref and ref.get(Record.PATH) == path for ref in value.get(Record.RESOURCES, [])):
            raise ValueError("File is referenced by Skill resources; remove the reference first")
        unlink_file(self.checked(self.package(identifier) / path))
        del value[Record.FILES][path]
        self._commit(identifier, value, catalog)

    def import_directory(self, source, *, value=None):
        source = self.checked(Path(source).absolute())
        files, directories = self._tree(source)
        with FileComponent._stream(source / SkillDocument.FILENAME) as stream:
            raw = stream.read()
        document = SkillDocument.parse(raw.decode("utf-8"), identifier=source.name)
        candidate = {Record.MARKDOWN: document.text} if value is None else deepcopy(value)
        identifier = Record.document(candidate).name
        candidate.pop(Record.FILES, None)
        self.create(candidate, identifier)
        catalog, _ = self._catalog_records()
        package = self.package(identifier)
        for directory in directories:
            make_directory(self.checked(package / directory))
        for path, expected in files.items():
            destination = self.checked(package / path)
            if path == SkillDocument.FILENAME and candidate[Record.MARKDOWN] != document.text:
                # Explicit fork authoring has already written the new document.
                continue
            with FileComponent._stream(self.checked(source / path)) as stream:
                actual = self._copy_stream(stream, destination, mode=expected[Record.FILE_MODE])
            if actual != expected:
                raise ValueError("Skill source changed during import")
        if self._tree(source) != (files, directories):
            raise ValueError("Skill source changed during import")
        candidate[Record.FILES] = {name: info for name, info in files.items() if name != SkillDocument.FILENAME}
        self._commit(identifier, candidate, catalog)
        return identifier

    def export_directory(self, identifier, destination):
        self.load(identifier)
        source = self.package(identifier)
        destination = self.checked(Path(destination).absolute())
        if destination.name != identifier:
            raise ValueError("Export directory must match the Skill name")
        if source == destination or source in destination.parents or self.root in destination.parents:
            raise ValueError("Export destination must be outside Skill storage")
        files, directories = self._tree(source)
        if destination.exists():
            raise FileExistsError("Export never overwrites an existing directory")
        prepare_create(destination)
        destination.mkdir(parents=False)
        try:
            for directory in directories:
                make_directory(self.checked(destination / directory))
            for path, expected in files.items():
                with FileComponent._stream(self.checked(source / path)) as stream:
                    actual = self._copy_stream(stream, self.checked(destination / path), mode=expected[Record.FILE_MODE])
                if actual != expected:
                    raise ValueError("Skill source changed during export")
            if self._tree(destination) != (files, directories):
                raise ValueError("Skill export integrity mismatch")
            sync_directory(destination.parent)
        except BaseException:
            shutil.rmtree(destination)
            raise
        return destination
