"""Audio capture and visualizer settings owned by the audio feature."""
from __future__ import annotations

from dataclasses import dataclass

from ...core.colors import DockPalette, HexColor
from ...core.elements import ElementConfig
from ...core.records import Nested, Boolean, Number, Record, Sequence, SettingsError, Text, setting


@dataclass(frozen=True, slots=True)
class AudioConfig(Record):
    enabled: bool = setting(
        Boolean(),
        default=True,
        description="Enable Linux output audio capture. false prevents capture processes from running."
    )
    sources: tuple[str, ...] = setting(Sequence(Text(nonblank=True, single_line=True)), default=(),
        description="Output monitor names to capture. [] uses all discovered output monitors.")

    def _check(self):
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("Duplicate audio monitor source")

    def select(self, monitors: tuple[str, ...]) -> tuple[str, ...]:
        missing = set(self.sources) - set(monitors)
        if missing:
            raise ValueError(f"Output monitors unavailable: {', '.join(sorted(missing))}")
        return self.sources or monitors


@dataclass(frozen=True, slots=True)
class VisualizerStyle(Record):
    colors: tuple[str, ...] = setting(Sequence(HexColor(), minimum=1), default=(DockPalette.ACCENT,),
        description="Colors in #RRGGBB format. One color gives a solid fill; multiple colors form a left-to-right gradient.")

    @classmethod
    def initial(cls) -> VisualizerStyle:
        """Gradient example for newly created files; omitted colors stay solid."""
        return cls(colors=(DockPalette.ACCENT, "#c084fc", "#fb7185"))

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
class VisualizerConfig(ElementConfig):
    style: VisualizerStyle = setting(
        Nested(VisualizerStyle),
        factory=VisualizerStyle,
        description="Solid color or horizontal gradient stops."
    )
    width: int = setting(
        Number(minimum=1, integer=True),
        default=38,
        description="Visualizer length in terminal cells. Clamped to the dock width."
    )

    def display_levels(self, levels: tuple[float, ...], available: int) -> tuple[float, ...]:
        """Fit the full frequency range to the requested visible cell count."""
        cells = max(0, min(self.width, available))
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
