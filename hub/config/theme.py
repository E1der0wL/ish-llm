"""UI-only colors. 'default' leaves the terminal's own palette in control."""

from dataclasses import dataclass, fields
from typing import ClassVar
from ..asset.icon import IconStyle
import re
import math

from prompt_toolkit.styles import Style
from .theme_style import ThemeStyle


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
    output_refresh_interval: float = 0.1
    progress_refresh_interval: float = 0.1
    foreground: str = DEFAULT_COLOR
    background: str = DEFAULT_COLOR
    accent1: str = "#98C379"
    accent2: str = "#528BFF"
    accent3: str = "#D19A66"
    accent4: str = "#A8B2BF"
    accent5: str = "#1D2035"
    comment: str = "#8595a5"
    comment2: str = "#3A3A3A"

    # Roles reference configured colors verbatim. Only inactive FocusRegion
    # rendering computes grayscale; active colors never blend or auto-contrast.
    accent = property(lambda self: self.accent1)
    user = property(lambda self: self.accent2)
    notice = property(lambda self: self.accent3)
    muted = property(lambda self: self.comment)
    widget_background = property(lambda self: self.accent5)
    action_background = property(lambda self: self.accent1)
    shortcut_background = property(lambda self: self.accent4)
    user_background = property(lambda self: self.widget_background)
    header_background = property(lambda self: self.widget_background)
    footer_background = property(lambda self: self.widget_background)
    code_background = property(lambda self: self.widget_background)
    completion_background = property(lambda self: self.accent5)
    completion_current_background = property(lambda self: self.accent1)
    button_background = property(lambda self: self.action_background)
    checklist_background = property(lambda self: self.action_background)
    scrollbar_button = property(lambda self: self.accent1)
    scrollbar_arrow = property(lambda self: self.comment)
    bar_foreground = property(lambda self: self.foreground)
    code_foreground = property(lambda self: self.foreground)
    completion_foreground = property(lambda self: self.accent4)
    button_foreground = property(lambda self: self.accent5)
    icon_button_foreground = property(lambda self: self.accent4)
    checklist_foreground = property(lambda self: self.foreground)
    alert_error_background = property(lambda self: self.widget_background)
    alert_warning_background = property(lambda self: self.widget_background)
    alert_info_background = property(lambda self: self.widget_background)
    alert_success_background = property(lambda self: self.widget_background)
    alert_error_foreground = property(lambda self: self.accent3)
    alert_warning_foreground = property(lambda self: self.accent3)
    alert_info_foreground = property(lambda self: self.accent2)
    alert_success_foreground = property(lambda self: self.accent1)

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
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
