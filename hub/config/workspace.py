"""Hub data-root selection, independent of tool working directories."""

from pathlib import Path


class WorkspaceSelection:
    """Startup data-root binding and selections cleared when that root changes."""

    UNBOUND_ANCHOR = None
    NO_PROJECT = None
    NO_SESSION = None


class WorkspacePath:
    """Selection grammar and destination checks; never moves workspace data."""

    UNSELECTED = None
    FORBIDDEN = ("\0", "\n", "\r")
    INVALID = "workspace_root must be a single directory path"
    EXPANSION_FAILED = "Cannot expand workspace_root"
    RELATIVE = "workspace_root must be an absolute path or start with ~/"
    BROKEN_LINK = "workspace_root contains a broken symbolic link"
    NOT_DIRECTORY = "workspace_root must name a directory, not a file"
    INACCESSIBLE = "workspace_root must be writable and accessible"


def workspace_path(value: str) -> Path | None:
    if not isinstance(value, str) or any(
        char in value for char in WorkspacePath.FORBIDDEN
    ):
        raise ValueError(WorkspacePath.INVALID)
    if not value.strip():
        return WorkspacePath.UNSELECTED
    try:
        path = Path(value.strip()).expanduser()
    except RuntimeError as error:
        raise ValueError(WorkspacePath.EXPANSION_FAILED) from error
    if not path.is_absolute():
        raise ValueError(WorkspacePath.RELATIVE)
    return path


def validate_destination(value: str) -> None:
    """Validate without creating/copying a workspace or touching an active Run."""
    import os

    path = workspace_path(value)
    if path is WorkspacePath.UNSELECTED:
        return
    ancestor = path
    while not ancestor.exists():
        if ancestor.is_symlink():
            raise ValueError(WorkspacePath.BROKEN_LINK)
        parent = ancestor.parent
        if parent == ancestor:
            break
        ancestor = parent
    if not ancestor.is_dir():
        raise ValueError(WorkspacePath.NOT_DIRECTORY)
    if not os.access(ancestor, os.W_OK | os.X_OK):
        raise ValueError(WorkspacePath.INACCESSIBLE)
