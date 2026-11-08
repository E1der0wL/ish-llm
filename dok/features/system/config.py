"""Sampling policy and network interface selection owner."""
from dataclasses import dataclass

from ...core.records import Boolean, Nullable, Number, POSITIVE_SECONDS, Record, Sequence, Text, setting


@dataclass(frozen=True, slots=True)
class SystemConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Collect Linux system metrics while a system toolbar is visible.")
    refresh_s: float = setting(POSITIVE_SECONDS, default=1.0,
        description="Positive finite seconds between samples. Sampling runs outside the UI thread.")
    idle_s: float | None = setting(Nullable(POSITIVE_SECONDS), default=3.0,
        description="Sample interval while the dock is hidden or a general popup is focused. The visible toolbar keeps updating. null uses refresh_s; history and counter baselines are preserved when switching.")
    history: int = setting(Number(minimum=0, integer=True), default=120,
        description="Recent samples to keep in memory for graphs. Zero keeps only the current reading.")
    interfaces: tuple[str, ...] | None = setting(Nullable(Sequence(Text(nonblank=True, single_line=True), minimum=1)),
        default=None, description="Network interfaces to sum, for example [\"eth0\"]. null selects active non-loopback interfaces; bridges/VPNs may count traffic twice.")

    def interval(self, idle):
        return self.idle_s if idle and self.idle_s is not None else self.refresh_s
