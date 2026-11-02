"""Own the five-row presentation and shared geometry of the dock."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from prompt_toolkit.mouse_events import MouseButton, MouseEventType

from .audio import AudioSnapshot, AudioStatus
from .appearance import DockPalette
from .config import DockConfig
from .player import PlaybackSnapshot
from .shortcuts import Shortcut
from .text import display_text, fit, fit_labels, width


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
    BACKGROUND = f"{BASE} {DockPalette.TEXT}"
    ACCENT = f"{BASE} {DockPalette.ACCENT}"
    DIM = f"{BASE} {DockPalette.MUTED}"
    ERROR = f"{BASE} {DockPalette.ERROR}"

    @classmethod
    def foreground(cls, color: str) -> str:
        return f"{cls.BASE} {color}"


@dataclass(frozen=True, slots=True)
class DockLine:
    row: DockRow
    left: int
    width: int
    fragments: tuple


class DockView:
    LEVEL_GLYPHS = "▁▂▃▄▅▆▇█"
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

    def _progress(self, config, snapshot, body_width):
        left, right = self._time(snapshot.position), self._time(snapshot.duration)
        length = config.progress.display_length(body_width - width(left + right) - 2 * width(self.TIME_SEPARATOR))
        filled = int(snapshot.progress * length)
        return self.TIME_SEPARATOR.join((left, "━" * filled + "─" * (length - filled), right))

    def _shortcuts(self, config, body_width):
        slots = [config.entry(slot) for slot in Shortcut.slots()]
        separator = config.shortcut_layout.separator(body_width)
        compact = [Shortcut.compact_label(slot) for slot in Shortcut.slots()]
        for detailed in (True, False):
            labels = [display_text(item.display(detailed=detailed)) if item else label
                      for item, label in zip(slots, compact)]
            if width(separator.join(labels)) <= body_width:
                break
        else:
            labels = fit_labels(labels, body_width, separator,
                                fixed=tuple(index for index, item in enumerate(slots) if item is None))
        result = []
        for index, (item, label) in enumerate(zip(slots, labels)):
            if index:
                result.append((DockStyle.BACKGROUND, separator, self._swallow))
            enabled = item is not None and item.enabled
            result.append((DockStyle.foreground(item.display_color) if item else DockStyle.DIM, label,
                           self._shortcut_handler(item) if enabled else self._swallow))
        return result

    def _transport(self, config, body_width):
        buttons = config.transport.buttons
        separator = config.transport.SEPARATOR
        labels = fit_labels([button.display() for button in buttons], body_width, separator)
        result = []
        for index, (button, label) in enumerate(zip(buttons, labels)):
            if index:
                result.append((DockStyle.BACKGROUND, separator, self._swallow))
            result.append((DockStyle.foreground(button.color), label, self._swallow))
        return result

    def _visualizer(self, config, audio, body_width):
        if audio.status == AudioStatus.UNAVAILABLE and audio.message:
            return [(DockStyle.ERROR, fit(audio.message, body_width), self._swallow)]
        levels = config.visualizer.display_levels(audio.levels, body_width)
        text = "".join(self.LEVEL_GLYPHS[round(value * (len(self.LEVEL_GLYPHS) - 1))] for value in levels)
        return [(DockStyle.foreground(color), char, self._swallow)
                for char, color in zip(text, config.visualizer.palette(len(text)))]

    def render(self, config: DockConfig, snapshot: PlaybackSnapshot, audio: AudioSnapshot, diagnostic: str,
               total_width: int, body_width: int):
        title = snapshot.caption if snapshot.has_metadata else config.title.application_caption(audio.applications) or snapshot.caption
        rows = {
            DockRow.VISUALIZER: self._visualizer(config, audio, body_width),
            DockRow.PROGRESS: [(DockStyle.foreground(config.progress.color),
                               fit(self._progress(config, snapshot, body_width), body_width), self._swallow)],
            DockRow.TITLE: [(DockStyle.ERROR if diagnostic else DockStyle.foreground(config.title.color),
                            fit(diagnostic or title, body_width), self._swallow)],
            # Playback controls intentionally remain disabled until a player adapter exists.
            DockRow.TRANSPORT: self._transport(config, body_width),
            DockRow.SHORTCUTS: self._shortcuts(config, body_width),
        }
        lines = []
        for row in DockRow:
            content = rows[row]
            occupied = sum(width(part[1]) for part in content)
            left = (total_width - body_width) // 2 + (body_width - occupied) // 2
            lines.append(DockLine(row, left, occupied, tuple(content)))
        return tuple(lines)
