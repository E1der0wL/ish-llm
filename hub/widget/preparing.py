"""Immediate local submission feedback, separate from durable chat history."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, Window
from prompt_toolkit.layout.controls import FormattedTextControl


class PreparingResponse:
    def __init__(self, view):
        self.view = view
        self.pending = {}
        self.container = ConditionalContainer(Window(FormattedTextControl(self.text),
            height=1, style="class:hub.muted"), Condition(self.visible))

    def visible(self):
        view = self.view
        return (getattr(view, "project_id", ""), view.sessions[view.selected].id) in self.pending

    def text(self):
        return "  " + self.view.t("assistant") + " · " + self.view.t("preparing_response")

    def start(self, key):
        self.pending[key] = None
        get_app().invalidate()

    def finish(self, key):
        self.pending.pop(key, None)
        get_app().invalidate()

    def accepted(self, key, message_id):
        self.pending[key] = message_id
        snapshot = getattr(self.view, "_last_snapshot", None)
        if snapshot:
            self.observe(snapshot)

    def observe(self, snapshot):
        key = (snapshot.project_id, snapshot.selected_id)
        request_id = self.pending.get(key)
        if not request_id:
            return
        messages = snapshot.messages
        index = next((i for i, message in enumerate(messages) if message.id == request_id), None)
        if index is None:
            return
        request = messages[index]
        following = messages[index + 1:]
        # Queue admission during another Run is already represented as queued.
        # For a new Run retain preparation until its own assistant is observed.
        if request.status == "cancelled" or (request.status == "queued" and snapshot.run and snapshot.run.active):
            self.finish(key)
        elif following and following[0].role in ("assistant", "reasoning"):
            self.finish(key)
