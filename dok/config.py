"""Public settings records and atomic persistence; consumers receive typed values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import math
import os
from pathlib import Path
import tempfile
from typing import ClassVar

from .audio import AudioConfig
from .appearance import ProgressConfig, TitleConfig, TransportConfig, VisualizerConfig
from .keys import KeyRule, KeySequence
from .records import Choice, Nested, Number, Record, Sequence, SettingsError, setting
from .shortcuts import Separator, Shortcut, ShortcutLayout, SlotEntryRule


class WidthMode(StrEnum):
    FIXED = "fixed"
    RATIO = "ratio"


@dataclass(frozen=True, slots=True)
class Width(Record):
    DEFAULT_CELLS: ClassVar[int] = 80
    mode: WidthMode = setting(Choice(WidthMode), default=WidthMode.FIXED)
    value: int | float = setting(Number(), default=DEFAULT_CELLS)

    @classmethod
    def value_rule(cls, mode: WidthMode) -> Number:
        if WidthMode(mode) == WidthMode.FIXED:
            return Number(minimum=Shortcut.minimum_width(), integer=True)
        return Number(minimum=0, maximum=1, exclusive_minimum=True)

    def _check(self) -> None:
        self.value_rule(self.mode).decode(self.value)

    def resolve(self, pty_columns: int, available: int) -> int:
        requested = self.value if self.mode == WidthMode.FIXED else math.floor(pty_columns * self.value)
        return max(0, min(int(requested), available))

    @classmethod
    def schema(cls) -> dict:
        result = super(Width, cls).schema()
        names = cls.names()
        result["allOf"] = [
            {"if": {"properties": {names.mode: {"const": mode}},
                    "required": [names.mode] if mode == WidthMode.RATIO else []},
             "then": {"properties": {names.value: cls.value_rule(mode).schema()},
                      "required": [names.value] if mode == WidthMode.RATIO else []}}
            for mode in WidthMode
        ]
        return result


@dataclass(frozen=True, slots=True)
class DockConfig(Record):
    toggle_key: KeySequence = setting(KeyRule(), default=("f8",))
    width: Width = setting(Nested(Width), factory=Width)
    shortcuts: tuple[Shortcut | Separator, ...] = setting(Sequence(SlotEntryRule(), maximum=Shortcut.COUNT), default=())
    audio: AudioConfig = setting(Nested(AudioConfig), factory=AudioConfig)
    visualizer: VisualizerConfig = setting(Nested(VisualizerConfig), factory=VisualizerConfig)
    title: TitleConfig = setting(Nested(TitleConfig), factory=TitleConfig)
    progress: ProgressConfig = setting(Nested(ProgressConfig), factory=ProgressConfig)
    transport: TransportConfig = setting(Nested(TransportConfig), factory=TransportConfig)
    shortcut_layout: ShortcutLayout = setting(Nested(ShortcutLayout), factory=ShortcutLayout)

    def _check(self) -> None:
        slots, keys = set(), [self.toggle_key]
        for shortcut in self.shortcuts:
            if shortcut.slot in slots:
                raise SettingsError(f"Duplicate shortcut slot: {shortcut.slot}")
            slots.add(shortcut.slot)
            if shortcut.enabled and shortcut.key is not None:
                if any(shortcut.key.overlaps(key.keys) for key in keys):
                    raise SettingsError(f"Overlapping dok keys: {shortcut.key.to_list()}")
                keys.append(shortcut.key)

    def entry(self, slot: int) -> Shortcut | Separator | None:
        Shortcut.validate_slot(slot)
        return next((item for item in self.shortcuts if item.slot == slot), None)

    def shortcut(self, slot: int) -> Shortcut | None:
        item = self.entry(slot)
        return item if isinstance(item, Shortcut) else None

    @classmethod
    def schema(cls) -> dict:
        result = super(DockConfig, cls).schema()
        result["description"] = "DockConfig also validates unique slots and non-overlapping normalized key sequences."
        return result


class ConfigStore:
    DIRECTORY = "dok"
    FILENAME = "config.json"
    ENCODING = "utf-8"

    def __init__(self, path: Path | str):
        self.path = Path(path)

    @classmethod
    def for_home(cls, home: Path | str) -> ConfigStore:
        return cls(Path(home) / cls.DIRECTORY / cls.FILENAME)

    @staticmethod
    def _object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SettingsError(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    def load(self) -> DockConfig:
        try:
            text = self.path.read_text(encoding=self.ENCODING)
        except FileNotFoundError:
            return DockConfig()
        try:
            data = json.loads(text, object_pairs_hook=self._object)
        except json.JSONDecodeError as exc:
            raise SettingsError(f"Invalid config JSON: {exc}") from exc
        return DockConfig.from_dict(data)

    def save(self, config: DockConfig) -> None:
        if not isinstance(config, DockConfig):
            raise TypeError("save expects a DockConfig")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding=self.ENCODING, dir=self.path.parent,
                                             prefix=f".{self.path.name}.", delete=False) as output:
                name = output.name
                json.dump(config.to_dict(), output, ensure_ascii=False, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
            name = None
            descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if name is not None:
                Path(name).unlink(missing_ok=True)
