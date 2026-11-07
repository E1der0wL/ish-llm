"""Popup dimensions, fitted geometry and appearance are declared here."""
from dataclasses import dataclass
from enum import StrEnum
import math
from typing import ClassVar

from ..core.theme import Theme, ThemeRole, theme_reference
from ..core.records import Boolean, Choice, Nested, Nullable, Number, POSITIVE_SECONDS, Record, setting
from ..integrations.ish.keys import KeyRule, KeySequence


class DimensionMode(StrEnum):
    FIXED = "fixed"
    RATIO = "ratio"


@dataclass(frozen=True, slots=True)
class PopupInput(Record):
    escape_s: float = setting(POSITIVE_SECONDS, default=0.05,
        description="Seconds to distinguish Escape from terminal/Alt sequences while a popup has focus. Applies to both parser and key binding waits; original host values are restored on exit. Increase for slow remote terminals.")


@dataclass(frozen=True, slots=True)
class PopupDimension(Record):
    mode: DimensionMode = setting(Choice(DimensionMode), default=DimensionMode.FIXED,
        description="fixed counts terminal cells; ratio is a fraction of the available input area.")
    value: int | float = setting(Number(), default=72,
        description="Positive whole cells for fixed; greater than 0 and at most 1 for ratio. Fitted to the viewport.")

    @classmethod
    def value_rule(cls, mode):
        return (Number(minimum=1, integer=True) if mode == DimensionMode.FIXED
                else Number(minimum=0, maximum=1, exclusive_minimum=True))

    def _check(self):
        self.value_rule(self.mode).decode(self.value)

    def resolve(self, available: int) -> int:
        requested = self.value if self.mode == DimensionMode.FIXED else math.floor(available * self.value)
        return min(available, max(1, int(requested))) if available > 0 else 0

    @classmethod
    def from_dict(cls, value):
        return super(PopupDimension, cls).from_dict(value.to_dict() if isinstance(value, PopupDimension) else value)

    @classmethod
    def schema(cls):
        result = super(PopupDimension, cls).schema()
        names = cls.names()
        result["allOf"] = [
            {"if": {"properties": {names.mode: {"const": mode}},
                    "required": [names.mode] if mode == DimensionMode.RATIO else []},
             "then": {"properties": {names.value: cls.value_rule(mode).schema()},
                      "required": [names.value] if mode == DimensionMode.RATIO else []}}
            for mode in DimensionMode
        ]
        return result


@dataclass(frozen=True, slots=True)
class PopupHeight(PopupDimension):
    value: int | float = setting(Number(), default=20)


@dataclass(frozen=True, slots=True)
class NotificationWidth(PopupDimension):
    value: int | float = setting(Number(), default=40)


@dataclass(frozen=True, slots=True)
class NotificationHeight(PopupDimension):
    value: int | float = setting(Number(), default=7)


@dataclass(frozen=True, slots=True)
class PopupInsets(Record):
    top: int = setting(Number(minimum=0, integer=True), default=0, description="Nonnegative top rows, fitted to the viewport.")
    right: int = setting(Number(minimum=0, integer=True), default=1, description="Nonnegative right cells, fitted to the viewport.")
    bottom: int = setting(Number(minimum=0, integer=True), default=0, description="Nonnegative bottom rows, fitted to the viewport.")
    left: int = setting(Number(minimum=0, integer=True), default=1, description="Nonnegative left cells, fitted to the viewport.")

    def fit(self, width: int, height: int, *, keep_width=1, keep_height=1):
        left = min(self.left, max(0, width - keep_width))
        right = min(self.right, max(0, width - keep_width - left))
        top = min(self.top, max(0, height - keep_height))
        bottom = min(self.bottom, max(0, height - keep_height - top))
        return left, top, right, bottom

    @classmethod
    def from_dict(cls, value):
        return super(PopupInsets, cls).from_dict(value.to_dict() if isinstance(value, PopupInsets) else value)


@dataclass(frozen=True, slots=True)
class PopupMargin(PopupInsets):
    top: int = setting(Number(minimum=0, integer=True), default=1)
    bottom: int = setting(Number(minimum=0, integer=True), default=1)


@dataclass(frozen=True, slots=True)
class PopupStyle(Record):
    color: str = theme_reference()
    background: str = theme_reference(ThemeRole.BACKGROUND, "Panel background")
    border_color: str = theme_reference(ThemeRole.SEPARATOR, "Panel border")

    def paint(self, *, theme: Theme = Theme(), border=False, selected=False, color=None, bold=False):
        foreground = self.border_color if border else self.color if color is None else color
        return theme.span(ThemeRole.SELECTED_FG if selected else foreground,
                          background=ThemeRole.SELECTED_BG if selected else self.background, bold=bold)


@dataclass(frozen=True, slots=True)
class PopupRect:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class PopupPanel(Record):
    COMPACT_WIDTH: ClassVar[int] = 4
    COMPACT_HEIGHT: ClassVar[int] = 3
    width: PopupDimension = setting(Nested(PopupDimension), factory=PopupDimension, description="Popup width.")
    height: PopupHeight = setting(Nested(PopupHeight), factory=PopupHeight, description="Popup height.")
    margin: PopupMargin = setting(Nested(PopupMargin), factory=PopupMargin,
        description="Space outside the panel. General popups center in the remaining input area; notifications align bottom right.")
    padding: PopupInsets = setting(Nested(PopupInsets), factory=PopupInsets, description="Space inside the panel; reduced when needed to keep content visible.")
    border: bool = setting(Boolean(), default=True, description="Draw a box border when the viewport has room.")
    style: PopupStyle = setting(Nested(PopupStyle), factory=PopupStyle, description="Panel text, background and border colors.")

    def fit(self, width: int, height: int, *, notification=False) -> PopupRect:
        left, top, right, bottom = self.margin.fit(width, height, keep_width=self.COMPACT_WIDTH, keep_height=self.COMPACT_HEIGHT)
        room_width, room_height = max(0, width - left - right), max(0, height - top - bottom)
        w, h = self.width.resolve(room_width), self.height.resolve(room_height)
        return PopupRect(left + (room_width - w if notification else (room_width - w) // 2),
                         top + (room_height - h if notification else (room_height - h) // 2), w, h)

    @classmethod
    def from_dict(cls, value):
        return super(PopupPanel, cls).from_dict(value.to_dict() if isinstance(value, PopupPanel) else value)


@dataclass(frozen=True, slots=True)
class NotificationPanel(PopupPanel):
    width: NotificationWidth = setting(Nested(NotificationWidth), factory=NotificationWidth)
    height: NotificationHeight = setting(Nested(NotificationHeight), factory=NotificationHeight)


@dataclass(frozen=True, slots=True)
class NotificationConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Allow queued in-process notifications. Disabling clears pending notifications.")
    panel: NotificationPanel = setting(Nested(NotificationPanel), factory=NotificationPanel, description="Small bottom-right panel above the bottom toolbar.")
    duration_s: float | None = setting(Nullable(POSITIVE_SECONDS), default=6,
        description="Visible seconds before closing; null requires dismissal. Countdown pauses while hidden or a general popup is open.")
    dismiss_key: KeySequence | None = setting(KeyRule(optional=True), default=("escape", "x"),
        description="Dismiss the visible notification. Default Alt+X; null disables this binding.")
    details_key: KeySequence | None = setting(KeyRule(optional=True), default=("escape", "n"),
        description="Open details for the visible notification. Default Alt+N; null disables this binding.")
