"""Terminal glyphs used by Hub. Change visual symbols here."""

from types import MappingProxyType

SELECTED = "▶"
SCROLL_UP = "△"
SCROLL_DOWN = "▽"
SESSIONS = ""
REASONING = "💭"
WAITING = "⏳"
PROMPT = "›"
RULE = "─"
EDIT = "✎"
SCROLL_TRACK = " "
SCROLL_THUMB = " "
ALERT = MappingProxyType({"error": "", "info": "", "warning": "", "success": ""})
STATUS = MappingProxyType({
    "idle": "○", "running": "⚙", "queued": "◷", "committed": "✓",
    "streaming": "✍", "completed": "✓", "interrupted": "■", "cancelled": "✕",
    "failed": "⚠", "paused": "Ⅱ", "pending": "◷", "applied": "✓",
    "unapplied": "○", "partially_applied": "◐",
})
VALUES = MappingProxyType({"icon_sessions": SESSIONS, "icon_reasoning": REASONING,
                          "icon_waiting": WAITING, **{"icon_" + key: value for key, value in STATUS.items()}})
