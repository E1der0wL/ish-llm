"""Shared, display-only target/status/detail captions."""

from dataclasses import dataclass
from typing import ClassVar

@dataclass(frozen=True, slots=True)
class Caption:
    """Empty parts disappear; Language.status owns glyphs, not separators."""

    SEPARATOR: ClassVar[str] = " • "
    target: str = ""
    status: str = ""
    details: tuple[str, ...] = ()

    def render(self) -> str:
        return self.SEPARATOR.join(value for value in
                                   (self.target, self.status, *self.details) if value)
