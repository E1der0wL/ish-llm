"""Display-only contracts shared by transcript renderers and their cache."""


class RenderSection:
    ALL = "all"
    BODY = "body"
    HEADER = "header"
    TOOLS = "tools"


class TranscriptLayout:
    MIN_WIDTH = 1
    HORIZONTAL_MARGIN = 4
    EMPTY = ""
    INDENT = "  "
    SPACE = " "
    NEWLINE = "\n"
    PAD = False
    BLANK_LINE = ((EMPTY, EMPTY),)
    MESSAGE_GAP = (BLANK_LINE, BLANK_LINE)

    @classmethod
    def inner_width(cls, width):
        return max(cls.MIN_WIDTH, width - cls.HORIZONTAL_MARGIN)


class ToolCaption:
    MAX_NAME = 64
    FORMAT = "{icon} {name} · {status}"

    @classmethod
    def render(cls, name, status, language):
        return cls.FORMAT.format(
            icon=language.icons.tool,
            name=TranscriptLayout.SPACE.join(name.split())[: cls.MAX_NAME],
            status=language.status(status),
        )
