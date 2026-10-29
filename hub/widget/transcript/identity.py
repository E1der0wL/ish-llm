"""Stable presentation anchors shared by rendering, cache and tag navigation."""

from typing import NamedTuple


class TranscriptIdentity:
    INDEX = "index:{index}"
    BLOCK_SEPARATOR = ":block:"
    BLOCK = "{message}" + BLOCK_SEPARATOR + "{offset}"

    @classmethod
    def message(cls, message, index):
        return message.id or cls.INDEX.format(index=index)

    @classmethod
    def block(cls, message, index, offset):
        return cls.BLOCK.format(message=cls.message(message, index), offset=offset)

    @classmethod
    def message_id(cls, anchor):
        return anchor.split(cls.BLOCK_SEPARATOR, 1)[0]




class TranscriptAnchor(NamedTuple):
    identifier: str
    row: int

    @staticmethod
    def row_of(anchor):
        identifier, row = anchor
        return row
