"""Active audio capture lifetime, frame publication and retry policy."""
from __future__ import annotations

from contextlib import aclosing
import asyncio

from ...core.tasks import TaskSlot, TaskStarter
from ...integrations.audio.pulse import PulseBackend
from .config import AudioConfig
from .models import AudioSnapshot, AudioStatus
from .spectrum import SpectrumAnalyzer


class AudioRuntime:
    FPS = 20
    DISCOVERY_INTERVAL = 2.0
    STALE_AFTER = 0.3
    APPLICATION_INTERVAL = 0.5

    def __init__(self, invalidate, backend=None):
        self._invalidate = invalidate
        self._backend = backend if backend is not None else PulseBackend()
        self._snapshot = AudioSnapshot()
        self._task = TaskSlot(invalidate)
        self._failure = None

    def _publish(self, snapshot: AudioSnapshot):
        if not self._task.closed and snapshot != self._snapshot:
            self._snapshot = snapshot
            self._invalidate()

    async def _capture(self, source, latest):
        try:
            async with aclosing(self._backend.frames(source)) as frames:
                async for levels in frames:
                    latest[source] = (asyncio.get_running_loop().time(), levels)
            raise OSError(f"오디오 모니터 연결 종료: {source}")
        finally:
            latest.pop(source, None)

    async def _monitor(self, config):
        readers, latest = {}, {}
        applications = ()
        next_discovery = 0.0
        loop = asyncio.get_running_loop()

        async def watch_applications():
            nonlocal applications
            while True:
                applications = await self._backend.applications(tuple(readers)) if readers else ()
                await asyncio.sleep(self.APPLICATION_INTERVAL)

        async with asyncio.TaskGroup() as group:
            group.create_task(watch_applications())
            while True:
                now = loop.time()
                if now >= next_discovery:
                    sources = config.select(await self._backend.monitors())
                    if not sources:
                        raise OSError("Linux 출력 장치 없음")
                    for source in set(readers) - set(sources):
                        reader = readers.pop(source)
                        reader.cancel()
                        await asyncio.gather(reader, return_exceptions=True)
                    for source in sources:
                        if source not in readers:
                            readers[source] = group.create_task(self._capture(source, latest))
                    next_discovery = loop.time() + self.DISCOVERY_INTERVAL
                frames = [levels for stamp, levels in latest.values() if loop.time() - stamp <= self.STALE_AFTER]
                status = AudioStatus.RUNNING if latest else AudioStatus.CONNECTING
                self._publish(AudioSnapshot(status=status, levels=SpectrumAnalyzer.combine(frames),
                                            sources=tuple(readers), applications=applications))
                await asyncio.sleep(1 / self.FPS)

    async def _run(self, config):
        self._publish(AudioSnapshot(status=AudioStatus.CONNECTING))
        try:
            await self._monitor(config)
        except (OSError, ValueError, TimeoutError, ExceptionGroup) as exc:
            message = str(exc)
            if isinstance(exc, ExceptionGroup):
                message = "; ".join(str(error) for error in exc.exceptions)
            self._failure = AudioSnapshot(status=AudioStatus.UNAVAILABLE,
                                          message=f"오디오 연결 실패 (pulseaudio-utils 확인): {message}")
            self._publish(self._failure)

    @property
    def snapshot(self) -> AudioSnapshot:
        return self._snapshot

    def begin_activation(self):
        """Allow a fresh connection attempt when the user opens the dock."""
        self._failure = None

    def sync(self, *, active: bool, config: AudioConfig, start_task: TaskStarter):
        active = active and config.enabled and not self._task.closed
        running = self._task.sync(
            active=active and self._failure is None, key=config,
            run=lambda: self._run(config), start=start_task,
        )
        if active and self._failure is not None:
            self._publish(self._failure)
        elif not running:
            self._publish(AudioSnapshot())

    def close(self):
        self._publish(AudioSnapshot())
        self._task.close()
