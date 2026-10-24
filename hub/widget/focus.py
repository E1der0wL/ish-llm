"""Keyboard focus rules shared by Hub inputs, dialogs and page navigation."""

from prompt_toolkit.application.current import get_app
from .input_contract import EditableControl


def focus_control(target) -> None:
    app = get_app()
    previous = app.layout.current_control
    app.layout.focus(target)
    current = app.layout.current_control
    if current is not previous and EditableControl.writable(current):
        current.buffer.exit_selection()
        current.buffer.cursor_position = len(current.buffer.text)
