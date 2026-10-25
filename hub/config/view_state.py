"""Hub-owned reading positions; no changes to LLM domain records."""

from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from .store import WorkspaceJSONStore
from .view_records import ProjectReadingState, ReadingPosition, ViewStateRecord


class ViewStateStore(WorkspaceJSONStore):
    SELECTED_POSITION = 0
    VERSION = ViewStateRecord.VERSION
    FILE = "view-state.json"

    def __init__(self, workspace):
        super().__init__(workspace, self.FILE)

    def _read(self):
        return ViewStateRecord.read(
            self._read_json(ViewStateRecord.empty(self.VERSION)), self.VERSION
        )

    def last_project(self):
        return self._read().last_project

    def load(self, project_id):
        project = self._read().project(project_id)
        positions = project.positions()
        return project.selected, positions

    @classmethod
    def selected_from(cls, loaded):
        return loaded[cls.SELECTED_POSITION]

    def save(self, project_id, selected, positions):
        data = self._read()
        data.save_positions(project_id, selected, positions)
        self._write_json(data.to_dict())


class ViewStateWriter:
    """Coalesce UI position writes outside the PTK and backend execution loops."""

    MAX_WORKERS = 1
    THREAD_NAME = "hub-view-state"
    WAIT_ON_CLOSE = True
    WRITER_RUNNING = True
    WRITER_IDLE = False
    NO_LOAD_ERROR = None

    def __init__(self, store, on_error):
        self.store, self.on_error = store, on_error
        self.executor = ThreadPoolExecutor(
            max_workers=self.MAX_WORKERS, thread_name_prefix=self.THREAD_NAME
        )
        self.lock = Lock()
        self.pending = {}
        self.latest = {}
        self.running = self.WRITER_IDLE
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
            error = self.NO_LOAD_ERROR
            try:
                selected, positions = self.store.load(project_id)
            except Exception as caught:
                selected, positions, error = ProjectReadingState.UNSELECTED, {}, caught
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
            self.latest[project_id] = (
                selected,
                {**self.latest.get(project_id, (selected, {}))[1], **positions},
            )
            if not self.running:
                self.running = self.WRITER_RUNNING
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
                    self.running = self.WRITER_IDLE
                    return
                project_id = next(iter(self.pending))
                selected, positions = self.pending.pop(project_id)
            try:
                self.store.save(project_id, selected, positions)
            except Exception as error:
                self.on_error(error)

    def close(self):
        self.executor.shutdown(wait=self.WAIT_ON_CLOSE)
