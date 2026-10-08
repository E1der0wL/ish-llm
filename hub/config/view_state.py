"""Hub-owned reading positions; no changes to LLM domain records."""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Lock


@dataclass(frozen=True, slots=True)
class ReadingPosition:
    message_id: str = ""
    offset: int = 0
    line: int = 0


class ViewStateStore:
    def __init__(self, workspace):
        self.path = Path(workspace).expanduser() / ".hub" / "view-state.json"

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "projects": {}}
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("projects"), dict):
            raise ValueError("Invalid Hub view state")
        return data

    def last_project(self):
        value = self._read().get("last_project", "")
        return value if isinstance(value, str) else ""

    def load(self, project_id):
        project = self._read()["projects"].get(project_id, {})
        if not isinstance(project, dict) or not isinstance(project.get("positions", {}), dict):
            raise ValueError("Invalid Hub project view state")
        positions = {}
        for session_id, value in project.get("positions", {}).items():
            if (isinstance(value, dict) and isinstance(value.get("message_id"), str)
                    and type(value.get("offset")) is int and value["offset"] >= 0
                    and type(value.get("line")) is int and value["line"] >= 0):
                positions[session_id] = ReadingPosition(value["message_id"], value["offset"], value["line"])
        return project.get("selected", ""), positions

    def save(self, project_id, selected, positions):
        data = self._read()
        projects = data["projects"]
        data["last_project"] = project_id
        project = projects.setdefault(project_id, {})
        project["selected"] = selected
        project.setdefault("positions", {}).update({key: asdict(value) for key, value in positions.items()})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="view-state-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


class ViewStateWriter:
    """Coalesce UI position writes outside the PTK and backend execution loops."""

    def __init__(self, store, on_error):
        self.store, self.on_error = store, on_error
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hub-view-state")
        self.lock = Lock()
        self.pending = {}
        self.latest = {}
        self.running = False

    def save(self, project_id, selected, positions):
        with self.lock:
            # Moving a project to the end preserves the latest active project.
            previous = self.pending.pop(project_id, (selected, {}))[1]
            self.pending[project_id] = (selected, {**previous, **positions})
            self.latest[project_id] = (selected, {**self.latest.get(project_id, (selected, {}))[1], **positions})
            if not self.running:
                self.running = True
                self.executor.submit(self._write)

    def load(self, project_id):
        with self.lock:
            cached = self.latest.get(project_id)
            if cached is not None:
                return cached[0], dict(cached[1])
        return self.store.load(project_id)

    def _write(self):
        while True:
            with self.lock:
                if not self.pending:
                    self.running = False
                    return
                project_id = next(iter(self.pending))
                selected, positions = self.pending.pop(project_id)
            try:
                self.store.save(project_id, selected, positions)
            except Exception as error:
                self.on_error(error)

    def close(self):
        self.executor.shutdown(wait=True)
