"""Display-only contracts shared by transcript renderers and their cache."""


class RenderSection:
    ALL = "all"
    BODY = "body"
    HEADER = "header"
    TOOLS = "tools"


class TranscriptLayout:
    ABSENT = None
    MIN_WIDTH = 1
    HORIZONTAL_MARGIN = 4
    EMPTY = ""
    INDENT = "  "
    SPACE = " "
    NEWLINE = "\n"
    PAD = False
    BLANK_LINE = ((EMPTY, EMPTY),)
    MESSAGE_GAP = (BLANK_LINE, BLANK_LINE)
    GRAPH_CODE_INSET = 4
    TEXT_FENCE_OPEN = "```text\n"
    TEXT_FENCE_CLOSE = "\n```"

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


class TranscriptPresentation:
    ORIGIN = 0
    MIN_INSET_WIDTH = 3
    USER_INSET = 1
    NO_INSET = 0
    USER_WIDTH_RATIO = 0.78
    SIDES = 2
    LABEL_SEPARATOR = " · "
    SYSTEM_TIME_SEPARATOR = " • "
    OVERFLOW = "fold"
