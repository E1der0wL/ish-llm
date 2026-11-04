"""Visibility and vertical spacing rules shared by all widgets."""
from dataclasses import dataclass
from typing import ClassVar
from .records import Boolean, Nested, Nullable, Number, Record, setting
from .state import DockMode

@dataclass(frozen=True, slots=True)
class Spacing(Record):
    ROWS_RULE: ClassVar[Number] = Number(minimum=0, maximum=20, integer=True)
    after_rows: int = setting(
        ROWS_RULE,
        default=0,
        description="Blank rows after this widget, including bottom spacing for the last widget."
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

    def arrange[T](self, rows: tuple[T, ...], mode: DockMode, blank: T) -> tuple[T, ...]:
        if not self.enabled or not rows:
            return ()
        return rows + (blank,) * self.spacing.for_mode(mode)
