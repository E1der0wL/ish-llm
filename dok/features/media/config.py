"""Title, progress and transport appearance settings."""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ...core.colors import DockPalette, HexColor
from ...core.elements import ElementConfig, Spacing
from ...core.records import Choice, Nested, Nullable, Number, Record, Sequence, SettingsError, Text, setting
from ...core.styles import TextStyle, MutedStyle
from .models import PlaybackAction


@dataclass(frozen=True, slots=True)
class TitleTexts(Record):
    prefix: str = setting(
        Text(single_line=True),
        default="Now playing: ",
        description="Text before application names. An empty string shows names only. Trailing spaces are preserved."
    )
    audio_error: str = setting(
        Text(single_line=True),
        default="Audio connection failed",
        description="Title text when the audio connection fails. An empty string uses the normal title instead."
    )



@dataclass(frozen=True, slots=True)
class TitleSpacing(Spacing):
    after_rows: int = setting(Spacing.ROWS_RULE, default=1)
    basic_after_rows: int | None = setting(Nullable(Spacing.ROWS_RULE), default=0)


@dataclass(frozen=True, slots=True)
class TitleConfig(ElementConfig):
    APPLICATION_SEPARATOR: ClassVar[str] = " · "
    spacing: TitleSpacing = setting(Nested(TitleSpacing), factory=TitleSpacing)
    style: TextStyle = setting(Nested(TextStyle), factory=TextStyle, description="Foreground appearance.")
    texts: TitleTexts = setting(
        Nested(TitleTexts),
        factory=TitleTexts,
        description="Title prefix and audio connection message."
    )

    def application_caption(self, applications: tuple[str, ...]) -> str:
        return self.texts.prefix + self.APPLICATION_SEPARATOR.join(applications) if applications else ""

    def audio_error_caption(self, diagnostic: str) -> str:
        """Keep backend details separate from the user-facing title text."""
        return self.texts.audio_error if diagnostic else ""


@dataclass(frozen=True, slots=True)
class ProgressConfig(ElementConfig):
    style: MutedStyle = setting(Nested(MutedStyle), factory=MutedStyle, description="Foreground appearance.")
    width_columns: int = setting(
        Number(minimum=1, integer=True),
        default=48,
        description="Progress bar length in cells, excluding the time labels. Clamped to the available space."
    )

    def display_length(self, available: int) -> int:
        return max(0, min(self.width_columns, available))


@dataclass(frozen=True, slots=True)
class TransportButton(Record):
    action: PlaybackAction = setting(
        Choice(PlaybackAction),
        description="Button action: previous, play_pause, next, play, pause, or stop. Duplicate actions are not allowed."
    )
    text: str = setting(
        Text(single_line=True),
        default="",
        description="Button icon or text. An empty string uses the default action symbol. Nerd Font glyphs require a compatible font."
    )
    style: MutedStyle = setting(Nested(MutedStyle), factory=MutedStyle, description="Foreground appearance.")

    def display(self) -> str:
        return self.text if self.text.strip() else self.action.symbol


@dataclass(frozen=True, slots=True)
class TransportSpacing(Spacing):
    after_rows: int = setting(Spacing.ROWS_RULE, default=2)
    basic_after_rows: int | None = setting(Nullable(Spacing.ROWS_RULE), default=1)


@dataclass(frozen=True, slots=True)
class TransportConfig(ElementConfig):
    SEPARATOR: ClassVar[str] = "   "
    spacing: TransportSpacing = setting(Nested(TransportSpacing), factory=TransportSpacing)
    buttons: tuple[TransportButton, ...] = setting(
        Sequence(Nested(TransportButton), maximum=len(PlaybackAction)),
        description="Buttons in display order. [] draws no buttons.",
        default=tuple(TransportButton(action, text=action.symbol)
                      for action in (
                          PlaybackAction.PREVIOUS,
                          PlaybackAction.PLAY_PAUSE,
                          PlaybackAction.NEXT
                      )),
    )

    def _check(self):
        if len({button.action for button in self.buttons}) != len(self.buttons):
            raise SettingsError("Duplicate transport button action")

    @classmethod
    def schema(cls):
        result = super(TransportConfig, cls).schema()
        result["properties"][cls.names().buttons]["allOf"] = [
            {"contains": {"properties": {TransportButton.names().action: {"const": action}},
                          "required": [TransportButton.names().action]},
             "minContains": 0, "maxContains": 1} for action in PlaybackAction
        ]
        return result
