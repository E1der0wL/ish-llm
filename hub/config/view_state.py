"""Hub-owned reading positions; no changes to LLM domain records."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from threading import Lock

from .store import WorkspaceJSONStore


@dataclass(frozen=True, slots=True)
class ReadingPosition:
    message_id: str = ""
    offset: int = 0
    line: int = 0


class ViewStateStore(WorkspaceJSONStore):
    def __init__(self, workspace):
        super().__init__(workspace, "view-state.json")

    def _read(self):
        data = self._read_json({"version": 1, "projects": {}})
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
        self._write_json(data)


class ViewStateWriter:
    """Coalesce UI position writes outside the PTK and backend execution loops."""

    def __init__(self, store, on_error):
        self.store, self.on_error = store, on_error
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hub-view-state")
        self.lock = Lock()
        self.pending = {}
        self.latest = {}
        self.running = False
        self.loading = set()

    def is_loaded(self, project_id):
        with self.lock:
            return project_id in self.latest

    def load_background(self, project_id, ready):
        """Use the existing writer lane for first-read filesystem work too."""
        with self.lock:
            if project_id in self.loading:
                return
            self.loading.add(project_id)
        def read():
            error = None
            try:
                selected, positions = self.store.load(project_id)
            except Exception as caught:
                selected, positions, error = "", {}, caught
            with self.lock:
                self.latest.setdefault(project_id, (selected, positions))
                self.loading.discard(project_id)
            ready(error)
        self.executor.submit(read)

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
