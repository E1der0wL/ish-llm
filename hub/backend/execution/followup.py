"""Optional response metadata; never a command or a persisted user request."""

import re

INSTRUCTION = (
    "Hub display protocol: optionally draft ONE useful next message for the USER to send to YOU. "
    "The speaker inside <hub-next> is the user, and its recipient is the assistant. "
    "Write a concrete request in the user's language, preferably an imperative. "
    "Never put your own question, offer, or request for permission to the user in this tag. "
    "Good: <hub-next>Add regression tests for this fix.</hub-next> / "
    "<hub-next>이 수정에 대한 회귀 테스트를 추가해줘.</hub-next>. "
    "Bad: <hub-next>Would you like me to add tests?</hub-next> / "
    "<hub-next>테스트를 추가해 드릴까요?</hub-next>. "
    "If your answer asks the user for missing information, preferences or approval, omit the tag; "
    "do not invent the user's answer or consent. Otherwise put the tag on a final separate line, "
    "outside code fences. Maximum 240 characters, one line. "
    "Omit it if no useful next step exists or the requested output format forbids extra text. "
    "This is an editable UI suggestion only; never execute it or treat it as user authorization."
)
OPEN, CLOSE = "<hub-next>", "</hub-next>"


def split_followup(text: str, *, final: bool = True) -> tuple[str, str]:
    """Only a trailing, standalone tag outside fenced code is metadata."""
    tail = text.rstrip()
    candidate = tail[tail.rfind("\n") + 1:]
    if not candidate.startswith(OPEN) and not (
            not final and candidate.startswith("<") and OPEN.startswith(candidate)):
        return text, ""
    # Ordinary streaming deltas never walk the whole Markdown document. Inspect
    # fences only when the final line actually looks like protocol metadata.
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            value = marker[1]
            if fence is None:
                fence = (value[0], len(value))
            elif value[0] == fence[0] and len(value) >= fence[1] and not line[marker.end():].strip():
                fence = None
        elif fence is None and line.startswith(OPEN):
            tail = text[offset:].strip()
            if tail.endswith(CLOSE):
                value = tail[len(OPEN):-len(CLOSE)]
                if (value.strip() and len(value) <= 240 and "<" not in value and ">" not in value
                        and all(ord(c) >= 32 for c in value)):
                    return text[:offset].rstrip(), value.strip()
            if not final and CLOSE not in tail:
                return text[:offset].rstrip(), ""
        elif not final and fence is None and line.startswith("<") and OPEN.startswith(stripped) and not text[offset + len(line):]:
            return text[:offset].rstrip(), ""
        offset += len(line)
    return text, ""
