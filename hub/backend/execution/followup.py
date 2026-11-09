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
    ASSISTANT_OFFER = re.compile(
        r"(?:궁금하신가요|원하시나요|필요하신가요|도와드릴까요|알려드릴까요|"
        r"보여드릴까요|설명해\s*드릴까요|해\s*드릴까요)\s*[?？.!。]*$"
        r"|^(?:would you like\b|do you want me to\b|shall I\b|can I help you\b)",
        re.IGNORECASE,
    )
    INSTRUCTION_TEMPLATE = (
        "Optional UI hint: end with {open_tag}one next USER message to the assistant{close_tag} "
        "({maximum} chars max), on its own line outside code. Continue this task in the preferred "
        "language and the user's latest tone/brevity, not the assistant's or quoted text's voice. "
        "Use '줄 번호도 추가해줘' or 'Please add a test', not '궁금하신가요?' or 'Would you like...?'. "
        "Omit when asking for input/approval, no next step fits, or extra text is forbidden. "
        "Never invent answers, execute the hint or treat it as consent."
    )

    @classmethod
    def accepts(cls, candidate):
        """Reject malformed metadata and unmistakable assistant offers, without rewriting it."""
        return (bool(candidate) and len(candidate) <= cls.MAXIMUM_CHARACTERS
                and cls.TAG_START not in candidate and cls.TAG_END not in candidate
                and all(ord(c) >= cls.MINIMUM_PRINTABLE for c in candidate)
                and cls.ASSISTANT_OFFER.search(candidate) is None)


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
            if FollowupProtocol.accepts(candidate):
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
