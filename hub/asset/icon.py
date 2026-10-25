"""Terminal glyphs used by Hub. Change visual symbols here."""

from types import MappingProxyType
from enum import StrEnum
from dataclasses import dataclass, field, fields
from collections.abc import Mapping
from llm.core.models import SessionStatus, MessageStatus, RunStatus
from llm.core.steering import InstructionStatus
from ..config.notifications import NotificationKind
from ..model import ChatMessage, SessionSummary


class AuxiliaryIcon(StrEnum):
    HISTORY = "history"
    TAGS = "tags"
    PARTICIPANTS = "participants"
    FIND = "find"
    ATTACH = "attach"
    LOGS = "logs"
    SETTINGS = "settings"
    FILTER = "filter"
    JSON = "json"
    STOP = "stop"
    RESUME = "resume"
    SEND = "send"


class IconStyle(StrEnum):
    NERD = "nerd"
    UNICODE = "unicode"


class OutputIcon(StrEnum):
    CODE = "code"
    TABLE = "table"
    IMAGE = "image"
    DIFF = "diff"
    CHART = "chart"
    CARD = "card"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class IconPlaceholders:
    PREFIX = "icon_"
    sessions: str
    reasoning: str
    waiting: str
    tool: str
    question: str

    def to_dict(self, statuses):
        return {
            **{
                self.PREFIX + item.name: getattr(self, item.name)
                for item in fields(self)
            },
            **{self.PREFIX + key: value for key, value in statuses.items()},
        }


class IconContract:
    INVALID_STYLE = "icon_style must be 'nerd' or 'unicode'"


class IconGlyphs:
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
    ALERT = MappingProxyType(
        {
            NotificationKind.ERROR.value: "",
            NotificationKind.INFO.value: "",
            NotificationKind.WARNING.value: "",
            NotificationKind.SUCCESS.value: "",
        }
    )
    STATUS = MappingProxyType(
        {
            SessionStatus.IDLE.value: "○",
            RunStatus.RUNNING.value: "⚙",
            ChatMessage.PREPARING: "⚙",
            MessageStatus.QUEUED.value: "◷",
            MessageStatus.COMMITTED.value: "✓",
            MessageStatus.STREAMING.value: "✍",
            RunStatus.COMPLETED.value: "✓",
            RunStatus.INTERRUPTED.value: "■",
            RunStatus.CANCELLED.value: "✕",
            RunStatus.FAILED.value: "⚠",
            RunStatus.PAUSED.value: "Ⅱ",
            RunStatus.PENDING.value: "◷",
            InstructionStatus.APPLIED.value: "✓",
            InstructionStatus.UNAPPLIED.value: "○",
            InstructionStatus.PARTIALLY_APPLIED.value: "◐",
            SessionSummary.WAITING_APPROVAL: "◷",
        }
    )
    TOOL_NERD = ""
    QUESTION_NERD = ""
    VALUES = MappingProxyType(
        IconPlaceholders(
            SESSIONS, REASONING, WAITING, TOOL_NERD, QUESTION_NERD
        ).to_dict(STATUS)
    )

    OUTPUT_UNICODE = MappingProxyType(
        {
            OutputIcon.CODE.value: "⌘",
            OutputIcon.TABLE.value: "▦",
            OutputIcon.IMAGE.value: "▧",
            OutputIcon.DIFF.value: "±",
            OutputIcon.CHART.value: "▥",
            OutputIcon.CARD.value: "▤",
            OutputIcon.TEXT.value: "≡",
        }
    )
    OUTPUT_NERD = MappingProxyType(
        {
            OutputIcon.CODE.value: "",
            OutputIcon.TABLE.value: "",
            OutputIcon.IMAGE.value: "",
            OutputIcon.DIFF.value: "",
            OutputIcon.CHART.value: "",
            OutputIcon.CARD.value: "",
            OutputIcon.TEXT.value: "",
        }
    )
    AUXILIARY_UNICODE = MappingProxyType(
        {
            AuxiliaryIcon.HISTORY.value: "☷",
            AuxiliaryIcon.TAGS.value: "▤",
            AuxiliaryIcon.PARTICIPANTS.value: "👥",
            AuxiliaryIcon.FIND.value: "⌕",
            AuxiliaryIcon.ATTACH.value: "📎",
            AuxiliaryIcon.LOGS.value: "≡",
            AuxiliaryIcon.SETTINGS.value: "⚙",
            AuxiliaryIcon.FILTER.value: "⚲",
            AuxiliaryIcon.JSON.value: "⌘",
            AuxiliaryIcon.STOP.value: "■",
            AuxiliaryIcon.RESUME.value: "▶",
            AuxiliaryIcon.SEND.value: "↑",
        }
    )


SELECTED = IconGlyphs.SELECTED
HEADER_MARK = IconGlyphs.HEADER_MARK
SCROLL_UP = IconGlyphs.SCROLL_UP
SCROLL_DOWN = IconGlyphs.SCROLL_DOWN
SESSIONS = IconGlyphs.SESSIONS
REASONING = IconGlyphs.REASONING
WAITING = IconGlyphs.WAITING
PROMPT = IconGlyphs.PROMPT
RULE = IconGlyphs.RULE
EDIT = IconGlyphs.EDIT
SCROLL_TRACK = IconGlyphs.SCROLL_TRACK
SCROLL_THUMB = IconGlyphs.SCROLL_THUMB
ALERT = IconGlyphs.ALERT
STATUS = IconGlyphs.STATUS
TOOL_NERD = IconGlyphs.TOOL_NERD
QUESTION_NERD = IconGlyphs.QUESTION_NERD
VALUES = IconGlyphs.VALUES
OUTPUT_UNICODE = IconGlyphs.OUTPUT_UNICODE
OUTPUT_NERD = IconGlyphs.OUTPUT_NERD


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
    recipient: str = "✓"
    auxiliary: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType(dict(IconGlyphs.AUXILIARY_UNICODE))
    )

    def action(self, name):
        return self.auxiliary[name]

    def for_output(self, kind):
        # Late import keeps glyph assets independent of theme construction.
        from ..ui.output.model import OutputBlock

        return self.output.get(
            OutputBlock.icon_kind(kind), self.output[OutputIcon.TEXT.value]
        )

    @property
    def values(self):
        return IconPlaceholders(
            self.sessions, self.reasoning, self.waiting, self.tool, self.question
        ).to_dict(self.statuses)


class IconPalettes:
    NERD = IconSet(
        HEADER_MARK,
        SESSIONS,
        "",
        "",
        "",
        ALERT,
        MappingProxyType(
            {
                **STATUS,
                RunStatus.RUNNING.value: "",
                MessageStatus.STREAMING.value: "",
                RunStatus.FAILED.value: "",
                RunStatus.PAUSED.value: "",
                SessionSummary.WAITING_APPROVAL: "",
                ChatMessage.PREPARING: "",
            }
        ),
        tool=TOOL_NERD,
        question=QUESTION_NERD,
        output=OUTPUT_NERD,
        settings="",
        project="",
        collapse="",
        restart="",
        minimize="",
        dropdown="",
        auxiliary=MappingProxyType(
            {
                AuxiliaryIcon.HISTORY.value: "",
                AuxiliaryIcon.TAGS.value: "",
                AuxiliaryIcon.PARTICIPANTS.value: "",
                AuxiliaryIcon.FIND.value: "",
                AuxiliaryIcon.ATTACH.value: "",
                AuxiliaryIcon.LOGS.value: "",
                AuxiliaryIcon.SETTINGS.value: "",
                AuxiliaryIcon.FILTER.value: "",
                AuxiliaryIcon.JSON.value: "",
                AuxiliaryIcon.STOP.value: "",
                AuxiliaryIcon.RESUME.value: "",
                AuxiliaryIcon.SEND.value: "",
            }
        ),
    )
    UNICODE = IconSet(
        "•",
        "☷",
        REASONING,
        WAITING,
        EDIT,
        MappingProxyType(
            {
                NotificationKind.ERROR.value: "❌",
                NotificationKind.INFO.value: "ℹ",
                NotificationKind.WARNING.value: "⚠",
                NotificationKind.SUCCESS.value: "✅",
            }
        ),
        STATUS,
    )


NERD = IconPalettes.NERD
UNICODE = IconPalettes.UNICODE


def for_style(style: str) -> IconSet:
    if style not in (IconStyle.NERD, IconStyle.UNICODE):
        raise ValueError(IconContract.INVALID_STYLE)
    return NERD if style == IconStyle.NERD else UNICODE
