"""On-demand, bounded read-only directory map using native file access grants."""

import ast
import asyncio
from collections import OrderedDict, deque
from dataclasses import replace
import os
import re
from threading import Event, Lock
from time import monotonic
from typing import Any

from llm.components.tools import Tool
from llm.components.tools.builtin import BuiltinTools
from llm.components.tools.builtin.files import FileTools
from llm.components.tools.builtin.path_access import current_path_access
from llm.services.infrastructure.storage import drain_on_cancel

from .repo_map_values import RepoMapArguments, RepoMapFormat as Format, RepoMapLimits, RepoMapResult


class RepoMap:
    """Toolkit-local symbol cache; every read rechecks the current access grant."""

    def __init__(self, toolkit: BuiltinTools, limits: RepoMapLimits | None = None):
        self.toolkit = toolkit
        self.limits = RepoMapLimits() if limits is None else limits
        self._cache = OrderedDict()
        self._lock = Lock()

    @staticmethod
    def _identity(path, info):
        return (str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _symbols(self, files, path, omissions):
        if path.suffix != Format.PYTHON:
            return ()
        with files.read_access.open(files.root, str(path)) as stream:
            info = os.fstat(stream.fileno())
            maximum = self.limits.file_bytes
            if files.max_bytes is not None:
                maximum = min(maximum, files.max_bytes)
            if info.st_size > maximum:
                omissions.add(Format.LARGE)
                return ()
            identity = self._identity(path, info)
            with self._lock:
                cached = self._cache.get(identity)
                if cached is not None:
                    self._cache.move_to_end(identity)
                    lines, reason = cached
                    if reason:
                        omissions.add(reason)
                    return lines
            source = stream.read(maximum + 1)
            if len(source) > maximum or self._identity(path, os.fstat(stream.fileno())) != identity:
                omissions.add(Format.ACCESS)
                return ()
        lines, reason = self._parse(source)
        with self._lock:
            self._cache[identity] = (lines, reason)
            while len(self._cache) > self.limits.cache_files:
                self._cache.popitem(last=False)
        if reason:
            omissions.add(reason)
        return lines

    @staticmethod
    def _parse(source: bytes) -> tuple[tuple[str, ...], str | None]:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError, RecursionError):
            return (), Format.PARSE
        lines, reason = [], None
        for index, node in enumerate(ast.walk(tree)):
            if index >= Format.MAX_NODES or len(lines) >= Format.MAX_SYMBOLS:
                reason = Format.SYMBOLS
                break
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [arg.arg for arg in node.args.posonlyargs]
                if args:
                    args.append("/")
                args.extend(arg.arg for arg in node.args.args)
                if node.args.vararg:
                    args.append("*" + node.args.vararg.arg)
                elif node.args.kwonlyargs:
                    args.append("*")
                args.extend(arg.arg for arg in node.args.kwonlyargs)
                if node.args.kwarg:
                    args.append("**" + node.args.kwarg.arg)
                text = ("async " if isinstance(node, ast.AsyncFunctionDef) else "") + f"def {node.name}({', '.join(args)})"
            elif isinstance(node, ast.ClassDef):
                text = f"class {node.name}"
            elif isinstance(node, ast.Import):
                text = "import " + ", ".join(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                text = "from " + "." * node.level + (node.module or "") + " import " + ", ".join(alias.name for alias in node.names)
            else:
                continue
            lines.append((node.lineno, text[:Format.LINE_CHARS]))
        return tuple(f"L{line}: {text}" for line, text in sorted(lines)), reason

    @staticmethod
    def _ignored(path, directory, rules):
        ignored = False
        for parent, spec in rules:
            relative = path.relative_to(parent).as_posix() + ("/" if directory else "")
            result = spec.check_file(relative)
            if result.include is not None:
                ignored = result.include
        return ignored

    @staticmethod
    def _rules(files, directory, inherited, omissions):
        from pathspec import GitIgnoreSpec

        path = directory / Format.IGNORE_FILE
        try:
            maximum = Format.IGNORE_BYTES if files.max_bytes is None else min(Format.IGNORE_BYTES, files.max_bytes)
            with files.read_access.open(files.root, str(path)) as stream:
                source = stream.read(maximum + 1)
            if len(source) > maximum:
                omissions.add(Format.IGNORE)
                return None
            spec = GitIgnoreSpec.from_lines(source.decode(Format.ENCODING).splitlines())
            return (*inherited, (directory, spec))
        except FileNotFoundError:
            return inherited
        except (OSError, ValueError, UnicodeError):
            omissions.add(Format.IGNORE)
            return None

    def _scan(self, files, arguments, stop):
        root = files.read_path(arguments.path)
        # Verify the root before treating any failure as a partial observation.
        with files.read_access.open(files.root, str(root), directory=True):
            pass
        deadline = monotonic() + self.limits.seconds
        maximum = min(arguments.max_files, self.limits.files)
        depth = min(arguments.max_depth, self.limits.depth)
        chars = int(min(arguments.max_chars, self.limits.chars))
        queue, candidates, omissions = deque([(root, 0, ())]), [], set()
        directories = []
        entries = 0
        while queue and not stop.is_set():
            if monotonic() >= deadline:
                omissions.add(Format.TIME)
                break
            directory, level, inherited = queue.popleft()
            rules = self._rules(files, directory, inherited, omissions)
            if rules is None:
                continue
            try:
                with files.read_access.open(files.root, str(directory), directory=True) as fd, os.scandir(fd) as iterator:
                    for entry in iterator:
                        if stop.is_set() or monotonic() >= deadline:
                            omissions.add(Format.TIME)
                            break
                        if entries >= self.limits.entries or len(candidates) >= maximum:
                            omissions.add(Format.ENTRIES if entries >= self.limits.entries else Format.FILES)
                            break
                        entries += 1
                        if entry.name in Format.EXCLUDED or entry.name.startswith(FileTools.RESERVED_PREFIX):
                            continue
                        path = directory / entry.name
                        if entry.is_symlink():
                            omissions.add(Format.LINKS)
                            continue
                        is_dir = entry.is_dir(follow_symlinks=False)
                        if self._ignored(path, is_dir, rules):
                            continue
                        if is_dir:
                            directories.append(path)
                            if level < depth:
                                queue.append((path, level + 1, rules))
                            else:
                                omissions.add(Format.DEPTH)
                        elif entry.is_file(follow_symlinks=False):
                            candidates.append(path)
            except (OSError, ValueError):
                omissions.add(Format.ACCESS)
            if entries >= self.limits.entries or len(candidates) >= maximum:
                omissions.add(Format.ENTRIES if entries >= self.limits.entries else Format.FILES)
                break
        terms = re.findall(Format.WORDS, arguments.focus[:Format.FOCUS_CHARS].casefold())

        def rank(path):
            name = path.relative_to(root).as_posix()
            return (-sum(term in name.casefold() for term in terms), path.name.casefold() not in Format.KEY_FILES,
                    len(path.relative_to(root).parts), name)

        selected = sorted((*candidates, *directories), key=rank)
        directory_set = set(directories)
        sections, used = [], 0
        for path in selected:
            if stop.is_set():
                break
            directory = path in directory_set
            name = path.relative_to(root).as_posix() + ("/" if directory else "")
            lines = ()
            if monotonic() < deadline and not directory:
                try:
                    lines = self._symbols(files, path, omissions)
                except (OSError, ValueError):
                    omissions.add(Format.ACCESS)
            elif monotonic() >= deadline:
                omissions.add(Format.TIME)
            text = Format.NEWLINE.join((name, *(Format.INDENT + line for line in lines)))
            sections.append(text)
            used += len(text) + 1
            if used >= chars:
                omissions.add(Format.OUTPUT)
                break
        rendered = Format.NEWLINE.join(sections) if sections else Format.EMPTY
        if len(rendered) > chars:
            omissions.add(Format.OUTPUT)
        return RepoMapResult(str(root), rendered[:chars], len(candidates), entries,
                             bool(omissions), sorted(omissions)).to_dict()

    async def invoke(self, value: dict[str, Any]) -> dict[str, Any]:
        if self.toolkit.closed:
            raise RuntimeError(BuiltinTools.CLOSED)
        arguments = RepoMapArguments.read(value)
        access = current_path_access()
        files = self.toolkit.files if access is None else self.toolkit.files.with_access(access)
        files = files.for_arguments(value)
        stop = Event()
        pending = asyncio.create_task(asyncio.to_thread(self._scan, files, arguments, stop))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            stop.set()
            await drain_on_cancel(pending)
            raise

    def tool(self) -> Tool:
        return Tool(RepoMapArguments.NAME, RepoMapArguments.DESCRIPTION, RepoMapArguments.schema(), self.invoke,
                    contract=replace(BuiltinTools.READ_CONTRACT, revision=Format.REVISION,
                                     retry_fields=(RepoMapArguments.path.__name__,)))
