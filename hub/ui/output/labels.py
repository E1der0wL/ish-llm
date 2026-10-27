"""Compact, cell-aware labels for output objects and conversation anchors."""

from hub.ui.output.model import OutputBlock, TitleAttributes
from .images import ImageAttributes
from .table import TableData
from dataclasses import dataclass

import json
import re

from prompt_toolkit.utils import get_cwidth


class LabelText:
    EMPTY = ""
    SPACE = " "
    ELLIPSIS = "…"
    NEWLINE = "\n"
    CODE_START = r"^\s*(?:`{3,}|~{3,})[^\n]*\n"
    CODE_END = r"\n\s*(?:`{3,}|~{3,})\s*$"
    TABLE_TRIM = "| "
    TABLE_SEPARATOR = "|"
    COLUMN_JOIN = "·"
    STRUCTURED_JOIN = " • "
    MIN_WIDTH = 0
    ELLIPSIS_WIDTH = 1
    MAX_WIDTH = 160
    NO_PENDING_SPACE = False


@dataclass(frozen=True, slots=True)
class BlockCaption:
    title: TitleAttributes
    image: ImageAttributes

    @classmethod
    def read(cls, pairs):
        values = dict(pairs)
        return cls(
            TitleAttributes.from_record(values), ImageAttributes.from_record(values)
        )

    @property
    def explicit(self):
        return self.title.title or self.image.alt


@dataclass(frozen=True, slots=True)
class StructuredCaption:
    """Best-effort label projection; strict table validation remains TableData's."""

    EMPTY_COLUMNS = ()
    title: object
    columns: object

    @classmethod
    def read(cls, record):
        return cls(
            TitleAttributes.from_record(record).title,
            record.get(TableData.columns.__name__, cls.EMPTY_COLUMNS),
        )


def ellipsize(text: str, width: int) -> str:
    """Collapse whitespace and clip by terminal cells, without scanning huge bodies."""
    width = max(LabelText.MIN_WIDTH, width)
    chars, used, pending_space = [], 0, LabelText.NO_PENDING_SPACE
    for char in text:
        if char.isspace():
            pending_space = bool(chars)
            continue
        if not char.isprintable():
            continue
        for token in (LabelText.SPACE, char) if pending_space else (char,):
            size = get_cwidth(token)
            if used + size > width:
                while chars and used + LabelText.ELLIPSIS_WIDTH > width:
                    used -= get_cwidth(chars.pop())
                return LabelText.EMPTY.join(chars).rstrip() + (
                    LabelText.ELLIPSIS if width else LabelText.EMPTY
                )
            chars.append(token)
            used += size
        pending_space = LabelText.NO_PENDING_SPACE
    return LabelText.EMPTY.join(chars)


def block_title(block) -> str:
    attrs = BlockCaption.read(block.attributes)
    explicit = attrs.explicit
    if explicit and explicit.strip():
        return explicit
    text = block.text.strip()
    if block.kind == OutputBlock.CODE:
        text = re.sub(LabelText.CODE_START, LabelText.EMPTY, text)
        text = re.sub(LabelText.CODE_END, LabelText.EMPTY, text)
    elif block.kind == OutputBlock.TABLE:
        text = (
            text.partition(LabelText.NEWLINE)[0]
            .strip(LabelText.TABLE_TRIM)
            .replace(LabelText.TABLE_SEPARATOR, LabelText.COLUMN_JOIN)
        )
    elif block.kind in (
        OutputBlock.HUB_TABLE,
        OutputBlock.HUB_CHART,
        OutputBlock.HUB_CARD,
    ):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                caption = StructuredCaption.read(data)
                text = str(
                    caption.title
                    or LabelText.STRUCTURED_JOIN.join(map(str, caption.columns))
                    or text
                )
        except (ValueError, TypeError):
            pass
    if not text:
        text = attrs.image.src
    return (
        ellipsize(text.partition(LabelText.NEWLINE)[0], LabelText.MAX_WIDTH)
        or LabelText.ELLIPSIS
    )
