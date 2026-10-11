"""Optional response metadata; never a command or a persisted user request."""

import re

INSTRUCTION = (
    "Hub display protocol: when completing a useful response, optionally suggest ONE concise next "
    "request in the user's language. Write it as the user's next message, on a final separate line "
    "<hub-next>suggested request</hub-next>, outside code fences. Maximum 240 characters, one line. "
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
