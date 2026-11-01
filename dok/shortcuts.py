"""Shortcut fields, slot limits and command composition belong to Shortcut."""

from dataclasses import dataclass
from typing import ClassVar

from .keys import KeyRule, KeySequence
from .records import Boolean, Number, Record, Text, setting


@dataclass(frozen=True, slots=True)
class Shortcut(Record):
    COUNT: ClassVar[int] = 10
    FIRST: ClassVar[int] = 1
    SEPARATOR: ClassVar[str] = " "
    SLOT_RULE: ClassVar[Number] = Number(minimum=FIRST, maximum=COUNT, integer=True)

    slot: int = setting(SLOT_RULE)
    command: str = setting(Text(nonblank=True))
    emoji: str = setting(Text(single_line=True), default="")
    label: str = setting(Text(single_line=True), default="")
    key: KeySequence | None = setting(KeyRule(optional=True), default=None)
    enabled: bool = setting(Boolean(), default=True)

    @classmethod
    def slots(cls) -> range:
        return range(cls.FIRST, cls.COUNT + cls.FIRST)

    @classmethod
    def validate_slot(cls, slot: int) -> int:
        return cls.SLOT_RULE.decode(slot)

    @classmethod
    def compact_label(cls, slot: int) -> str:
        cls.validate_slot(slot)
        return f"[{slot % cls.COUNT}]"

    @classmethod
    def minimum_width(cls) -> int:
        return len(cls.SEPARATOR.join(cls.compact_label(slot) for slot in cls.slots()))

    def compose(self, buffer: str) -> str:
        if not buffer or self.command[-1].isspace() or buffer[0].isspace():
            return self.command + buffer
        return self.command + self.SEPARATOR + buffer

    def display(self, *, detailed: bool = True) -> str:
        parts = [self.compact_label(self.slot), self.emoji]
        if detailed:
            parts.append(self.label)
        return self.SEPARATOR.join(part for part in parts if part)
