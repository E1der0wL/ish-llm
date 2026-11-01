"""Own the five-row presentation and shared geometry of the dock."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from prompt_toolkit.mouse_events import MouseButton, MouseEventType

from .audio import AudioSnapshot, AudioStatus
from .config import DockConfig
from .player import PlaybackSnapshot
from .shortcuts import Shortcut
from .text import fit, width


class DockRow(IntEnum):
    VISUALIZER = 0
    PROGRESS = 1
    TITLE = 2
    TRANSPORT = 3
    SHORTCUTS = 4


class DockGeometry:
    BODY_ROWS = len(DockRow)
    PADDING_ROWS = 1
    HEIGHT = BODY_ROWS + PADDING_ROWS
    MIN_HEIGHT = HEIGHT + 1

    @classmethod
    def fits(cls, available_height, body_width):
        return available_height >= cls.MIN_HEIGHT and body_width >= Shortcut.minimum_width()


class DockStyle:
    # Use the terminal background, including its configured transparency.
    BASE = "bg:default"
    BACKGROUND = f"{BASE} #d1d5db"
    ACCENT = f"{BASE} #67e8f9"
    DIM = f"{BASE} #6b7280"
    ERROR = f"{BASE} #fbbf24"


@dataclass(frozen=True, slots=True)
class DockLine:
    row: DockRow
    left: int
    width: int
    fragments: tuple


class DockView:
    LEVEL_GLYPHS = "▁▂▃▄▅▆▇█"
    TRANSPORT = "⏮   ⏯   ⏭"
    BAR_MAX = 48
    TIME_SEPARATOR = " "

    def __init__(self, execute):
        self._execute = execute

    @staticmethod
    def _swallow(event):
        return None

    def _shortcut_handler(self, shortcut):
        def handler(event):
            if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                self._execute(shortcut.slot)
            return None
        return handler

    @staticmethod
    def _time(seconds):
        if seconds is None:
            return "--:--"
        minutes, seconds = divmod(int(seconds), 60)
        return f"{minutes:02}:{seconds:02}"

    def _progress(self, snapshot, body_width):
        left, right = self._time(snapshot.position), self._time(snapshot.duration)
        length = max(1, min(self.BAR_MAX, body_width - width(left + right) - 2 * len(self.TIME_SEPARATOR)))
        filled = int(snapshot.progress * length)
        return self.TIME_SEPARATOR.join((left, "━" * filled + "─" * (length - filled), right))

    def _shortcuts(self, config, body_width):
        slots = [config.shortcut(slot) for slot in Shortcut.slots()]
        compact = [Shortcut.compact_label(slot) for slot in Shortcut.slots()]
        for detailed in (True, False):
            labels = [item.display(detailed=detailed) if item else label for item, label in zip(slots, compact)]
            if width(Shortcut.SEPARATOR.join(labels)) <= body_width:
                break
        else:
            labels = compact
        result = []
        for index, (item, label) in enumerate(zip(slots, labels)):
            if index:
                result.append((DockStyle.BACKGROUND, Shortcut.SEPARATOR, self._swallow))
            enabled = item is not None and item.enabled
            result.append((DockStyle.ACCENT if enabled else DockStyle.DIM, label,
                           self._shortcut_handler(item) if enabled else self._swallow))
        return result

    def render(self, config: DockConfig, snapshot: PlaybackSnapshot, audio: AudioSnapshot, diagnostic: str,
               total_width: int, body_width: int):
        visualizer = "".join(self.LEVEL_GLYPHS[round(value * (len(self.LEVEL_GLYPHS) - 1))] for value in audio.levels)
        if audio.status == AudioStatus.UNAVAILABLE and audio.message:
            visualizer = audio.message
        title = snapshot.caption if snapshot.has_metadata else audio.application_caption or snapshot.caption
        rows = {
            DockRow.VISUALIZER: [(DockStyle.ACCENT, fit(visualizer, body_width), self._swallow)],
            DockRow.PROGRESS: [(DockStyle.DIM, fit(self._progress(snapshot, body_width), body_width), self._swallow)],
            DockRow.TITLE: [(DockStyle.ERROR if diagnostic else DockStyle.BACKGROUND,
                            fit(diagnostic or title, body_width), self._swallow)],
            # Playback controls intentionally remain disabled until a player adapter exists.
            DockRow.TRANSPORT: [(DockStyle.DIM, self.TRANSPORT, self._swallow)],
            DockRow.SHORTCUTS: self._shortcuts(config, body_width),
        }
        lines = []
        for row in DockRow:
            content = rows[row]
            occupied = sum(width(part[1]) for part in content)
            left = (total_width - body_width) // 2 + (body_width - occupied) // 2
            lines.append(DockLine(row, left, occupied, tuple(content)))
        return tuple(lines)
