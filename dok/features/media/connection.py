"""Media connection settings and typed player/control contracts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...core.records import Boolean, Nullable, Number, Record, Text, setting
from .models import PlaybackAction, PlaybackSnapshot, PlaybackStatus


@dataclass(frozen=True, slots=True)
class MediaConfig(Record):
    enabled: bool = setting(Boolean(), default=True,
        description="Connect to MPRIS players on the Linux session bus while the dock is visible.")
    player: str | None = setting(Nullable(Text(nonblank=True, single_line=True)), default=None,
        description="Exact MPRIS bus name, or null to select a playing player and retain it until it exits.")
    refresh_seconds: float = setting(Number(minimum=0.5, maximum=60), default=1.0,
        description="Seconds between player state queries while visible. No polling while hidden.")
    timeout_seconds: float = setting(Number(minimum=0.1, maximum=30), default=2.0,
        description="Maximum seconds for each D-Bus request. Failed commands are never retried.")


@dataclass(frozen=True, slots=True)
class PlayerState:
    name: str = ""
    owner: str = ""
    track: str = ""
    playback: PlaybackSnapshot = PlaybackSnapshot()
    actions: frozenset[PlaybackAction] = frozenset()
    seekable: bool = False

    @property
    def token(self) -> tuple[str, str, str]:
        return self.name, self.owner, self.track

    @property
    def connected(self) -> bool:
        return bool(self.owner)

    def supports(self, action: PlaybackAction) -> bool:
        return self.connected and action in self.actions

    @property
    def can_seek(self) -> bool:
        return (self.connected and self.seekable and bool(self.track)
                and self.playback.duration is not None and self.playback.duration > 0)

    def accepts_seek(self, fraction: float) -> bool:
        return self.can_seek and type(fraction) in (int, float) and 0 <= fraction <= 1

    def seek_position(self, fraction: float) -> float:
        if not self.accepts_seek(fraction):
            raise ValueError("Player cannot seek to this position")
        return self.playback.duration * fraction

    @staticmethod
    def select(players: tuple[PlayerState, ...], retained: str | None) -> PlayerState:
        for player in players:
            if player.name == retained:
                return player
        return next((p for p in players if p.playback.status == PlaybackStatus.PLAYING),
                    players[0] if players else PlayerState())


class PlayerBackend(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def discover(self) -> tuple[str, ...]: ...
    async def read(self, name: str) -> PlayerState: ...
    async def control(self, player: PlayerState, action: PlaybackAction) -> None: ...
    async def seek(self, player: PlayerState, position: float) -> None: ...
