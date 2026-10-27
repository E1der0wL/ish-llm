"""Portable source evidence and isolated Linux test snapshots (never deployment)."""

import hashlib
import os
from pathlib import Path
import subprocess


TEXT_SUFFIXES = {'.py', '.md', '.json', '.toml', '.lock', '.txt', '.sh', '.yaml', '.yml', '.apparmor'}
TEXT_NAMES = {'.gitignore', '.python-version'}
DIRECTORIES = ('llm', 'hub', 'tests/llm', 'tests/hub', 'examples/llm', 'examples/hub',
               'docs', 'ish.platform/src', '.github/workflows')
ROOT_FILES = ('pyproject.toml', '.python-version', '.gitignore', 'README.md', 'AGENTS.md',
              'tests/__init__.py', 'tests/README.md', 'tests/source_tree.py', 'examples/__init__.py',
              'ish.platform/pyproject.toml')
EXCLUDED = {'__pycache__', 'reports', 'manual', '.git'}


def source_bytes(path: Path) -> bytes:
    """Hash/copy text as LF across Git checkouts; preserve every other byte."""
    raw = path.read_bytes()
    if path.suffix in TEXT_SUFFIXES or path.name in TEXT_NAMES:
        return raw.replace(b'\r\n', b'\n')
    return raw


def source_hash(path: Path) -> str:
    return hashlib.sha256(source_bytes(path)).hexdigest()


def snapshot_files(root: Path) -> list[Path]:
    paths = {root / name for name in ROOT_FILES if (root / name).is_file()}
    for directory in DIRECTORIES:
        for folder, dirs, names in os.walk(root / directory):
            dirs[:] = [name for name in dirs if name not in EXCLUDED]
            for name in names:
                path = Path(folder) / name
                if path.suffix in TEXT_SUFFIXES or name in TEXT_NAMES:
                    paths.add(path)
    return sorted(paths)


def copy_snapshot(root: Path, target: Path) -> dict:
    """Copy both plugins and shared evidence even when only one suite is selected."""
    files = {}
    for path in snapshot_files(root):
        if path.is_symlink():
            raise ValueError(f'Snapshot input must be a regular source file: {path}')
        relative = path.relative_to(root)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        raw = path.read_bytes()
        copied = source_bytes(path)
        destination.write_bytes(copied)
        files[relative.as_posix()] = {'source_sha256': hashlib.sha256(raw).hexdigest(),
                                     'sha256': hashlib.sha256(copied).hexdigest()}
    # Coverage uses Git's product inventory, including new/uncommitted files.
    # A private index reproduces that inventory without borrowing the real .git.
    subprocess.run(['git', 'init', '--quiet', str(target)], check=True)
    subprocess.run(['git', 'add', '--force', '--all'], cwd=target, check=True,
                   stdout=subprocess.DEVNULL)
    return files
