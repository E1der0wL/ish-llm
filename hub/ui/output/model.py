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
class OutputObject:
    """One rendered form and its lossless source, addressed across reflows."""

    id: str
    message_id: str
    block: OutputBlock
    title: str
    lines: tuple[tuple[tuple[str, str], ...], ...]
    start_line: int
    end_line: int

    @property
    def raw(self):
        return self.block.raw or self.block.text


@dataclass(frozen=True, slots=True)
class RenderContext:
    width: int
    theme: HubTheme
    language: Language
    root: Path
    invalidate: Callable[[], None]


@dataclass(frozen=True, slots=True)
class OutputUIContext:
    """Host capabilities for trusted, locally registered expanded widgets.

    Model output supplies data only. Factories run on the PTK thread; expensive
    parsing/rendering belongs to render_worker. No backend execution is exposed.
    """

    language: Language
    rows: Callable[[], int]
    columns: Callable[[], int]
    close: Callable[[], None]
    viewport: Callable
    render_worker: object
