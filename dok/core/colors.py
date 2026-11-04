"""Shared color defaults, RGB validation and interpolation."""
from __future__ import annotations

import re

from .records import Rule, SettingsError


class DockPalette:
    TEXT = "#d1d5db"
    ACCENT = "#67e8f9"
    MUTED = "#6b7280"
    ERROR = "#fbbf24"


class HexColor(Rule):
    LENGTH = 7
    PATTERN = rf"^#[0-9a-fA-F]{{{LENGTH - 1}}}$"

    def decode(self, value):
        if not isinstance(value, str) or re.fullmatch(self.PATTERN, value) is None:
            raise SettingsError("Expected a color in #RRGGBB format")
        return value.lower()

    def schema(self):
        return {"type": "string", "pattern": self.PATTERN, "minLength": self.LENGTH, "maxLength": self.LENGTH}

    @staticmethod
    def blend(left: str, right: str, fraction: float) -> str:
        channels = (round(int(left[index:index + 2], 16) * (1 - fraction)
                          + int(right[index:index + 2], 16) * fraction) for index in (1, 3, 5))
        return "#" + "".join(f"{channel:02x}" for channel in channels)
