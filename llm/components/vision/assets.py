"""Vision asset layout and transactional file effects; record policy stays with its Owner."""

import os
from pathlib import Path
from llm.services.infrastructure.storage import (
    make_directory,
    prepare_replace,
    temporary_file,
    sync_directory,
)
from .records import VisionImageIdentity


class VisionAssets:
    DIRECTORY = "assets"
    SUFFIX = ".bin"
    WRITE_MODE = "wb"
    MISSING_TEMPORARY_OK = True

    @classmethod
    def path(cls, component, project, digest):
        if not VisionImageIdentity.valid_digest(digest):
            raise ValueError(VisionImageIdentity.INVALID_DIGEST)
        return component._checked(component.root(project) / cls.DIRECTORY / (digest + cls.SUFFIX))

    @classmethod
    def write(cls, component, project, raw, digest):
        path = component._blob(project, digest)
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError(VisionImageIdentity.ASSET_MISMATCH)
            return
        make_directory(path.parent)
        prepare_replace(path)
        fd, temporary = temporary_file(path)
        try:
            with os.fdopen(fd, cls.WRITE_MODE) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            sync_directory(path.parent)
        finally:
            Path(temporary).unlink(missing_ok=cls.MISSING_TEMPORARY_OK)
