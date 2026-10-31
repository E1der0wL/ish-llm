"""Keep the LLM loop alive between ish's repeated prompt_async invocations."""

from .observability.reads import ObservationReads
from .callbacks import CommandCallbacks
from ..config.preferences import PreferencesStore
import asyncio
from contextlib import nullcontext
from concurrent.futures import Future
import threading

from .runtime import HubConfig, HubRuntime
from .bootstrap import create_backend
from ..errors.adapters import from_exception
from ..errors.contracts import ErrorOrigin
from .progress import BackendProgress, OperationProgress
from ..locales.keys import MessageKey
from .project.settings_contracts import SettingsOperation


class BackendWorker:
    WRITE_OPERATIONS = frozenset({
        HubRuntime.new_session.__name__, HubRuntime.delete_session.__name__,
        HubRuntime.rename_session.__name__, HubRuntime.invite_agent.__name__,
        HubRuntime.delete_turn.__name__, HubRuntime.cancel_request.__name__,
    })
    READ_OPERATIONS = frozenset({
        HubRuntime.select.__name__, HubRuntime.turns.__name__,
        HubRuntime.delete_turn_plan.__name__, HubRuntime.delete_turns_plan.__name__,
        HubRuntime.project_activity.__name__, HubRuntime.execution_catalog.__name__,
        HubRuntime.approval_details.__name__, HubRuntime.snapshot.__name__,
    })
    ABSENT = None
    OPEN = False
    CLOSED = True
    NOT_RESTARTING = False
    RESTARTING = True
    INITIAL_COUNT = 0
    COUNT_STEP = 1
    SNAPSHOT_COUNT_FIELD = "snapshot_count"
    DEFAULT_OUTPUT_INTERVAL = 0.1
    RESTART_POLL = 0.1
    RECONCILE_INTERVAL = ObservationReads.RECONCILE_SECONDS
    HIDDEN_INTERVAL = 1.0
    CLEANUP_RESULT = -1
    THREAD_NAME = "hub-backend"
    DAEMON = True
    CAPTURE_ERRORS = True
    WORKER_OPERATION = "worker"
    CLOSING_MESSAGE = "Hub backend is closing"
    CLOSED_MESSAGE = "Hub backend is closed"
    SHUTDOWN_FAILED = "Hub backend shutdown failed"
    PREEMPT_OBSERVATION = frozenset(
        {
            HubRuntime.submit_input.__name__,
            HubRuntime.submit.__name__,
            HubRuntime.steer.__name__,
            HubRuntime.interrupt.__name__,
            HubRuntime.submission_state.__name__,
            HubRuntime.instruction_targets.__name__,
            HubRuntime.answer_question.__name__,
            HubRuntime.cancel_request.__name__,
            HubRuntime.select.__name__,
            HubRuntime.focus_history.__name__,
        }
    )
    INDEPENDENT_OPERATIONS = frozenset(
        {
            HubRuntime.project_activity.__name__,
            HubRuntime.diagnose_failures.__name__,
            HubRuntime.execution_catalog.__name__,
            HubRuntime.manage_tools.__name__,
            HubRuntime.manage_records.__name__,
            HubRuntime.approval_details.__name__,
            HubRuntime.answer_question.__name__,
            HubRuntime.interrupt.__name__,
        }
    )
    OBSERVATION_OPERATIONS = frozenset(
        {
            HubRuntime.submission_state.__name__,
            HubRuntime.instruction_targets.__name__,
            HubRuntime.snapshot.__name__,
            HubRuntime.focus_history.__name__,
        }
    )

    def __init__(
        self,
        config: HubConfig,
        publish,
        report_error,
        *,
        backend_factory=create_backend
    ):
        self.config = config
        self.publish = publish
        self.report_error = report_error
        self.backend_factory = backend_factory
        self.loop = BackendWorker.ABSENT
        self.runtime = BackendWorker.ABSENT
        self._ready = threading.Event()
        self._closed = BackendWorker.OPEN
        self._restarting = BackendWorker.NOT_RESTARTING
        self._failure = BackendWorker.ABSENT
        self._shutdown_error = BackendWorker.ABSENT
        self._commands = set()
        self._snapshot_task = BackendWorker.ABSENT
        self.snapshot_count = BackendWorker.INITIAL_COUNT
        self._output_interval = BackendWorker.DEFAULT_OUTPUT_INTERVAL
        self._visible = True
        self._interval_changed = BackendWorker.ABSENT
        self._sidebar_width_pending = BackendWorker.ABSENT
        self._sidebar_width_task = BackendWorker.ABSENT
        self._startup_lock = threading.Lock()
        self._startup_observer = None
        self._request_observer = None
        self._startup_progress = BackendProgress(MessageKey.PROGRESS_BACKEND_PREPARE)
        self._thread = threading.Thread(
            target=self._thread_main,
            name=BackendWorker.THREAD_NAME,
            daemon=BackendWorker.DAEMON,
        )
        self._thread.start()
        self._ready.wait()

    def _thread_main(self) -> None:
        try:
            with asyncio.Runner() as runner:
                self.loop = runner.get_loop()
                self._initialized = asyncio.Event()
                self._stop = asyncio.Event()
                self._interval_changed = asyncio.Event()
                self._ready.set()
                runner.run(self._serve())
        except BaseException as error:
            self._failure = error
            self._shutdown_error = error
            self._ready.set()
            if isinstance(error, Exception):
                self.report_error(
                    from_exception(
                        error,
                        origin=ErrorOrigin.BACKEND,
                        operation=BackendWorker.WORKER_OPERATION,
                    )
                )

    async def _serve(self) -> None:
        self.runtime = HubRuntime(self.config, self.backend_factory)
        self.runtime.request_started = self._publish_request_started
        refresher = BackendWorker.ABSENT
        try:
            try:
                with OperationProgress.bind(self._report_startup):
                    await self.runtime.start()
                    OperationProgress.report(MessageKey.PROGRESS_BACKEND_HISTORY)
                    self.runtime.dirty.clear()
                    initial = await self.runtime.snapshot()
                    self.publish(initial)
                refresher = asyncio.create_task(self._refresh(initial))
            except Exception as error:
                self._failure = error
                self.report_error(
                    from_exception(
                        error,
                        origin=ErrorOrigin.BACKEND,
                        operation=HubRuntime.start.__name__,
                    )
                )
            finally:
                self._initialized.set()
            await self._stop.wait()
            if self._commands:
                await asyncio.gather(
                    *tuple(self._commands),
                    return_exceptions=BackendWorker.CAPTURE_ERRORS
                )
        finally:
            if refresher:
                refresher.cancel()
                await asyncio.gather(
                    refresher, return_exceptions=BackendWorker.CAPTURE_ERRORS
                )
            await self.runtime.close()

    def _report_startup(self, update):
        with self._startup_lock:
            self._startup_progress = update
            if self._startup_observer is not None:
                self._startup_observer(update)

    def observe_startup(self, observer):
        """Replay the latest stage, including work before the UI attached."""
        with self._startup_lock:
            self._startup_observer = observer
            observer(self._startup_progress)

    def observe_requests(self, observer) -> None:
        """Register the UI-loop dispatcher before accepting user input."""
        self._request_observer = observer

    def _publish_request_started(self, observation) -> None:
        if self._request_observer is not None:
            self._request_observer(observation)

    async def _read_snapshot(self):
        async with self.runtime.lock:
            self.snapshot_count = (
                getattr(
                    self,
                    BackendWorker.SNAPSHOT_COUNT_FIELD,
                    BackendWorker.INITIAL_COUNT,
                )
                + BackendWorker.COUNT_STEP
            )
            return await self.runtime.snapshot()

    async def _await_snapshot(self, *, preempt=True):
        """Lifecycle changes supersede an old observation, never a durable write."""
        read = self._snapshot_task
        if not preempt:
            return await read
        wake = None
        changed = False
        try:
            while not read.done():
                wake = asyncio.create_task(self.runtime.dirty.wait())
                done, _ = await asyncio.wait((read, wake), return_when=asyncio.FIRST_COMPLETED)
                if read in done:
                    break
                self.runtime.dirty.clear()
                changed = True
                if self.runtime.urgent_observation:
                    read.cancel()
                    break
            snapshot = await read
            if self.runtime.urgent_observation:
                changed = True
                raise asyncio.CancelledError  # A lifecycle event raced the final read callback.
            return snapshot
        except BaseException:
            read.cancel()
            await asyncio.gather(read, return_exceptions=self.CAPTURE_ERRORS)
            raise
        finally:
            if wake is not None:
                wake.cancel()
                await asyncio.gather(wake, return_exceptions=self.CAPTURE_ERRORS)
            if changed:
                self.runtime.dirty.set()

    async def _refresh(self, initial=None) -> None:
        last = initial
        preempt = True
        loop = asyncio.get_running_loop()
        last_read = loop.time() if initial is not None or not self._visible else -float('inf')
        reconcile_at = loop.time() + self.RECONCILE_INTERVAL
        if initial is None:
            self.runtime.dirty.set()
        while True:
            if self._restarting:
                await asyncio.sleep(BackendWorker.RESTART_POLL)
                continue
            # Events request observations; the interval is only a rate ceiling.
            # Quiet active Runs do not poll at the streaming frame rate. Keep a
            # separate, low-frequency reconciliation for external API edits and
            # subscription loss (neither is execution authority).
            if not self.runtime.dirty.is_set():
                try:
                    async with asyncio.timeout(max(0, reconcile_at - loop.time())):
                        await self.runtime.dirty.wait()
                except TimeoutError:
                    pass
            while True:
                self._interval_changed.clear()
                deadline = last_read + self._refresh_interval()
                if loop.time() >= deadline:
                    break
                # Leave dirty set until the read begins. A burst is one pending
                # frame, not a debounce that postpones the final update forever.
                wake = changed = None
                try:
                    if not self._visible:
                        # Hidden frames have a slower ceiling. A lifecycle or
                        # interaction signal must be able to shorten that wait.
                        # The pending read is already retained by this loop.
                        self.runtime.dirty.clear()
                        wake = asyncio.create_task(self.runtime.dirty.wait())
                        changed = asyncio.create_task(self._interval_changed.wait())
                        await asyncio.wait((wake, changed), timeout=deadline - loop.time(),
                                           return_when=asyncio.FIRST_COMPLETED)
                    else:
                        async with asyncio.timeout(deadline - loop.time()):
                            await self._interval_changed.wait()
                except TimeoutError:
                    break
                finally:
                    if wake is not None:
                        wake.cancel()
                        changed.cancel()
                        await asyncio.gather(wake, changed, return_exceptions=True)
            try:
                self.runtime.dirty.clear()
                self.runtime.urgent_observation = False
                last_read = loop.time()
                self._snapshot_task = asyncio.create_task(self._read_snapshot())
                try:
                    snapshot = await self._await_snapshot(preempt=preempt)
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    # An input command preempted this observation. Retry after
                    # admission rather than making the user wait for all history.
                    self.runtime.dirty.set()
                    preempt = False  # A lifecycle storm must still allow the next frame through.
                    continue
                finally:
                    self._snapshot_task = BackendWorker.ABSENT
                    reconcile_at = loop.time() + self.RECONCILE_INTERVAL
                preempt = True
                if snapshot != last:
                    self.publish(snapshot)
                    last = snapshot
            except Exception as error:
                self.report_error(
                    from_exception(
                        error,
                        origin=ErrorOrigin.BACKEND,
                        operation=HubRuntime.snapshot.__name__,
                    )
                )

    def _refresh_interval(self):
        if self._visible or self.runtime.urgent_observation:
            return self._output_interval
        return max(self.HIDDEN_INTERVAL, self._output_interval)

    def set_visible(self, visible: bool) -> None:
        """Reduce hidden painting work without delaying lifecycle notifications."""
        def apply():
            if self._visible != visible:
                self._visible = visible
                if visible:
                    self.runtime.urgent_observation = True
                    self.runtime.dirty.set()
                self._interval_changed.set()
        if not self._closed and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(apply)

    async def _execute(
        self, operation: str, args, acknowledged=ABSENT, progress=ABSENT, stage=ABSENT
    ):
        with OperationProgress.bind(stage):
            return await self._execute_ready(operation, args, acknowledged, progress)

    async def _execute_ready(self, operation, args, acknowledged, progress):
        if not self._initialized.is_set():
            OperationProgress.report(MessageKey.PROGRESS_BACKEND_WAIT)
        await self._initialized.wait()
        if self._closed or self._restarting:
            raise RuntimeError(BackendWorker.CLOSING_MESSAGE)
        if self._failure:
            raise RuntimeError(str(self._failure))
        task = asyncio.current_task()
        self._commands.add(task)
        try:
            if operation in BackendWorker.PREEMPT_OBSERVATION:
                if self._snapshot_task is not BackendWorker.ABSENT:
                    self._snapshot_task.cancel()
            # These operations capture explicit project handles and use service
            # lifecycle/version locks. They never change UI selection. Long RAG
            # writes and tool imports must not block unrelated output either.
            independent = operation in BackendWorker.INDEPENDENT_OPERATIONS or (
                operation == HubRuntime.settings.__name__ and args
                and args[0] == SettingsOperation.USAGE
            )
            if not independent and self.runtime.lock.locked():
                OperationProgress.report(MessageKey.PROGRESS_BACKEND_WAIT)
            async with nullcontext() if independent else self.runtime.lock:
                OperationProgress.report(
                    MessageKey.PROGRESS_BACKEND_WRITE if operation in self.WRITE_OPERATIONS
                    else MessageKey.PROGRESS_BACKEND_READ if operation in self.READ_OPERATIONS
                    else MessageKey.PROGRESS_BACKEND_WORK
                )
                kwargs = CommandCallbacks(acknowledged, progress).to_kwargs()
                result = await getattr(self.runtime, operation)(*args, **kwargs)
                if operation not in BackendWorker.OBSERVATION_OPERATIONS:
                    self.runtime.dirty.set()
                return result
        finally:
            self._commands.discard(task)

    async def _prepare_restart(self):
        await self._initialized.wait()
        self._restarting = BackendWorker.RESTARTING
        commands = tuple(self._commands)
        for task in commands:
            task.cancel()
        if self._snapshot_task is not BackendWorker.ABSENT:
            self._snapshot_task.cancel()
        # Cancellation must finish storage writes and component cleanup before
        # another runtime can claim the same workspace.
        from .execution.restarting import stop_for_restart

        # Stop Runs concurrently with cancelled component commands, so a slow
        # RAG cancellation does not let other Sessions continue executing.
        cleanup = asyncio.create_task(stop_for_restart(self.runtime))
        results = await asyncio.gather(
            *commands, cleanup, return_exceptions=BackendWorker.CAPTURE_ERRORS
        )
        if isinstance(results[BackendWorker.CLEANUP_RESULT], BaseException):
            raise results[BackendWorker.CLEANUP_RESULT]

    def call(
        self, operation: str, *args, acknowledged=ABSENT, progress=ABSENT, stage=ABSENT
    ) -> Future:
        if self._closed or self.loop.is_closed():
            raise RuntimeError(BackendWorker.CLOSED_MESSAGE)
        return asyncio.run_coroutine_threadsafe(
            self._execute(operation, args, acknowledged, progress, stage), self.loop
        )

    async def _restart_with_progress(self, stage):
        with OperationProgress.bind(stage):
            OperationProgress.report(MessageKey.PROGRESS_BACKEND_CANCEL)
            await self._prepare_restart()
            OperationProgress.report(MessageKey.PROGRESS_BACKEND_RELEASE)

    def prepare_restart(self, *, stage=None) -> Future:
        """Freeze admission, cancel commands and stop all Hub-owned execution."""
        if self._closed or self.loop.is_closed():
            raise RuntimeError(BackendWorker.CLOSED_MESSAGE)
        return asyncio.run_coroutine_threadsafe(self._restart_with_progress(stage), self.loop)

    def _cancel_restart_on_loop(self):
        self._restarting = BackendWorker.NOT_RESTARTING

    def cancel_restart(self) -> None:
        if not self._closed and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._cancel_restart_on_loop)

    def set_output_interval(self, seconds: float) -> None:
        """Change only observation batching on the backend loop."""

        def apply():
            if self._output_interval != seconds:
                self._output_interval = seconds
                self._interval_changed.set()

        if not self._closed and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(apply)

    async def _write_sidebar_width(self):
        await self._initialized.wait()
        while self._sidebar_width_pending is not BackendWorker.ABSENT:
            width, self._sidebar_width_pending = (
                self._sidebar_width_pending,
                BackendWorker.ABSENT,
            )
            try:
                async with self.runtime.lock:
                    await asyncio.to_thread(
                        self.runtime.settings_service.preferences.save_sidebar_width,
                        width,
                    )
            except Exception as error:
                self.report_error(
                    from_exception(
                        error,
                        origin=ErrorOrigin.HUB,
                        operation=PreferencesStore.save_sidebar_width.__name__,
                    )
                )

    def set_sidebar_width(self, width: int) -> None:
        """Coalesce only width writes, serialized with explicit settings saves."""

        def queue():
            if self._restarting:
                return
            self._sidebar_width_pending = width
            if (
                self._sidebar_width_task is BackendWorker.ABSENT
                or self._sidebar_width_task.done()
            ):
                self._sidebar_width_task = asyncio.create_task(
                    self._write_sidebar_width()
                )
                self._commands.add(self._sidebar_width_task)
                self._sidebar_width_task.add_done_callback(self._commands.discard)

        if not self._closed and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(queue)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = BackendWorker.CLOSED
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._stop.set)
        self._thread.join()
        if self._shutdown_error:
            raise RuntimeError(BackendWorker.SHUTDOWN_FAILED) from self._shutdown_error
