"""UI-only colors. 'default' leaves the terminal's own palette in control."""

from dataclasses import dataclass, fields
from typing import ClassVar
from ..asset.icon import IconStyle
import re
import math

from prompt_toolkit.styles import Style
from .theme_style import ThemeStyle
from .theme_mode import ThemeMode


@dataclass(frozen=True, slots=True)
class HubTheme:
    DEFAULT_COLOR: ClassVar[str] = "default"
    HEX_PREFIX: ClassVar[str] = "#"
    HEX_CHANNEL_FORMAT: ClassVar[str] = "{value:02x}"
    HEX_CHANNEL_OFFSETS: ClassVar[tuple] = (1, 3, 5)
    HEX_CHANNEL_WIDTH: ClassVar[int] = 2
    HEX_RADIX: ClassVar[int] = 16
    LUMINANCE_WEIGHTS: ClassVar[tuple] = ((1, 0.2126), (3, 0.7152), (5, 0.0722))
    MINIMUM_REFRESH_SECONDS: ClassVar[float] = 0.01
    MAXIMUM_REFRESH_SECONDS: ClassVar[int] = 10
    MINIMUM_SIDEBAR_WIDTH: ClassVar[int] = 18
    MAXIMUM_SIDEBAR_WIDTH: ClassVar[int] = 60
    COLOR_PATTERN: ClassVar[str] = r"default|#[0-9a-fA-F]{6}"
    INVALID_REFRESH: ClassVar[str] = (
        "{name} must be a number between 0.01 and 10 seconds"
    )
    INVALID_WIDTH: ClassVar[str] = "sidebar_width must be an integer between 18 and 60"
    INVALID_COLOR: ClassVar[str] = "{name} must be 'default' or a #RRGGBB color"

    sidebar_width: int = 26
    icon_style: str = IconStyle.NERD.value
    mode: str = ThemeMode.AUTO.value
    output_refresh_interval: float = 0.1
    progress_refresh_interval: float = 0.1
    foreground: str = DEFAULT_COLOR
    background: str = DEFAULT_COLOR
    # One Dark-inspired named defaults; bright variants extend its syntax palette.
    # Terminal foreground/background remain transparent/user-controlled.
    black: str = "#21252B"
    red: str = "#E06C75"
    orange: str = "#D19A66"
    green: str = "#98C379"
    yellow: str = "#E5C07B"
    blue: str = "#61AFEF"
    purple: str = "#C678DD"
    cyan: str = "#56B6C2"
    white: str = "#ABB2BF"
    gray: str = "#5C6370"
    bright_black: str = "#282C34"
    bright_red: str = "#E9969D"
    bright_orange: str = "#DFB28D"
    bright_green: str = "#B3D596"
    bright_yellow: str = "#ECD29F"
    bright_blue: str = "#8FC6F4"
    bright_purple: str = "#D7A1E7"
    bright_cyan: str = "#85CCD5"
    bright_white: str = "#E6E6E6"
    bright_gray: str = "#828997"

    # Roles reference configured colors verbatim. Only inactive FocusRegion
    # rendering computes grayscale; active colors never blend or auto-contrast.
    accent = property(lambda self: self.green)
    user = property(lambda self: self.blue)
    notice = property(lambda self: self.orange)
    muted = property(lambda self: self.bright_gray)
    widget_foreground = property(lambda self: self.bright_black if self.mode == ThemeMode.LIGHT else self.white)
    widget_background = property(lambda self: self.white if self.mode == ThemeMode.LIGHT else self.bright_black)
    action_background = property(lambda self: self.green)
    shortcut_foreground = property(lambda self: self.widget_background)
    shortcut_background = property(lambda self: self.widget_foreground)
    user_background = property(lambda self: self.widget_background)
    header_background = property(lambda self: self.widget_background)
    footer_background = property(lambda self: self.widget_background)
    code_background = property(lambda self: self.widget_background)
    completion_background = property(lambda self: self.widget_background)
    completion_current_background = property(lambda self: self.widget_foreground)
    completion_current_foreground = property(lambda self: self.widget_background)
    button_background = property(lambda self: self.widget_background)
    checklist_background = property(lambda self: self.widget_background)
    scrollbar_button = property(lambda self: self.green)
    scrollbar_arrow = property(lambda self: self.bright_gray)
    bar_foreground = property(lambda self: self.widget_foreground)
    code_foreground = property(lambda self: self.widget_foreground)
    completion_foreground = property(lambda self: self.widget_foreground)
    button_foreground = property(lambda self: self.widget_foreground)
    icon_button_foreground = property(lambda self: self.widget_foreground)
    checklist_foreground = property(lambda self: self.widget_foreground)
    alert_error_background = property(lambda self: self.widget_background)
    alert_warning_background = property(lambda self: self.widget_background)
    alert_info_background = property(lambda self: self.widget_background)
    alert_success_background = property(lambda self: self.widget_background)
    alert_error_foreground = property(lambda self: self.orange)
    alert_warning_foreground = property(lambda self: self.orange)
    alert_info_foreground = property(lambda self: self.blue)
    alert_success_foreground = property(lambda self: self.green)

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == HubTheme.mode.__name__:
                ThemeMode(value)
                continue
            if field.name in (
                HubTheme.output_refresh_interval.__name__,
                HubTheme.progress_refresh_interval.__name__,
            ):
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not self.MINIMUM_REFRESH_SECONDS
                    <= value
                    <= self.MAXIMUM_REFRESH_SECONDS
                ):
                    raise ValueError(self.INVALID_REFRESH.format(name=field.name))
                continue
            if field.name == HubTheme.icon_style.__name__:
                from ..asset.icon import for_style

                for_style(value)
                continue
            if field.name == HubTheme.sidebar_width.__name__:
                if (
                    type(value) is not int
                    or not self.MINIMUM_SIDEBAR_WIDTH
                    <= value
                    <= self.MAXIMUM_SIDEBAR_WIDTH
                ):
                    raise ValueError(self.INVALID_WIDTH)
                continue
            if not isinstance(value, str) or not re.fullmatch(
                self.COLOR_PATTERN, value
            ):
                raise ValueError(self.INVALID_COLOR.format(name=field.name))

    def style(self) -> Style:
        return Style.from_dict(ThemeStyle.rules(self))
