"""Translate psutil's Linux counters; no process scans or shell commands."""
import time

from ...features.system.models import NetworkCounter, SystemReading


class PsutilBackend:
    def read(self, interfaces: tuple[str, ...] | None = None) -> SystemReading:
        import psutil

        errors = []
        total = busy = memory_total = available = None
        network = ()
        try:
            cpu = psutil.cpu_times()
            # Linux includes guest time in user/nice; iowait is not busy time.
            total = sum(cpu) - cpu.guest - cpu.guest_nice
            busy = total - cpu.idle - cpu.iowait
        except (OSError, ValueError) as exc:
            errors.append(f"CPU: {exc}")
        try:
            memory = psutil.virtual_memory()
            memory_total, available = memory.total, memory.available
        except (OSError, ValueError) as exc:
            errors.append(f"Memory: {exc}")
        try:
            counters = psutil.net_io_counters(pernic=True, nowrap=False)
            if interfaces is None:
                stats = psutil.net_if_stats()
                names = sorted(name for name, state in stats.items()
                               if state.isup and "loopback" not in state.flags.split(","))
            else:
                names = sorted(set(interfaces))
            network = tuple(NetworkCounter(name, counters[name].bytes_recv, counters[name].bytes_sent)
                            for name in names if name in counters)
            missing = set(names) - counters.keys()
            if missing:
                errors.append(f"Network interfaces unavailable: {', '.join(sorted(missing))}")
                network = ()
        except (OSError, ValueError) as exc:
            errors.append(f"Network: {exc}")
        return SystemReading(time.monotonic(), total, busy, memory_total, available, network, "; ".join(errors))
