"""Compose the single supported settings structure from feature owners."""
from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar
import math
from ..core.records import Choice, Nested, Nullable, Number, Record, setting
from ..locales import Language
from ..core.theme import Theme
from ..dashboard.config import WidgetsConfig
from ..features.audio.config import AudioConfig, VisualizerConfig, VisualizerStyle
from ..features.clock.config import ClockConfig
from ..features.shortcuts.config import Shortcut, ShortcutsConfig
from ..features.media.connection import MediaConfig
from ..features.media.config import TransportConfig
from ..features.system.config import SystemConfig
from ..features.toolbar.config import ToolbarConfig
from ..integrations.ish.mouse import MouseConfig
from ..integrations.ish.keys import KeyRule, KeySequence
from .popups import PopupSettings

class WidthMode(StrEnum):
    FIXED = "fixed"
    RATIO = "ratio"


@dataclass(frozen=True, slots=True)
class Width(Record):
    DEFAULT_CELLS: ClassVar[int] = 80
    mode: WidthMode = setting(
        Choice(WidthMode),
        default=WidthMode.FIXED,
        description="Width mode: fixed uses terminal cells; ratio uses a fraction of the ish PTY width."
    )
    value: int | float = setting(
        Number(),
        default=DEFAULT_CELLS,
        description=f"Fixed width needs at least {Shortcut.minimum_width()} cells to fit the shortcut slots. Ratio accepts a fraction greater than 0 and at most 1. Both fit the available width."
    )

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
class DockKeys(Record):
    toggle: KeySequence = setting(
        KeyRule(),
        default=("c-w",),
        description='Toggle dock visibility. Opens maximized by default and fits the available space. Examples: ["c-w"], ["f8"].'
    )

@dataclass(frozen=True, slots=True)
class DockSettings(Record):
    keys: DockKeys = setting(Nested(DockKeys), factory=DockKeys, description="Dock mode key sequences.")
    width: Width = setting(
        Nested(Width),
        factory=Width,
        description="Overall dock width; widget rows are centered within it."
    )
    mouse: MouseConfig = setting(Nested(MouseConfig), factory=MouseConfig,
        description="Mouse reporting and dock-only action routing.")

@dataclass(frozen=True, slots=True)
class DockConfig(Record):
    dock: DockSettings = setting(
        Nested(DockSettings),
        factory=DockSettings,
        description="Dock width and mode keys."
    )
    audio: AudioConfig = setting(
        Nested(AudioConfig),
        factory=AudioConfig,
        description="Linux output audio capture. Native Windows audio is not included."
    )
    clock: ClockConfig = setting(
        Nested(ClockConfig),
        factory=ClockConfig,
        description="Shared clock settings."
    )
    widgets: WidgetsConfig = setting(
        Nested(WidgetsConfig),
        factory=WidgetsConfig,
        description="Widget content, appearance and spacing."
    )
    media: MediaConfig = setting(Nested(MediaConfig), factory=MediaConfig,
        description="MPRIS discovery, player selection and control. Independent of audio capture.")
    system: SystemConfig = setting(Nested(SystemConfig), factory=SystemConfig,
        description="Linux CPU, memory and network sampling. WSL metrics do not represent the entire Windows host.")
    toolbar: ToolbarConfig = setting(Nested(ToolbarConfig), factory=ToolbarConfig,
        description="Left-aligned date and prompt status, with system metrics at the right edge.")
    popups: PopupSettings = setting(Nested(PopupSettings), factory=PopupSettings,
        description="Centered calendar/weather popups and bottom-right notifications. Focused general popups always support mouse actions; notifications follow dock.mouse.enabled.")
    theme: Theme = setting(Nested(Theme), factory=Theme,
        description="Shared named colors and semantic roles. All themed style fields reference these names; shortcuts and the visualizer retain their own RGB colors.")
    language: Language | None = setting(Nullable(Choice(Language)), default=None,
        description='UI language: null follows ish\'s selected language with English fallback. "en" selects English; "ko" selects Korean. Custom text, event content and ASCII weekday art are preserved.')

    def _check(self):
        music = (button.key for button in self.widgets.controls.keyed_buttons())
        self.widgets.shortcuts.validate_keys((self.dock.keys.toggle, *music, *self.popups.keys()))

    @classmethod
    def initial(cls) -> DockConfig:
        """Initial file examples, distinct from ordinary missing-field defaults."""
        return cls(toolbar=ToolbarConfig.initial(), widgets=WidgetsConfig(
            controls=TransportConfig.initial(),
            shortcuts=ShortcutsConfig.initial(),
            visualizer=VisualizerConfig(style=VisualizerStyle.initial()),
        ))

    @classmethod
    def schema(cls):
        result = super(DockConfig, cls).schema()
        result["description"] = "The owner also validates unique slots and non-overlapping normalized key sequences."
        return result
