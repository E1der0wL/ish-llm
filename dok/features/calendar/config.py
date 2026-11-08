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
    reminders: bool = setting(Boolean(), default=True, description="Deliver saved reminders while ish is running. Missed reminders coalesce to the latest occurrence per registered offset; receipts survive restart.")
    poll_s: float = setting(POSITIVE_SECONDS, default=1, description="Seconds between external calendar file checks while the calendar popup is open. Saved changes and due alarms wake immediately.")
    idle_s: float = setting(POSITIVE_SECONDS, default=30, description="Seconds between external calendar file checks while the calendar popup is closed. Known alarms keep their exact deadlines; this is only the external-edit detection interval.")
    clock_s: float = setting(POSITIVE_SECONDS, default=1, description="Lightweight wall-clock recheck interval while a future alarm exists, including after suspend or a clock change. Does not reread the calendar file.")

    def watch_interval(self, visible):
        return self.poll_s if visible else self.idle_s

    @property
    def runtime_key(self):
        return self.enabled, self.reminders, self.poll_s, self.idle_s, self.clock_s
