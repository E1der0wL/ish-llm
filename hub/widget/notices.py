"""Non-interactive empty/error guidance shared by pages, lists and status bars.

Callers supply the known message and next action. This module never diagnoses,
retries, changes focus or turns explanatory text into an execution permission.
"""

from dataclasses import dataclass, replace
from prompt_toolkit.layout import HSplit
from prompt_toolkit.widgets import Label

from ..config.notifications import NotificationKind
from ..config.theme_style import ThemeSelector
from ..locales.keys import MessageKey
from .captions import Caption
from .section import Heading


@dataclass(frozen=True, slots=True)
class NoticeText:
    PARAGRAPH = '\n\n'
    SPACE = ' '
    title: str
    description: str = ""
    hint: str = ""

    def inline(self) -> str:
        return Caption(self.title, details=(self.description, self.hint)).render()

    def paragraphs(self) -> str:
        return NoticeText.PARAGRAPH.join(value for value in (self.title, self.description, self.hint) if value)

    def sidebar(self, key=""):
        from .list_presentation import ListPresentation
        return replace(ListPresentation(self.title, description=Caption(
            details=(self.description, self.hint)).render()).sidebar(key), selectable=False)

    @classmethod
    def error(cls, message, language):
        return cls(language.icons.alerts[NotificationKind.ERROR.value] + NoticeText.SPACE
                   + language(MessageKey.ERROR, error=message))


class Notice(HSplit):
    def __init__(self, title: str, description: str = "", hint: str = ""):
        self.text = NoticeText(title, description, hint)
        super().__init__([
            Heading(title),
            *[Label(value, style=ThemeSelector.MUTED.style)
              for value in (description, hint) if value],
        ])
