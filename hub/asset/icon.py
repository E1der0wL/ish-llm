"""Terminal glyphs used by Hub. Change visual symbols here."""

from types import MappingProxyType
from enum import StrEnum
from dataclasses import dataclass, field
from collections.abc import Mapping

class AuxiliaryIcon(StrEnum):
    HISTORY = 'history'
    TAGS = 'tags'
    FIND = 'find'
    ATTACH = 'attach'
    LOGS = 'logs'
    SETTINGS = 'settings'
    FILTER = 'filter'
    JSON = 'json'
    STOP = 'stop'
    RESUME = 'resume'
    SEND = 'send'


class IconStyle(StrEnum):
    NERD = "nerd"
    UNICODE = "unicode"


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
    collapse: str = "›"
    restart: str = "↻"
    minimize: str = "⊘"
    dropdown: str = "▾"
    auxiliary: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({
        AuxiliaryIcon.HISTORY.value: "☷", AuxiliaryIcon.TAGS.value: "▤", AuxiliaryIcon.FIND.value: "⌕", AuxiliaryIcon.ATTACH.value: "📎", AuxiliaryIcon.LOGS.value: "≡",
        AuxiliaryIcon.SETTINGS.value: "⚙", AuxiliaryIcon.FILTER.value: "⚲", AuxiliaryIcon.JSON.value: "⌘", AuxiliaryIcon.STOP.value: "■", AuxiliaryIcon.RESUME.value: "▶", AuxiliaryIcon.SEND.value: "↑"}))

    def action(self, name):
        return self.auxiliary[name]

    @property
    def values(self):
        return {"icon_sessions": self.sessions, "icon_reasoning": self.reasoning,
                "icon_waiting": self.waiting, "icon_tool": self.tool, "icon_question": self.question,
                **{"icon_" + key: value for key, value in self.statuses.items()}}


NERD = IconSet(HEADER_MARK, SESSIONS, "", "", "", ALERT, MappingProxyType({
    **STATUS, "running": "", "streaming": "", "failed": "",
    "paused": "", "waiting_approval": "", "preparing": "",
}), tool="", question="", output=OUTPUT_NERD, settings="", project="", collapse="",
    restart="", minimize="", dropdown="",
    auxiliary=MappingProxyType({AuxiliaryIcon.HISTORY.value: "", AuxiliaryIcon.TAGS.value: "", AuxiliaryIcon.FIND.value: "", AuxiliaryIcon.ATTACH.value: "",
                               AuxiliaryIcon.LOGS.value: "", AuxiliaryIcon.SETTINGS.value: "", AuxiliaryIcon.FILTER.value: "", AuxiliaryIcon.JSON.value: "",
                               AuxiliaryIcon.STOP.value: "", AuxiliaryIcon.RESUME.value: "", AuxiliaryIcon.SEND.value: ""}))
UNICODE = IconSet("•", "☷", REASONING, WAITING, EDIT,
                  MappingProxyType({"error": "❌", "info": "ℹ", "warning": "⚠", "success": "✅"}), STATUS)


def for_style(style: str) -> IconSet:
    if style not in (IconStyle.NERD, IconStyle.UNICODE):
        raise ValueError("icon_style must be 'nerd' or 'unicode'")
    return NERD if style == IconStyle.NERD else UNICODE
