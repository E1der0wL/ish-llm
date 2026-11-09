"""Explicit completion work and immutable snapshots; no periodic command execution."""
import asyncio
from dataclasses import dataclass
from enum import StrEnum

from ...core.tasks import run_blocking
from .analysis import HelpAnalysis
from .models import Catalog, MergeMode, Registration
from .store import CompletionStore


class Phase(StrEnum):
    IDLE = "idle"
    EXECUTING = "executing"
    ANALYZING = "analyzing"
    SAVING = "saving"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Request:
    prefix: tuple[str, ...]
    command: str
    mode: MergeMode


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    prefix: tuple[str, ...]
    count: int = 0
    error: str = ""


@dataclass(frozen=True, slots=True)
class Progress:
    prefix: tuple[str, ...]
    phase: Phase
    hub: bool = False


class CompletionRuntime:
    def __init__(self, directory, publish, invalidate, agent, completed=lambda result: None,
                 *, progressed=lambda progress: None):
        self.store = CompletionStore(directory)
        self._publish, self._invalidate, self._agent = publish, invalidate, agent
        self._completed = completed
        self._progressed = progressed
        self.catalog = Catalog()
        self.phase = Phase.IDLE
        self.request = None
        self._expected = None
        self.error = ""
        self.used_hub = False
        self._loaded = self._closed = False
        self._catalog_ready = False
        self._task = None
        self._lock = asyncio.Lock()

    @property
    def busy(self):
        return self._task is not None and not self._task.done()

    @property
    def catalog_ready(self):
        """A successfully loaded catalog remains editable after a failed lookup."""
        return self._catalog_ready

    def _start(self, operation):
        if self._closed or self.busy:
            return False
        async def work():
            try:
                await operation()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = str(exc) or type(exc).__name__
                if self.phase in (Phase.ANALYZING, Phase.SAVING):
                    self.failed(self.error)
            finally:
                self._invalidate()
        self._task = asyncio.create_task(work())
        self._invalidate()
        return True

    def _set_catalog(self, catalog):
        self.catalog = catalog
        self._catalog_ready = True
        self._publish(catalog)

    def _set_phase(self, phase, *, hub=False):
        self.phase = phase
        if self.request is not None and not self._closed:
            self._progressed(Progress(self.request.prefix, phase, hub))
        self._invalidate()

    def load(self):
        if self.busy or self.phase == Phase.EXECUTING:
            return False
        async def operation():
            async with self._lock:
                catalog = await run_blocking(self.store.load)
                if not self._closed:
                    self._set_catalog(catalog)
        self.error = ""
        return self._start(operation)

    def ensure_loaded(self):
        if not self._loaded:
            self._loaded = self.load()

    def begin(self, request):
        if self.busy or self.phase == Phase.EXECUTING:
            raise ValueError("Completion work is already running")
        self.request, self._expected = request, self.catalog
        self.error = ""
        self._set_phase(Phase.EXECUTING)

    def failed(self, message):
        self.phase, self.error = Phase.FAILED, message
        if self.request is not None and not self._closed:
            self._completed(RegistrationResult(self.request.prefix, error=message))
        self._invalidate()

    def receive(self, output, config):
        if self._closed or self.request is None:
            return
        if output.error:
            self.failed(output.error)
            return
        if output.command != self.request.command:
            self.failed("The returned lookup does not match the pending request")
            return
        if output.truncated:
            self.failed("ish truncated the lookup output; existing completions were preserved")
            return
        self._set_phase(Phase.ANALYZING, hub=config.hub)
        self.used_hub = False
        request, expected = self.request, self._expected
        async def analyze():
            try:
                result = await self._agent.analyze(output.text, config.timeout_s) if config.hub else None
            except Exception as exc:
                self.error = "Hub: " + (str(exc) or type(exc).__name__)
                result = None
            self.used_hub = result is not None
            if self._closed or self.request is not request:
                return
            if result is None:
                if config.hub:
                    self._set_phase(Phase.ANALYZING)
                result = await run_blocking(HelpAnalysis.local, output.text)
            if not result.items:
                raise ValueError("No supported options or subcommands found; existing completions were preserved")
            if self._closed or self.request is not request:
                return
            self._set_phase(Phase.SAVING)
            async with self._lock:
                catalog = await run_blocking(self.store.update, expected, lambda catalog:
                    catalog.put((catalog.get(request.prefix) or Registration(request.prefix))
                                .apply(result, request.mode, request.command)))
            if not self._closed:
                self._set_catalog(catalog)
                self.phase, self.request, self._expected = Phase.IDLE, None, None
                previous = expected.get(request.prefix) or Registration(request.prefix)
                self._completed(RegistrationResult(request.prefix, len(previous.collection_items(result))))
        if not self._start(analyze):
            self.failed("Completion analysis could not start")

    def cancel_analysis(self):
        if self.phase in (Phase.EXECUTING, Phase.ANALYZING):
            if self.busy:
                self._task.cancel()
            self._set_phase(Phase.IDLE)
            self.request = self._expected = None

    def save_changes(self, expected, draft, completed):
        """Persist an explicit management draft with the same optimistic store contract."""
        if self._closed or self.busy or self.phase == Phase.EXECUTING:
            return False
        async def operation():
            try:
                async with self._lock:
                    catalog = await run_blocking(self.store.update, expected,
                                                lambda current: current.replace_contents(draft))
            except Exception as exc:
                if not self._closed:
                    completed(str(exc) or type(exc).__name__)
            else:
                if not self._closed:
                    self._set_catalog(catalog)
                    completed("")
        return self._start(operation)

    def close(self):
        self._closed = True
        if self.busy:
            self._task.cancel()
