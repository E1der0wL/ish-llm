"""Color declarations, validation and horizontal gradient interpretation."""

from dataclasses import dataclass
import re
from typing import ClassVar

from .player import PlaybackAction
from .records import Choice, Nested, Number, Record, Rule, Sequence, SettingsError, Text, setting


class DockPalette:
    TEXT = "#d1d5db"
    ACCENT = "#67e8f9"
    MUTED = "#6b7280"
    ERROR = "#fbbf24"


class HexColor(Rule):
    LENGTH = 7
    PATTERN = rf"^#[0-9a-fA-F]{{{LENGTH - 1}}}$"

    def decode(self, value):
        if not isinstance(value, str) or re.fullmatch(self.PATTERN, value) is None:
            raise SettingsError("Expected a color in #RRGGBB format")
        return value.lower()

    def schema(self):
        return {"type": "string", "pattern": self.PATTERN, "minLength": self.LENGTH, "maxLength": self.LENGTH}

    @staticmethod
    def blend(left: str, right: str, fraction: float) -> str:
        channels = (round(int(left[index:index + 2], 16) * (1 - fraction)
                          + int(right[index:index + 2], 16) * fraction) for index in (1, 3, 5))
        return "#" + "".join(f"{channel:02x}" for channel in channels)


@dataclass(frozen=True, slots=True)
class VisualizerConfig(Record):
    colors: tuple[str, ...] = setting(Sequence(HexColor(), minimum=1), default=(DockPalette.ACCENT,))
    length: int = setting(Number(minimum=1, integer=True), default=24)

    def display_levels(self, levels: tuple[float, ...], available: int) -> tuple[float, ...]:
        """Fit the full frequency range to the requested visible cell count."""
        cells = max(0, min(self.length, available))
        count = len(levels)
        if not count:
            return (0.0,) * cells
        if cells == count:
            return levels
        if cells < count:
            # Retain peaks when several frequency bands share a display cell.
            return tuple(max(levels[index * count // cells:(index + 1) * count // cells])
                         for index in range(cells))
        result = []
        for index in range(cells):
            left, remainder = divmod(index * (count - 1), cells - 1)
            fraction = remainder / (cells - 1)
            result.append(levels[left] * (1 - fraction) + levels[min(left + 1, count - 1)] * fraction)
        return tuple(result)

    def palette(self, cells: int) -> tuple[str, ...]:
        """Evenly spaced RGB stops across the displayed row, independent of level."""
        if type(cells) is not int or cells < 0:
            raise SettingsError("Palette width must be a nonnegative integer")
        if cells <= 1 or len(self.colors) == 1:
            return (self.colors[0],) * cells
        result = []
        for cell in range(cells):
            position = cell * (len(self.colors) - 1) / (cells - 1)
            left = min(int(position), len(self.colors) - 2)
            result.append(HexColor.blend(self.colors[left], self.colors[left + 1], position - left))
        return tuple(result)


@dataclass(frozen=True, slots=True)
class TitleConfig(Record):
    APPLICATION_SEPARATOR: ClassVar[str] = " · "
    color: str = setting(HexColor(), default=DockPalette.TEXT)
    prefix: str = setting(Text(single_line=True), default="재생 중: ")

    def application_caption(self, applications: tuple[str, ...]) -> str:
        return self.prefix + self.APPLICATION_SEPARATOR.join(applications) if applications else ""


@dataclass(frozen=True, slots=True)
class ProgressConfig(Record):
    color: str = setting(HexColor(), default=DockPalette.MUTED)
    length: int = setting(Number(minimum=1, integer=True), default=48)

    def display_length(self, available: int) -> int:
        return max(0, min(self.length, available))


@dataclass(frozen=True, slots=True)
class TransportButton(Record):
    action: PlaybackAction = setting(Choice(PlaybackAction))
    text: str = setting(Text(single_line=True), default="")
    color: str = setting(HexColor(), default=DockPalette.MUTED)

    def display(self) -> str:
        return self.text if self.text.strip() else self.action.symbol


@dataclass(frozen=True, slots=True)
class TransportConfig(Record):
    SEPARATOR: ClassVar[str] = "   "
    buttons: tuple[TransportButton, ...] = setting(
        Sequence(Nested(TransportButton), maximum=len(PlaybackAction)),
        default=(TransportButton(PlaybackAction.PREVIOUS), TransportButton(PlaybackAction.PLAY_PAUSE),
                 TransportButton(PlaybackAction.NEXT)),
    )

    def _check(self):
        if len({button.action for button in self.buttons}) != len(self.buttons):
            raise SettingsError("Duplicate transport button action")

    @classmethod
    def schema(cls):
        result = super(TransportConfig, cls).schema()
        result["properties"][cls.names().buttons]["allOf"] = [
            {"contains": {"properties": {TransportButton.names().action: {"const": action}},
                          "required": [TransportButton.names().action]},
             "minContains": 0, "maxContains": 1} for action in PlaybackAction
        ]
        return result
