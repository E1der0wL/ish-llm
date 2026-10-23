"""Choose a local file path without reading its contents on the UI thread."""

from hub.locales.keys import MessageKey

from pathlib import Path

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit
from prompt_toolkit.widgets import Label

from .path_input import PathInput


def attach_file(view):
    root = Path(view.file_root)
    field = PathInput(root)
    error = Label("")
    body = HSplit([Label(view.t(MessageKey.ATTACH_HINT)), field, error])

    def validate():
        if not field.text.strip():
            error.text = view.t(MessageKey.ATTACH_REQUIRED)
            return False
        return True

    def accept():
        path = Path(field.text.strip()).expanduser()
        if not path.is_absolute():
            path = root / path
        # A quoted path keeps whitespace and line breaks unambiguous to the model.
        import json
        buffer = view.composer.buffer
        prefix = " " if buffer.cursor_position and not buffer.document.char_before_cursor.isspace() else ""
        buffer.insert_text(prefix + json.dumps(str(path), ensure_ascii=False) + " ")
        view._focus_composer()

    view.open_dialog(view.t(MessageKey.ATTACH_TITLE), body, accept, field, validate=validate)

    def entered(_):
        if validate():
            view.close_dialog()
            accept()
        get_app().invalidate()
        return True

    field.buffer.accept_handler = entered
