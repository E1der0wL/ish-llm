"""UI-only colors. 'default' leaves the terminal's own palette in control."""

from dataclasses import dataclass, fields
from enum import StrEnum
from typing import ClassVar
from ..asset.icon import IconStyle
import re
import math

from prompt_toolkit.styles import Style
from .theme_style import ThemeStyle


class ThemeMode(StrEnum):
    DARK = "dark"
    LIGHT = "light"


@dataclass(frozen=True, slots=True)
class HubTheme:
    DEFAULT_COLOR: ClassVar[str] = "default"
    DEFAULT_SURFACE: ClassVar[float] = 0.16
    BACKGROUND_TINT: ClassVar[float] = 0.12
    SUBTLE_SURFACE: ClassVar[float] = 0.10
    SELECTED_SURFACE: ClassVar[float] = 0.35
    BUTTON_SURFACE: ClassVar[float] = 0.22
    ERROR_SURFACE: ClassVar[float] = 0.30
    HEX_PREFIX: ClassVar[str] = "#"
    HEX_JOINER: ClassVar[str] = ""
    HEX_CHANNEL_FORMAT: ClassVar[str] = "{value:02x}"
    HEX_CHANNEL_OFFSETS: ClassVar[tuple] = (1, 3, 5)
    HEX_CHANNEL_WIDTH: ClassVar[int] = 2
    HEX_RADIX: ClassVar[int] = 16
    LUMINANCE_WEIGHTS: ClassVar[tuple] = ((1, 0.2126), (3, 0.7152), (5, 0.0722))
    LIGHT_THRESHOLD: ClassVar[int] = 150
    LIGHT_FOREGROUND: ClassVar[str] = "#17212b"
    DARK_FOREGROUND: ClassVar[str] = "#d9e2ef"
    LIGHT_BACKGROUND: ClassVar[str] = "#f5f7fa"
    DARK_BACKGROUND: ClassVar[str] = "#111923"
    DARK_ACCENT1: ClassVar[str] = "#7cddc7"
    DARK_ACCENT2: ClassVar[str] = "#8ecbff"
    DARK_ACCENT3: ClassVar[str] = "#dfc890"
    DARK_COMMENT: ClassVar[str] = "#91a4b8"
    MINIMUM_REFRESH_SECONDS: ClassVar[float] = 0.01
    MAXIMUM_REFRESH_SECONDS: ClassVar[int] = 10
    MINIMUM_SIDEBAR_WIDTH: ClassVar[int] = 18
    MAXIMUM_SIDEBAR_WIDTH: ClassVar[int] = 60
    COLOR_PATTERN: ClassVar[str] = r"default|#[0-9a-fA-F]{6}"
    INVALID_REFRESH: ClassVar[str] = (
        "{name} must be a number between 0.01 and 10 seconds"
    )
    INVALID_MODE: ClassVar[str] = "mode must be 'dark' or 'light'"
    INVALID_WIDTH: ClassVar[str] = "sidebar_width must be an integer between 18 and 60"
    INVALID_COLOR: ClassVar[str] = "{name} must be 'default' or a #RRGGBB color"

    sidebar_width: int = 26
    icon_style: str = IconStyle.NERD.value
    mode: str = ThemeMode.DARK.value
    output_refresh_interval: float = 0.1
    progress_refresh_interval: float = 0.1
    foreground: str = DEFAULT_COLOR
    background: str = DEFAULT_COLOR
    accent1: str = "#46b59e"
    accent2: str = "#67a9dd"
    accent3: str = "#bc9850"
    comment: str = "#8595a5"

    # Semantic rendering roles are derived, never stored or edited separately.
    base_background = property(
        lambda self: (
            self.LIGHT_BACKGROUND
            if self.mode == ThemeMode.LIGHT.value
            else self.DARK_BACKGROUND
        )
    )
    accent = property(lambda self: self.accent1)
    user = property(lambda self: self.accent2)
    notice = property(lambda self: self.accent3)
    muted = property(lambda self: self.comment)
    user_background = property(lambda self: self._surface(self.accent2))
    header_background = property(lambda self: self._surface(self.accent1))
    footer_background = property(
        lambda self: self._surface(self.accent1, self.SUBTLE_SURFACE)
    )
    code_background = property(
        lambda self: self._surface(self.comment, self.SUBTLE_SURFACE)
    )
    completion_background = property(lambda self: self._surface(self.comment))
    completion_current_background = property(
        lambda self: self._surface(self.accent1, self.SELECTED_SURFACE)
    )
    button_background = property(
        lambda self: self._surface(self.accent2, self.BUTTON_SURFACE)
    )
    checklist_background = property(lambda self: self._surface(self.comment))
    scrollbar_button = property(lambda self: self.accent1)
    scrollbar_arrow = property(lambda self: self.comment)
    bar_foreground = property(lambda self: self._surface_text(self.header_background))
    code_foreground = property(lambda self: self._surface_text(self.code_background))
    completion_foreground = property(
        lambda self: self._surface_text(self.completion_background)
    )
    button_foreground = property(
        lambda self: self._surface_text(self.button_background)
    )
    checklist_foreground = property(
        lambda self: self._surface_text(self.checklist_background)
    )
    alert_error_background = property(
        lambda self: self._surface(self.accent3, self.ERROR_SURFACE)
    )
    alert_warning_background = property(lambda self: self._surface(self.accent3))
    alert_info_background = property(lambda self: self._surface(self.accent2))
    alert_success_background = property(lambda self: self._surface(self.accent1))
    alert_error_foreground = property(lambda self: self.accent3)
    alert_warning_foreground = property(lambda self: self.accent3)
    alert_info_foreground = property(lambda self: self.accent2)
    alert_success_foreground = property(lambda self: self.accent1)

    def _surface(self, color: str, amount: float = DEFAULT_SURFACE) -> str:
        if color == self.DEFAULT_COLOR:
            return self.background
        base = (
            self.background
            if self.background != self.DEFAULT_COLOR
            else self.base_background
        )
        if self.background != self.DEFAULT_COLOR:
            # Keep the chosen page background, but give derived surfaces the
            # selected theme's light/dark shading even with a custom palette.
            base = self._blend(base, self.base_background, self.BACKGROUND_TINT)
        return self._blend(base, color, amount)

    @staticmethod
    def _blend(base: str, color: str, amount: float) -> str:
        return HubTheme.HEX_PREFIX + HubTheme.HEX_JOINER.join(
            HubTheme.HEX_CHANNEL_FORMAT.format(
                value=round(
                    int(base[i : i + HubTheme.HEX_CHANNEL_WIDTH], HubTheme.HEX_RADIX)
                    * (1 - amount)
                    + int(color[i : i + HubTheme.HEX_CHANNEL_WIDTH], HubTheme.HEX_RADIX)
                    * amount
                )
            )
            for i in HubTheme.HEX_CHANNEL_OFFSETS
        )

    def _surface_text(self, background: str) -> str:
        if self.foreground != self.DEFAULT_COLOR or background == self.DEFAULT_COLOR:
            return self.foreground
        brightness = sum(
            int(background[i : i + self.HEX_CHANNEL_WIDTH], self.HEX_RADIX) * weight
            for i, weight in self.LUMINANCE_WEIGHTS
        )
        return (
            self.LIGHT_FOREGROUND
            if brightness > self.LIGHT_THRESHOLD
            else self.DARK_FOREGROUND
        )

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
            if field.name == HubTheme.mode.__name__:
                if value not in (ThemeMode.DARK, ThemeMode.LIGHT):
                    raise ValueError(self.INVALID_MODE)
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

    @classmethod
    def dark(cls) -> "HubTheme":
        return cls(
            foreground=cls.DARK_FOREGROUND,
            background=cls.DARK_BACKGROUND,
            accent1=cls.DARK_ACCENT1,
            comment=cls.DARK_COMMENT,
            accent2=cls.DARK_ACCENT2,
            accent3=cls.DARK_ACCENT3,
        )
