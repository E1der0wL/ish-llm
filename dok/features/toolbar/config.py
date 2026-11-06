"""Toolbar fields, appearance, defaults and presentation rules."""
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from ...core.records import Boolean, Choice, Nested, Nullable, Number, Record, Sequence, SettingsError, Text, setting
from ...core.styles import TextStyle
from ...core.colors import HexColor
from ...features.clock.config import ClockFormat, ClockSchedule
from ...rendering.text import fit, width
from .editor import EditorSnapshot, InputField, NumberCalculator, NumberScope, NumberSummary


VALUE_WIDTH = Number(minimum=1, integer=True)
GAP_COLUMNS = Number(minimum=0, integer=True)


@dataclass(frozen=True, slots=True)
class RegionStyle(Record):
    color: str | None = setting(Nullable(HexColor()), default=None,
        description="Foreground for every element and padding cell in this region. null uses individual element colors.")
    background: str | None = setting(Nullable(HexColor()), default=None,
        description="Background in #RRGGBB for the region including padding. null uses the toolbar background.")

    def paint(self, element_color: str | None, background: str | None) -> str:
        foreground = self.color or element_color or "fg:default"
        return f"noreverse bg:{self.background or background or 'default'} {foreground}"


@dataclass(frozen=True, slots=True)
class RegionLayout(Record):
    width_columns: int | None = setting(Nullable(VALUE_WIDTH), default=None,
        description="Positive content width in cells, including internal gaps and graphs. null fits the contents. Outer padding is added separately.")
    padding_before_columns: int = setting(GAP_COLUMNS, default=0,
        description="Padding cells before this region.")
    padding_after_columns: int | None = setting(Nullable(GAP_COLUMNS), default=None,
        description="Padding cells after this region. null uses the normal separator between visible regions.")
    item_gap_columns: int | None = setting(Nullable(GAP_COLUMNS), default=None,
        description="Nonnegative spaces between fields, the sum label and result, or metric labels, values and graphs. null uses their usual spacing. Date text uses date.format.")
    style: RegionStyle = setting(Nested(RegionStyle), factory=RegionStyle,
        description="Optional foreground and background overrides for this entire region.")

    def after_width(self, default: str) -> int:
        return width(default) if self.padding_after_columns is None else self.padding_after_columns

    def after(self, default: str, available: int | None = None) -> str:
        return self._spaces(self.padding_after_columns, default, available)

    def between(self, default: str, available: int | None = None) -> str:
        return self._spaces(self.item_gap_columns, default, available)

    @staticmethod
    def _spaces(count: int | None, default: str, available: int | None) -> str:
        if count is None:
            return default
        return " " * (count if available is None else min(count, available))

    def content_room(self, available: int, after: str) -> int:
        room = max(0, available - self.padding_before_columns - width(after))
        return min(room, self.width_columns) if self.width_columns is not None else room

    @classmethod
    def from_dict(cls, value):
        if isinstance(value, RegionLayout):
            value = value.to_dict()
        return super(RegionLayout, cls).from_dict(value)


class NumberAlignment(StrEnum):
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True, slots=True)
class NumericRegionLayout(RegionLayout):
    alignment: NumberAlignment = setting(Choice(NumberAlignment), default=NumberAlignment.LEFT,
        description="Align values within their fields: left or right. Labels stay in place.")
    max_digits: int | None = setting(Nullable(Number(minimum=1, integer=True)), default=None,
        description="Maximum displayed digits per number; null shows all digits. On overflow try K/M/B/T, then leading digits and three dots. Sum and mean are limited separately.")

    def caption(self, label, value, field_width, minimum, available):
        requested = field_width if field_width is not None else max(minimum, width(value))
        cells = _value_room(label, requested, available)
        return _caption(label, value, cells, available, align_left=self.alignment == NumberAlignment.LEFT)


@dataclass(frozen=True, slots=True)
class MetricRegionLayout(RegionLayout):
    width_columns: int | None = setting(Nullable(VALUE_WIDTH), default=19)


@dataclass(frozen=True, slots=True)
class ToolbarRegions(Record):
    date: RegionLayout = setting(Nested(RegionLayout), factory=RegionLayout, description="Date and weekday region.")
    size: NumericRegionLayout = setting(Nested(NumericRegionLayout), factory=NumericRegionLayout, description="Length and line count region.")
    cursor: NumericRegionLayout = setting(Nested(NumericRegionLayout), factory=NumericRegionLayout, description="Line, column, position and optional editor fields.")
    numbers: NumericRegionLayout = setting(Nested(NumericRegionLayout), factory=NumericRegionLayout, description="Numeric sum and mean region.")
    cpu: MetricRegionLayout = setting(Nested(MetricRegionLayout), factory=MetricRegionLayout, description="CPU label, value and graph region.")
    memory: MetricRegionLayout = setting(Nested(MetricRegionLayout), factory=MetricRegionLayout, description="Memory label, percentage and graph region.")
    network: MetricRegionLayout = setting(Nested(MetricRegionLayout), factory=MetricRegionLayout, description="Network label, receive/send values and graph region.")

    def metric(self, kind):
        return getattr(self, kind.value)


def _value_room(label: str, requested: int | None, available: int | None) -> int | None:
    if available is None:
        return requested
    room = max(0, available - width(label))
    return room if requested is None else min(requested, room)


def _caption(label: str, value: str, cells: int, available: int | None, *, align_left=False) -> str:
    if not cells and available is not None:
        return fit(label + value, available)
    value = fit(value, cells)
    padding = " " * (cells - width(value))
    return label + (value + padding if align_left else padding + value)


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
        default=("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), description="Seven weekday labels, Monday through Sunday.")
    style: TextStyle = setting(Nested(TextStyle), factory=TextStyle, description="Date foreground color.")

    def visible(self, dock_visible: bool) -> bool:
        return self.enabled and bool(self.format) and not (self.hide_when_dock_visible and dock_visible)

    def render(self, now) -> str:
        return ClockFormat.render(self.format, now, weekdays=self.weekdays)

    def schedule(self, clock):
        return ClockSchedule.for_formats(clock, self.format)


@dataclass(frozen=True, slots=True)
class InputItem(Record):
    MIN_AUTO_WIDTH = 5
    field: InputField = setting(Choice(InputField), default=InputField.LINE, description="Prompt value to show. Length/columns/positions count Unicode code points, not terminal cells. Positions are one-based.")
    label: str = setting(Text(single_line=True), default="Ln:", description="Text immediately before the value.")
    enabled: bool = setting(Boolean(), default=True, description="Show this input status item.")
    style: TextStyle = setting(Nested(TextStyle), factory=TextStyle, description="Input status foreground color.")
    value_width_columns: int | None = setting(Nullable(VALUE_WIDTH), default=None,
        description="Fixed field width in cells, or null to reserve five cells and grow for longer values.")

    def render(self, editor: EditorSnapshot, available: int, layout: NumericRegionLayout = NumericRegionLayout()) -> str:
        cells = _value_room(self.label, self.value_width_columns, available)
        value = editor.value(self.field)
        if self.field not in (InputField.MODE, InputField.ENCODING):
            if layout.max_digits is not None or len(value) > cells:
                value = NumberCalculator.compact(Decimal(value), value, cells, max_digits=layout.max_digits)
        return layout.caption(self.label, value, self.value_width_columns, self.MIN_AUTO_WIDTH, available)


@dataclass(frozen=True, slots=True)
class InputConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Show status of the shell prompt buffer, excluding prompt decoration and suggestions.")
    items: tuple[InputItem, ...] = setting(Sequence(Nested(InputItem)),
        factory=lambda: tuple(InputItem(field=field, label=label) for field, label in (
            (InputField.LENGTH, "Len:"), (InputField.LINES, "Lines:"), (InputField.LINE, "Ln:"),
            (InputField.COLUMN, "Col:"), (InputField.POSITION, "Pos:"))),
        description="Input status items in order within each group. Length and lines form the first group. Cursor and editor fields form the next.")

    def groups(self):
        sizes, positions = [], []
        if self.enabled:
            for item in self.items:
                if item.enabled:
                    group = sizes if item.field in (InputField.LENGTH, InputField.LINES) else positions
                    group.append(item)
        return sizes, positions


@dataclass(frozen=True, slots=True)
class NumbersConfig(Record):
    MIN_AUTO_WIDTH = 15
    enabled: bool = setting(Boolean(), default=True, description="Show the sum and mean of extracted numbers; never executes expressions.")
    scope: NumberScope = setting(Choice(NumberScope), default=NumberScope.INPUT,
        description="selection uses the prompt's selected ranges; input uses the entire prompt buffer. Terminal-native mouse selection is not visible to dok.")
    label: str = setting(Text(single_line=True), default="Σ ", description="Text before sum(mean).")
    decimal_places: int | None = setting(Nullable(Number(minimum=0, integer=True)), default=None,
        description="Optional fractional-place limit before display, rounded half up. null uses the calculated precision.")
    empty_text: str = setting(Text(single_line=True), default="—", description="Shown when no numbers are selected/found.")
    limit_text: str = setting(Text(single_line=True), default="…", description=(
        f"Shown if a calculation exceeds {NumberCalculator.MAX_CHARACTERS:,} input characters, "
        f"{NumberCalculator.MAX_NUMBERS:,} numbers or {NumberCalculator.MAX_DIGITS} digits per number."))
    style: TextStyle = setting(Nested(TextStyle), factory=TextStyle, description="Sum and mean foreground color.")
    value_width_columns: int | None = setting(Nullable(VALUE_WIDTH), default=None,
        description="Fixed width for sum(mean), or null to reserve fifteen cells and grow for longer results.")

    def render(self, summary: NumberSummary, available: int | None = None, gap: int | None = None,
               layout: NumericRegionLayout = NumericRegionLayout()) -> str:
        label = self.label
        if gap is not None and label:
            label = label.rstrip(" ") + " " * (gap if available is None else min(gap, available))
        cells = _value_room(label, self.value_width_columns, available)
        if summary.limited:
            value = self.limit_text
        elif not summary.count:
            value = self.empty_text
        else:
            value = NumberCalculator.format_pair(summary, self.decimal_places, cells, max_digits=layout.max_digits)
        if not (label or value):
            return ""
        return layout.caption(label, value, self.value_width_columns, self.MIN_AUTO_WIDTH, available)


@dataclass(frozen=True, slots=True)
class GraphConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Show a compact graph; it shrinks before any text is removed.")
    columns: int = setting(VALUE_WIDTH, default=12,
        description="Maximum terminal columns. CPU/network use two Braille samples per column; memory uses a thin capacity bar.")


@dataclass(frozen=True, slots=True)
class MetricConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Show this metric in the right-hand group.")
    label: str = setting(Text(single_line=True), default="", description="Metric icon or label, usually a Nerd Font glyph.")
    style: TextStyle = setting(Nested(TextStyle), factory=TextStyle, description="Metric text and graph foreground color.")
    graph: GraphConfig = setting(Nested(GraphConfig), factory=GraphConfig, description="Graph visibility and maximum length.")
    value_width_columns: int = setting(VALUE_WIDTH, default=4,
        description="Fixed terminal cells for the right-aligned value, excluding the metric label and graph. Longer values are clipped with an ellipsis.")

    def caption(self, value: str | tuple[str, ...], available: int, separator: str = " ") -> str:
        prefix = self.label + separator if self.label else ""
        cells = _value_room(prefix, self.value_width_columns, available)
        if isinstance(value, tuple):
            value = next((text for text in value if width(text) <= cells), value[-1])
        return _caption(prefix, value, cells, available)


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
    value_width_columns: int = setting(VALUE_WIDTH, default=27)


@dataclass(frozen=True, slots=True)
class SystemDisplay(Record):
    enabled: bool = setting(Boolean(), default=True, description="Keep system metrics aligned to the right edge of the toolbar.")
    order: tuple[Metric, ...] = setting(UniqueSequence(Choice(Metric), maximum=len(Metric)),
        default=tuple(Metric), description="Metric display order and priority; later metrics are omitted first if their text cannot fit.")
    cpu: CpuDisplay = setting(Nested(CpuDisplay), factory=CpuDisplay, description="Average CPU utilization and recent-history line graph.")
    memory: MemoryDisplay = setting(Nested(MemoryDisplay), factory=MemoryDisplay, description="Memory utilization percentage and optional capacity bar.")
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
    group_gap_columns: int = setting(GAP_COLUMNS, default=4,
        description="Spaces between visible date, input size, cursor and numeric summary groups.")
    regions: ToolbarRegions = setting(Nested(ToolbarRegions), factory=ToolbarRegions,
        description="Width, padding, internal gaps and colors for each toolbar region.")
    background: str | None = setting(Nullable(HexColor()), default=None,
        description="Toolbar background in #RRGGBB, including the space between left and right groups. null uses the terminal default.")

    @classmethod
    def initial(cls):
        settings = cls()
        date = replace(settings.regions.date, padding_after_columns=settings.group_gap_columns)
        return replace(settings, group_gap_columns=8, regions=replace(settings.regions, date=date))

    @property
    def needs_system(self) -> bool:
        return self.enabled and bool(self.system.items())
