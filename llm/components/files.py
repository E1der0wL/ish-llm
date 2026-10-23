"""파일을 소유하는 Component의 패키지 저장. 실행·승인·보관 정책은 소유하지 않는다."""

from copy import deepcopy
import io
import os
from pathlib import Path, PurePosixPath
import stat

from llm.core.models import new_id
from llm.services.infrastructure.storage import (
    make_directory, prepare_replace, temporary_file, sync_directory,
    remove_named_tree, unlink_file, check_revision,
)
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method
from llm.services.lifecycle.components import ComponentData
from .base import validate_name
from .usage import ModelUsageRecord
from .definitions import DefinitionComponent
from .file_evidence import FileEvidence
from .file_manifest import FileManifest
from .file_ranges import FileRange


FILE_MANIFEST = FileManifest.schema()


class FilePath:
    """The normalized relative POSIX path accepted by package file APIs."""

    SEPARATOR = "/"
    FORBIDDEN_CHARACTERS = ("\\", ":", "\x00")
    FORBIDDEN_PARTS = ("", ".", "..")
    INVALID = "File path must be a normalized relative POSIX path"

    @classmethod
    def validate(cls, value):
        if (not isinstance(value, str) or not value
                or any(character in value for character in cls.FORBIDDEN_CHARACTERS)
                or any(part in cls.FORBIDDEN_PARTS for part in value.split(cls.SEPARATOR))
                or PurePosixPath(value).is_absolute()):
            raise ValueError(cls.INVALID)
        return value


def file_path(value):
    """정규화된 상대 POSIX 경로만 허용한다. 절대·빈 요소·부모 경로는 거부한다."""
    return FilePath.validate(value)


class FileComponent(DefinitionComponent):
    """정의와 검증된 files manifest가 하나의 revision을 구성한다."""

    NO_OUTPUT = None
    AUTO_IDENTIFIER = None
    SOURCE_IDENTIFIER = None
    ORIGINAL_DEFINITION = None
    TEMPORARY_MISSING_OK = True
    HAS_RECORD_DIRECTORY = False
    record_name = "record.json"
    MANAGED_MANIFEST = "File manifests are managed by file APIs"
    INVALID_FILE_CONTENT = "File content must be bytes or text"
    TEXT_ENCODING = "utf-8"
    INTEGRITY_MISMATCH = "Package file integrity mismatch: {name}"
    RESERVED_IDENTIFIER = 'Reserved package identifier'
    FILES_DIRECTORY = 'files'
    LEGACY_RECORDS = 'Legacy records directory is unsupported'
    INVALID_PACKAGE = 'Invalid Component package entry'
    MISSING_DEFINITION = 'Component package definition is missing'
    READ_MODE = 'rb'
    NONREGULAR_FILE = 'Package files must be regular files'
    UNREGISTERED_ENTRY = 'Unregistered package entry; use file APIs'
    NON_DIRECTORY_FILES = 'Package files root must be a directory'
    ALL_ENTRIES = '*'
    INVENTORY_MISMATCH = 'Package file inventory mismatch'
    WRITE_MODE = 'wb'
    LEGACY_INITIALIZATION = 'Legacy records directory is unsupported; explicitly re-register packages'
    EXISTING_PACKAGE = 'Package already exists'

    def _package(self, project, identifier):
        identifier = validate_name(identifier)
        if identifier in (self.RECORDS_DIRECTORY, ModelUsageRecord.DIRECTORY):
            raise ValueError(self.RESERVED_IDENTIFIER)
        return self._checked(self.root(project) / identifier)

    def _record_path(self, project, identifier):
        return self._checked(self._package(project, identifier) / self.record_name)

    def _files(self, project, identifier):
        return self._checked(self._package(project, identifier) / self.FILES_DIRECTORY)

    def _file(self, project, identifier, path):
        return self._checked(self._files(project, identifier) / file_path(path))

    def _definition(self, project, identifier):
        return super().load(project, identifier)

    def _record_files(self, project):
        root = self.root(project)
        if self._checked(root / self.RECORDS_DIRECTORY).exists():
            raise ValueError(self.LEGACY_RECORDS)
        paths = {}
        for package in root.iterdir():
            self._checked(package)
            if package.name in (ModelUsageRecord.DIRECTORY, self.RECORDS_MANIFEST):
                continue
            if not package.is_dir():
                raise ValueError(self.INVALID_PACKAGE)
            path = self._record_path(project, package.name)
            if not path.is_file():
                raise ValueError(self.MISSING_DEFINITION)
            paths[package.name] = path
        return paths

    @staticmethod
    def _stream(path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stream = os.fdopen(fd, FileComponent.READ_MODE)
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            stream.close()
            raise ValueError(FileComponent.NONREGULAR_FILE)
        return stream

    @staticmethod
    def _digest(stream, output=NO_OUTPUT):
        return FileEvidence.measure(stream, output).to_dict()

    def _verify(self, project, identifier, value):
        package = self._package(project, identifier)
        root = self._files(project, identifier)
        for entry in package.iterdir():
            self._checked(entry)
            if entry.name not in (self.record_name, root.name):
                raise ValueError(self.UNREGISTERED_ENTRY)
        actual = set()
        if root.exists():
            if not root.is_dir():
                raise ValueError(self.NON_DIRECTORY_FILES)
            for path in root.rglob(self.ALL_ENTRIES):
                self._checked(path)
                if path.is_dir():
                    continue
                relative = path.relative_to(root).as_posix()
                file_path(relative)
                actual.add(relative)
        if actual != set(FileManifest(value).entries):
            raise ValueError(self.INVENTORY_MISMATCH)
        for name, expected in FileManifest(value).entries.items():
            with self._stream(self._file(project, identifier, name)) as stream:
                if self._digest(stream) != expected:
                    raise ValueError(self.INTEGRITY_MISMATCH.format(name=name))

    def _write_file(self, project, identifier, name, stream):
        value = self.load(project, identifier)
        destination = self._file(project, identifier, name)
        make_directory(destination.parent)
        prepare_replace(destination)
        fd, temporary = temporary_file(destination)
        try:
            with os.fdopen(fd, self.WRITE_MODE) as output:
                info = self._digest(stream, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            sync_directory(destination.parent)
        finally:
            Path(temporary).unlink(missing_ok=self.TEMPORARY_MISSING_OK)
        FileManifest(value).store(name, info)
        self._write(project, identifier, value)

    def initialize(self, project):
        self.get_config(project)
        root = self.root(project)
        if self._checked(root / self.RECORDS_DIRECTORY).exists():
            raise ValueError(self.LEGACY_INITIALIZATION)
        self._initialize_storage(project, records=self.HAS_RECORD_DIRECTORY)

    def validate_record(self, identifier, data):
        super().validate_record(identifier, data)
        for name in FileManifest(data).entries:
            file_path(name)

    def create(self, project, data, *, identifier=AUTO_IDENTIFIER):
        if FileManifest(data).present:
            raise ValueError(self.MANAGED_MANIFEST)
        identifier = new_id() if identifier is self.AUTO_IDENTIFIER else identifier
        if self._package(project, identifier).exists():
            raise FileExistsError(self.EXISTING_PACKAGE)
        self.validate_record(identifier, data)
        self._write(project, identifier, data)
        return identifier

    def load(self, project, identifier):
        value = self._definition(project, identifier)
        self._verify(project, identifier, value)
        return value

    def list(self, project):
        records = super().list(project)
        for identifier, value in records.items():
            self._verify(project, identifier, value)
        return records

    def save(self, project, identifier, data):
        old = self.load(project, identifier)
        if FileManifest(old).optional != FileManifest(data).optional:
            raise ValueError(self.MANAGED_MANIFEST)
        self._write(project, identifier, data)

    def delete(self, project, identifier):
        self.load(project, identifier)
        super().delete(project, identifier)

    def _remove_record(self, project, identifier):
        remove_named_tree(self.root(project), self._package(project, identifier), identifier)

    def read_file(self, project, identifier, path, *, offset=FileRange.START, length=FileRange.TO_END):
        value = self.load(project, identifier)
        if file_path(path) not in FileManifest(value).entries:
            raise FileNotFoundError(path)
        selection = FileRange(offset, length)
        with self._stream(self._file(project, identifier, path)) as stream:
            stream.seek(selection.offset)
            return stream.read(selection.read_size)

    @classmethod
    def content_stream(cls, content):
        if not isinstance(content, (str, bytes)):
            raise TypeError(cls.INVALID_FILE_CONTENT)
        return io.BytesIO(content.encode(cls.TEXT_ENCODING) if isinstance(content, str) else content)

    def write_file(self, project, identifier, path, content):
        with self.content_stream(content) as stream:
            self._write_file(project, identifier, file_path(path), stream)

    def import_file(self, project, identifier, path, source):
        source = self._checked(Path(source).absolute())
        with self._stream(source) as stream:
            self._write_file(project, identifier, file_path(path), stream)

    def delete_file(self, project, identifier, path):
        value = self.load(project, identifier)
        if file_path(path) not in FileManifest(value).entries:
            raise FileNotFoundError(path)
        unlink_file(self._file(project, identifier, path))
        FileManifest(value).remove(path)
        self._write(project, identifier, value)

    def copy_record(self, source, destination, identifier, *, target_id=SOURCE_IDENTIFIER, definition=ORIGINAL_DEFINITION):
        value = self.load(source, identifier)
        target_id = identifier if target_id is self.SOURCE_IDENTIFIER else target_id
        definition = deepcopy(value if definition is self.ORIGINAL_DEFINITION else definition)
        FileManifest(definition).discard()
        self.create(destination, definition, identifier=target_id)
        for path in FileManifest(value).entries:
            with self._stream(self._file(source, identifier, path)) as stream:
                self._write_file(destination, target_id, path, stream)

    def clone(self, source, destination):
        for identifier in self.list(source):
            self.copy_record(source, destination, identifier)


class FileData(ComponentData):
    """파일 변경은 필수 CAS와 기존 workspace transaction 안에서 완료한다."""

    NO_REVISION = None
    REQUIRED_REVISION = "File changes require expected_revision from snapshot"

    def _edit(self, identifier, expected_revision):
        if not isinstance(expected_revision, str) or not expected_revision:
            raise ValueError(self.REQUIRED_REVISION)
        project, component = self._current()
        check_revision(component.load(project, identifier), expected_revision)
        return project, component

    @workspace_locked
    def list_files(self, identifier):
        return FileManifest(self.load(identifier)).copy_entries()

    @workspace_locked
    def read_file(self, identifier, path, *, expected_revision=NO_REVISION, offset=FileRange.START, length=FileRange.TO_END):
        project, component = self._current()
        check_revision(component.load(project, identifier), expected_revision)
        return component.read_file(project, identifier, path, offset=offset, length=length)

    @workspace_locked
    def write_file(self, identifier, path, content, *, expected_revision):
        project, component = self._edit(identifier, expected_revision)
        component.write_file(project, identifier, path, content)
        return self.snapshot(identifier)

    @workspace_locked
    def import_file(self, identifier, path, source, *, expected_revision):
        project, component = self._edit(identifier, expected_revision)
        component.import_file(project, identifier, path, source)
        return self.snapshot(identifier)

    @workspace_locked
    def delete_file(self, identifier, path, *, expected_revision):
        project, component = self._edit(identifier, expected_revision)
        self.references.require_unreferenced(project, self.name, identifier, path=path)
        component.delete_file(project, identifier, path)
        return self.snapshot(identifier)

    alist_files = async_method(list_files)
    aread_file = async_method(read_file)
    awrite_file = async_method(write_file)
    aimport_file = async_method(import_file)
    adelete_file = async_method(delete_file)
