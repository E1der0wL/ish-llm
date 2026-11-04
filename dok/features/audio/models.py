"""Audio measurements, connection status and application names."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ...core.records import Choice, Number, Record, Sequence, Text, setting
from ..media.config import TitleConfig
from .spectrum import SpectrumAnalyzer


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
        return TitleConfig().application_caption(self.applications)

    @property
    def diagnostic(self) -> str:
        return self.message if self.status == AudioStatus.UNAVAILABLE else ""
