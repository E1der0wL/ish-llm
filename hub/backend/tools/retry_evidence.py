"""Optional, bounded evidence for argument repair, independent of the selector."""

import asyncio
from dataclasses import dataclass, fields
from difflib import SequenceMatcher
import os
from pathlib import Path
from time import monotonic
from jsonschema import ValidationError

from llm.components.tools.constraints import constrained_arguments
from llm.components.tools.builtin.file_values import FilePathArguments
from llm.components.tools.builtin.workspace_values import WorkspaceSelection
from llm.components.tools.builtin.files import FileTools


class FileCandidateSearch:
    INITIAL_DEPTH = 0
    DEPTH_INCREMENT = 1
    INITIAL_COUNT = 0
    COUNT_INCREMENT = 1
    MAXIMUM_ENTRIES = 4096
    MAXIMUM_SECONDS = 0.15
    MAXIMUM_DEPTH = 5
    MINIMUM_SCORE = 0.6
    MAXIMUM_RESULTS = 12
    HIDDEN_PREFIX = "."
    EXCLUDED_DIRECTORIES = ("node_modules", "__pycache__", "venv")
    FOLLOW_LINKS = False
    NO_JUNK_FILTER = None


@dataclass(frozen=True, slots=True)
class FileCandidate:
    score: float
    path: str


@dataclass(frozen=True, slots=True)
class FileCandidateResult:
    existing_path_candidates: list[str]
    search_complete: bool

    def to_record(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


async def file_candidates(request):
    # A workspace keeps its own root; neither this observer nor model evidence grants access.
    if not request.working_directory or WorkspaceSelection.supplied(
        request.failure.arguments
    ):
        return {}
    return await asyncio.to_thread(_file_candidates, request)


def _file_candidates(request):
    root = Path(request.working_directory)
    original = FilePathArguments.optional_path(request.failure.arguments)
    if not isinstance(original, str) or not root.is_dir():
        return {}
    original = Path(original)
    if original.is_absolute():
        try:
            original = original.relative_to(root)
        except ValueError:
            return {}
    if FileTools.PARENT_PATH in original.parts:
        return {}
    matches, folders = [], [(root, FileCandidateSearch.INITIAL_DEPTH)]
    count, started = FileCandidateSearch.INITIAL_COUNT, monotonic()
    while (
        folders
        and count < FileCandidateSearch.MAXIMUM_ENTRIES
        and monotonic() - started < FileCandidateSearch.MAXIMUM_SECONDS
    ):
        folder, depth = folders.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    count += FileCandidateSearch.COUNT_INCREMENT
                    if (
                        count >= FileCandidateSearch.MAXIMUM_ENTRIES
                        or monotonic() - started >= FileCandidateSearch.MAXIMUM_SECONDS
                    ):
                        break
                    if entry.is_symlink() or entry.name.startswith(
                        FileCandidateSearch.HIDDEN_PREFIX
                    ):
                        continue
                    if entry.is_dir(follow_symlinks=FileCandidateSearch.FOLLOW_LINKS):
                        if (
                            depth < FileCandidateSearch.MAXIMUM_DEPTH
                            and entry.name
                            not in FileCandidateSearch.EXCLUDED_DIRECTORIES
                        ):
                            folders.append(
                                (
                                    Path(entry.path),
                                    depth + FileCandidateSearch.DEPTH_INCREMENT,
                                )
                            )
                        continue
                    if not entry.is_file(
                        follow_symlinks=FileCandidateSearch.FOLLOW_LINKS
                    ):
                        continue
                    path = Path(entry.path).relative_to(root).as_posix()
                    score = SequenceMatcher(
                        FileCandidateSearch.NO_JUNK_FILTER,
                        original.name.lower(),
                        entry.name.lower(),
                    ).ratio()
                    if score < FileCandidateSearch.MINIMUM_SCORE:
                        continue
                    try:
                        constrained_arguments(
                            request.schema,
                            FilePathArguments.with_path(
                                request.failure.arguments, path
                            ),
                            request.constraints,
                        )
                    except (ValueError, TypeError, ValidationError):
                        continue
                    matches.append(FileCandidate(score, path))
        except OSError:
            continue
    return FileCandidateResult(
        [
            item.path
            for item in sorted(matches, key=lambda item: (-item.score, item.path))[
                : FileCandidateSearch.MAXIMUM_RESULTS
            ]
        ],
        not folders
        and count < FileCandidateSearch.MAXIMUM_ENTRIES
        and monotonic() - started < FileCandidateSearch.MAXIMUM_SECONDS,
    ).to_record()
