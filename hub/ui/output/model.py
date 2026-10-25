"""UI output contracts, independent of the LLM execution model."""

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Callable, ClassVar

from ...config.theme import HubTheme
from ...locales import Language

FragmentLines = list[list[tuple[str, str]]]


@dataclass(frozen=True, slots=True)
class OutputBlock:
    MARKDOWN: ClassVar[str] = "markdown"
    CODE: ClassVar[str] = "code"
    TABLE: ClassVar[str] = "table"
    LITERAL: ClassVar[str] = "literal"
    PENDING: ClassVar[str] = "pending"
    HUB_CODE: ClassVar[str] = "hub-code"
    HUB_DIFF: ClassVar[str] = "hub-diff"
    HUB_TABLE: ClassVar[str] = "hub-table"
    HUB_CARD: ClassVar[str] = "hub-card"
    HUB_CHART: ClassVar[str] = "hub-chart"
    HUB_IMAGE: ClassVar[str] = "hub-image"
    ENCODING: ClassVar[str] = "utf-8"
    TAG_PATTERN: ClassVar[str] = r"hub-[a-z][a-z0-9-]*"

    kind: str
    text: str
    start: int
    attributes: tuple[tuple[str, str], ...] = ()
    raw: str = ""


class DisplayAttributes:
    """Read only declared display fields, retaining explicit values and defaults."""

    @classmethod
    def from_pairs(cls, pairs):
        return cls.from_record(dict(pairs))

    @classmethod
    def from_record(cls, values):
        return cls(**{field.name: values[field.name] for field in fields(cls) if field.name in values})

    def to_pairs(self):
        return tuple((field.name, getattr(self, field.name)) for field in fields(self))


@dataclass(frozen=True, slots=True)
class CodeAttributes(DisplayAttributes):
    language: str = "text"


@dataclass(frozen=True, slots=True)
class TitleAttributes(DisplayAttributes):
    title: str = ""


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
    icon: str = ""

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
