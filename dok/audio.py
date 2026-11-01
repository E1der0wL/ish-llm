"""Linux output-monitor capture, independent of player transport and metadata."""

from __future__ import annotations

import asyncio
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
import json

from .records import Boolean, Choice, Number, Record, Sequence, Text, setting
from .spectrum import SpectrumAnalyzer


@dataclass(frozen=True, slots=True)
class AudioConfig(Record):
    enabled: bool = setting(Boolean(), default=True)
    sources: tuple[str, ...] = setting(Sequence(Text(nonblank=True, single_line=True)), default=())

    def _check(self):
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("Duplicate audio monitor source")

    def select(self, monitors: tuple[str, ...]) -> tuple[str, ...]:
        missing = set(self.sources) - set(monitors)
        if missing:
            raise ValueError(f"Output monitors unavailable: {', '.join(sorted(missing))}")
        return self.sources or monitors


class AudioStatus(StrEnum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    RUNNING = "running"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class AudioSnapshot(Record):
    status: AudioStatus = setting(Choice(AudioStatus), default=AudioStatus.STOPPED)
    levels: tuple[float, ...] = setting(Sequence(Number(minimum=0, maximum=1),
                                              minimum=SpectrumAnalyzer.BANDS, maximum=SpectrumAnalyzer.BANDS),
                                        factory=SpectrumAnalyzer.silence)
    sources: tuple[str, ...] = setting(Sequence(Text(nonblank=True)), default=())
    message: str = setting(Text(), default="")
    applications: tuple[str, ...] = setting(Sequence(Text(nonblank=True)), default=())

    @property
    def application_caption(self) -> str:
        return "재생 중: " + " · ".join(self.applications) if self.applications else ""


@dataclass(frozen=True, slots=True)
class OutputMonitor:
    index: int
    name: str


class PulseBackend:
    """Only this adapter interprets PulseAudio JSON and command-line options."""

    MONITOR_FIELD = "monitor_source"
    INDEX_FIELD = "index"
    SINK_FIELD = "sink"
    CORKED_FIELD = "corked"
    MUTE_FIELD = "mute"
    VOLUME_FIELD = "volume"
    VOLUME_VALUE_FIELD = "value"
    PROPERTIES_FIELD = "properties"
    APPLICATION_FIELDS = ("application.name", "application.process.binary", "media.name")
    UNKNOWN_APPLICATION = "이름 없는 프로그램"
    QUERY = ("pactl", "--format=json", "list", "sinks")
    APPLICATION_QUERY = ("pactl", "--format=json", "list", "sink-inputs")
    LATENCY_MS = 50
    QUERY_TIMEOUT = 3.0
    STOP_TIMEOUT = 1.0

    def __init__(self):
        self._outputs = ()

    @classmethod
    async def _stop(cls, process):
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                async with asyncio.timeout(cls.STOP_TIMEOUT):
                    await process.wait()
            except TimeoutError:
                if process.returncode is None:
                    process.kill()
                await process.wait()

    @classmethod
    @asynccontextmanager
    async def _process(cls, *command):
        process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            yield process
        finally:
            cleanup = asyncio.create_task(cls._stop(process))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise

    @classmethod
    def _objects(cls, data: bytes) -> list[dict]:
        objects = json.loads(data)
        if not isinstance(objects, list) or any(not isinstance(item, dict) for item in objects):
            raise ValueError("Expected PulseAudio object list")
        return objects

    @classmethod
    def decode_outputs(cls, data: bytes) -> tuple[OutputMonitor, ...]:
        monitors = []
        for sink in cls._objects(data):
            name = sink.get(cls.MONITOR_FIELD)
            index = sink.get(cls.INDEX_FIELD)
            if not isinstance(name, str) or not name or "\0" in name:
                raise ValueError("Missing output monitor name")
            if type(index) is not int or index < 0:
                raise ValueError("Invalid output sink index")
            monitors.append(OutputMonitor(index, name))
        return tuple(monitors)

    @classmethod
    def decode_monitors(cls, data: bytes) -> tuple[str, ...]:
        return tuple(dict.fromkeys(output.name for output in cls.decode_outputs(data)))

    @classmethod
    def decode_applications(cls, data: bytes, outputs: tuple[OutputMonitor, ...], sources: tuple[str, ...]) -> tuple[str, ...]:
        selected = {output.index for output in outputs if output.name in sources}
        names = set()
        for stream in cls._objects(data):
            if (stream.get(cls.SINK_FIELD) not in selected
                    or stream.get(cls.CORKED_FIELD) is not False or stream.get(cls.MUTE_FIELD) is not False):
                continue
            volume = stream.get(cls.VOLUME_FIELD)
            if not isinstance(volume, dict) or not any(
                isinstance(channel, dict) and type(channel.get(cls.VOLUME_VALUE_FIELD)) in (int, float)
                and channel[cls.VOLUME_VALUE_FIELD] > 0 for channel in volume.values()
            ):
                continue
            properties = stream.get(cls.PROPERTIES_FIELD)
            if not isinstance(properties, dict):
                properties = {}
            name = next((properties[field].strip() for field in cls.APPLICATION_FIELDS
                         if isinstance(properties.get(field), str) and properties[field].strip()), cls.UNKNOWN_APPLICATION)
            names.add(name.replace("\0", "").strip() or cls.UNKNOWN_APPLICATION)
        return tuple(sorted(names, key=str.casefold))

    async def _query(self, command) -> bytes:
        async with self._process(*command) as process:
            async with asyncio.timeout(self.QUERY_TIMEOUT):
                output, _ = await process.communicate()
            if process.returncode:
                raise OSError("PulseAudio/PipeWire 출력 장치에 연결할 수 없습니다")
            return output

    async def monitors(self) -> tuple[str, ...]:
        self._outputs = self.decode_outputs(await self._query(self.QUERY))
        return tuple(dict.fromkeys(output.name for output in self._outputs))

    async def applications(self, sources: tuple[str, ...]) -> tuple[str, ...]:
        return self.decode_applications(await self._query(self.APPLICATION_QUERY), self._outputs, sources)

    async def analyze_stream(self, stream, source: str):
        """Drain PCM independently of FFT work; retain only the newest block."""
        analyzer = SpectrumAnalyzer()
        ready = asyncio.Event()
        latest = None
        failure = None

        async def drain():
            nonlocal latest, failure
            try:
                while True:
                    latest = await stream.readexactly(analyzer.PCM.size)
                    ready.set()
            except asyncio.IncompleteReadError:
                failure = OSError(f"오디오 모니터 연결 종료: {source}")
            except Exception as exc:
                failure = exc
            finally:
                ready.set()

        reader = asyncio.create_task(drain())
        try:
            while True:
                await ready.wait()
                if failure is not None:
                    raise failure
                pcm = latest
                ready.clear()
                yield await asyncio.to_thread(analyzer.analyze, pcm)
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    async def frames(self, source: str):
        analyzer = SpectrumAnalyzer
        async with self._process(
            "parec", f"--device={source}", "--raw", f"--format={analyzer.SAMPLE_FORMAT}",
            f"--rate={analyzer.SAMPLE_RATE}", f"--channels={analyzer.CHANNELS}",
            f"--latency-msec={self.LATENCY_MS}", "--client-name=dok", "--stream-name=dok visualizer",
        ) as process:
            async with aclosing(self.analyze_stream(process.stdout, source)) as frames:
                async for levels in frames:
                    yield levels


class AudioRuntime:
    FPS = 20
    DISCOVERY_INTERVAL = 2.0
    STALE_AFTER = 0.3
    RETRY_INTERVAL = 3.0
    APPLICATION_INTERVAL = 0.5

    def __init__(self, invalidate, backend=None):
        self._invalidate = invalidate
        self._backend = backend if backend is not None else PulseBackend()
        self._snapshot = AudioSnapshot()
        self._task = None
        self._config = None
        self._closed = False

    def _publish(self, snapshot: AudioSnapshot):
        if snapshot != self._snapshot:
            self._snapshot = snapshot
            self._invalidate()

    async def _capture(self, source, latest, errors):
        try:
            async with aclosing(self._backend.frames(source)) as frames:
                async for levels in frames:
                    latest[source] = (asyncio.get_running_loop().time(), levels)
                    errors.pop(source, None)
        except (OSError, ValueError) as exc:
            errors[source] = str(exc)
        finally:
            latest.pop(source, None)

    async def _monitor(self, config):
        readers, latest, errors = {}, {}, {}
        applications, application_error = (), ""
        next_discovery = 0.0
        loop = asyncio.get_running_loop()

        async def watch_applications():
            nonlocal applications, application_error
            while True:
                try:
                    applications = await self._backend.applications(tuple(readers)) if readers else ()
                    application_error = ""
                except (OSError, ValueError, TimeoutError) as exc:
                    applications, application_error = (), str(exc)
                await asyncio.sleep(self.APPLICATION_INTERVAL)

        async with asyncio.TaskGroup() as group:
            group.create_task(watch_applications())
            while True:
                now = loop.time()
                if now >= next_discovery:
                    sources = config.select(await self._backend.monitors())
                    for source in set(readers) - set(sources):
                        reader = readers.pop(source)
                        reader.cancel()
                        await asyncio.gather(reader, return_exceptions=True)
                        errors.pop(source, None)
                    for source in sources:
                        if source not in readers or readers[source].done():
                            readers[source] = group.create_task(self._capture(source, latest, errors))
                    next_discovery = loop.time() + self.DISCOVERY_INTERVAL
                frames = [levels for stamp, levels in latest.values() if loop.time() - stamp <= self.STALE_AFTER]
                status = (AudioStatus.RUNNING if latest else AudioStatus.CONNECTING) if readers else AudioStatus.UNAVAILABLE
                if readers and all(task.done() for task in readers.values()):
                    status = AudioStatus.UNAVAILABLE
                message = "; ".join(errors.values()) if errors else ("Linux 출력 장치 없음" if not readers else "")
                if application_error:
                    message = "; ".join(part for part in (message, application_error) if part)
                self._publish(AudioSnapshot(status=status, levels=SpectrumAnalyzer.combine(frames),
                                            sources=tuple(readers), message=message, applications=applications))
                await asyncio.sleep(1 / self.FPS)

    async def _run(self, config):
        while True:
            self._publish(AudioSnapshot(status=AudioStatus.CONNECTING))
            try:
                await self._monitor(config)
            except (OSError, ValueError, TimeoutError, ExceptionGroup) as exc:
                message = str(exc)
                if isinstance(exc, ExceptionGroup):
                    message = "; ".join(str(error) for error in exc.exceptions)
                self._publish(AudioSnapshot(status=AudioStatus.UNAVAILABLE,
                                            message=f"오디오 연결 실패 (pulseaudio-utils 확인): {message}"))
                await asyncio.sleep(self.RETRY_INTERVAL)

    @property
    def snapshot(self) -> AudioSnapshot:
        return self._snapshot

    def sync(self, *, active: bool, config: AudioConfig, app):
        active = active and config.enabled and not self._closed
        if self._task is not None and not self._task.done():
            if active and self._config == config and not self._task.cancelling():
                return
            if not self._task.cancelling():
                self._task.cancel()
            self._publish(AudioSnapshot())
            return
        if active:
            self._config = config
            self._task = app.create_background_task(self._run(config))
            self._task.add_done_callback(lambda task: self._invalidate())
        else:
            self._publish(AudioSnapshot())

    def close(self):
        self._closed = True
        if self._task is not None and not self._task.done() and not self._task.cancelling():
            self._task.cancel()
        self._publish(AudioSnapshot())
