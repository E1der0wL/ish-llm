"""체크섬이 있는 디렉토리 스냅샷. 도메인/Component 형식 검증은 호출 서비스가 담당한다."""

import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path

from llm.services.infrastructure.storage import atomic_json, read_json, sync_directory, prepare_create
from llm.services.infrastructure.transactions import NO_TRANSACTION, current_transaction


class BackupManifest:
    FORMAT_VERSION = 1
    VERSION = "format_version"
    METADATA = "metadata"
    FILES = "files"
    UNSUPPORTED = "Unsupported backup format_version"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, version, metadata, files):
        return cls({cls.VERSION: version, cls.METADATA: metadata, cls.FILES: files})

    @classmethod
    def read(cls, value, version):
        if type(value.get(cls.VERSION)) is not int or value[cls.VERSION] != version:
            raise ValueError(cls.UNSUPPORTED)
        return cls(value)

    @property
    def files(self):
        return self._value[self.FILES]

    @property
    def record(self):
        return self._value


class DirectoryBackups:
    """신규 경로로만 공개하며, 실패한 복원은 원본과 기존 대상을 수정하지 않는다."""

    FORMAT_VERSION = BackupManifest.FORMAT_VERSION
    PROJECT = "project"
    MANIFEST = "manifest.json"
    ALL_FILES = "*"
    BINARY_READ = "rb"
    BLOCK_BYTES = 1024 * 1024
    END_OF_FILE = b""
    STAGING_PREFIX = ".llm-backup-"
    CREATE_PARENTS = True
    EXIST_OK = True
    DEEPEST_FIRST = True
    COPY_INTO_STAGE = True
    LINKED_PATH = "Backup paths cannot follow symlinks"
    INVALID_CONTENTS = "Backup supports only regular files and directories"
    EXISTS = "Backup/restore destination already exists"
    NESTED_DESTINATION = "Backup destination cannot be inside its source"
    SOURCE_CHANGED = "Source changed while creating backup"
    CHECKSUM_MISMATCH = "Backup checksum mismatch"
    RESTORE_CHANGED = "Backup changed during restore"

    @classmethod
    def project_root(cls, archive):
        return Path(archive) / cls.PROJECT

    @classmethod
    def manifest_path(cls, archive):
        return archive / cls.MANIFEST

    @staticmethod
    def _checked(path):
        path = Path(path).absolute()
        for entry in (path, *path.parents):
            if entry.is_symlink():
                raise ValueError(DirectoryBackups.LINKED_PATH)
        return path

    def _inventory(self, root):
        result = {}
        for path in sorted(root.rglob(DirectoryBackups.ALL_FILES)):
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ValueError(DirectoryBackups.INVALID_CONTENTS)
            digest = hashlib.sha256()
            with path.open(DirectoryBackups.BINARY_READ) as stream:
                for block in iter(
                    lambda: stream.read(DirectoryBackups.BLOCK_BYTES), DirectoryBackups.END_OF_FILE
                ):
                    digest.update(block)
            result[path.relative_to(root).as_posix()] = digest.hexdigest()
        return result

    def _publish(self, destination, build):
        destination = self._checked(destination)
        if destination.exists():
            raise FileExistsError(DirectoryBackups.EXISTS)
        destination.parent.mkdir(parents=self.CREATE_PARENTS, exist_ok=self.EXIST_OK)
        transaction = current_transaction()
        stage = (
            transaction.staging_directory()
            if transaction is not NO_TRANSACTION and transaction.owns(destination)
            else Path(tempfile.mkdtemp(prefix=DirectoryBackups.STAGING_PREFIX, dir=destination.parent))
        )
        try:
            build(stage)
            for path in stage.rglob(DirectoryBackups.ALL_FILES):
                if path.is_file():
                    with path.open(DirectoryBackups.BINARY_READ) as stream:
                        os.fsync(stream.fileno())
            for directory in sorted(
                (p for p in stage.rglob(DirectoryBackups.ALL_FILES) if p.is_dir()),
                key=lambda p: len(p.parts),
                reverse=self.DEEPEST_FIRST,
            ):
                sync_directory(directory)
            sync_directory(stage)
            if destination.exists():
                raise FileExistsError(DirectoryBackups.EXISTS)
            prepare_create(destination)
            os.rename(stage, destination)
            sync_directory(destination.parent)
        finally:
            # mkdtemp가 생성한 형제 staging만 제거한다. 사용자 경로는 삭제하지 않는다.
            if stage.exists():
                shutil.rmtree(stage)
        return destination

    def create(self, source, destination, *, metadata):
        source, destination = self._checked(source), self._checked(destination)
        if source == destination or source in destination.parents:
            raise ValueError(DirectoryBackups.NESTED_DESTINATION)

        def build(stage):
            before = self._inventory(source)
            shutil.copytree(source, self.project_root(stage))
            after = self._inventory(self.project_root(stage))
            if before != after or before != self._inventory(source):
                raise RuntimeError(DirectoryBackups.SOURCE_CHANGED)
            atomic_json(
                self.manifest_path(stage), BackupManifest.create(self.FORMAT_VERSION, metadata, after).record
            )

        return self._publish(destination, build)

    def verify(self, source):
        source = self._checked(source)
        self._checked(self.project_root(source))
        self._checked(self.manifest_path(source))
        manifest = BackupManifest.read(read_json(self.manifest_path(source)), self.FORMAT_VERSION)
        if manifest.files != self._inventory(self.project_root(source)):
            raise ValueError(DirectoryBackups.CHECKSUM_MISMATCH)
        return manifest.record

    def restore(self, source, destination, *, validate):
        self.verify(source)

        def build(stage):
            shutil.copytree(self.project_root(source), stage, dirs_exist_ok=self.COPY_INTO_STAGE)
            if self._inventory(stage) != BackupManifest(self.verify(source)).files:
                raise ValueError(DirectoryBackups.RESTORE_CHANGED)
            validate(stage)

        return self._publish(destination, build)
