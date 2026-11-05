"""Sampling policy and network interface selection owner."""
from dataclasses import dataclass

from ...core.records import Boolean, Nullable, Number, Record, Sequence, Text, setting


@dataclass(frozen=True, slots=True)
class SystemConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Collect Linux system metrics while a system toolbar is visible.")
    refresh_seconds: float = setting(Number(minimum=0.5, maximum=60), default=1.0,
        description="Seconds between samples. Sampling runs outside the UI thread.")
    history_samples: int = setting(Number(minimum=2, maximum=3600, integer=True), default=120,
        description="Maximum recent samples retained in memory for graphs. No history files are written.")
    interfaces: tuple[str, ...] | None = setting(Nullable(Sequence(Text(nonblank=True, single_line=True), minimum=1)),
        default=None, description="Network interfaces to sum, for example [\"eth0\"]. null selects active non-loopback interfaces; bridges/VPNs may count traffic twice.")
