"""Common foreground styles; specialized features own their extra colors."""
from dataclasses import dataclass
from .colors import DockPalette, HexColor
from .records import Boolean, Record, setting

@dataclass(frozen=True, slots=True)
class TextStyle(Record):
    color: str = setting(
        HexColor(),
        default=DockPalette.TEXT,
        description="Foreground color in #RRGGBB format."
    )

@dataclass(frozen=True, slots=True)
class BoldStyle(TextStyle):
    bold: bool = setting(Boolean(), default=False, description="Render text in bold.")

@dataclass(frozen=True, slots=True)
class MutedStyle(TextStyle):
    color: str = setting(HexColor(), default=DockPalette.MUTED)

@dataclass(frozen=True, slots=True)
class AccentStyle(TextStyle):
    color: str = setting(HexColor(), default=DockPalette.ACCENT)
