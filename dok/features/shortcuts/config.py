"""Shortcut fields, slot limits and command composition belong to Shortcut."""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from ...core.colors import DockPalette, HexColor
from ...core.elements import ElementConfig, Spacing
from ...core.records import Nested, Sequence, SettingsError, Boolean, Choice, Number, Record, Rule, Text, setting
from ...core.styles import TextStyle, MutedStyle, AccentStyle
from ...settings.files import ConfigFiles
from ...integrations.ish.keys import KeyRule, KeySequence


class ShortcutKind(StrEnum):
    SHORTCUT = "shortcut"


@dataclass(frozen=True, slots=True)
class Shortcut(Record):
    COUNT: ClassVar[int] = 10
    FIRST: ClassVar[int] = 1
    SEPARATOR: ClassVar[str] = " "
    SLOT_RULE: ClassVar[Number] = Number(minimum=FIRST, maximum=COUNT, integer=True)

    slot: int = setting(SLOT_RULE, description="Shortcut slot number. Must be unique across all entries.")
    command: str = setting(
        Text(nonblank=True),
        description="Shell command prepended to the current prompt buffer. Runs on its own when the buffer is empty."
    )
    icon: str = setting(
        Text(single_line=True),
        default="",
        description="Icon or emoji. Nerd Font glyphs require a compatible font. An empty string is allowed."
    )
    type: ShortcutKind = setting(
        Choice(ShortcutKind),
        default=ShortcutKind.SHORTCUT,
        description="Executable shortcut entry type."
    )
    label: str = setting(
        Text(single_line=True),
        default="",
        description="Label after the icon. May be omitted when space is limited."
    )
    key: KeySequence | None = setting(KeyRule(optional=True), default=None,
        description="Shortcut key sequence. Examples: [\"c-1\"], [\"escape\", \"1\"]. null registers no key. Ctrl+number requires terminal key mappings.")
    enabled: bool = setting(
        Boolean(),
        default=True,
        description="Enable this shortcut. false dims it and disables execution by click or keyboard."
    )
    style: AccentStyle = setting(
        Nested(AccentStyle),
        factory=AccentStyle,
        description="Entry foreground color."
    )

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
        parts = [part for part in (self.icon, self.label) if part.strip()]
        if not parts:
            return self.command
        return self.SEPARATOR.join(parts) if detailed else parts[0]

    @property
    def display_color(self) -> str:
        return self.style.color if self.enabled else DockPalette.MUTED


class SeparatorKind(StrEnum):
    SEPARATOR = "separator"


@dataclass(frozen=True, slots=True)
class Separator(Record):
    slot: int = setting(
        Shortcut.SLOT_RULE,
        description="Slot occupied by the separator. Must be unique across all entries."
    )
    type: SeparatorKind = setting(
        Choice(SeparatorKind),
        default=SeparatorKind.SEPARATOR,
        description="separator marks a non-executable divider. command and key are not allowed."
    )
    text: str = setting(
        Text(nonblank=True, single_line=True),
        default="│",
        description="Single-line text displayed as the separator."
    )
    style: MutedStyle = setting(Nested(MutedStyle), factory=MutedStyle, description="Entry foreground color.")

    @property
    def enabled(self) -> bool:
        return False

    @property
    def key(self) -> None:
        return None

    @property
    def display_color(self) -> str:
        return self.style.color

    def display(self, *, detailed: bool = True) -> str:
        return self.text


class SlotEntryRule(Rule):
    """Own the JSON discriminator and the schema for both kinds of slot entry."""

    def decode(self, value):
        if isinstance(value, (Shortcut, Separator)):
            return value
        if not isinstance(value, dict):
            raise SettingsError("Expected a shortcut entry object")
        if value.get(Separator.names().type) == SeparatorKind.SEPARATOR:
            return Separator.from_dict(value)
        if value.get(Shortcut.names().type) == ShortcutKind.SHORTCUT:
            return Shortcut.from_dict(value)
        raise SettingsError("Entry type must be shortcut or separator")

    def encode(self, value):
        return value.to_dict()

    def documented(self, value):
        return value

    def schema(self):
        separator = Separator.schema()
        separator["required"].append(Separator.names().type)
        shortcut = Shortcut.schema()
        shortcut["required"].append(Shortcut.names().type)
        return {"oneOf": [shortcut, separator]}


@dataclass(frozen=True, slots=True)
class ShortcutSpacing(Spacing):
    after_rows: int = setting(Spacing.ROWS_RULE, default=1)
    item_gap_columns: int = setting(
        Number(minimum=0, integer=True),
        default=2,
        description="Horizontal cells between entries. Reduced when space is limited."
    )

@dataclass(frozen=True, slots=True)
class ShortcutsConfig(ElementConfig):
    spacing: ShortcutSpacing = setting(
        Nested(ShortcutSpacing),
        factory=ShortcutSpacing,
        description="Vertical rows and horizontal cells between shortcut entries."
    )
    items: tuple[Shortcut | Separator, ...] = setting(
        Sequence(SlotEntryRule(), maximum=Shortcut.COUNT),
        description="Shortcut and separator entries. Omitted or [] leaves all numbered slots empty.",
        default=(),
    )

    @classmethod
    def initial(cls) -> "ShortcutsConfig":
        """Editable examples for a newly created file, never implicit defaults."""
        return cls(items=(
            Shortcut(1, "fzf", icon="\uf002", label="Find", style=AccentStyle(color="#e67e80"), key=("c-1",)),
            Shortcut(
                2,
                "yazi",
                icon="\uf07c",
                label="File",
                style=AccentStyle(color="#e69875"),
                key=("c-2",)
            ),
            Shortcut(3, "htop", icon="\uf080", label="Sys", style=AccentStyle(color="#dbbc7f"), key=("c-3",)),
            Shortcut(4, "vim", icon="\ue7c5", label="Vim", style=AccentStyle(color="#83c092"), key=("c-4",)),
            Shortcut(
                5,
                "lazygit",
                icon="\ue702",
                label="Git",
                style=AccentStyle(color="#7fbbb3"),
                key=("c-5",)
            ),
            Shortcut(
                6,
                "firefox &",
                icon="\uf269",
                label="Web",
                style=AccentStyle(color="#84c8fa"),
                key=("c-6",)
            ),
            Separator(7),
            Shortcut(
                8,
                f"vim ~/ish/{ConfigFiles.DIRECTORY}/{ConfigFiles.FILENAME}",
                icon="\uf1de",
                label="Dock",
                style=AccentStyle(color="#a6a1fb"),
                key=("c-7",)
            ),
            Shortcut(
                9,
                "vim ~/ish/.ishrc.py",
                icon="\uf085",
                label="Conf",
                style=AccentStyle(color="#d699b6"),
                key=("c-8",)
            ),
            Shortcut(
                10,
                "ish_reload",
                icon="\uf021",
                label="Rld",
                style=AccentStyle(color="#c084fc"),
                key=("c-9",)
            ),
        ))

    def _check(self):
        slots = [item.slot for item in self.items]
        if len(set(slots)) != len(slots):
            raise SettingsError("Duplicate shortcut slot")
        self.validate_keys(())

    def validate_keys(self, reserved):
        keys = list(reserved)
        for item in self.items:
            if item.enabled and item.key is not None:
                if any(item.key.overlaps(key.keys) for key in keys):
                    raise SettingsError(f"Overlapping dok keys: {item.key.to_list()}")
                keys.append(item.key)

    def entry(self, slot):
        Shortcut.validate_slot(slot)
        return next((item for item in self.items if item.slot == slot), None)

    def shortcut(self, slot):
        item = self.entry(slot)
        return item if isinstance(item, Shortcut) else None

    def separator(self, available: int) -> str:
        reserved = sum(len(Shortcut.compact_label(slot)) for slot in Shortcut.slots())
        maximum = max(0, (available - reserved) // (Shortcut.COUNT - 1))
        return " " * min(self.spacing.item_gap_columns, maximum)
