"""PulseAudio monitor discovery, subprocess capture and cleanup."""
from __future__ import annotations

from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
import asyncio
import json

from ...features.audio.spectrum import SpectrumAnalyzer


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
