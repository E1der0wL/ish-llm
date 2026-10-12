"""Keep the LLM loop alive between ish's repeated prompt_async invocations."""

import asyncio
from contextlib import nullcontext
from concurrent.futures import Future
import threading

from .runtime import HubConfig, HubRuntime, create_backend


class BackendWorker:
    def __init__(self, config: HubConfig, publish, report_error, *, backend_factory=create_backend):
        self.config = config
        self.publish = publish
        self.report_error = report_error
        self.backend_factory = backend_factory
        self.loop = None
        self.runtime = None
        self._ready = threading.Event()
        self._closed = False
        self._restarting = False
        self._failure = None
        self._shutdown_error = None
        self._commands = set()
        self._snapshot_task = None
        self.snapshot_count = 0
        self._output_interval = 0.1
        self._interval_changed = None
        self._sidebar_width_pending = None
        self._sidebar_width_task = None
        self._thread = threading.Thread(target=self._thread_main, name="hub-backend", daemon=True)
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
            self.report_error(error)

    async def _serve(self) -> None:
        self.runtime = HubRuntime(self.config, self.backend_factory)
        refresher = None
        try:
            try:
                await self.runtime.start()
                self.publish(await self.runtime.snapshot())
                refresher = asyncio.create_task(self._refresh())
            except Exception as error:
                self._failure = error
                self.report_error(error)
            finally:
                self._initialized.set()
            await self._stop.wait()
            if self._commands:
                await asyncio.gather(*tuple(self._commands), return_exceptions=True)
        finally:
            if refresher:
                refresher.cancel()
                await asyncio.gather(refresher, return_exceptions=True)
            await self.runtime.close()

    async def _read_snapshot(self):
        async with self.runtime.lock:
            self.snapshot_count = getattr(self, "snapshot_count", 0) + 1
            return await self.runtime.snapshot()

    async def _refresh(self) -> None:
        last = None
        loop = asyncio.get_running_loop()
        last_read = loop.time()
        while True:
            dirty = self.runtime.dirty.is_set()
            self.runtime.dirty.clear()
            active = last is None or bool(getattr(getattr(last, "run", None), "active", False)) or bool(getattr(last, "queued_count", 0))
            active = active or any(getattr(s, "status", "") in ("running", "streaming", "queued") or getattr(s, "queued_count", 0)
                                   for s in getattr(last, "sessions", ()))
            # Idle reconciliation is a safety net. Events wake it immediately,
            # throttled only by the configured output interval.
            deadline = last_read + (self._output_interval if active or dirty else max(.5, min(2., self._output_interval * 10)))
            while True:
                self._interval_changed.clear()
                if loop.time() >= deadline:
                    break
                wake = asyncio.create_task(self.runtime.dirty.wait())
                changed = asyncio.create_task(self._interval_changed.wait())
                try:
                    done, _ = await asyncio.wait((wake, changed), timeout=max(0, deadline - loop.time()), return_when=asyncio.FIRST_COMPLETED)
                    if not done:
                        break
                    self.runtime.dirty.clear()
                    deadline = min(deadline, last_read + self._output_interval)
                finally:
                    wake.cancel()
                    changed.cancel()
                    await asyncio.gather(wake, changed, return_exceptions=True)
            last_read = loop.time()
            try:
                self._snapshot_task = asyncio.create_task(self._read_snapshot())
                try:
                    snapshot = await self._snapshot_task
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    # An input command preempted this observation. Retry after
                    # admission rather than making the user wait for all history.
                    self.runtime.dirty.set()
                    continue
                finally:
                    self._snapshot_task = None
                if snapshot != last:
                    self.publish(snapshot)
                    last = snapshot
            except Exception as error:
                self.report_error(error)

    async def _execute(self, operation: str, args, acknowledged=None):
        await self._initialized.wait()
        if self._closed or self._restarting:
            raise RuntimeError("Hub backend is closing")
        if self._failure:
            raise RuntimeError(str(self._failure))
        task = asyncio.current_task()
        self._commands.add(task)
        try:
            if operation in {"submit_input", "submit", "steer", "interrupt", "submission_state", "instruction_targets", "answer_question", "cancel_request"}:
                if self._snapshot_task is not None:
                    self._snapshot_task.cancel()
            # These operations capture explicit project handles and use service
            # lifecycle/version locks. They never change UI selection. Long RAG
            # writes and tool imports must not block unrelated output either.
            independent = operation in {"project_activity", "execution_catalog", "component_command", "manage_tools", "approval_details", "answer_question"}
            async with (nullcontext() if independent else self.runtime.lock):
                kwargs = {"acknowledged": acknowledged} if acknowledged is not None else {}
                result = await getattr(self.runtime, operation)(*args, **kwargs)
                if operation not in {"submission_state", "instruction_targets", "snapshot"}:
                    self.runtime.dirty.set()
                return result
        finally:
            self._commands.discard(task)

    async def _prepare_restart(self):
        await self._initialized.wait()
        async with self.runtime.lock:
            if self._commands:
                raise RuntimeError(self.runtime.t("settings_restart_busy"))
            if self.runtime.backend is not None:
                for project in await self.runtime.backend.projects.alist():
                    for session in await project.sessions.alist():
                        status = await session.run.astatus()
                        if status.active_run_id or status.queued_count or session.id in self.runtime.namer.tasks:
                            raise RuntimeError(self.runtime.t("settings_restart_busy"))
            if self._commands:
                raise RuntimeError(self.runtime.t("settings_restart_busy"))
            self._restarting = True

    def call(self, operation: str, *args, acknowledged=None) -> Future:
        if self._closed or self.loop.is_closed():
            raise RuntimeError("Hub backend is closed")
        return asyncio.run_coroutine_threadsafe(self._execute(operation, args, acknowledged), self.loop)

    def prepare_restart(self) -> Future:
        """Freeze admission only when all projects and background commands are idle."""
        if self._closed or self.loop.is_closed():
            raise RuntimeError("Hub backend is closed")
        return asyncio.run_coroutine_threadsafe(self._prepare_restart(), self.loop)

    def cancel_restart(self) -> None:
        if not self._closed and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(setattr, self, "_restarting", False)

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
        while self._sidebar_width_pending is not None:
            width, self._sidebar_width_pending = self._sidebar_width_pending, None
            try:
                async with self.runtime.lock:
                    await asyncio.to_thread(self.runtime.settings_service.preferences.save_sidebar_width, width)
            except Exception as error:
                self.report_error(error)

    def set_sidebar_width(self, width: int) -> None:
        """Coalesce only width writes, serialized with explicit settings saves."""
        def queue():
            if self._restarting:
                return
            self._sidebar_width_pending = width
            if self._sidebar_width_task is None or self._sidebar_width_task.done():
                self._sidebar_width_task = asyncio.create_task(self._write_sidebar_width())
                self._commands.add(self._sidebar_width_task)
                self._sidebar_width_task.add_done_callback(self._commands.discard)
        if not self._closed and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(queue)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._stop.set)
        self._thread.join()
        if self._shutdown_error:
            raise RuntimeError("Hub backend shutdown failed") from self._shutdown_error
