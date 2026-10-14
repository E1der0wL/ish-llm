"""Terminal glyphs used by Hub. Change visual symbols here."""

from types import MappingProxyType
from dataclasses import dataclass, field
from collections.abc import Mapping

SELECTED = "▶"
HEADER_MARK = ""
SCROLL_UP = "△"
SCROLL_DOWN = "▽"
SESSIONS = ""
REASONING = "◌"
WAITING = "⏳"
PROMPT = "›"
RULE = "─"
EDIT = "✎"
SCROLL_TRACK = " "
SCROLL_THUMB = " "
ALERT = MappingProxyType({"error": "", "info": "", "warning": "", "success": ""})
STATUS = MappingProxyType({
    "idle": "○", "running": "⚙", "preparing": "⚙", "queued": "◷", "committed": "✓",
    "streaming": "✍", "completed": "✓", "interrupted": "■", "cancelled": "✕",
    "failed": "⚠", "paused": "Ⅱ", "pending": "◷", "applied": "✓",
    "unapplied": "○", "partially_applied": "◐",
    "waiting_approval": "◷",
})
VALUES = MappingProxyType({"icon_sessions": SESSIONS, "icon_reasoning": REASONING,
                          "icon_waiting": WAITING, "icon_tool": "", "icon_question": "",
                          **{"icon_" + key: value for key, value in STATUS.items()}})

OUTPUT_UNICODE = MappingProxyType({"code": "⌘", "table": "▦", "image": "▧",
                                  "diff": "±", "chart": "▥", "card": "▤", "text": "≡"})
OUTPUT_NERD = MappingProxyType({"code": "", "table": "", "image": "",
                               "diff": "", "chart": "", "card": "", "text": ""})


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
    tool: str = "⚒"
    question: str = "?"
    output: Mapping[str, str] = field(default_factory=lambda: OUTPUT_UNICODE)
    settings: str = "⚙"
    project: str = "●"

    @property
    def values(self):
        return {"icon_sessions": self.sessions, "icon_reasoning": self.reasoning,
                "icon_waiting": self.waiting, "icon_tool": self.tool, "icon_question": self.question,
                **{"icon_" + key: value for key, value in self.statuses.items()}}


NERD = IconSet(HEADER_MARK, SESSIONS, "", "", "", ALERT, MappingProxyType({
    **STATUS, "running": "", "streaming": "", "failed": "",
    "paused": "", "waiting_approval": "", "preparing": "",
}), tool="", question="", output=OUTPUT_NERD, settings="", project="")
UNICODE = IconSet("•", "☷", REASONING, WAITING, EDIT,
                  MappingProxyType({"error": "❌", "info": "ℹ", "warning": "⚠", "success": "✅"}), STATUS)


def for_style(style: str) -> IconSet:
    if style not in ("nerd", "unicode"):
        raise ValueError("icon_style must be 'nerd' or 'unicode'")
    return NERD if style == "nerd" else UNICODE
