"""Terminal glyphs used by Hub. Change visual symbols here."""

from types import MappingProxyType
from dataclasses import dataclass
from collections.abc import Mapping

SELECTED = "▶"
HEADER_MARK = "•"
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


@dataclass(frozen=True, slots=True)
class IconSet:
    """Immutable per-view glyphs; changing one Hub never changes another."""

    chat: str
    sessions: str
    reasoning: str
    waiting: str
    edit: str
    alerts: Mapping[str, str]
    statuses: Mapping[str, str]

    @property
    def values(self):
        return {"icon_sessions": self.sessions, "icon_reasoning": self.reasoning,
                "icon_waiting": self.waiting,
                **{"icon_" + key: value for key, value in self.statuses.items()}}


NERD = IconSet(HEADER_MARK, SESSIONS, "󰧑", "", "", ALERT, MappingProxyType({
    **STATUS, "running": "", "streaming": "", "failed": "",
}))
UNICODE = IconSet(HEADER_MARK, "☷", REASONING, WAITING, EDIT,
                  MappingProxyType({"error": "❌", "info": "ℹ", "warning": "⚠", "success": "✅"}), STATUS)


def for_style(style: str) -> IconSet:
    if style not in ("nerd", "unicode"):
        raise ValueError("icon_style must be 'nerd' or 'unicode'")
    return NERD if style == "nerd" else UNICODE
