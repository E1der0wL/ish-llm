"""Keep the LLM loop alive between ish's repeated prompt_async invocations."""

import asyncio
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
        self._failure = None
        self._shutdown_error = None
        self._commands = set()
        self._thread = threading.Thread(target=self._thread_main, name="hub-backend", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _thread_main(self) -> None:
        try:
            with asyncio.Runner() as runner:
                self.loop = runner.get_loop()
                self._initialized = asyncio.Event()
                self._stop = asyncio.Event()
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

    async def _refresh(self) -> None:
        last = None
        while True:
            # Events coalesce; periodic reads also reconcile dropped notifications
            # and output batches flushed after the last observed engine event.
            try:
                async with asyncio.timeout(1):
                    await self.runtime.dirty.wait()
            except TimeoutError:
                pass
            self.runtime.dirty.clear()
            await asyncio.sleep(0.1)
            try:
                async with self.runtime.lock:
                    snapshot = await self.runtime.snapshot()
                if snapshot != last:
                    self.publish(snapshot)
                    last = snapshot
            except Exception as error:
                self.report_error(error)

    async def _execute(self, operation: str, args):
        await self._initialized.wait()
        if self._closed:
            raise RuntimeError("Hub backend is closing")
        if self._failure:
            raise RuntimeError(str(self._failure))
        task = asyncio.current_task()
        self._commands.add(task)
        try:
            async with self.runtime.lock:
                result = await getattr(self.runtime, operation)(*args)
                self.runtime.dirty.set()
                return result
        finally:
            self._commands.discard(task)

    def call(self, operation: str, *args) -> Future:
        if self._closed or self.loop.is_closed():
            raise RuntimeError("Hub backend is closed")
        return asyncio.run_coroutine_threadsafe(self._execute(operation, args), self.loop)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._stop.set)
        self._thread.join()
        if self._shutdown_error:
            raise RuntimeError("Hub backend shutdown failed") from self._shutdown_error
