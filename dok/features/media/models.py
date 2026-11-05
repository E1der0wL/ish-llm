"""Immutable playback presentation and action names; no I/O is performed here."""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar
import math


class PlaybackStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    STOPPED = "stopped"
    PAUSED = "paused"
    PLAYING = "playing"


class PlaybackAction(StrEnum):
    PREVIOUS = "previous"
    PLAY_PAUSE = "play_pause"
    NEXT = "next"
    PLAY = "play"
    PAUSE = "pause"
    STOP = "stop"

    @property
    def symbol(self) -> str:
        return {self.PREVIOUS: "\U000f04ae", self.PLAY_PAUSE: "\U000f040e", self.NEXT: "\U000f04ad",
                self.PLAY: "▶", self.PAUSE: "⏸", self.STOP: "⏹"}[self]


@dataclass(frozen=True, slots=True)
class PlaybackSnapshot:
    DISCONNECTED_TITLE: ClassVar[str] = "플레이어 연결 안 됨"
    status: PlaybackStatus = PlaybackStatus.UNAVAILABLE
    title: str = DISCONNECTED_TITLE
    artist: str = ""
    position: float = 0.0
    duration: float | None = None

    def __post_init__(self):
        object.__setattr__(self, "status", PlaybackStatus(self.status))
        if not isinstance(self.title, str) or not isinstance(self.artist, str):
            raise ValueError("Playback title and artist must be text")
        for value in (self.position, *(() if self.duration is None else (self.duration,))):
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Playback measurements must be finite and nonnegative")

    @property
    def progress(self):
        return min(1.0, self.position / self.duration) if self.duration else 0.0

    @property
    def caption(self):
        return " — ".join(part for part in (self.artist, self.title) if part)

    @property
    def has_metadata(self) -> bool:
        return self.status != PlaybackStatus.UNAVAILABLE or self.title != self.DISCONNECTED_TITLE or bool(self.artist)
