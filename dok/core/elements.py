"""Visibility and vertical spacing rules shared by all widgets."""
from dataclasses import dataclass
from typing import ClassVar
from .records import Boolean, Nested, Nullable, Number, Record, setting
from .state import DockMode

@dataclass(frozen=True, slots=True)
class Spacing(Record):
    ROWS_RULE: ClassVar[Number] = Number(minimum=0, integer=True)
    after_rows: int = setting(
        ROWS_RULE,
        default=0,
        description="Nonnegative blank rows after this widget. If the dock cannot fit, it falls back to basic or hidden."
    )
    basic_after_rows: int | None = setting(
        Nullable(ROWS_RULE),
        default=None,
        description="Basic-mode trailing rows. null uses after_rows."
    )

    def for_mode(self, mode: DockMode) -> int:
        return self.basic_after_rows if mode == DockMode.BASIC and self.basic_after_rows is not None else self.after_rows

@dataclass(frozen=True, slots=True)
class ElementConfig(Record):
    enabled: bool = setting(
        Boolean(),
        default=True,
        description="Show this widget. false removes its content and trailing spacing."
    )
    spacing: Spacing = setting(
        Nested(Spacing),
        factory=Spacing,
        description="Vertical spacing in terminal rows."
    )

    def row_count(self, content_rows: int, mode: DockMode) -> int:
        return content_rows + self.spacing.for_mode(mode) if self.enabled and content_rows else 0

    def arrange[T](self, rows: tuple[T, ...], mode: DockMode, blank: T) -> tuple[T, ...]:
        count = self.row_count(len(rows), mode)
        return rows + (blank,) * (count - len(rows)) if count else ()
