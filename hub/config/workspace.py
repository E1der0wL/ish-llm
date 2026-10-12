"""Hub data-root selection, independent of tool working directories."""

from pathlib import Path


def workspace_path(value: str) -> Path | None:
    if not isinstance(value, str) or "\0" in value or "\n" in value or "\r" in value:
        raise ValueError("workspace_root must be a single directory path")
    if not value.strip():
        return None
    try:
        path = Path(value.strip()).expanduser()
    except RuntimeError as error:
        raise ValueError("Cannot expand workspace_root") from error
    if not path.is_absolute():
        raise ValueError("workspace_root must be an absolute path or start with ~/")
    return path


def validate_destination(value: str) -> None:
    """Validate without creating/copying a workspace or touching an active Run."""
    import os
    path = workspace_path(value)
    if path is None:
        return
    ancestor = path
    while not ancestor.exists():
        if ancestor.is_symlink():
            raise ValueError("workspace_root contains a broken symbolic link")
        parent = ancestor.parent
        if parent == ancestor:
            break
        ancestor = parent
    if not ancestor.is_dir():
        raise ValueError("workspace_root must name a directory, not a file")
    if not os.access(ancestor, os.W_OK | os.X_OK):
        raise ValueError("workspace_root must be writable and accessible")
