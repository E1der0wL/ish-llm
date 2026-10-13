"""Optional, bounded evidence for argument repair, independent of the selector."""

import asyncio
from difflib import SequenceMatcher
import os
from pathlib import Path
from time import monotonic
from jsonschema import ValidationError

from llm.components.tools.constraints import constrained_arguments


async def file_candidates(request):
    # Workspace IDs have a different ownership/root contract. Never substitute the
    # shell root for a sandbox. The generic model/custom evidence provider can abstain.
    if not request.working_directory or "workspace_id" in request.failure.arguments:
        return {}
    return await asyncio.to_thread(_file_candidates, request)


def _file_candidates(request):
    root = Path(request.working_directory)
    original = request.failure.arguments.get("path")
    if not isinstance(original, str) or not root.is_dir():
        return {}
    original = Path(original)
    if original.is_absolute():
        try:
            original = original.relative_to(root)
        except ValueError:
            return {}
    if ".." in original.parts:
        return {}
    matches, folders = [], [(root, 0)]
    count, started = 0, monotonic()
    while folders and count < 4096 and monotonic() - started < 0.15:
        folder, depth = folders.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    count += 1
                    if count >= 4096 or monotonic() - started >= 0.15:
                        break
                    if entry.is_symlink() or entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if depth < 5 and entry.name not in ("node_modules", "__pycache__", "venv"):
                            folders.append((Path(entry.path), depth + 1))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    path = Path(entry.path).relative_to(root).as_posix()
                    score = SequenceMatcher(None, original.name.lower(), entry.name.lower()).ratio()
                    if score < 0.6:
                        continue
                    try:
                        constrained_arguments(request.schema, {**request.failure.arguments, "path": path},
                                              request.constraints)
                    except (ValueError, TypeError, ValidationError):
                        continue
                    matches.append((score, path))
        except OSError:
            continue
    return {"existing_path_candidates": [path for _, path in sorted(matches, key=lambda item: (-item[0], item[1]))[:12]],
            "search_complete": not folders and count < 4096 and monotonic() - started < 0.15}
