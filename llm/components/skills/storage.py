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
from llm.components.file_ranges import FileRange
from llm.components.usage import ModelUsageRecord, ModelUsageManifest
from llm.services.infrastructure.storage import (
    atomic_bytes, atomic_json, read_json, make_directory, prepare_create, prepare_replace,
    remove_named_tree, temporary_file, sync_directory, unlink_file,
)
from .records import SkillRecord as Record, SkillFileEvidence
from .state import SkillStateIdentity, SkillCatalog, SkillOperationalRecord


class SkillStorage:
    VERSION = SkillStateIdentity.VERSION
    STATE = "_state"
    CATALOG = "manifest.json"
    RECORDS = "records"
    VERSION_KEY = SkillStateIdentity.schema_version.__name__
    PROJECT = SkillStateIdentity.project_id.__name__
    COMPONENT = SkillStateIdentity.component.__name__
    ID = SkillOperationalRecord.id.__name__
    DOCUMENT = SkillOperationalRecord.document.__name__
    DIRECTORIES = SkillOperationalRecord.directories.__name__
    CATALOG_FIELDS = SkillCatalog.field_names()
    ENTRY_FIELDS = SkillOperationalRecord.field_names()
    RECORD_SUFFIX = '.json'
    TREE_PATTERN = '*'
    WRITE_MODE = 'wb'
    MISSING_DIRECTORY = 'Skill directory is missing'
    MISSING_DOCUMENT = 'Skill directory requires SKILL.md; legacy definitions are unsupported'
    UNDECLARED_STORAGE = 'Cannot initialize undeclared or legacy Skill storage'
    MISSING_RECORDS = 'Skill operational records are missing'
    UNDECLARED_RECORDS = 'Skill operational records are missing or undeclared'
    UNDECLARED_STATE = 'Skill operational storage is missing or undeclared'
    UNDECLARED_PACKAGES = 'Skill packages are missing or undeclared; legacy layouts are unsupported'
    RECORD_INTEGRITY = 'Skill operational record integrity mismatch'
    PACKAGE_INTEGRITY = 'Skill package file integrity mismatch'
    DOCUMENT_CHANGED = 'Skill document changed during read'
    DOCUMENT_CHANGED_BEFORE_COMMIT = 'Skill document changed before operational commit'
    UNMANAGED_FILES = 'Skill files must be managed by file APIs'
    PACKAGE_EXISTS = 'Skill package already exists'
    IMMUTABLE_AUTHORSHIP = 'Skill lineage and source are immutable'
    REFERENCED_FILE = 'File is referenced by Skill resources; remove the reference first'
    IMPORT_CHANGED = 'Skill source changed during import'
    EXPORT_NAME = 'Export directory must match the Skill name'
    EXPORT_LOCATION = 'Export destination must be outside Skill storage'
    EXPORT_EXISTS = 'Export never overwrites an existing directory'
    EXPORT_CHANGED = 'Skill source changed during export'
    EXPORT_INTEGRITY = 'Skill export integrity mismatch'
    NEW_FILE_MODE = 0o600
    TEMPORARY_MISSING_OK = True
    EXPORT_CREATE_PARENTS = False

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
        return self.checked(self.records / (SkillName.validate(identifier) + SkillStorage.RECORD_SUFFIX))

    def usage_root(self):
        return self.checked(self.state / ModelUsageRecord.DIRECTORY)

    def _identity(self):
        return SkillStateIdentity(self.VERSION, self.project.id, self.component.name)

    @staticmethod
    def _info(stream):
        mode = stat.S_IMODE(os.fstat(stream.fileno()).st_mode)
        return SkillFileEvidence.measure(stream, mode=mode)

    def _tree(self, package):
        self.checked(package)
        if not package.is_dir():
            raise ValueError(SkillStorage.MISSING_DIRECTORY)
        files, directories = {}, []
        for path in sorted(package.rglob(SkillStorage.TREE_PATTERN)):
            self.checked(path)
            name = file_path(path.relative_to(package).as_posix())
            if path.is_dir():
                directories.append(name)
            else:
                with FileComponent._stream(path) as stream:
                    files[name] = self._info(stream)
        if SkillDocument.FILENAME not in files:
            raise ValueError(SkillStorage.MISSING_DOCUMENT)
        return files, directories

    def initialize(self):
        if self.component.name in self.project.initialized_components:
            self.inventory()
            self.component._model_usage_records(self.project)
            return
        if self.root.exists():
            raise ValueError(SkillStorage.UNDECLARED_STORAGE)
        make_directory(self.records)
        atomic_json(self.catalog, SkillCatalog.empty(self._identity()).to_dict())
        atomic_json(self.usage_root() / ModelUsageRecord.MANIFEST,
                    ModelUsageManifest.empty(project_id=self.project.id, component=self.component.name).to_dict())

    def _catalog_records(self):
        value = SkillCatalog.read(read_json(self.catalog), identity=self._identity())
        expected = value.records
        if not self.records.is_dir():
            raise ValueError(SkillStorage.MISSING_RECORDS)
        if {self.checked(p).name for p in self.records.iterdir()} != {name + SkillStorage.RECORD_SUFFIX for name in expected}:
            raise ValueError(SkillStorage.UNDECLARED_RECORDS)
        if {self.checked(p).name for p in self.state.iterdir()} != {self.CATALOG, self.RECORDS, ModelUsageRecord.DIRECTORY}:
            raise ValueError(SkillStorage.UNDECLARED_STATE)
        if {self.checked(p).name for p in self.root.iterdir()} != {self.STATE, *expected}:
            raise ValueError(SkillStorage.UNDECLARED_PACKAGES)
        entries = {}
        for name, digest in expected.items():
            with FileComponent._stream(self.record_path(name)) as stream:
                raw = stream.read()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError(SkillStorage.RECORD_INTEGRITY)
            entries[name] = SkillOperationalRecord.read(self.component.deserialize(raw.decode(Record.TEXT_ENCODING)),
                identity=self._identity(), identifier=name)
        return value, entries

    def _read(self, name, entry):
        package = self.package(name)
        files, directories = self._tree(package)
        document_info = files.pop(SkillDocument.FILENAME)
        if ({name: info.to_dict() for name, info in files.items()} != entry.files
                or document_info != SkillFileEvidence.from_dict(entry.document)
                or directories != entry.directories):
            raise ValueError(SkillStorage.PACKAGE_INTEGRITY)
        with FileComponent._stream(self.checked(package / SkillDocument.FILENAME)) as stream:
            raw = stream.read()
        if not document_info.matches(raw):
            raise ValueError(SkillStorage.DOCUMENT_CHANGED)
        return Record.from_storage(raw.decode(Record.TEXT_ENCODING), files=entry.files,
            resources=entry.resources, lineage=entry.lineage, source=entry.source, identifier=name).to_dict()

    def inventory(self):
        catalog, entries = self._catalog_records()
        return catalog, {name: self._read(name, entry) for name, entry in entries.items()}

    def load(self, identifier):
        SkillName.validate(identifier)
        _, records = self.inventory()
        if identifier not in records:
            raise FileNotFoundError(identifier)
        return records[identifier]

    def _commit(self, identifier, value, catalog):
        record = Record.read(value, identifier=identifier)
        files, directories = self._tree(self.package(identifier))
        raw = record.markdown.encode(Record.TEXT_ENCODING)
        document = files[SkillDocument.FILENAME]
        if not document.matches(raw):
            raise ValueError(SkillStorage.DOCUMENT_CHANGED_BEFORE_COMMIT)
        document = files.pop(SkillDocument.FILENAME)
        entry = SkillOperationalRecord.create(self._identity(), identifier, document=document,
            directories=directories, files=files, record=record)
        if files != record.files:
            raise ValueError(SkillStorage.UNMANAGED_FILES)
        path = self.record_path(identifier)
        atomic_json(path, entry.to_dict())
        catalog.records[identifier] = hashlib.sha256(path.read_bytes()).hexdigest()
        atomic_json(self.catalog, catalog.to_dict())

    @staticmethod
    def _copy_stream(stream, destination, *, mode):
        make_directory(destination.parent)
        prepare_replace(destination)
        fd, temporary = temporary_file(destination)
        try:
            with os.fdopen(fd, SkillStorage.WRITE_MODE) as output:
                info = SkillFileEvidence.measure(stream, output, mode=mode)
                os.fchmod(output.fileno(), mode)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            sync_directory(destination.parent)
        finally:
            Path(temporary).unlink(missing_ok=SkillStorage.TEMPORARY_MISSING_OK)
        return info

    def create(self, value, identifier=None):
        record = Record.read(value, identifier=identifier)
        document = record.parsed_document
        identifier = document.name
        if record.has_files:
            raise ValueError(FileComponent.MANAGED_MANIFEST)
        catalog, _ = self.inventory()
        package = self.package(identifier)
        if package.exists():
            raise FileExistsError(SkillStorage.PACKAGE_EXISTS)
        make_directory(package)
        atomic_bytes(package / SkillDocument.FILENAME, document.text.encode(Record.TEXT_ENCODING))
        self._commit(identifier, value, catalog)
        return identifier

    def save(self, identifier, value):
        catalog, records = self.inventory()
        old = Record.read(records[identifier])
        record = Record.read(value, identifier=identifier)
        document = record.parsed_document
        if not old.same_files(record):
            raise ValueError(FileComponent.MANAGED_MANIFEST)
        if not old.same_authorship(record):
            raise ValueError(SkillStorage.IMMUTABLE_AUTHORSHIP)
        path = self.package(identifier) / SkillDocument.FILENAME
        if old.markdown != document.text:
            self._copy_stream(io.BytesIO(document.text.encode(Record.TEXT_ENCODING)), path, mode=stat.S_IMODE(path.stat().st_mode))
        self._commit(identifier, value, catalog)

    def delete(self, identifier):
        catalog, _ = self.inventory()
        self.load(identifier)
        remove_named_tree(self.root, self.package(identifier), identifier)
        unlink_file(self.record_path(identifier))
        del catalog.records[identifier]
        atomic_json(self.catalog, catalog.to_dict())

    def write_file(self, identifier, path, stream, *, mode=None):
        path = Record.resource_path(path)
        catalog, records = self.inventory()
        record = Record.read(records[identifier])
        destination = self.checked(self.package(identifier) / path)
        if mode is None:
            evidence = record.files.get(path)
            mode = self.NEW_FILE_MODE if evidence is None else evidence.mode
        info = self._copy_stream(stream, destination, mode=mode)
        self._commit(identifier, record.with_file(path, info), catalog)

    def read_file(self, identifier, path, *, offset=FileRange.START, length=FileRange.TO_END):
        record = Record.read(self.load(identifier))
        if Record.resource_path(path) not in record.files:
            raise FileNotFoundError(path)
        selection = FileRange(offset, length)
        with FileComponent._stream(self.checked(self.package(identifier) / path)) as stream:
            stream.seek(selection.offset)
            return stream.read(selection.read_size)

    def delete_file(self, identifier, path):
        path = Record.resource_path(path)
        catalog, records = self.inventory()
        record = Record.read(records[identifier])
        if path not in record.files:
            raise FileNotFoundError(path)
        if any(not ref.external and ref.path == path for ref in record.resources):
            raise ValueError(SkillStorage.REFERENCED_FILE)
        unlink_file(self.checked(self.package(identifier) / path))
        self._commit(identifier, record.without_file(path), catalog)

    def import_directory(self, source, *, value=None):
        source = self.checked(Path(source).absolute())
        files, directories = self._tree(source)
        with FileComponent._stream(source / SkillDocument.FILENAME) as stream:
            raw = stream.read()
        document = SkillDocument.parse(raw.decode(Record.TEXT_ENCODING), identifier=source.name)
        candidate = Record.markdown_change(document.text) if value is None else deepcopy(value)
        identifier = Record.document(candidate).name
        candidate = Record.without_files(candidate)
        self.create(candidate, identifier)
        catalog, _ = self._catalog_records()
        package = self.package(identifier)
        for directory in directories:
            make_directory(self.checked(package / directory))
        for path, expected in files.items():
            destination = self.checked(package / path)
            if path == SkillDocument.FILENAME and Record.document(candidate).text != document.text:
                # Explicit fork authoring has already written the new document.
                continue
            with FileComponent._stream(self.checked(source / path)) as stream:
                actual = self._copy_stream(stream, destination, mode=expected.mode)
            if actual != expected:
                raise ValueError(SkillStorage.IMPORT_CHANGED)
        if self._tree(source) != (files, directories):
            raise ValueError(SkillStorage.IMPORT_CHANGED)
        candidate = Record.with_files(candidate, {name: info for name, info in files.items() if name != SkillDocument.FILENAME})
        self._commit(identifier, candidate, catalog)
        return identifier

    def export_directory(self, identifier, destination):
        self.load(identifier)
        source = self.package(identifier)
        destination = self.checked(Path(destination).absolute())
        if destination.name != identifier:
            raise ValueError(SkillStorage.EXPORT_NAME)
        if source == destination or source in destination.parents or self.root in destination.parents:
            raise ValueError(SkillStorage.EXPORT_LOCATION)
        files, directories = self._tree(source)
        if destination.exists():
            raise FileExistsError(SkillStorage.EXPORT_EXISTS)
        prepare_create(destination)
        destination.mkdir(parents=self.EXPORT_CREATE_PARENTS)
        try:
            for directory in directories:
                make_directory(self.checked(destination / directory))
            for path, expected in files.items():
                with FileComponent._stream(self.checked(source / path)) as stream:
                    actual = self._copy_stream(stream, self.checked(destination / path), mode=expected.mode)
                if actual != expected:
                    raise ValueError(SkillStorage.EXPORT_CHANGED)
            if self._tree(destination) != (files, directories):
                raise ValueError(SkillStorage.EXPORT_INTEGRITY)
            sync_directory(destination.parent)
        except BaseException:
            shutil.rmtree(destination)
            raise
        return destination
