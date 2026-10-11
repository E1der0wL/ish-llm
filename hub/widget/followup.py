"""Session-local ghost input, accepted explicitly with Right at end of input."""

from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from ..backend.followup import split_followup


class FollowupSuggestion(AutoSuggest):
    def __init__(self, view):
        self.view = view
        self.key = None
        self.text = ""
        self.source = None

    def refresh(self, key, messages):
        item = messages[-1] if messages else None
        source = (key, item)
        if source != self.source:
            self.source = source
            self.key = key
            self.text = (split_followup(item.text)[1]
                         if item and item.role == "assistant" and item.status == "completed" else "")
        self.view.composer.buffer.suggestion = self.get_suggestion(
            self.view.composer.buffer, self.view.composer.buffer.document)

    def get_suggestion(self, buffer, document):
        view = self.view
        key = (getattr(view, "project_id", ""), view.sessions[view.selected].id)
        if (key != self.key or not self.text or view.question.item is not None or view.settings_open
                or key in view.preparing.pending or buffer.complete_state or view.input_history.browsing
                or document.cursor_position != len(document.text)):
            return None
        if self.text.startswith(document.text) and document.text != self.text:
            return Suggestion(self.text[len(document.text):])
        return None

    def accept(self):
        buffer = self.view.composer.buffer
        suggestion = self.get_suggestion(buffer, buffer.document)
        if suggestion:
            buffer.insert_text(suggestion.text)
            return True
        return False
