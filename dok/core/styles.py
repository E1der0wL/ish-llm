"""Common foreground styles; specialized features own their extra colors."""
from dataclasses import dataclass
from .colors import DockPalette, ForegroundColor, HexColor
from .records import Boolean, Record, setting
from .theme import Theme, ThemeRole, theme_reference


def fixed_foreground(default: str):
    return setting(HexColor(), default=default, description="Foreground color in #RRGGBB format.")

@dataclass(frozen=True, slots=True)
class TextStyle(Record):
    color: str = theme_reference()

    @property
    def foreground(self) -> str:
        color = self.color if self.color.startswith("#") else Theme().resolve(self.color)
        return ForegroundColor.resolve(color)

    def paint(self, theme: Theme) -> str:
        return theme.span(self.color)

@dataclass(frozen=True, slots=True)
class BoldStyle(TextStyle):
    bold: bool = setting(Boolean(), default=False, description="Render text in bold.")

@dataclass(frozen=True, slots=True)
class MutedStyle(TextStyle):
    color: str = fixed_foreground(DockPalette.MUTED)

@dataclass(frozen=True, slots=True)
class AccentStyle(TextStyle):
    color: str = fixed_foreground(DockPalette.ACCENT)


@dataclass(frozen=True, slots=True)
class SecondaryStyle(TextStyle):
    color: str = theme_reference(ThemeRole.MUTED)
