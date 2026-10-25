"""Stable presentation anchors shared by rendering, cache and tag navigation."""


class TranscriptIdentity:
    INDEX = "index:{index}"
    BLOCK = "{message}:block:{offset}"

    @classmethod
    def message(cls, message, index):
        return message.id or cls.INDEX.format(index=index)

    @classmethod
    def block(cls, message, index, offset):
        return cls.BLOCK.format(message=cls.message(message, index), offset=offset)
