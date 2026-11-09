"""Submit shortcut compositions through the existing primary prompt Buffer."""

from prompt_toolkit.buffer import ValidationState
from prompt_toolkit.document import Document

from ...features.shortcuts.config import Shortcut
from ...features.calendar.commands import CommandResult
from ...locales import Locale, Message


class ShortcutSubmission:
    def __init__(self, prompt, state, *, locale=Locale):
        self._prompt = prompt
        self._state = state
        self._locale = locale

    def _ready(self, *, empty=False) -> bool:
        app = self._prompt.app
        buffer = self._prompt.default_buffer
        return (app.is_running and not app.is_done
                and getattr(app.layout.current_control, "buffer", None) is buffer
                and self._state.can_submit(app.future) and (not empty or not buffer.text))

    def _submit(self, command: str) -> bool:
        app = self._prompt.app
        buffer = self._prompt.default_buffer
        self._state.enter_prompt(app.future)
        if not self._state.begin_submission():
            return False
        original = buffer.document
        completion = buffer.complete_state
        suggestion = buffer.suggestion
        try:
            buffer.save_to_undo_stack()
            buffer.set_document(Document(command, cursor_position=len(command)))
            buffer.validate_and_handle()
            if buffer.validation_state == ValidationState.INVALID and not app.is_done:
                error = buffer.validation_error
                buffer.set_document(original)
                buffer.complete_state = completion
                buffer.suggestion = suggestion
                self._state.finish_submission(accepted=False, error=error.message if error else self._locale().text(Message.INPUT_INVALID))
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

    def execute(self, shortcut: Shortcut) -> bool:
        if not shortcut.enabled or not self._ready():
            return False
        return self._submit(shortcut.compose(self._prompt.default_buffer.text))

    def execute_command(self, command: str) -> CommandResult:
        """A scheduled command never consumes or replaces an in-progress prompt."""
        shell = getattr(self._prompt, "interactive_shell", None)
        continuation = getattr(shell, "continuation_active", None)
        if continuation is not None and continuation.is_set():
            return CommandResult.SKIPPED
        if hasattr(shell, "command_done_event"):
            done = shell.command_done_event
            if done is None or not done.is_set():
                return CommandResult.SKIPPED
        if not self._ready(empty=True):
            return CommandResult.SKIPPED
        return CommandResult.SUBMITTED if self._submit(command) else CommandResult.FAILED

    def execute_lookup(self, command: str) -> bool:
        """An explicit popup action submits the displayed command through normal validation."""
        return self._ready() and self._submit(command)
