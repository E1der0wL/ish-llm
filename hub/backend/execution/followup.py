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
    """Only a trailing, standalone tag outside fenced code is metadata."""
    tail = text.rstrip()
    candidate = tail[
        tail.rfind(FollowupProtocol.NEWLINE) + len(FollowupProtocol.NEWLINE) :
    ]
    if not candidate.startswith(OPEN) and not (
        not final
        and candidate.startswith(FollowupProtocol.TAG_START)
        and OPEN.startswith(candidate)
    ):
        return FollowupSplit(text, FollowupProtocol.EMPTY)
    # Ordinary streaming deltas never walk the whole Markdown document. Inspect
    # fences only when the final line actually looks like protocol metadata.
    fence = FollowupProtocol.NO_FENCE
    offset = FollowupProtocol.INITIAL_OFFSET
    for line in text.splitlines(keepends=FollowupProtocol.KEEP_LINE_ENDINGS):
        stripped = line.strip()
        marker = re.match(FollowupProtocol.FENCE_PATTERN, line)
        if marker:
            value = marker[FollowupProtocol.FENCE_GROUP]
            if fence is FollowupProtocol.NO_FENCE:
                fence = MarkdownFence(value[0], len(value))
            elif (
                value[0] == fence.marker
                and len(value) >= fence.length
                and not line[marker.end() :].strip()
            ):
                fence = FollowupProtocol.NO_FENCE
        elif fence is FollowupProtocol.NO_FENCE and line.startswith(OPEN):
            tail = text[offset:].strip()
            if tail.endswith(CLOSE):
                value = tail[len(OPEN) : -len(CLOSE)]
                if (
                    value.strip()
                    and len(value) <= FollowupProtocol.MAXIMUM_CHARACTERS
                    and FollowupProtocol.TAG_START not in value
                    and FollowupProtocol.TAG_END not in value
                    and all(ord(c) >= FollowupProtocol.MINIMUM_PRINTABLE for c in value)
                ):
                    return FollowupSplit(text[:offset].rstrip(), value.strip())
            if not final and CLOSE not in tail:
                return FollowupSplit(text[:offset].rstrip(), FollowupProtocol.EMPTY)
        elif (
            not final
            and fence is FollowupProtocol.NO_FENCE
            and line.startswith(FollowupProtocol.TAG_START)
            and OPEN.startswith(stripped)
            and not text[offset + len(line) :]
        ):
            return FollowupSplit(text[:offset].rstrip(), FollowupProtocol.EMPTY)
        offset += len(line)
    return FollowupSplit(text, FollowupProtocol.EMPTY)
