"""System counter semantics, adapter decoding and sampler lifetime."""
import asyncio
from collections import namedtuple
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dok.features.system.config import SystemConfig
from dok.features.system.models import NetworkCounter, SystemReading, SystemSnapshot
from dok.features.system.runtime import SystemRuntime
from dok.integrations.system.psutil import PsutilBackend


def reading(timestamp=1, total=100, busy=20, received=100, sent=200):
    return SystemReading(timestamp, total, busy, 1000, 600, (NetworkCounter("eth0", received, sent),))


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


class SystemTests(unittest.TestCase):
    def test_first_sample_and_elapsed_rates_use_available_memory(self):
        first = SystemSnapshot.between(None, reading())
        self.assertIsNone(first.cpu_percent)
        self.assertIsNone(first.received_per_second)
        self.assertEqual((first.memory_used, first.memory_percent), (400, 40))
        second = SystemSnapshot.between(reading(), reading(3, 300, 70, 500, 800))
        self.assertEqual((second.cpu_percent, second.received_per_second, second.sent_per_second), (25, 200, 300))

    def test_counter_reset_and_interface_changes_rebaseline_without_spikes(self):
        for current in (reading(2, 50, 5, 50, 60),
                        replace(reading(2), network=(NetworkCounter("wlan0", 10000, 20000),)),
                        replace(reading(2), network=()), reading(timestamp=1)):
            with self.subTest(current=current):
                result = SystemSnapshot.between(reading(), current)
                self.assertIsNone(result.received_per_second)
                self.assertIsNone(result.sent_per_second)
        self.assertIsNone(SystemSnapshot.between(reading(), reading(2, 50, 5)).cpu_percent)

    def test_psutil_adapter_excludes_guest_iowait_loopback_and_disconnected_interfaces(self):
        Cpu = namedtuple("Cpu", "user nice system idle iowait irq softirq steal guest guest_nice")
        cpu = Cpu(40, 10, 20, 100, 30, 0, 0, 0, 10, 5)
        counters = {name: SimpleNamespace(bytes_recv=20, bytes_sent=30) for name in ("eth0", "lo", "down")}
        states = {"eth0": SimpleNamespace(isup=True, flags="up,broadcast"),
                  "lo": SimpleNamespace(isup=True, flags="up,loopback"),
                  "down": SimpleNamespace(isup=False, flags="broadcast")}
        with patch("psutil.cpu_times", return_value=cpu), patch("psutil.virtual_memory", return_value=SimpleNamespace(total=100, available=70)), \
             patch("psutil.net_io_counters", return_value=counters) as net, patch("psutil.net_if_stats", return_value=states):
            sample = PsutilBackend().read()
            self.assertEqual((sample.cpu_total, sample.cpu_busy), (200, 70))
            self.assertEqual(sample.network, (NetworkCounter("eth0", 20, 30),))
            self.assertEqual(PsutilBackend().read(("lo",)).network[0].name, "lo")
            self.assertEqual(PsutilBackend().read(("missing",)).network, ())
            net.assert_called_with(pernic=True, nowrap=False)

    def test_partial_adapter_failure_keeps_other_metrics_available(self):
        with patch("psutil.cpu_times", side_effect=OSError("unavailable")):
            sample = PsutilBackend().read()
        self.assertIsNone(sample.cpu_total)
        self.assertGreater(sample.memory_total, 0)
        self.assertIn("CPU:", sample.diagnostic)

    def test_live_linux_snapshot(self):
        sample = PsutilBackend().read()
        self.assertGreater(sample.cpu_total, 0)
        self.assertGreater(sample.memory_total, 0)
        self.assertGreaterEqual(sample.memory_available, 0)


class SystemRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_is_bounded_and_failure_recovers_without_a_tight_loop(self):
        calls = []
        def read(interfaces):
            calls.append(interfaces)
            if len(calls) == 2:
                raise OSError("temporary")
            return reading(len(calls), total=100 * len(calls), busy=20 * len(calls))
        runtime = SystemRuntime(lambda: None, SimpleNamespace(read=read))
        config = SystemConfig(refresh_seconds=0.5, history_samples=2)
        runtime.sync(active=True, config=config, start_task=asyncio.create_task)
        try:
            await until(lambda: len(calls) == 2)
            await until(lambda: "temporary" in runtime.snapshot.diagnostic)
            await asyncio.sleep(0.05)
            self.assertEqual(len(calls), 2)
            await until(lambda: len(calls) >= 4)
            await until(lambda: runtime.snapshot.cpu_percent is not None)
            self.assertEqual(len(runtime.history), 2)
        finally:
            runtime.close()
            await until(lambda: not runtime._task.running)

    async def test_read_runs_off_loop_and_replacement_waits_for_cancelled_worker(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def read(interfaces):
            calls.append((interfaces, threading.get_ident()))
            entered.set()
            release.wait(3)
            return reading()
        runtime = SystemRuntime(lambda: None, SimpleNamespace(read=read))
        first, second = SystemConfig(), SystemConfig(interfaces=("eth0",))
        runtime.sync(active=True, config=first, start_task=asyncio.create_task)
        try:
            await until(entered.is_set)
            self.assertNotEqual(calls[0][1], threading.get_ident())
            runtime.sync(active=True, config=second, start_task=asyncio.create_task)
            await asyncio.sleep(0.02)
            runtime._task._task.cancel()  # Host shutdown may request cancellation again.
            await asyncio.sleep(0.02)
            runtime.sync(active=True, config=second, start_task=asyncio.create_task)
            self.assertEqual(len(calls), 1)
            release.set()
            await until(lambda: not runtime._task.running)
            self.assertIsNone(runtime.snapshot.timestamp)
            runtime.sync(active=True, config=second, start_task=asyncio.create_task)
            await until(lambda: len(calls) == 2)
            self.assertEqual(calls[1][0], ("eth0",))
            runtime.sync(active=False, config=second, start_task=asyncio.create_task)
            await until(lambda: not runtime._task.running)
            self.assertEqual(runtime.history, ())
        finally:
            release.set()
            runtime.close()
            await until(lambda: not runtime._task.running)
