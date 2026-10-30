"""Repo map input and bounded observation declarations; no execution authority."""

from dataclasses import asdict, dataclass, field
from typing import ClassVar

from llm.components.tools.builtin.arguments import BuiltinArguments
from llm.components.tools.builtin.file_values import FileLimits
from ...config.defaults import HubDefaults


@dataclass(frozen=True, slots=True, kw_only=True)
class RepoMapArguments(FileLimits):
    NAME: ClassVar[str] = "repo_map"
    DESCRIPTION: ClassVar[str] = (
        "Map a directory before analyzing or editing it. Returns bounded file structure, "
        "important project files and Python declarations/imports with line numbers, not full source. "
        "Uses the current working directory and existing read permissions. Optional focus ranks "
        "matching paths. Honors local .gitignore files; skips generated directories and "
        "child symlinks. Check incomplete/omissions and use a narrower path or file_read for details. "
        "Requested sizes are capped to the host scan/output budget."
    )
    path: str = field(default=".", metadata={BuiltinArguments.SCHEMA: {"type": "string", "minLength": 1}})
    focus: str = field(default="", metadata={BuiltinArguments.SCHEMA: {"type": "string"}})
    max_chars: int = field(default=HubDefaults.REPO_MAP_MAX_CHARS,
                          metadata={BuiltinArguments.SCHEMA: {"type": "integer", "minimum": 1}})
    max_files: int = field(default=HubDefaults.REPO_MAP_MAX_FILES,
                          metadata={BuiltinArguments.SCHEMA: {"type": "integer", "minimum": 1}})
    max_depth: int = field(default=HubDefaults.REPO_MAP_MAX_DEPTH,
                          metadata={BuiltinArguments.SCHEMA: {"type": "integer", "minimum": 0}})


@dataclass(frozen=True, slots=True)
class RepoMapLimits:
    files: int = HubDefaults.REPO_MAP_MAX_FILES
    entries: int = HubDefaults.REPO_MAP_MAX_ENTRIES
    depth: int = HubDefaults.REPO_MAP_MAX_DEPTH
    chars: int = HubDefaults.REPO_MAP_MAX_CHARS
    file_bytes: int = HubDefaults.REPO_MAP_FILE_BYTES
    seconds: float = HubDefaults.REPO_MAP_SECONDS
    cache_files: int = HubDefaults.REPO_MAP_CACHE_FILES


@dataclass(frozen=True, slots=True)
class RepoMapResult:
    root: str
    map: str
    files_scanned: int
    entries_scanned: int
    incomplete: bool
    omissions: list[str]
    next_action: str = "Read selected paths with file_read; narrow path/focus if this map is incomplete."

    def to_dict(self):
        return asdict(self)


class RepoMapFormat:
    REVISION = "hub-repo-map-v1"
    IGNORE_FILE = ".gitignore"
    IGNORE_BYTES = 32768
    EXCLUDED = frozenset(HubDefaults.REPO_MAP_EXCLUDED)
    KEY_FILES = frozenset(("readme.md", "readme", "pyproject.toml", "package.json", "cargo.toml",
                          "go.mod", "pom.xml", "makefile", "cmakelists.txt", "setup.py",
                          "main.py", "__main__.py", "manage.py", "dockerfile"))
    FOCUS_CHARS = 240
    WORDS = r"[\w.-]+"
    PYTHON = ".py"
    ENCODING = "utf-8-sig"
    MAX_SYMBOLS = 24
    MAX_NODES = 1000
    LINE_CHARS = 160
    NEWLINE = "\n"
    INDENT = "  "
    FILES = "file scan budget"
    ENTRIES = "directory entry budget"
    DEPTH = "depth budget"
    TIME = "scan time budget"
    OUTPUT = "output budget"
    LINKS = "child symlinks skipped"
    ACCESS = "unreadable or changed entries"
    LARGE = "large files: structure only"
    SYMBOLS = "Python declaration budget"
    PARSE = "unparsed Python files: structure only"
    IGNORE = "unreadable, oversized or invalid ignore file; directory skipped"
    EMPTY = "(No visible files in the selected directory.)"
