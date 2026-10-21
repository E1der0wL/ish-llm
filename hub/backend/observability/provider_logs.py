"""Bounded, read-only projection of llm's existing provider diagnostic files."""

from collections import deque
import json
from pathlib import Path


READ_FAILURE_STATUS = "warning"


def read_provider_logs(workspace, project_id, *, limit=300):
    root = Path(workspace).expanduser()
    # RunManager scopes logging to the ProjectRepository root (workspace/projects).
    # Direct provider calls and initialization use workspace/logs instead.
    directories = (root / "projects" / "logs", root / "logs")
    rows = []
    try:
        paths = sorted((p for directory in directories for p in directory.glob("providers-*.log") if not p.is_symlink()),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:8]
    except OSError as error:
        return [_read_error(error)]
    for path in paths:
        try:
            with path.open("rb") as stream:
                stream.seek(0, 2)
                start = max(0, stream.tell() - 128 * 1024)
                stream.seek(start)
                if start:
                    stream.readline()  # Discard a truncated record.
                lines = deque(stream.read(128 * 1024).decode("utf-8", errors="replace").splitlines(), maxlen=limit)
            for line in lines:
                prefix, separator, payload = line.partition(" {")
                if not separator:
                    continue
                try:
                    value = json.loads("{" + payload)
                    date, time, level = prefix.split(maxsplit=2)
                    if not isinstance(value, dict):
                        continue
                    details = value.get("details") or {}
                    if not isinstance(details, dict):
                        continue
                    owner = details.get("project_id")
                    if owner and owner != project_id:
                        continue
                    rows.append({"time": f"{date} {time}", "event": "provider", "status": level.lower(),
                        "source": {"session_id": details.get("session_id") or "—", "run_id": details.get("run_id") or "—",
                                   **({"step_id": details["step_id"]} if details.get("step_id") else {})},
                        "code": value.get("code") or "provider_library_log", "provider": True,
                        "scope": "project" if owner else "workspace", "details": value, "log_path": str(path)})
                except (ValueError, TypeError):
                    continue
        except OSError as error:
            rows.append(_read_error(error))
    return sorted(rows, key=lambda row: row["time"])[-limit:]


def associate_provider_logs(activity, provider):
    """Attach Run-scoped evidence to its failure without changing commit order.

    Group all attempts under run.failed, retaining Step/call IDs in each record;
    only use step.failed if the bounded activity window lacks that Run event.
    """
    result = [dict(row) for row in activity]
    targets = {}
    for row in result:
        if row["event"] not in ("step.failed", "run.failed"):
            continue
        source = row["source"]
        key = source.get("session_id"), source.get("run_id")
        if all(key) and (key not in targets or row["event"] == "run.failed"):
            targets[key] = row
    remaining = []
    for row in provider:
        source = row["source"]
        target = targets.get((source.get("session_id"), source.get("run_id")))
        if target is None or not row.get("provider"):
            remaining.append(row)
        else:
            target.setdefault("provider_diagnostics", []).append(row)
    return result + remaining


def _read_error(error):
    return {"time": "", "event": "provider", "status": READ_FAILURE_STATUS, "source": {},
            "code": "provider_log_read_failed", "provider": True, "scope": "workspace",
            "details": {"error": str(error)}}
