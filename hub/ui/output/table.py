"""Bounded, immutable table data shared by renderers and interactive viewers."""

from dataclasses import dataclass
import json
import math

from markdown_it import MarkdownIt


@dataclass(frozen=True, slots=True)
class TableData:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    searchable: tuple[str, ...]

    @classmethod
    def from_block(cls, block):
        if len(block.text.encode("utf-8")) > 262144:
            raise ValueError("Table source exceeds 256 KiB")
        if block.kind == "hub-table":
            data = json.loads(block.text)
            columns, rows = data["columns"], data["rows"]
        else:
            tokens = MarkdownIt("commonmark").enable("table").parse(block.text)
            columns, rows, current, header = [], [], [], False
            for token in tokens:
                if token.type == "thead_open":
                    header = True
                elif token.type == "thead_close":
                    header = False
                elif token.type == "tr_open":
                    current = []
                elif token.type == "inline":
                    # Keep visible inline text, including escaped pipes and code.
                    current.append("".join(child.content if child.type not in ("softbreak", "hardbreak") else " "
                                           for child in token.children or ()
                                           if child.type in ("text", "code_inline", "image", "softbreak", "hardbreak")))
                elif token.type == "tr_close":
                    if header:
                        columns = current
                    else:
                        rows.append(current)
        if (not isinstance(columns, list) or not isinstance(rows, list)
                or not 1 <= len(columns) <= 32 or len(rows) > 500):
            raise ValueError("Expected 1–32 columns and at most 500 rows")
        if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
            raise ValueError("Table rows must match columns")
        columns = tuple(map(str, columns))
        rows = tuple(tuple(map(str, row)) for row in rows)
        return cls(columns, rows, tuple("\n".join(row).casefold() for row in rows))

    def select(self, query="", column=None, descending=False):
        needle = query.casefold()
        rows = [i for i, text in enumerate(self.searchable) if needle in text]
        if column is not None:
            def key(index):
                value = self.rows[index][column]
                try:
                    number = float(value)
                    if math.isfinite(number):
                        return 0, number
                except ValueError:
                    pass
                return 1, value.casefold()
            rows.sort(key=key, reverse=descending)
        return rows
