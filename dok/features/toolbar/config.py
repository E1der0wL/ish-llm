"""Toolbar fields, appearance, defaults and presentation rules."""
from dataclasses import dataclass
from enum import StrEnum

from ...core.records import Boolean, Choice, Nested, Number, Record, Sequence, SettingsError, Text, setting
from ...core.styles import TextStyle
from ...core.colors import HexColor
from ...features.clock.config import ClockFormat, ClockSchedule
from .editor import InputField, NumberCalculator, NumberScope


class UniqueSequence(Sequence):
    def decode(self, value):
        result = super().decode(value)
        if len(set(result)) != len(result):
            raise SettingsError("Duplicate items are not allowed")
        return result

    def schema(self):
        return dict(super().schema(), uniqueItems=True)


class Metric(StrEnum):
    CPU = "cpu"
    MEMORY = "memory"
    NETWORK = "network"


@dataclass(frozen=True, slots=True)
class ToolbarDate(Record):
    enabled: bool = setting(Boolean(), default=True, description="Show the date at the far left.")
    hide_when_dock_visible: bool = setting(Boolean(), default=True,
        description="Hide this date only while the dock is actually painted, in either basic or maximized mode.")
    format: str = setting(ClockFormat(), default="%Y-%m-%d(%a)",
        description="Date format using the shared clock timezone. %a and %A use the weekday labels below.")
    weekdays: tuple[str, ...] = setting(Sequence(Text(single_line=True), minimum=7, maximum=7),
        default=("월", "화", "수", "목", "금", "토", "일"), description="Seven weekday labels, Monday through Sunday.")
    style: TextStyle = setting(Nested(TextStyle), factory=TextStyle, description="Date foreground color.")

    def visible(self, dock_visible: bool) -> bool:
        return self.enabled and bool(self.format) and not (self.hide_when_dock_visible and dock_visible)

    def render(self, now) -> str:
        return ClockFormat.render(self.format, now, weekdays=self.weekdays)

    def schedule(self, clock):
        return ClockSchedule.for_formats(clock, self.format)


@dataclass(frozen=True, slots=True)
class InputItem(Record):
    field: InputField = setting(Choice(InputField), default=InputField.LINE, description="Prompt value to show. Length/columns/positions count Unicode code points, not terminal cells. Positions are one-based.")
    label: str = setting(Text(single_line=True), default="Ln:", description="Text immediately before the value.")
    enabled: bool = setting(Boolean(), default=True, description="Show this input status item.")
    style: TextStyle = setting(Nested(TextStyle), factory=TextStyle, description="Input status foreground color.")


@dataclass(frozen=True, slots=True)
class InputConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Show status of the shell prompt buffer, excluding prompt decoration and suggestions.")
    items: tuple[InputItem, ...] = setting(Sequence(Nested(InputItem), maximum=16),
        factory=lambda: tuple(InputItem(field=field, label=label) for field, label in (
            (InputField.LENGTH, "Len:"), (InputField.LINES, "Lines:"), (InputField.LINE, "Ln:"),
            (InputField.COLUMN, "Col:"), (InputField.POSITION, "Pos:"))),
        description="Input status items in display order. On narrow terminals, later items are omitted first.")


@dataclass(frozen=True, slots=True)
class NumbersConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Show the sum and mean of extracted numbers; never executes expressions.")
    scope: NumberScope = setting(Choice(NumberScope), default=NumberScope.SELECTION,
        description="selection uses the prompt's selected ranges; input uses the entire prompt buffer. Terminal-native mouse selection is not visible to dok.")
    label: str = setting(Text(single_line=True), default="Σ ", description="Text before sum(mean).")
    decimal_places: int = setting(Number(minimum=0, maximum=8, integer=True), default=2,
        description="Maximum decimal places, rounded half up; trailing zeros are removed.")
    empty_text: str = setting(Text(single_line=True), default="—", description="Shown when no numbers are selected/found.")
    limit_text: str = setting(Text(single_line=True), default="…", description="Shown instead of partial results when calculation limits are exceeded.")
    style: TextStyle = setting(Nested(TextStyle), factory=TextStyle, description="Sum and mean foreground color.")

    def render(self, summary):
        if summary.limited:
            return self.label + self.limit_text
        if not summary.count:
            return self.label + self.empty_text
        return self.label + NumberCalculator.format(summary.total, self.decimal_places) + "(" + NumberCalculator.format(summary.average, self.decimal_places) + ")"


@dataclass(frozen=True, slots=True)
class GraphConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Show a compact graph; it shrinks before any text is removed.")
    columns: int = setting(Number(minimum=1, maximum=80, integer=True), default=12,
        description="Maximum terminal columns. CPU/network use two Braille samples per column; memory uses a thin capacity bar.")


@dataclass(frozen=True, slots=True)
class MetricConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Show this metric in the right-hand group.")
    label: str = setting(Text(single_line=True), default="", description="Metric icon or label, usually a Nerd Font glyph.")
    style: TextStyle = setting(Nested(TextStyle), factory=TextStyle, description="Metric text and graph foreground color.")
    graph: GraphConfig = setting(Nested(GraphConfig), factory=GraphConfig, description="Graph visibility and maximum length.")


@dataclass(frozen=True, slots=True)
class CpuStyle(TextStyle):
    color: str = setting(HexColor(), default="#b6c78c")


@dataclass(frozen=True, slots=True)
class MemoryStyle(TextStyle):
    color: str = setting(HexColor(), default="#8ccfc8")


@dataclass(frozen=True, slots=True)
class NetworkStyle(TextStyle):
    color: str = setting(HexColor(), default="#d49abb")


@dataclass(frozen=True, slots=True)
class CpuDisplay(MetricConfig):
    label: str = setting(Text(single_line=True), default="\uf4bc")
    style: CpuStyle = setting(Nested(CpuStyle), factory=CpuStyle)


@dataclass(frozen=True, slots=True)
class MemoryDisplay(MetricConfig):
    label: str = setting(Text(single_line=True), default="\uefc5")
    style: MemoryStyle = setting(Nested(MemoryStyle), factory=MemoryStyle)


@dataclass(frozen=True, slots=True)
class NetworkGraph(GraphConfig):
    enabled: bool = setting(Boolean(), default=False)


@dataclass(frozen=True, slots=True)
class NetworkDisplay(MetricConfig):
    label: str = setting(Text(single_line=True), default="NET")
    style: NetworkStyle = setting(Nested(NetworkStyle), factory=NetworkStyle)
    graph: NetworkGraph = setting(Nested(NetworkGraph), factory=NetworkGraph)


@dataclass(frozen=True, slots=True)
class SystemDisplay(Record):
    enabled: bool = setting(Boolean(), default=True, description="Keep system metrics aligned to the right edge of the toolbar.")
    order: tuple[Metric, ...] = setting(UniqueSequence(Choice(Metric), maximum=len(Metric)),
        default=tuple(Metric), description="Metric display order and priority; later metrics are omitted first if their text cannot fit.")
    cpu: CpuDisplay = setting(Nested(CpuDisplay), factory=CpuDisplay, description="Average CPU utilization and recent-history line graph.")
    memory: MemoryDisplay = setting(Nested(MemoryDisplay), factory=MemoryDisplay, description="Used/total memory, percentage and capacity bar.")
    network: NetworkDisplay = setting(Nested(NetworkDisplay), factory=NetworkDisplay, description="Receive/send rates and optional combined-traffic history graph.")
    download_label: str = setting(Text(single_line=True), default="↓", description="Receive-rate label.")
    upload_label: str = setting(Text(single_line=True), default="↑", description="Send-rate label.")

    def items(self):
        return tuple((kind, getattr(self, kind.value)) for kind in self.order
                     if self.enabled and getattr(self, kind.value).enabled)


@dataclass(frozen=True, slots=True)
class ToolbarConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Let dok define the existing ish bottom toolbar. Disabling restores its previous content.")
    separator: str = setting(Text(single_line=True), default="  ", description="Separator between items. The left/right gap expands to fill the terminal.")
    date: ToolbarDate = setting(Nested(ToolbarDate), factory=ToolbarDate, description="Far-left date and weekday, using the shared clock settings.")
    input: InputConfig = setting(Nested(InputConfig), factory=InputConfig, description="Left-hand shell prompt status fields.")
    numbers: NumbersConfig = setting(Nested(NumbersConfig), factory=NumbersConfig, description="Numeric sum and mean after the input status fields.")
    system: SystemDisplay = setting(Nested(SystemDisplay), factory=SystemDisplay, description="Right-aligned CPU, memory and network display.")

    @property
    def needs_system(self) -> bool:
        return self.enabled and bool(self.system.items())
