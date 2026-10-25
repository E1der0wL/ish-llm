"""Bounded, read-only projection of llm's existing provider diagnostic files."""

from llm.core.contracts import ActivityEventType
from llm.core.paths import BackendPaths
from llm.providers.runtime_values import ProviderLogPaths, ProviderLogRecord
from .activity_values import ActivityRecord

from collections import deque
import os
from pathlib import Path


READ_FAILURE_STATUS = ActivityRecord.READ_FAILURE_STATUS


class ProviderLogWindow:
    DEFAULT_LIMIT = 300
    MAX_FILES = 8
    MAX_BYTES = 128 * 1024
    FIRST_BYTE = 0
    NEWEST_FIRST = True
    BINARY_READ = "rb"
    DECODING_ERRORS = "replace"
    ABSENT = None


def read_provider_logs(workspace, project_id, *, limit=ProviderLogWindow.DEFAULT_LIMIT):
    root = Path(workspace).expanduser()
    # RunManager scopes logging to the ProjectRepository root (workspace/projects).
    # Direct provider calls and initialization use workspace/logs instead.
    directories = (
        ProviderLogPaths.scoped_directory(BackendPaths(root).projects),
        ProviderLogPaths.scoped_directory(root),
    )
    rows = []
    try:
        paths = sorted(
            (
                p
                for directory in directories
                for p in directory.glob(ProviderLogPaths.FILE_PATTERN)
                if not p.is_symlink()
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=ProviderLogWindow.NEWEST_FIRST,
        )[: ProviderLogWindow.MAX_FILES]
    except OSError as error:
        return [_read_error(error)]
    for path in paths:
        try:
            with path.open(ProviderLogWindow.BINARY_READ) as stream:
                stream.seek(ProviderLogWindow.FIRST_BYTE, os.SEEK_END)
                start = max(
                    ProviderLogWindow.FIRST_BYTE,
                    stream.tell() - ProviderLogWindow.MAX_BYTES,
                )
                stream.seek(start)
                if start:
                    stream.readline()  # Discard a truncated record.
                lines = deque(
                    stream.read(ProviderLogWindow.MAX_BYTES)
                    .decode(
                        ProviderLogPaths.ENCODING,
                        errors=ProviderLogWindow.DECODING_ERRORS,
                    )
                    .splitlines(),
                    maxlen=limit,
                )
            for line in lines:
                try:
                    value = ProviderLogRecord.read_line(line)
                    if value is ProviderLogWindow.ABSENT:
                        continue
                    owner = value.project_id
                    if owner and owner != project_id:
                        continue
                    rows.append(ActivityRecord.provider(value, path))
                except (ValueError, TypeError):
                    continue
        except OSError as error:
            rows.append(_read_error(error))
    return sorted(rows, key=lambda row: ActivityRecord(row).time)[-limit:]


def associate_provider_logs(activity, provider):
    """Attach Run-scoped evidence to its failure without changing commit order.

    Group all attempts under run.failed, retaining Step/call IDs in each record;
    only use step.failed if the bounded activity window lacks that Run event.
    """
    result = [dict(row) for row in activity]
    targets = {}
    for row in result:
        if ActivityRecord(row).event not in (
            ActivityEventType.STEP_FAILED,
            ActivityEventType.RUN_FAILED,
        ):
            continue
        source = ActivityRecord(row).source
        key = source.identity
        if all(key) and (
            key not in targets
            or ActivityRecord(row).event == ActivityEventType.RUN_FAILED
        ):
            targets[key] = row
    remaining = []
    for row in provider:
        source = ActivityRecord(row).source
        target = targets.get(source.identity)
        if target is ProviderLogWindow.ABSENT or not ActivityRecord(row).is_provider:
            remaining.append(row)
        else:
            ActivityRecord(target).attach_provider(row)
    return result + remaining


def _read_error(error):
    return ActivityRecord.read_error(error)
