"""The MarkdownIt grammar shared by parsing, tables and clean-code extraction."""

from markdown_it import MarkdownIt


class MarkdownSyntax:
    PROFILE = "commonmark"
    TABLE_RULE = "table"
    FENCE = "fence"
    CODE_BLOCK = "code_block"
    TABLE_OPEN = "table_open"
    HEAD_OPEN = "thead_open"
    HEAD_CLOSE = "thead_close"
    ROW_OPEN = "tr_open"
    ROW_CLOSE = "tr_close"
    INLINE = "inline"
    TEXT = "text"
    CODE_INLINE = "code_inline"
    IMAGE = "image"
    SOFT_BREAK = "softbreak"
    HARD_BREAK = "hardbreak"
    CODE_TOKENS = (FENCE, CODE_BLOCK)
    BLOCK_TOKENS = (*CODE_TOKENS, TABLE_OPEN)
    BREAK_TOKENS = (SOFT_BREAK, HARD_BREAK)
    VISIBLE_TOKENS = (TEXT, CODE_INLINE, IMAGE, *BREAK_TOKENS)

    @classmethod
    def parser(cls):
        return MarkdownIt(cls.PROFILE)

    @classmethod
    def table_parser(cls):
        return cls.parser().enable(cls.TABLE_RULE)
