"""Session-local ghost input, accepted explicitly with Right at end of input."""

from llm.core.models import MessageStatus, MessageRole
from ...model import SessionIdentity
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from ...backend.execution.followup import split_followup


class FollowupSuggestion(AutoSuggest):
    ABSENT = None
    EMPTY_TEXT = ""
    ACCEPTED = True
    NOT_ACCEPTED = False

    def __init__(self, view):
        self.view = view
        self.key = self.ABSENT
        self.text = self.EMPTY_TEXT
        self.source = self.ABSENT

    def refresh(self, key, messages):
        item = messages[-1] if messages else self.ABSENT
        source = (key, item)
        if source != self.source:
            self.source = source
            self.key = key
            self.text = (
                split_followup(item.text).suggestion
                if item
                and item.role == MessageRole.ASSISTANT
                and item.status == MessageStatus.COMPLETED
                else self.EMPTY_TEXT
            )
        self.update(self.view.composer.buffer)

    def bind(self, buffer):
        # PTK's automatic suggestion task runs on insertion, not deletion.
        # Observe all edits and cursor/completion changes without a model call.
        buffer.on_text_changed += self.update
        buffer.on_cursor_position_changed += self.update
        buffer.on_completions_changed += self.update

    def update(self, buffer):
        suggestion = self.get_suggestion(buffer, buffer.document)
        if (buffer.suggestion.text if buffer.suggestion else self.ABSENT) != (
            suggestion.text if suggestion else self.ABSENT
        ):
            buffer.suggestion = suggestion
            buffer.on_suggestion_set.fire()

    def get_suggestion(self, buffer, document):
        view = self.view
        key = SessionIdentity.current(view).to_tuple()
        if (
            key != self.key
            or not self.text
            or view.question.item is not self.ABSENT
            or view.settings_open
            or key in view.preparing.pending
            or (buffer.complete_state and buffer.complete_state.completions)
            or view.input_history.browsing
            or document.cursor_position != len(document.text)
        ):
            return self.ABSENT
        if self.text.startswith(document.text) and document.text != self.text:
            return Suggestion(self.text[len(document.text) :])
        return self.ABSENT

    def accept(self):
        buffer = self.view.composer.buffer
        suggestion = self.get_suggestion(buffer, buffer.document)
        if suggestion:
            buffer.insert_text(suggestion.text)
            return self.ACCEPTED
        return self.NOT_ACCEPTED
