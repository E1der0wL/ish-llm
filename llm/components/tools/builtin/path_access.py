"""Application-selected file/cwd authority, independent of the working directory."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
from pathlib import Path
import stat

from jsonschema import Draft202012Validator
from llm.core.schema import object_schema


_path_access = ContextVar("builtin_path_access", default=None)


@contextmanager
def path_scope(access):
    token = _path_access.set(access)
    try:
        yield
    finally:
        _path_access.reset(token)


def current_path_access():
    return _path_access.get()


@dataclass(frozen=True, slots=True)
class PathAccess:
    roots: tuple[Path, ...]
    follow_symlinks: bool

    @classmethod
    def schema(cls):
        return object_schema({
            "roots": {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True},
            "follow_symlinks": {"type": "boolean"},
        }, required=["roots", "follow_symlinks"])

    @classmethod
    def read(cls, value):
        Draft202012Validator(cls.schema()).validate(value)
        roots = tuple(Path(item) for item in value["roots"])
        if any(not root.is_absolute() or ".." in root.parts for root in roots):
            raise ValueError("Access roots must be absolute normalized paths")
        return cls(roots, value["follow_symlinks"])

    def to_dict(self):
        return {"roots": [str(root) for root in self.roots], "follow_symlinks": self.follow_symlinks}

    def resolve(self, root, value, *, missing=False):
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError("Expected a file path")
        requested = Path(value)
        target = requested if requested.is_absolute() else root / requested
        protected = target.relative_to(root).parts if target.is_relative_to(root) else ()
        if any(part.startswith(".llm-") for part in protected):
            raise ValueError("Path is reserved for Tool storage")
        if not self.follow_symlinks and any(p.is_symlink() for p in (target, *target.parents)):
            raise ValueError("Access does not permit symlinks (symbolic links)")
        resolved = target.resolve(strict=not missing)
        protected = resolved.relative_to(root).parts if resolved.is_relative_to(root) else ()
        if any(part.startswith(".llm-") for part in protected):
            raise ValueError("Path is reserved for Tool storage")
        # Roots are authority declarations, not links whose targets may silently
        # change the grant. The final canonical target must be beneath a declared root.
        if not any(resolved == allowed or resolved.is_relative_to(allowed) for allowed in self.roots):
            raise ValueError("Path is outside the allowed roots (working root unless explicit access is configured)")
        return resolved

    @staticmethod
    @contextmanager
    def directory(target, *, create=False, create_within=None):
        """Pin canonical parents; never follow a link introduced after resolution."""
        fd = os.open(target.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            current = Path(target.anchor)
            for part in target.parts[1:]:
                current = current / part
                if create and (create_within is None or current.is_relative_to(create_within)):
                    try:
                        os.mkdir(part, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def open(self, root, value, *, directory=False):
        target = self.resolve(root, value)
        # Walk canonical components using directory descriptors. O_NOFOLLOW on
        # every component prevents a link replacement between resolution and open.
        fd = os.open(target.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = target.parts[1:]
            for index, part in enumerate(parts):
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if index < len(parts) - 1 or directory:
                    flags |= os.O_DIRECTORY
                next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            mode = os.fstat(fd).st_mode
            if directory:
                if not stat.S_ISDIR(mode):
                    raise ValueError("Expected a directory")
                yield fd
            else:
                if not stat.S_ISREG(mode):
                    raise ValueError("Expected a regular file")
                with os.fdopen(os.dup(fd), "rb") as stream:
                    yield stream
        finally:
            os.close(fd)
