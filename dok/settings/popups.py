"""Compose popup and feature owners without reinterpreting their settings."""
from dataclasses import dataclass

from ..core.records import Nested, Record, setting
from ..features.calendar.config import CalendarConfig
from ..features.weather.popup import WeatherPopupConfig
from ..popups.config import NotificationConfig, PopupPanel, PopupInput


@dataclass(frozen=True, slots=True)
class PopupSettings(Record):
    general: PopupPanel = setting(Nested(PopupPanel), factory=PopupPanel, description="Centered popup defaults within the ish input area.")
    notifications: NotificationConfig = setting(Nested(NotificationConfig), factory=NotificationConfig, description="Non-focusing bottom-right notification queue.")
    calendar: CalendarConfig = setting(Nested(CalendarConfig), factory=CalendarConfig, description="Monthly calendar popup.")
    weather: WeatherPopupConfig = setting(Nested(WeatherPopupConfig), factory=WeatherPopupConfig, description="Current, daily and hourly weather details popup.")
    input: PopupInput = setting(Nested(PopupInput), factory=PopupInput, description="Popup-only keyboard timing; shell settings are restored when focus leaves.")

    def keys(self):
        return tuple(key for key in (self.calendar.key, self.weather.key,
                     self.notifications.dismiss_key, self.notifications.details_key) if key is not None)
