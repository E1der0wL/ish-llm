"""Application-selected file/cwd authority, independent of the working directory."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field, fields
import os
from pathlib import Path
import stat

from jsonschema import Draft202012Validator
from llm.core.schema import object_schema
from llm.core.contracts import JsonValue


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
class PathAccess(JsonValue):
    INVALID_ROOTS = 'Access roots must be absolute normalized paths'
    INVALID_PATH = 'Expected a file path'
    RESERVED_PATH = 'Path is reserved for Tool storage'
    LINKED_PATH = 'Access does not permit symlinks (symbolic links)'
    OUTSIDE_ROOTS = 'Path is outside the allowed roots (working root unless explicit access is configured)'
    DIRECTORY_REQUIRED = 'Expected a directory'
    REGULAR_FILE = 'Expected a regular file'

    PARENT_PATH = ".."
    RESERVED_PREFIX = ".llm-"
    NUL = "\x00"
    BINARY_MODE = "rb"
    EXISTING_PATH = False
    EXISTING_DIRECTORY = False
    NO_CREATE_BOUNDARY = None
    READ_FILE = False
    DESCRIPTOR_PATH = "/proc/self/fd/{descriptor}"

    roots: tuple[Path, ...] = field(metadata={
        "type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True})
    follow_symlinks: bool = field(metadata={"type": "boolean"})

    @classmethod
    def schema(cls):
        properties = {item.name: deepcopy(dict(item.metadata)) for item in fields(cls)}
        return object_schema(properties, required=list(properties))

    @classmethod
    def read(cls, value):
        Draft202012Validator(cls.schema()).validate(value)
        result = cls.from_dict(value)
        if any(not root.is_absolute() or cls.PARENT_PATH in root.parts for root in result.roots):
            raise ValueError(PathAccess.INVALID_ROOTS)
        return result

    def resolve(self, root, value, *, missing=EXISTING_PATH):
        if not isinstance(value, str) or not value or self.NUL in value:
            raise ValueError(PathAccess.INVALID_PATH)
        requested = Path(value)
        target = requested if requested.is_absolute() else root / requested
        protected = target.relative_to(root).parts if target.is_relative_to(root) else ()
        if any(part.startswith(self.RESERVED_PREFIX) for part in protected):
            raise ValueError(PathAccess.RESERVED_PATH)
        if not self.follow_symlinks and any(p.is_symlink() for p in (target, *target.parents)):
            raise ValueError(PathAccess.LINKED_PATH)
        resolved = target.resolve(strict=not missing)
        protected = resolved.relative_to(root).parts if resolved.is_relative_to(root) else ()
        if any(part.startswith(self.RESERVED_PREFIX) for part in protected):
            raise ValueError(PathAccess.RESERVED_PATH)
        # Roots are authority declarations, not links whose targets may silently
        # change the grant. The final canonical target must be beneath a declared root.
        if not any(resolved == allowed or resolved.is_relative_to(allowed) for allowed in self.roots):
            raise ValueError(PathAccess.OUTSIDE_ROOTS)
        return resolved

    @staticmethod
    @contextmanager
    def directory(target, *, create=EXISTING_DIRECTORY, create_within=NO_CREATE_BOUNDARY):
        """Pin canonical parents; never follow a link introduced after resolution."""
        fd = os.open(target.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            current = Path(target.anchor)
            for part in target.parts[1:]:
                current = current / part
                if create and (create_within is PathAccess.NO_CREATE_BOUNDARY or current.is_relative_to(create_within)):
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
    def open(self, root, value, *, directory=READ_FILE):
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
                    raise ValueError(PathAccess.DIRECTORY_REQUIRED)
                yield fd
            else:
                if not stat.S_ISREG(mode):
                    raise ValueError(PathAccess.REGULAR_FILE)
                with os.fdopen(os.dup(fd), self.BINARY_MODE) as stream:
                    yield stream
        finally:
            os.close(fd)

    @classmethod
    def descriptor_path(cls, descriptor):
        """Linux descriptor path keeps the already-pinned directory identity."""
        return cls.DESCRIPTOR_PATH.format(descriptor=descriptor)
