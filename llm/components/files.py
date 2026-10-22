"""파일을 소유하는 Component의 패키지 저장. 실행·승인·보관 정책은 소유하지 않는다."""

from copy import deepcopy
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import stat

from llm.core.models import new_id
from llm.core.schema import object_schema, open_schema
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


FILE_MANIFEST = open_schema("component-owned file manifest", category="data", readOnly=True)
FILE_MANIFEST["additionalProperties"] = object_schema({
    "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
    "bytes": {"type": "integer", "minimum": 0},
}, required=["sha256", "bytes"])


def file_path(value):
    """정규화된 상대 POSIX 경로만 허용한다. 절대·빈 요소·부모 경로는 거부한다."""
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value
            or any(part in ("", ".", "..") for part in value.split("/"))
            or PurePosixPath(value).is_absolute()):
        raise ValueError("File path must be a normalized relative POSIX path")
    return value


class FileComponent(DefinitionComponent):
    """정의와 검증된 files manifest가 하나의 revision을 구성한다."""

    record_name = "record.json"

    def _package(self, project, identifier):
        identifier = validate_name(identifier)
        if identifier in ("records", ModelUsageRecord.DIRECTORY):
            raise ValueError("Reserved package identifier")
        return self._checked(self.root(project) / identifier)

    def _record_path(self, project, identifier):
        return self._checked(self._package(project, identifier) / self.record_name)

    def _files(self, project, identifier):
        return self._checked(self._package(project, identifier) / "files")

    def _file(self, project, identifier, path):
        return self._checked(self._files(project, identifier) / file_path(path))

    def _definition(self, project, identifier):
        return super().load(project, identifier)

    def _record_files(self, project):
        root = self.root(project)
        if self._checked(root / "records").exists():
            raise ValueError("Legacy records directory is unsupported")
        paths = {}
        for package in root.iterdir():
            self._checked(package)
            if package.name in (ModelUsageRecord.DIRECTORY, self.RECORDS_MANIFEST):
                continue
            if not package.is_dir():
                raise ValueError("Invalid Component package entry")
            path = self._record_path(project, package.name)
            if not path.is_file():
                raise ValueError("Component package definition is missing")
            paths[package.name] = path
        return paths

    @staticmethod
    def _stream(path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stream = os.fdopen(fd, "rb")
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            stream.close()
            raise ValueError("Package files must be regular files")
        return stream

    @staticmethod
    def _digest(stream, output=None):
        digest, size = hashlib.sha256(), 0
        # I/O buffer size only; no content/retention limit.
        while block := stream.read(1024 * 1024):
            digest.update(block)
            size += len(block)
            if output is not None:
                output.write(block)
        return {"sha256": digest.hexdigest(), "bytes": size}

    def _verify(self, project, identifier, value):
        package = self._package(project, identifier)
        root = self._files(project, identifier)
        for entry in package.iterdir():
            self._checked(entry)
            if entry.name not in (self.record_name, root.name):
                raise ValueError("Unregistered package entry; use file APIs")
        actual = set()
        if root.exists():
            if not root.is_dir():
                raise ValueError("Package files root must be a directory")
            for path in root.rglob("*"):
                self._checked(path)
                if path.is_dir():
                    continue
                relative = path.relative_to(root).as_posix()
                file_path(relative)
                actual.add(relative)
        if actual != set(value.get("files", {})):
            raise ValueError("Package file inventory mismatch")
        for name, expected in value.get("files", {}).items():
            with self._stream(self._file(project, identifier, name)) as stream:
                if self._digest(stream) != expected:
                    raise ValueError(f"Package file integrity mismatch: {name}")

    def _write_file(self, project, identifier, name, stream):
        value = self.load(project, identifier)
        destination = self._file(project, identifier, name)
        make_directory(destination.parent)
        prepare_replace(destination)
        fd, temporary = temporary_file(destination)
        try:
            with os.fdopen(fd, "wb") as output:
                info = self._digest(stream, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            sync_directory(destination.parent)
        finally:
            Path(temporary).unlink(missing_ok=True)
        value.setdefault("files", {})[name] = info
        self._write(project, identifier, value)

    def initialize(self, project):
        self.get_config(project)
        root = self.root(project)
        if self._checked(root / "records").exists():
            raise ValueError("Legacy records directory is unsupported; explicitly re-register packages")
        self._initialize_storage(project, records=False)

    def validate_record(self, identifier, data):
        super().validate_record(identifier, data)
        for name in data.get("files", {}):
            file_path(name)

    def create(self, project, data, *, identifier=None):
        if "files" in data:
            raise ValueError("File manifests are managed by file APIs")
        identifier = new_id() if identifier is None else identifier
        if self._package(project, identifier).exists():
            raise FileExistsError("Package already exists")
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
        if old.get("files") != data.get("files"):
            raise ValueError("File manifests are managed by file APIs")
        self._write(project, identifier, data)

    def delete(self, project, identifier):
        self.load(project, identifier)
        super().delete(project, identifier)

    def _remove_record(self, project, identifier):
        remove_named_tree(self.root(project), self._package(project, identifier), identifier)

    def read_file(self, project, identifier, path, *, offset=0, length=None):
        value = self.load(project, identifier)
        if file_path(path) not in value.get("files", {}):
            raise FileNotFoundError(path)
        if type(offset) is not int or offset < 0 or length is not None and (type(length) is not int or length < 0):
            raise ValueError("File ranges must be nonnegative integers")
        with self._stream(self._file(project, identifier, path)) as stream:
            stream.seek(offset)
            return stream.read(-1 if length is None else length)

    def write_file(self, project, identifier, path, content):
        if not isinstance(content, (str, bytes)):
            raise TypeError("File content must be bytes or text")
        with io.BytesIO(content.encode("utf-8") if isinstance(content, str) else content) as stream:
            self._write_file(project, identifier, file_path(path), stream)

    def import_file(self, project, identifier, path, source):
        source = self._checked(Path(source).absolute())
        with self._stream(source) as stream:
            self._write_file(project, identifier, file_path(path), stream)

    def delete_file(self, project, identifier, path):
        value = self.load(project, identifier)
        if file_path(path) not in value.get("files", {}):
            raise FileNotFoundError(path)
        unlink_file(self._file(project, identifier, path))
        del value["files"][path]
        if not value["files"]:
            del value["files"]
        self._write(project, identifier, value)

    def copy_record(self, source, destination, identifier, *, target_id=None, definition=None):
        value = self.load(source, identifier)
        target_id = identifier if target_id is None else target_id
        definition = deepcopy(value if definition is None else definition)
        definition.pop("files", None)
        self.create(destination, definition, identifier=target_id)
        for path in value.get("files", {}):
            with self._stream(self._file(source, identifier, path)) as stream:
                self._write_file(destination, target_id, path, stream)

    def clone(self, source, destination):
        for identifier in self.list(source):
            self.copy_record(source, destination, identifier)


class FileData(ComponentData):
    """파일 변경은 필수 CAS와 기존 workspace transaction 안에서 완료한다."""

    def _edit(self, identifier, expected_revision):
        if not isinstance(expected_revision, str) or not expected_revision:
            raise ValueError("File changes require expected_revision from snapshot")
        project, component = self._current()
        check_revision(component.load(project, identifier), expected_revision)
        return project, component

    @workspace_locked
    def list_files(self, identifier):
        return deepcopy(self.load(identifier).get("files", {}))

    @workspace_locked
    def read_file(self, identifier, path, *, expected_revision=None, offset=0, length=None):
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
