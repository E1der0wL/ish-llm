"""Shared calendar field presentation, independent of the storage destination."""
from ...core.theme import ThemeRole
from ...rendering.message_form import MessageForm


class CalendarFormLayout:
    HEADER_ROWS = MessageForm.HEADER_ROWS
    TIME_GAP = 3
    TIME_PADDING = 2
    TIME_ARROW = ""
    PICKER_MARKER = " "

    @staticmethod
    def heading(day, cells, theme):
        return MessageForm.heading(day.isoformat() if day else "", cells, theme, background=ThemeRole.EDITOR_BG)
