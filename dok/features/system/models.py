"""Immutable samples and the sole interval/rate calculation owner."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class NetworkCounter:
    name: str
    received: int
    sent: int


@dataclass(frozen=True, slots=True)
class SystemReading:
    timestamp: float
    cpu_total: float | None = None
    cpu_busy: float | None = None
    memory_total: int | None = None
    memory_available: int | None = None
    network: tuple[NetworkCounter, ...] = ()
    diagnostic: str = ""


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    timestamp: float | None = None
    cpu_percent: float | None = None
    memory_used: int | None = None
    memory_total: int | None = None
    received_per_second: float | None = None
    sent_per_second: float | None = None
    interfaces: tuple[str, ...] = ()
    diagnostic: str = ""

    @property
    def display_key(self):
        """Values that can affect the toolbar or its history, excluding sample time."""
        return (self.cpu_percent, self.memory_used, self.memory_total, self.received_per_second,
                self.sent_per_second, self.interfaces, self.diagnostic)

    @property
    def memory_percent(self) -> float | None:
        return self.memory_used / self.memory_total * 100 if self.memory_total and self.memory_used is not None else None

    @classmethod
    def between(cls, previous: SystemReading | None, current: SystemReading) -> "SystemSnapshot":
        cpu = received = sent = used = total = None
        if current.memory_total and current.memory_available is not None:
            total = current.memory_total
            used = max(0, min(total, total - current.memory_available))
        if previous is not None and current.timestamp > previous.timestamp:
            if all(value is not None for value in (current.cpu_total, current.cpu_busy, previous.cpu_total, previous.cpu_busy)):
                elapsed_cpu = current.cpu_total - previous.cpu_total
                busy = current.cpu_busy - previous.cpu_busy
                if elapsed_cpu > 0 and busy >= 0:
                    cpu = max(0.0, min(100.0, busy / elapsed_cpu * 100))
            before = {item.name: item for item in previous.network}
            if current.network and set(before) == {item.name for item in current.network}:
                deltas = [(item.received - before[item.name].received, item.sent - before[item.name].sent)
                          for item in current.network]
                if all(rx >= 0 and tx >= 0 for rx, tx in deltas):
                    elapsed = current.timestamp - previous.timestamp
                    received = sum(rx for rx, _ in deltas) / elapsed
                    sent = sum(tx for _, tx in deltas) / elapsed
        return cls(current.timestamp, cpu, used, total, received, sent,
                   tuple(item.name for item in current.network), current.diagnostic)


class SystemBackend(Protocol):
    def read(self, interfaces: tuple[str, ...] | None) -> SystemReading: ...
