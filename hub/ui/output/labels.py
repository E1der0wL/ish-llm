"""Compact, cell-aware labels for output objects and conversation anchors."""

import json
import re

from prompt_toolkit.utils import get_cwidth


def ellipsize(text: str, width: int) -> str:
    """Collapse whitespace and clip by terminal cells, without scanning huge bodies."""
    width = max(0, width)
    chars, used, pending_space = [], 0, False
    for char in text:
        if char.isspace():
            pending_space = bool(chars)
            continue
        if not char.isprintable():
            continue
        for token in ((" ", char) if pending_space else (char,)):
            size = get_cwidth(token)
            if used + size > width:
                while chars and used + 1 > width:
                    used -= get_cwidth(chars.pop())
                return "".join(chars).rstrip() + ("…" if width else "")
            chars.append(token)
            used += size
        pending_space = False
    return "".join(chars)


def block_title(block) -> str:
    attrs = dict(block.attributes)
    explicit = attrs.get("title") or attrs.get("alt")
    if explicit and explicit.strip():
        return explicit
    text = block.text.strip()
    if block.kind == "code":
        text = re.sub(r"^\s*(?:`{3,}|~{3,})[^\n]*\n", "", text)
        text = re.sub(r"\n\s*(?:`{3,}|~{3,})\s*$", "", text)
    elif block.kind == "table":
        text = text.partition("\n")[0].strip("| ").replace("|", "·")
    elif block.kind in ("hub-table", "hub-chart", "hub-card"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                text = str(data.get("title") or " · ".join(map(str, data.get("columns", ()))) or text)
        except (ValueError, TypeError):
            pass
    if not text:
        text = attrs.get("src", "")
    return ellipsize(text.partition("\n")[0], 160) or "…"
