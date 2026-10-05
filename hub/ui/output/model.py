"""UI output contracts, independent of the LLM execution model."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ...config.theme import HubTheme
from ...locales import Language

FragmentLines = list[list[tuple[str, str]]]


@dataclass(frozen=True, slots=True)
class OutputBlock:
    kind: str
    text: str
    start: int
    attributes: tuple[tuple[str, str], ...] = ()
    raw: str = ""


@dataclass(frozen=True, slots=True)
class RenderContext:
    width: int
    theme: HubTheme
    language: Language
    root: Path
    invalidate: Callable[[], None]
