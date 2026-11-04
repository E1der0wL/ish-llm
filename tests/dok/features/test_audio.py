"""Deterministic PCM, multiple monitors, cancellation and UI lifetime tests."""

import asyncio
from contextlib import aclosing
import json
import math
from types import SimpleNamespace
import sys
import threading
import unittest
from unittest.mock import AsyncMock, patch

from dok import AudioConfig, AudioSnapshot, AudioStatus, DockConfig
from dok.features.audio.runtime import AudioRuntime
from dok.integrations.audio.pulse import OutputMonitor, PulseBackend
from dok.features.audio.spectrum import SpectrumAnalyzer


class SpectrumTests(unittest.TestCase):
    @staticmethod
    def tone(frequency, *, phase_inverted=False):
        values = []
        for index in range(SpectrumAnalyzer.FRAMES):
            sample = int(0.4 * SpectrumAnalyzer.SAMPLE_SCALE * math.sin(2 * math.pi * frequency * index / SpectrumAnalyzer.SAMPLE_RATE))
            values.extend((sample, -sample if phase_inverted else sample))
        return SpectrumAnalyzer.PCM.pack(*values)

    def test_silence_frequency_separation_and_stereo_phase(self):
        analyzer = SpectrumAnalyzer()
        self.assertEqual(analyzer.analyze(bytes(analyzer.PCM.size)), analyzer.silence())
        low = analyzer.analyze(self.tone(150))
        high = analyzer.analyze(self.tone(3000))
        self.assertLess(low.index(max(low)), high.index(max(high)))
        self.assertGreater(max(high), 0.7)
        self.assertEqual(high, analyzer.analyze(self.tone(3000, phase_inverted=True)))

    def test_multiple_outputs_combine_without_cancellation_or_double_gain(self):
        analyzer = SpectrumAnalyzer()
        first, second = analyzer.analyze(self.tone(150)), analyzer.analyze(self.tone(3000))
        combined = analyzer.combine((first, second))
        self.assertEqual(combined, tuple(max(a, b) for a, b in zip(first, second)))
        self.assertEqual(analyzer.combine((first, first)), first)

    def test_silent_pcm_skips_fft(self):
        analyzer = SpectrumAnalyzer()
        with patch.object(analyzer, "_fft", side_effect=AssertionError("silent FFT")):
            self.assertEqual(analyzer.analyze(bytes(analyzer.PCM.size)), analyzer.silence())

    def test_monitor_adapter_only_uses_sink_monitor_sources(self):
        self.assertEqual(PulseBackend.decode_monitors(b'[{"index":1,"monitor_source":"first.monitor"},{"index":2,"monitor_source":"second.monitor"}]'),
                         ("first.monitor", "second.monitor"))
        with self.assertRaises(ValueError):
            PulseBackend.decode_monitors(b'[{"name":"microphone"}]')
        self.assertEqual(AudioConfig().select(("first.monitor", "second.monitor")), ("first.monitor", "second.monitor"))
        with self.assertRaises(ValueError):
            AudioConfig(sources=("microphone",)).select(("first.monitor",))

    def test_configuration_and_measurements_use_owner_validation(self):
        config = DockConfig(audio=AudioConfig(enabled=False, sources=("test.monitor",)))
        self.assertEqual(DockConfig.from_dict(config.to_dict()), config)
        for value in (float("nan"), -1, 2, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AudioSnapshot(levels=(value,) * SpectrumAnalyzer.BANDS)

    def test_application_names_exclude_paused_muted_and_unselected_outputs(self):
        def stream(name, **changes):
            return {"sink": 1, "corked": False, "mute": False,
                    "volume": {"front-left": {"value": 100}},
                    "properties": {"application.name": name}, **changes}
        data = [stream("Firefox"), stream("Firefox"), stream("VLC"),
                stream("Paused", corked=True), stream("Muted", mute=True),
                stream("Zero", volume={"front-left": {"value": 0}}), stream("Elsewhere", sink=2),
                stream("", properties={"application.process.binary": "pacat"})]
        outputs = (OutputMonitor(1, "first.monitor"), OutputMonitor(2, "second.monitor"))
        actual = PulseBackend.decode_applications(json.dumps(data).encode(), outputs, ("first.monitor",))
        self.assertEqual(actual, ("Firefox", "pacat", "VLC"))
        self.assertEqual(AudioSnapshot(applications=actual).application_caption, "재생 중: Firefox · pacat · VLC")


class FakeBackend:
    def __init__(self):
        self.sources = ("first.monitor", "second.monitor")
        self.opened = set()
        self.closed = set()
        self.emit = True
        self.names = {"first.monitor": "Player", "second.monitor": "Browser"}

    async def monitors(self):
        return self.sources

    async def applications(self, sources):
        return tuple(sorted({self.names[source] for source in sources}))

    async def frames(self, source):
        self.opened.add(source)
        try:
            while True:
                if self.emit:
                    yield (0.25 if source == "first.monitor" else 0.75,) * SpectrumAnalyzer.BANDS
                await asyncio.sleep(0.01)
        finally:
            self.closed.add(source)


class AudioRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tasks = []
        def create(coroutine):
            task = asyncio.create_task(coroutine)
            self.tasks.append(task)
            return task
        self.app = SimpleNamespace(create_background_task=create)
        self.backend = FakeBackend()
        self.runtime = AudioRuntime(lambda: None, self.backend)
        self.runtime.DISCOVERY_INTERVAL = 0.05
        self.runtime.STALE_AFTER = 0.05
        self.runtime.APPLICATION_INTERVAL = 0.05

    async def asyncTearDown(self):
        self.runtime.close()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def until(self, predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(0.01)

    async def test_start_stop_restart_and_hotplug_all_outputs(self):
        config = AudioConfig()
        self.runtime.sync(active=True, config=config, app=self.app)
        await self.until(lambda: self.runtime.snapshot.levels[0] == 0.75)
        self.assertEqual(self.backend.opened, set(self.backend.sources))
        self.backend.sources = ("first.monitor",)
        await self.until(lambda: self.runtime.snapshot.sources == self.backend.sources and self.runtime.snapshot.levels[0] == 0.25)
        self.assertIn("second.monitor", self.backend.closed)
        self.runtime.sync(active=False, config=config, app=self.app)
        await self.until(lambda: self.backend.closed == self.backend.opened)
        self.assertEqual(self.runtime.snapshot.status, AudioStatus.STOPPED)
        self.runtime.sync(active=True, config=config, app=self.app)
        await self.until(lambda: self.runtime.snapshot.status == AudioStatus.RUNNING)

    async def test_stalled_stream_becomes_silence_and_disabled_config_stops_capture(self):
        self.runtime.sync(active=True, config=AudioConfig(), app=self.app)
        await self.until(lambda: max(self.runtime.snapshot.levels) > 0)
        self.backend.emit = False
        await self.until(lambda: self.runtime.snapshot.levels == SpectrumAnalyzer.silence())
        self.runtime.sync(active=True, config=AudioConfig(enabled=False), app=self.app)
        await self.until(lambda: self.backend.closed == self.backend.opened)

    async def test_missing_server_retries_only_on_new_activation(self):
        original = self.backend.monitors
        unavailable = AsyncMock(side_effect=OSError("server unavailable"))
        self.backend.monitors = unavailable
        self.runtime.sync(active=True, config=AudioConfig(), app=self.app)
        await self.until(lambda: self.runtime.snapshot.status == AudioStatus.UNAVAILABLE)
        self.assertIn("server unavailable", self.runtime.snapshot.message)
        await self.until(lambda: all(task.done() for task in self.tasks))
        for _ in range(5):
            self.runtime.sync(active=False, config=AudioConfig(), app=self.app)
            self.runtime.sync(active=True, config=AudioConfig(), app=self.app)
            await asyncio.sleep(0.05)
        self.assertEqual(unavailable.await_count, 1)
        self.assertEqual(len(self.tasks), 1)
        self.backend.monitors = original
        self.runtime.begin_activation()
        self.runtime.sync(active=True, config=AudioConfig(), app=self.app)
        await self.until(lambda: self.runtime.snapshot.status == AudioStatus.RUNNING)

    async def test_no_outputs_and_capture_or_metadata_failure_stop_all_work(self):
        for failure in ("no_outputs", "capture", "metadata", "discovery"):
            with self.subTest(failure=failure):
                backend = FakeBackend()
                if failure == "no_outputs":
                    backend.sources = ()
                elif failure == "capture":
                    async def broken_frames(source):
                        yield SpectrumAnalyzer.silence()
                        raise OSError("capture failed")
                    backend.frames = broken_frames
                elif failure == "metadata":
                    backend.applications = AsyncMock(side_effect=OSError("metadata failed"))
                elif failure == "discovery":
                    backend.monitors = AsyncMock(side_effect=[backend.sources, OSError("disconnected")])
                runtime = AudioRuntime(lambda: None, backend)
                runtime.DISCOVERY_INTERVAL = 0.05
                runtime.APPLICATION_INTERVAL = 0.05
                try:
                    runtime.sync(active=True, config=AudioConfig(), app=self.app)
                    await self.until(lambda: runtime.snapshot.status == AudioStatus.UNAVAILABLE)
                    await self.until(lambda: all(task.done() for task in self.tasks))
                    count = len(self.tasks)
                    for _ in range(3):
                        runtime.sync(active=True, config=AudioConfig(), app=self.app)
                        await asyncio.sleep(0.05)
                    self.assertEqual(len(self.tasks), count)
                    self.assertEqual(runtime.snapshot.levels, SpectrumAnalyzer.silence())
                    self.assertEqual(backend.opened, backend.closed)
                finally:
                    runtime.close()

    async def test_application_changes_update_without_restarting_capture(self):
        self.runtime.sync(active=True, config=AudioConfig(sources=("first.monitor",)), app=self.app)
        await self.until(lambda: self.runtime.snapshot.applications == ("Player",))
        self.backend.names["first.monitor"] = "New Player"
        await self.until(lambda: self.runtime.snapshot.applications == ("New Player",))
        self.assertEqual(self.backend.opened, {"first.monitor"})
        self.assertEqual(self.backend.closed, set())

    async def test_cancellation_terminates_and_reaps_actual_capture_child(self):
        opened = asyncio.get_running_loop().create_future()
        async def wait():
            async with PulseBackend._process(sys.executable, "-c", "import time; time.sleep(60)") as process:
                opened.set_result(process)
                await process.stdout.readexactly(1)
        task = asyncio.create_task(wait())
        process = await asyncio.wait_for(opened, 3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        self.assertIsNotNone(process.returncode)

    async def test_slow_analysis_discards_backlog_and_closing_cancels_reader(self):
        loop = asyncio.get_running_loop()
        started, drained = asyncio.Event(), asyncio.Event()
        release = threading.Event()
        last_marker = 100
        class Stream(asyncio.StreamReader):
            async def readexactly(self, count):
                block = await super().readexactly(count)
                if block[0] == last_marker:
                    drained.set()
                return block
        stream = Stream()
        calls = []
        def analyze(pcm):
            calls.append(pcm[0])
            if len(calls) == 1:
                loop.call_soon_threadsafe(started.set)
                if not release.wait(3):
                    raise TimeoutError("test did not release analyzer")
            return pcm[0]
        original_tasks = set(asyncio.all_tasks())
        frames = PulseBackend().analyze_stream(stream, "test.monitor")
        first = None
        try:
            with patch.object(SpectrumAnalyzer, "analyze", side_effect=analyze):
                first = asyncio.create_task(anext(frames))
                stream.feed_data(bytes(SpectrumAnalyzer.PCM.size))
                await asyncio.wait_for(started.wait(), 3)
                for marker in range(1, last_marker + 1):
                    stream.feed_data(bytes([marker]) * SpectrumAnalyzer.PCM.size)
                await asyncio.wait_for(drained.wait(), 3)
                release.set()
                self.assertEqual(await asyncio.wait_for(first, 3), 0)
                self.assertEqual(await asyncio.wait_for(anext(frames), 3), last_marker)
                self.assertEqual(calls, [0, last_marker])
        finally:
            release.set()
            if first is not None:
                await asyncio.gather(first, return_exceptions=True)
            await frames.aclose()
        self.assertFalse(set(asyncio.all_tasks()) - original_tasks)

    async def test_capture_eof_is_reported_and_reader_is_reaped(self):
        stream = asyncio.StreamReader()
        stream.feed_eof()
        original_tasks = set(asyncio.all_tasks())
        async with aclosing(PulseBackend().analyze_stream(stream, "test.monitor")) as frames:
            with self.assertRaisesRegex(OSError, "test.monitor"):
                await anext(frames)
        self.assertFalse(set(asyncio.all_tasks()) - original_tasks)
