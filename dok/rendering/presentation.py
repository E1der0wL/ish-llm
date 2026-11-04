"""Shared render data, terminal styles and non-interactive mouse handling."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from ..core.colors import DockPalette
from .text import fit, width

if TYPE_CHECKING:
    from ..features.audio.models import AudioSnapshot
    from ..features.media.models import PlaybackSnapshot


@dataclass(frozen=True, slots=True)
class RenderContext:
    playback: PlaybackSnapshot
    audio: AudioSnapshot
    diagnostic: str
    width: int


def swallow_mouse(event):
    """Painted dock content consumes clicks without taking focus."""
    return None


@dataclass(frozen=True, slots=True)
class TextSpan:
    text: str
    color: str
    bold: bool = False


@dataclass(frozen=True, slots=True)
class TextRow:
    spans: tuple[TextSpan, ...] = ()

    @classmethod
    def plain(cls, text: str, color: str, *, bold: bool = False):
        return cls((TextSpan(text, color, bold),))

    @property
    def text(self) -> str:
        return "".join(span.text for span in self.spans)

    @property
    def cells(self) -> int:
        return width(self.text)

    def fitted(self, available: int):
        """Clip at a Unicode cluster boundary without losing per-span styles."""
        if self.cells <= available:
            return self
        prefix = fit(self.text, available, marker="")
        remaining, result = len(prefix), []
        for span in self.spans:
            if not remaining:
                break
            text = span.text[:remaining]
            result.append(replace(span, text=text))
            remaining -= len(text)
        return TextRow(tuple(result))


class DockStyle:
    # Use the terminal background, including its configured transparency.
    BASE = "bg:default"
    BACKGROUND = f"{BASE} {DockPalette.TEXT}"
    ACCENT = f"{BASE} {DockPalette.ACCENT}"
    DIM = f"{BASE} {DockPalette.MUTED}"
    ERROR = f"{BASE} {DockPalette.ERROR}"

    @classmethod
    def foreground(cls, color: str, *, bold: bool = False) -> str:
        return f"{cls.BASE} {color}" + (" bold" if bold else "")


@dataclass(frozen=True, slots=True)
class DockLine:
    row: int
    left: int
    width: int
    fragments: tuple
