"""Toolbar fields, appearance, defaults and presentation rules."""
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from ...core.records import Boolean, Choice, Nested, Nullable, Number, Record, Sequence, SettingsError, Text, setting
from ...core.styles import TextStyle
from ...core.theme import Theme, ThemeRole, ThemeReference, ThemeBackground, theme_reference
from ...features.clock.config import ClockFormat, ClockSchedule
from ...rendering.text import fit, width
from .editor import EditorSnapshot, InputField, NumberCalculator, NumberScope, NumberSummary
from ...locales import Locale, Message


VALUE_WIDTH = Number(minimum=1, integer=True)
GAP_COLUMNS = Number(minimum=0, integer=True)


@dataclass(frozen=True, slots=True)
class RegionStyle(Record):
    color: str | None = setting(Nullable(ThemeReference()), default=None,
        description="Theme role or palette name for every element and padding cell in this region. null uses individual element selectors.")
    background: str | None = setting(Nullable(ThemeReference()), default=None,
        description="Theme role or palette name for the region including padding. null inherits toolbar.background; use background for the shared theme background.")

    def paint(self, element_color: str | None, background: str | None, theme: Theme = Theme()) -> str:
        return theme.paint(self.color or element_color or ThemeRole.FOREGROUND,
                           background=self.background or background or ThemeRole.BACKGROUND)


@dataclass(frozen=True, slots=True)
class RegionLayout(Record):
    width: int | None = setting(Nullable(VALUE_WIDTH), default=None,
        description="Positive content width in cells, including internal gaps and graphs. null fits the contents. Outer padding is added separately.")
    pad_left: int = setting(GAP_COLUMNS, default=0,
        description="Padding cells before this region.")
    pad_right: int | None = setting(Nullable(GAP_COLUMNS), default=None,
        description="Padding cells after this region. null uses the normal separator between visible regions.")
    gap: int | None = setting(Nullable(GAP_COLUMNS), default=None,
        description="Nonnegative spaces between fields, the sum label and result, or metric labels, values and graphs. null uses their usual spacing. Date text uses date.format.")
    style: RegionStyle = setting(Nested(RegionStyle), factory=RegionStyle,
        description="Optional foreground and background overrides for this entire region.")

    def after_width(self, default: str) -> int:
        return width(default) if self.pad_right is None else self.pad_right

    def after(self, default: str, available: int | None = None) -> str:
        return self._spaces(self.pad_right, default, available)

    def between(self, default: str, available: int | None = None) -> str:
        return self._spaces(self.gap, default, available)

    @staticmethod
    def _spaces(count: int | None, default: str, available: int | None) -> str:
        if count is None:
            return default
        return " " * (count if available is None else min(count, available))

    def content_room(self, available: int, after: str) -> int:
        room = max(0, available - self.pad_left - width(after))
        return min(room, self.width) if self.width is not None else room

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
    gap: int | None = setting(Nullable(GAP_COLUMNS), default=2)
    align: NumberAlignment = setting(Choice(NumberAlignment), default=NumberAlignment.LEFT,
        description="Align values within their fields: left or right. Labels stay in place.")
    digits: int | None = setting(Nullable(Number(minimum=1, integer=True)), default=None,
        description="Maximum displayed digits per number; null shows all digits. On overflow try K/M/B/T, then leading digits and three dots. Sum and mean are limited separately.")

    def caption(self, label, value, field_width, available):
        requested = field_width if field_width is not None else width(value)
        cells = _value_room(label, requested, available)
        return _caption(label, value, cells, available, align_left=self.align == NumberAlignment.LEFT)


@dataclass(frozen=True, slots=True)
class MetricRegionLayout(RegionLayout):
    width: int | None = setting(Nullable(VALUE_WIDTH), default=19)


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
    hide_with_dock: bool = setting(Boolean(), default=True,
        description="Hide this date only while the dock is actually painted, in either basic or maximized mode.")
    format: str = setting(ClockFormat(), default="%Y-%m-%d(%a)",
        description="Date format using the shared clock timezone. %a and %A use the weekday labels below.")
    weekdays: tuple[str, ...] | None = setting(Nullable(Sequence(Text(single_line=True), minimum=7, maximum=7)),
        default=None, description="null uses localized weekday abbreviations (English: Mon through Sun). An array supplies seven custom labels, Monday through Sunday.")
    style: TextStyle = setting(Nested(TextStyle), factory=TextStyle, description="Date foreground color.")

    def visible(self, dock_visible: bool) -> bool:
        return self.enabled and bool(self.format) and not (self.hide_with_dock and dock_visible)

    def render(self, now, locale=Locale()) -> str:
        return ClockFormat.render(self.format, now, weekdays=self.weekdays, locale=locale)

    def schedule(self, clock):
        return ClockSchedule.for_formats(clock, self.format)


@dataclass(frozen=True, slots=True)
class InputItem(Record):
    field: InputField = setting(Choice(InputField), default=InputField.LINE, description="Prompt value to show. Length/columns/positions count Unicode code points, not terminal cells. Positions are one-based.")
    label: str | None = setting(Nullable(Text(single_line=True)), default=None, description="Text before the value. null uses the field's localized label (English: Len:, Lines:, Ln:, Col:, Pos:); strings are preserved verbatim.")
    enabled: bool = setting(Boolean(), default=True, description="Show this input status item.")
    style: TextStyle = setting(Nested(TextStyle), factory=TextStyle, description="Input status foreground color.")
    value_width: int | None = setting(Nullable(VALUE_WIDTH), default=None,
        description="Fixed value width in cells, or null to fit the displayed value without extra padding. Use the region gap for spacing between items.")

    def render(self, editor: EditorSnapshot, available: int, layout: NumericRegionLayout = NumericRegionLayout(), *, locale=Locale()) -> str:
        label = self.field.label(locale) if self.label is None else self.label
        cells = _value_room(label, self.value_width, available)
        value = editor.value(self.field)
        if self.field not in (InputField.MODE, InputField.ENCODING):
            if layout.digits is not None or len(value) > cells:
                value = NumberCalculator.compact(Decimal(value), value, cells, digits=layout.digits)
        return layout.caption(label, value, self.value_width, available)


@dataclass(frozen=True, slots=True)
class InputConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Show status of the shell prompt buffer, excluding prompt decoration and suggestions.")
    items: tuple[InputItem, ...] = setting(Sequence(Nested(InputItem)),
        factory=lambda: tuple(InputItem(field=field) for field in (
            InputField.LENGTH, InputField.LINES, InputField.LINE, InputField.COLUMN, InputField.POSITION)),
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
    enabled: bool = setting(Boolean(), default=True, description="Show the sum and mean of extracted numbers; never executes expressions.")
    scope: NumberScope = setting(Choice(NumberScope), default=NumberScope.INPUT,
        description="selection uses the prompt's selected ranges; input uses the entire prompt buffer. Terminal-native mouse selection is not visible to dok.")
    label: str | None = setting(Nullable(Text(single_line=True)), default=None, description="Text before sum(mean). null uses the UI language (English: Total: ); a string is preserved verbatim.")
    decimals: int | None = setting(Nullable(Number(minimum=0, integer=True)), default=None,
        description="Optional fractional-place limit before display, rounded half up. null uses the calculated precision.")
    empty: str = setting(Text(single_line=True), default="—", description="Shown when no numbers are selected/found.")
    overflow: str = setting(Text(single_line=True), default="…", description=(
        f"Shown if a calculation exceeds {NumberCalculator.MAX_CHARACTERS:,} input characters, "
        f"{NumberCalculator.MAX_NUMBERS:,} numbers or {NumberCalculator.MAX_DIGITS} digits per number."))
    style: TextStyle = setting(Nested(TextStyle), factory=TextStyle, description="Sum and mean foreground color.")
    value_width: int | None = setting(Nullable(VALUE_WIDTH), default=None,
        description="Fixed width for sum(mean), or null to fit the displayed result without extra padding. Use region gaps for spacing.")

    def render(self, summary: NumberSummary, available: int | None = None, gap: int | None = None,
               layout: NumericRegionLayout = NumericRegionLayout(), *, locale=Locale()) -> str:
        label = locale.custom(self.label, Message.TOTAL)
        if gap is not None and label:
            label = label.rstrip(" ") + " " * (gap if available is None else min(gap, available))
        cells = _value_room(label, self.value_width, available)
        if summary.limited:
            value = self.overflow
        elif not summary.count:
            value = self.empty
        else:
            value = NumberCalculator.format_pair(summary, self.decimals, cells, digits=layout.digits)
        if not (label or value):
            return ""
        return layout.caption(label, value, self.value_width, available)


@dataclass(frozen=True, slots=True)
class GraphConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Show a compact graph; it shrinks before any text is removed.")
    width: int = setting(VALUE_WIDTH, default=12,
        description="Maximum terminal cells. CPU uses one sample per cell with eight block heights (12.5% steps); network uses two Braille samples per cell; memory uses a thin capacity bar.")


@dataclass(frozen=True, slots=True)
class MetricConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Show this metric in the right-hand group.")
    label: str = setting(Text(single_line=True), default="", description="Metric icon or label, usually a Nerd Font glyph.")
    style: TextStyle = setting(Nested(TextStyle), factory=TextStyle, description="Metric text and graph foreground color.")
    graph: GraphConfig = setting(Nested(GraphConfig), factory=GraphConfig, description="Graph visibility and maximum length.")
    value_width: int = setting(VALUE_WIDTH, default=4,
        description="Fixed terminal cells for the right-aligned value, excluding the metric label and graph. Longer values are clipped with an ellipsis.")

    def caption(self, value: str | tuple[str, ...], available: int, separator: str = " ") -> str:
        prefix = self.label + separator if self.label else ""
        cells = _value_room(prefix, self.value_width, available)
        if isinstance(value, tuple):
            value = next((text for text in value if width(text) <= cells), value[-1])
        return _caption(prefix, value, cells, available)


@dataclass(frozen=True, slots=True)
class CpuStyle(TextStyle):
    color: str = theme_reference(ThemeRole.CPU)


@dataclass(frozen=True, slots=True)
class MemoryStyle(TextStyle):
    color: str = theme_reference(ThemeRole.MEMORY)


@dataclass(frozen=True, slots=True)
class NetworkStyle(TextStyle):
    color: str = theme_reference(ThemeRole.NETWORK)


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
    label: str = setting(Text(single_line=True), default="\U000f06f3",
        description="Network icon or text. The default network glyph requires a Nerd Font.")
    style: NetworkStyle = setting(Nested(NetworkStyle), factory=NetworkStyle)
    graph: NetworkGraph = setting(Nested(NetworkGraph), factory=NetworkGraph)
    value_width: int = setting(VALUE_WIDTH, default=27)


@dataclass(frozen=True, slots=True)
class SystemDisplay(Record):
    enabled: bool = setting(Boolean(), default=True, description="Keep system metrics aligned to the right edge of the toolbar.")
    order: tuple[Metric, ...] = setting(UniqueSequence(Choice(Metric), maximum=len(Metric)),
        default=tuple(Metric), description="Metric display order and priority; later metrics are omitted first if their text cannot fit.")
    cpu: CpuDisplay = setting(Nested(CpuDisplay), factory=CpuDisplay, description="Average CPU utilization and eight-level history graph on a fixed 0–100% scale. The unused leading history uses the lowest block from startup; the numeric value stays unknown until measured.")
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
    gap: int = setting(GAP_COLUMNS, default=4,
        description="Spaces between visible date, input size, cursor and numeric summary groups.")
    regions: ToolbarRegions = setting(Nested(ToolbarRegions), factory=ToolbarRegions,
        description="Width, padding, internal gaps and colors for each toolbar region.")
    background: str = setting(ThemeBackground(), default=ThemeRole.BACKGROUND,
        description="Toolbar background including the center gap and outer padding. Use a theme role/palette name or #RRGGBB for a toolbar-only override. background follows the shared theme background. Runtime default: background; newly generated files: #1D2035.")
    pad_left: int = setting(GAP_COLUMNS, default=0,
        description="Nonnegative blank cells at the left edge of the entire toolbar. Runtime default: 0; newly generated files: 1. Fitted to the terminal width; left padding is reserved first if both edges cannot fit.")
    pad_right: int = setting(GAP_COLUMNS, default=0,
        description="Nonnegative blank cells at the right edge of the entire toolbar. System metrics align immediately before this padding. Runtime default: 0; newly generated files: 1. Fitted to the width remaining after left padding.")

    @classmethod
    def initial(cls):
        settings = cls()
        date = replace(settings.regions.date, pad_right=settings.gap)
        return replace(settings, regions=replace(settings.regions, date=date),
                       pad_left=1, pad_right=1, background="#1D2035")

    def split_width(self, columns: int) -> tuple[int, int, int]:
        """Allocate left padding, content and right padding within the viewport."""
        columns = max(0, columns)
        left = min(self.pad_left, columns)
        right = min(self.pad_right, columns - left)
        return left, columns - left - right, right

    @property
    def needs_system(self) -> bool:
        return self.enabled and bool(self.system.items())
