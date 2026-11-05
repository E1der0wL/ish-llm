"""MPRIS wire names, units and external variants belong to this adapter."""
from __future__ import annotations

import asyncio
import math

from ...features.media.connection import PlayerState
from ...features.media.models import PlaybackAction, PlaybackSnapshot, PlaybackStatus


class MprisBackend:
    PREFIX = "org.mpris.MediaPlayer2."
    PATH = "/org/mpris/MediaPlayer2"
    PLAYER = "org.mpris.MediaPlayer2.Player"
    PROPERTIES = "org.freedesktop.DBus.Properties"
    DBUS = "org.freedesktop.DBus"
    DBUS_PATH = "/org/freedesktop/DBus"
    NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
    MICROSECONDS = 1_000_000
    MAX_PLAYERS = 32
    METHODS = {
        PlaybackAction.PREVIOUS: "Previous", PlaybackAction.NEXT: "Next",
        PlaybackAction.PLAY_PAUSE: "PlayPause", PlaybackAction.PLAY: "Play",
        PlaybackAction.PAUSE: "Pause", PlaybackAction.STOP: "Stop",
    }

    def __init__(self, timeout: float):
        self._timeout = timeout
        self._bus = None

    async def _call(self, destination, path, interface, member, signature="", body=None):
        from dbus_fast import Message, MessageType
        async with asyncio.timeout(self._timeout):
            reply = await self._bus.call(Message(destination=destination, path=path,
                interface=interface, member=member, signature=signature, body=body or []))
        if reply is None or reply.message_type == MessageType.ERROR:
            raise RuntimeError(reply.error_name if reply else "No D-Bus reply")
        return reply.body

    @staticmethod
    def _value(properties, name, default=None):
        value = properties.get(name)
        return value.value if value is not None else default

    @classmethod
    def _seconds(cls, value):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            return None
        return value / cls.MICROSECONDS

    @classmethod
    def decode(cls, name: str, owner: str, properties: dict) -> PlayerState:
        value = lambda key, default=None: cls._value(properties, key, default)
        metadata = value("Metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        meta = lambda key, default=None: cls._value(metadata, key, default)
        status = {"Playing": PlaybackStatus.PLAYING, "Paused": PlaybackStatus.PAUSED,
                  "Stopped": PlaybackStatus.STOPPED}.get(value("PlaybackStatus"), PlaybackStatus.UNAVAILABLE)
        title, artist, track = meta("xesam:title", ""), meta("xesam:artist", []), meta("mpris:trackid", "")
        title = title if isinstance(title, str) else ""
        artist = ", ".join(x for x in artist if isinstance(x, str)) if isinstance(artist, list) else ""
        track = track if isinstance(track, str) and track.startswith("/") and track != cls.NO_TRACK else ""
        actions = set()
        if value("CanControl", False) is True:
            for action, capability in ((PlaybackAction.PREVIOUS, "CanGoPrevious"),
                    (PlaybackAction.NEXT, "CanGoNext"), (PlaybackAction.PLAY, "CanPlay"),
                    (PlaybackAction.PAUSE, "CanPause")):
                if value(capability, False) is True:
                    actions.add(action)
            actions.add(PlaybackAction.STOP)
            # PlayPause toggles to Play when paused/stopped, and to Pause when playing.
            if (PlaybackAction.PAUSE in actions and
                    (status == PlaybackStatus.PLAYING or PlaybackAction.PLAY in actions)):
                actions.add(PlaybackAction.PLAY_PAUSE)
        return PlayerState(name, owner, track, PlaybackSnapshot(status, title or name[len(cls.PREFIX):],
            artist, cls._seconds(value("Position")) or 0.0, cls._seconds(meta("mpris:length"))),
            frozenset(actions), value("CanControl", False) is True and value("CanSeek", False) is True)

    async def connect(self) -> None:
        from dbus_fast.aio import MessageBus
        self._bus = MessageBus(auth_timeout=self._timeout)
        async with asyncio.timeout(self._timeout):
            await self._bus.connect()

    async def close(self) -> None:
        if self._bus is not None:
            self._bus.disconnect()
            try:
                async with asyncio.timeout(self._timeout):
                    await self._bus.wait_for_disconnect()
            except (OSError, TimeoutError):
                pass
            self._bus = None

    async def discover(self) -> tuple[str, ...]:
        names, = await self._call(self.DBUS, self.DBUS_PATH, self.DBUS, "ListNames")
        return tuple(sorted(n for n in names if n.startswith(self.PREFIX)))[:self.MAX_PLAYERS]

    async def read(self, name: str) -> PlayerState:
        owner, = await self._call(self.DBUS, self.DBUS_PATH, self.DBUS, "GetNameOwner", "s", [name])
        properties, = await self._call(owner, self.PATH, self.PROPERTIES, "GetAll", "s", [self.PLAYER])
        return self.decode(name, owner, properties)

    async def control(self, player: PlayerState, action: PlaybackAction) -> None:
        await self._call(player.owner, self.PATH, self.PLAYER, self.METHODS[action])

    async def seek(self, player: PlayerState, position: float) -> None:
        await self._call(player.owner, self.PATH, self.PLAYER, "SetPosition", "ox",
                         [player.track, int(position * self.MICROSECONDS)])
