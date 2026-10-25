"""Bounded, immutable table data shared by renderers and interactive viewers."""

from hub.ui.output.model import OutputBlock

from dataclasses import dataclass
import json
import math

from .markdown_syntax import MarkdownSyntax


@dataclass(frozen=True, slots=True)
class TableData:
    MAX_SOURCE_BYTES = 262144
    SOURCE_TOO_LARGE = "Table source exceeds 256 KiB"
    MIN_COLUMNS = 1
    MAX_COLUMNS = 32
    MAX_ROWS = 500
    INVALID_DIMENSIONS = "Expected 1–32 columns and at most 500 rows"
    INVALID_ROW_WIDTH = "Table rows must match columns"
    HEADER = True
    BODY = False
    EMPTY = ""
    SPACE = " "
    NEWLINE = "\n"
    NO_COLUMN = None
    DESCENDING = False
    NUMERIC_ORDER = 0
    TEXT_ORDER = 1
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    searchable: tuple[str, ...]

    @classmethod
    def from_block(cls, block):
        cls.check_source(block.text)
        if block.kind == OutputBlock.HUB_TABLE:
            data = json.loads(block.text)
            columns, rows = data[cls.columns.__name__], data[cls.rows.__name__]
        else:
            tokens = MarkdownSyntax.table_parser().parse(block.text)
            columns, rows, current, header = [], [], [], cls.BODY
            for token in tokens:
                if token.type == MarkdownSyntax.HEAD_OPEN:
                    header = cls.HEADER
                elif token.type == MarkdownSyntax.HEAD_CLOSE:
                    header = cls.BODY
                elif token.type == MarkdownSyntax.ROW_OPEN:
                    current = []
                elif token.type == MarkdownSyntax.INLINE:
                    # Keep visible inline text, including escaped pipes and code.
                    current.append(
                        cls.EMPTY.join(
                            (
                                child.content
                                if child.type not in MarkdownSyntax.BREAK_TOKENS
                                else cls.SPACE
                            )
                            for child in token.children or ()
                            if child.type in MarkdownSyntax.VISIBLE_TOKENS
                        )
                    )
                elif token.type == MarkdownSyntax.ROW_CLOSE:
                    if header:
                        columns = current
                    else:
                        rows.append(current)
        return cls._validated(columns, rows)

    @classmethod
    def check_source(cls, source):
        if len(source.encode(OutputBlock.ENCODING)) > cls.MAX_SOURCE_BYTES:
            raise ValueError(cls.SOURCE_TOO_LARGE)

    @classmethod
    def from_record(cls, data, *, source):
        cls.check_source(source)
        return cls._validated(data[cls.columns.__name__], data[cls.rows.__name__])

    @classmethod
    def _validated(cls, columns, rows):
        if (
            not isinstance(columns, list)
            or not isinstance(rows, list)
            or not cls.MIN_COLUMNS <= len(columns) <= cls.MAX_COLUMNS
            or len(rows) > cls.MAX_ROWS
        ):
            raise ValueError(cls.INVALID_DIMENSIONS)
        if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
            raise ValueError(cls.INVALID_ROW_WIDTH)
        columns = tuple(map(str, columns))
        rows = tuple(tuple(map(str, row)) for row in rows)
        return cls(
            columns, rows, tuple(cls.NEWLINE.join(row).casefold() for row in rows)
        )

    def select(self, query=EMPTY, column=NO_COLUMN, descending=DESCENDING):
        needle = query.casefold()
        rows = [i for i, text in enumerate(self.searchable) if needle in text]
        if column is not self.NO_COLUMN:

            def key(index):
                value = self.rows[index][column]
                try:
                    number = float(value)
                    if math.isfinite(number):
                        return self.NUMERIC_ORDER, number
                except ValueError:
                    pass
                return self.TEXT_ORDER, value.casefold()

            rows.sort(key=key, reverse=descending)
        return rows
