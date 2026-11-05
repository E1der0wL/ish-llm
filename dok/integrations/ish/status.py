"""Read shell buffer status without changing its selection, text or key map."""
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.key_binding.vi_state import InputMode

from ...features.toolbar.editor import EditorSnapshot


class PromptStatus:
    VI_MODES = {
        InputMode.INSERT: "Vi insert", InputMode.NAVIGATION: "Vi normal",
        InputMode.REPLACE: "Vi replace", InputMode.REPLACE_SINGLE: "Vi replace once",
        InputMode.INSERT_MULTIPLE: "Vi insert multiple",
    }

    def __init__(self, prompt):
        self._prompt = prompt
        self._key = None
        self._snapshot = EditorSnapshot()

    def read(self) -> EditorSnapshot:
        prompt = self._prompt
        document = prompt.default_buffer.document
        selection = document.selection
        mode = (self.VI_MODES[prompt.app.vi_state.input_mode] if prompt.app.editing_mode == EditingMode.VI else "Emacs")
        encoding = prompt.encoder or ""
        key = (document.text, document.cursor_position, mode, encoding,
               (selection.original_cursor_position, selection.type) if selection else None)
        if key != self._key:
            self._key = key
            self._snapshot = EditorSnapshot(document.text, len(document.text), document.line_count,
                document.cursor_position_row + 1, document.cursor_position_col + 1,
                document.cursor_position + 1, mode, encoding,
                tuple(document.selection_ranges()) if selection else ())
        return self._snapshot
