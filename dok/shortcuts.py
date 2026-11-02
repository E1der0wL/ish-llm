"""Shortcut fields, slot limits and command composition belong to Shortcut."""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from .appearance import DockPalette, HexColor
from .keys import KeyRule, KeySequence
from .records import Boolean, Choice, Number, Record, Rule, Text, setting


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
    color: str = setting(HexColor(), default=DockPalette.ACCENT)

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
        parts = [part for part in (self.emoji, self.label) if part.strip()]
        if not parts:
            return self.command
        return self.SEPARATOR.join(parts) if detailed else parts[0]

    @property
    def display_color(self) -> str:
        return self.color if self.enabled else DockPalette.MUTED


class SeparatorKind(StrEnum):
    SEPARATOR = "separator"


@dataclass(frozen=True, slots=True)
class Separator(Record):
    slot: int = setting(Shortcut.SLOT_RULE)
    kind: SeparatorKind = setting(Choice(SeparatorKind), default=SeparatorKind.SEPARATOR)
    text: str = setting(Text(nonblank=True, single_line=True), default="│")
    color: str = setting(HexColor(), default=DockPalette.MUTED)

    @property
    def enabled(self) -> bool:
        return False

    @property
    def key(self) -> None:
        return None

    @property
    def display_color(self) -> str:
        return self.color

    def display(self, *, detailed: bool = True) -> str:
        return self.text


class SlotEntryRule(Rule):
    """Own the JSON discriminator and the schema for both kinds of slot entry."""

    def decode(self, value):
        if isinstance(value, (Shortcut, Separator)):
            return value
        if isinstance(value, dict) and value.get(Separator.names().kind) == SeparatorKind.SEPARATOR:
            return Separator.from_dict(value)
        return Shortcut.from_dict(value)

    def encode(self, value):
        return value.to_dict()

    def schema(self):
        separator = Separator.schema()
        separator["required"].append(Separator.names().kind)
        return {"anyOf": [Shortcut.schema(), separator]}


@dataclass(frozen=True, slots=True)
class ShortcutLayout(Record):
    padding: int = setting(Number(minimum=0, integer=True), default=1)

    def separator(self, available: int) -> str:
        reserved = sum(len(Shortcut.compact_label(slot)) for slot in Shortcut.slots())
        maximum = max(0, (available - reserved) // (Shortcut.COUNT - 1))
        return " " * min(self.padding, maximum)
