"""Submit shortcut compositions through the existing primary prompt Buffer."""

from prompt_toolkit.buffer import ValidationState
from prompt_toolkit.document import Document

from .shortcuts import Shortcut


class ShortcutSubmission:
    def __init__(self, prompt, state):
        self._prompt = prompt
        self._state = state

    def execute(self, shortcut: Shortcut) -> bool:
        app = self._prompt.app
        buffer = self._prompt.default_buffer
        if (not shortcut.enabled or not app.is_running or app.is_done
                or getattr(app.layout.current_control, "buffer", None) is not buffer):
            return False
        self._state.enter_prompt(app.future)
        if not self._state.begin_submission():
            return False
        original = buffer.document
        completion = buffer.complete_state
        suggestion = buffer.suggestion
        try:
            command = shortcut.compose(original.text)
            buffer.save_to_undo_stack()
            buffer.set_document(Document(command, cursor_position=len(command)))
            buffer.validate_and_handle()
            if buffer.validation_state == ValidationState.INVALID and not app.is_done:
                error = buffer.validation_error
                buffer.set_document(original)
                buffer.complete_state = completion
                buffer.suggestion = suggestion
                self._state.finish_submission(accepted=False, error=error.message if error else "입력 검증 실패")
                return False
            # An accept handler may deliberately keep the editor open. Do not replay it.
            self._state.finish_submission(accepted=True)
            return True
        except Exception as exc:
            if app.is_done:
                self._state.finish_submission(accepted=True, error=str(exc))
            else:
                buffer.set_document(original)
                buffer.complete_state = completion
                buffer.suggestion = suggestion
                self._state.finish_submission(accepted=False, error=str(exc))
            return False
        finally:
            app.invalidate()
