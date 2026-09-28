"""체크섬이 있는 디렉토리 스냅샷. 도메인/Component 형식 검증은 호출 서비스가 담당한다."""

import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path

from llm.services.infrastructure.storage import atomic_json, read_json, sync_directory, prepare_create
from llm.services.infrastructure.transactions import current_transaction


class DirectoryBackups:
    """신규 경로로만 공개하며, 실패한 변환/복원은 원본과 기존 대상을 수정하지 않는다."""

    @staticmethod
    def _checked(path):
        path = Path(path).absolute()
        for entry in (path, *path.parents):
            if entry.is_symlink():
                raise ValueError("Backup paths cannot follow symlinks")
        return path

    def _inventory(self, root):
        result = {}
        for path in sorted(root.rglob("*")):
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ValueError("Backup supports only regular files and directories")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            result[path.relative_to(root).as_posix()] = digest.hexdigest()
        return result

    def _publish(self, destination, build):
        destination = self._checked(destination)
        if destination.exists():
            raise FileExistsError("Backup/restore destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        transaction = current_transaction()
        stage = (transaction.staging_directory() if transaction is not None and transaction.owns(destination)
                 else Path(tempfile.mkdtemp(prefix=".llm-backup-", dir=destination.parent)))
        try:
            build(stage)
            for path in stage.rglob("*"):
                if path.is_file():
                    with path.open("rb") as stream:
                        os.fsync(stream.fileno())
            for directory in sorted((p for p in stage.rglob("*") if p.is_dir()),
                                    key=lambda p: len(p.parts), reverse=True):
                sync_directory(directory)
            sync_directory(stage)
            if destination.exists():
                raise FileExistsError("Backup/restore destination already exists")
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
            raise ValueError("Backup destination cannot be inside its source")
        def build(stage):
            before = self._inventory(source)
            shutil.copytree(source, stage / "project")
            after = self._inventory(stage / "project")
            if before != after or before != self._inventory(source):
                raise RuntimeError("Source changed while creating backup")
            atomic_json(stage / "manifest.json", {"format_version": 1, "metadata": metadata, "files": after})
        return self._publish(destination, build)

    def verify(self, source):
        source = self._checked(source)
        self._checked(source / "project")
        self._checked(source / "manifest.json")
        manifest = read_json(source / "manifest.json")
        if type(manifest.get("format_version")) is not int or manifest["format_version"] != 1:
            raise ValueError("Unsupported backup format_version")
        if manifest["files"] != self._inventory(source / "project"):
            raise ValueError("Backup checksum mismatch")
        return manifest

    def restore(self, source, destination, *, validate):
        self.verify(source)
        def build(stage):
            shutil.copytree(Path(source) / "project", stage, dirs_exist_ok=True)
            if self._inventory(stage) != self.verify(source)["files"]:
                raise ValueError("Backup changed during restore")
            validate(stage)
        return self._publish(destination, build)

    def upgrade(self, source, destination, *, transform, validate):
        """명시적 변환을 복사본에 적용한다. transform은 신뢰한 개발자 코드다."""
        self.verify(source)
        def build(stage):
            root = stage / "project"
            shutil.copytree(Path(source) / "project", root)
            if self._inventory(root) != self.verify(source)["files"]:
                raise ValueError("Backup changed during upgrade")
            transform(root)
            validate(root)
            files = self._inventory(root)
            atomic_json(stage / "manifest.json", {"format_version": 1,
                        "metadata": {"upgraded_from": str(Path(source).absolute())}, "files": files})
        return self._publish(destination, build)
