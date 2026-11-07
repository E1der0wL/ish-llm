"""Calendar popup preferences."""
from dataclasses import dataclass

from ...core.records import Boolean, Nested, Nullable, Number, POSITIVE_SECONDS, Record, setting
from ...integrations.ish.keys import KeyRule, KeySequence
from ...popups.config import PopupPanel, PopupDimension, PopupHeight


@dataclass(frozen=True, slots=True)
class CalendarWidth(PopupDimension):
    value: int | float = setting(Number(), default=136)


@dataclass(frozen=True, slots=True)
class CalendarHeight(PopupHeight):
    value: int | float = setting(Number(), default=44)


@dataclass(frozen=True, slots=True)
class CalendarPanel(PopupPanel):
    width: CalendarWidth = setting(Nested(CalendarWidth), factory=CalendarWidth)
    height: CalendarHeight = setting(Nested(CalendarHeight), factory=CalendarHeight)


@dataclass(frozen=True, slots=True)
class CalendarConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Enable the monthly calendar popup. No external calendar account is connected.")
    key: KeySequence | None = setting(KeyRule(optional=True), default=("escape", "c"), description="Open the calendar. Default Alt+C; null disables this binding.")
    panel: CalendarPanel | None = setting(Nullable(Nested(CalendarPanel)), factory=CalendarPanel, description="Calendar size and appearance. null uses popups.general; the default fits both calendar and editor within the available input area.")
    week_start: int = setting(Number(minimum=0, maximum=6, integer=True), default=6, description="First column weekday: 0 Monday through 6 Sunday. Default Sunday.")
    reminders: bool = setting(Boolean(), default=True, description="Deliver saved event reminders while ish is running. Late reminders coalesce to the latest occurrence per event; delivery receipts survive restart.")
    poll_s: float = setting(POSITIVE_SECONDS, default=1, description="Seconds between reminder checks. No shell commands or agents are executed.")
