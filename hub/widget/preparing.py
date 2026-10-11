"""Immediate local submission feedback, separate from durable chat history."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, Window
from prompt_toolkit.layout.controls import FormattedTextControl
import asyncio
from dataclasses import replace
from time import monotonic

from ..model import ChatMessage


class PreparingResponse:
    def __init__(self, view):
        self.view = view
        self.pending = {}
        self.started = {}
        self.clocks = {}
        self._ticker = None
        self.container = ConditionalContainer(Window(FormattedTextControl(self.text),
            height=1, style="class:hub.muted"), Condition(self.visible))

    def visible(self):
        view = self.view
        return (getattr(view, "project_id", ""), view.sessions[view.selected].id) in self.pending

    def text(self):
        return "  " + self.view.t("assistant") + " · " + self.view.t("preparing_response")

    def start(self, key):
        self.pending[key] = None
        self.started[key] = monotonic()
        self.view.composer.buffer.suggestion = None
        self.paint()
        if key == (getattr(self.view, "project_id", ""), self.view.sessions[self.view.selected].id) and self.view.general.auto_scroll:
            self.view.transcript.control.follow_tail = True

    async def _tick(self):
        while self.pending or self.clocks:
            await asyncio.sleep(self.view.theme.output_refresh_interval)
            if self.view.visible:
                self.paint()

    def project(self, key, messages):
        clock = self.clocks.get(key)
        if clock and messages and messages[-1].id == clock[0]:
            messages = (*messages[:-1], replace(messages[-1], elapsed_seconds=clock[1] + monotonic() - clock[2]))
        if key not in self.pending:
            return messages
        return (*messages, ChatMessage("assistant", "", status="preparing",
            id=f"preparing:{key[1]}", elapsed_seconds=monotonic() - self.started[key]))

    def paint(self):
        view = self.view
        session = view.sessions[view.selected]
        key = (getattr(view, "project_id", ""), session.id)
        control = view.transcript.control
        at_tail = not control.restoring and control.top_line + control._height >= len(control._lines) - 1
        control.messages = self.project(key, session.messages)
        if key in self.pending and view.general.auto_scroll and at_tail:
            control.follow_tail = True
        app = get_app()
        if (self.pending or self.clocks) and app.is_running and (self._ticker is None or self._ticker.done()):
            self._ticker = app.create_background_task(self._tick())
        app.invalidate()

    def finish(self, key):
        self.pending.pop(key, None)
        self.started.pop(key, None)
        self.paint()

    def accepted(self, key, message_id):
        self.pending[key] = message_id
        snapshot = getattr(self.view, "_last_snapshot", None)
        if snapshot:
            self.observe(snapshot)

    def observe(self, snapshot):
        key = (snapshot.project_id, snapshot.selected_id)
        self.clocks = {k: v for k, v in self.clocks.items()
                       if k[0] == snapshot.project_id and any(s.id == k[1] for s in snapshot.sessions)}
        last = snapshot.messages[-1] if snapshot.messages else None
        if last and last.role == "assistant" and last.status in ("streaming", "committed") and last.elapsed_seconds is not None:
            self.clocks[key] = (last.id, last.elapsed_seconds, monotonic())
        else:
            self.clocks.pop(key, None)
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
