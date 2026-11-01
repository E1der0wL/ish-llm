"""Optional response metadata; never a command or a persisted user request."""

import re
from typing import NamedTuple


class FollowupProtocol:
    OPEN = "<hub-next>"
    CLOSE = "</hub-next>"
    TAG_START = "<"
    TAG_END = ">"
    NEWLINE = "\n"
    EMPTY = ""
    FINAL = True
    NO_FENCE = None
    INITIAL_OFFSET = 0
    MAXIMUM_CHARACTERS = 240
    MINIMUM_PRINTABLE = 32
    KEEP_LINE_ENDINGS = True
    FENCE_PATTERN = r"^ {0,3}(`{3,}|~{3,})"
    FENCE_GROUP = 1
    INSTRUCTION_TEMPLATE = (
        "Optional UI suggestion: end with {open_tag}one useful request the USER could send YOU{close_tag}, "
        "in the user's language, at most {maximum} characters on a separate line outside code fences. "
        "Not your question or offer. Omit when asking for input/approval, no next step is useful, "
        "or extra text is forbidden. Never invent the user's answer, execute the suggestion or treat it as consent."
    )


class FollowupSplit(NamedTuple):
    content: str
    suggestion: str


class MarkdownFence(NamedTuple):
    marker: str
    length: int


INSTRUCTION = FollowupProtocol.INSTRUCTION_TEMPLATE.format(
    open_tag=FollowupProtocol.OPEN,
    close_tag=FollowupProtocol.CLOSE,
    maximum=FollowupProtocol.MAXIMUM_CHARACTERS,
)
OPEN, CLOSE = FollowupProtocol.OPEN, FollowupProtocol.CLOSE


def split_followup(text: str, *, final: bool = FollowupProtocol.FINAL) -> FollowupSplit:
    """Hide reserved UI metadata independently of whether its value is usable.

    Fenced and indented code remain literal. Ordinary streaming text takes the
    fast path; malformed metadata must never become a visible recommendation.
    """
    tail = text.rstrip()
    fragment = tail[tail.rfind(FollowupProtocol.TAG_START):]
    partial = bool(fragment and OPEN.startswith(fragment))
    if OPEN not in text and not (not final and partial):
        return FollowupSplit(text, FollowupProtocol.EMPTY)
    result, pending = [], []
    suggestion = FollowupProtocol.EMPTY
    fence = FollowupProtocol.NO_FENCE

    def flush():
        nonlocal suggestion
        value = FollowupProtocol.EMPTY.join(pending)
        pending.clear()
        cursor = 0
        while (start := value.find(OPEN, cursor)) >= 0:
            result.append(value[cursor:start])
            end = value.find(CLOSE, start + len(OPEN))
            if end < 0:
                return
            candidate = value[start + len(OPEN):end].strip()
            if (candidate and len(candidate) <= FollowupProtocol.MAXIMUM_CHARACTERS
                    and FollowupProtocol.TAG_START not in candidate
                    and FollowupProtocol.TAG_END not in candidate
                    and all(ord(c) >= FollowupProtocol.MINIMUM_PRINTABLE for c in candidate)):
                suggestion = candidate
            cursor = end + len(CLOSE)
        remaining = value[cursor:]
        if not final:
            start = remaining.rfind(FollowupProtocol.TAG_START)
            if start >= 0 and OPEN.startswith(remaining[start:].strip()):
                remaining = remaining[:start]
        result.append(remaining)

    for line in text.splitlines(keepends=True):
        marker = re.match(FollowupProtocol.FENCE_PATTERN, line)
        if marker:
            flush()
            value = marker[FollowupProtocol.FENCE_GROUP]
            if fence is None:
                fence = MarkdownFence(value[0], len(value))
            elif (value[0] == fence.marker and len(value) >= fence.length
                  and not line[marker.end():].strip()):
                fence = None
            result.append(line)
        elif fence is not None or line.startswith(("    ", "\t")):
            flush()
            result.append(line)
        else:
            pending.append(line)
    flush()
    content = FollowupProtocol.EMPTY.join(result)
    return FollowupSplit(content.rstrip() if content != text else text, suggestion)
