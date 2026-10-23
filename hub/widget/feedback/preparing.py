"""Immediate local submission feedback, separate from durable chat history."""

from hub.locales.keys import MessageKey

from llm.core.models import MessageStatus
from prompt_toolkit.application.current import get_app
import asyncio
from dataclasses import replace
from time import monotonic

from ...model import ChatMessage


class PreparingResponse:
    def __init__(self, view):
        self.view = view
        self.pending = {}
        self.started = {}
        self.drafts = {}
        self.clocks = {}
        self.resuming = {}
        self._ticker = None

    def visible(self):
        view = self.view
        return (getattr(view, "project_id", ""), view.sessions[view.selected].id) in self.pending

    def text(self):
        return self.view.t(MessageKey.PREPARING_RESPONSE) + " · " + self.view.model

    def start(self, key, text="", *, resume_run_id=None, turn_id=None):
        self.pending[key] = None
        self.started[key] = monotonic()
        if resume_run_id:
            snapshot = getattr(self.view, "_last_snapshot", None)
            anchor, assistant = turn_id, None
            if snapshot and key == (snapshot.project_id, snapshot.selected_id):
                preceding = None
                for message in snapshot.messages:
                    if message.role == "user":
                        preceding = message.id
                    if message.role == "assistant" and message.run_id == resume_run_id:
                        anchor, assistant = anchor or preceding, message.id
                        break
            self.resuming[key] = (resume_run_id, anchor, assistant)
        if text:
            self.drafts[key] = ChatMessage("user", text, status=ChatMessage.PREPARING, id=f"draft:{key[1]}",
                                          author=self.view.user_profile.display_name)
        self.view.composer.buffer.suggestion = None
        self.paint()
        if not resume_run_id and key == (getattr(self.view, "project_id", ""), self.view.sessions[self.view.selected].id) and self.view.general.auto_scroll:
            self.view.output.set_follow_tail(True)

    async def _tick(self):
        while self.pending or self.clocks:
            await asyncio.sleep(self.view.theme.output_refresh_interval)
            if self.view.visible:
                self.paint()

    def project(self, key, messages):
        messages = self.view.question.project(key, messages)
        clock = self.clocks.get(key)
        snapshot = getattr(self.view, "_last_snapshot", None)
        run = snapshot.run if snapshot and key == (snapshot.project_id, snapshot.selected_id) else None
        from .retry_status import retry_text
        activity = retry_text(run.retry if run and run.active else None, self.view.t)
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.role == "assistant" and message.status in (MessageStatus.STREAMING, MessageStatus.COMMITTED):
                elapsed = clock[1] + monotonic() - clock[2] if clock and message.id == clock[0] else message.elapsed_seconds
                messages = (*messages[:index], replace(message, elapsed_seconds=elapsed, activity=activity), *messages[index + 1:])
                break
        if key in self.pending:
            if key in self.resuming:
                # Resume changes the existing response header, never its position
                # or partial body. Keep this bridge until durable output arrives.
                run_id, _, assistant = self.resuming[key]
                has_body = any(m.role == "assistant" and m.run_id == run_id for m in messages)
                prepared = []
                for message in messages:
                    if message.run_id == run_id and message.role == "system":
                        if has_body:
                            continue
                        message = replace(message, role="assistant", id=assistant or message.id, text="")
                    if message.role == "assistant" and message.run_id == run_id:
                        message = replace(message, status=ChatMessage.PREPARING, phase="preparing", activity="")
                    prepared.append(message)
                messages = tuple(prepared)
            else:
                if key in self.drafts:
                    messages = (*messages, self.drafts[key])
                messages = (*messages, ChatMessage("assistant", "", status=ChatMessage.PREPARING,
                    id=f"preparing:{key[1]}", elapsed_seconds=monotonic() - self.started[key]))
        return self.view.deleting.project(key, self.view.interrupting.project(key, messages))

    def paint(self):
        view = self.view
        session = view.sessions[view.selected]
        key = (getattr(view, "project_id", ""), session.id)
        view.output.update_messages(self.project(key, session.messages),
                                    auto_scroll=True if key in self.pending and key not in self.resuming and view.general.auto_scroll else None)
        app = get_app()
        if (self.pending or self.clocks) and app.is_running and (self._ticker is None or self._ticker.done()):
            self._ticker = app.create_background_task(self._tick())
        app.invalidate()

    def finish(self, key):
        self.pending.pop(key, None)
        self.started.pop(key, None)
        self.drafts.pop(key, None)
        self.resuming.pop(key, None)
        self.paint()

    def accepted(self, key, message_id):
        self.drafts.pop(key, None)
        self.pending[key] = message_id
        snapshot = getattr(self.view, "_last_snapshot", None)
        if snapshot:
            self.observe(snapshot)

    def observe(self, snapshot):
        key = (snapshot.project_id, snapshot.selected_id)
        self.clocks = {k: v for k, v in self.clocks.items()
                       if k[0] == snapshot.project_id and any(s.id == k[1] for s in snapshot.sessions)}
        last = next((m for m in reversed(snapshot.messages) if m.role == "assistant"), None)
        if last and last.role == "assistant" and last.status in (MessageStatus.STREAMING, MessageStatus.COMMITTED) and last.elapsed_seconds is not None:
            self.clocks[key] = (last.id, last.elapsed_seconds, monotonic())
        else:
            self.clocks.pop(key, None)
        if key in self.resuming:
            run_id, anchor, assistant = self.resuming[key]
            target = next((m for m in snapshot.messages if m.id == assistant and m.role == "assistant"), None)
            if target is None and anchor:
                start = next((i for i, m in enumerate(snapshot.messages) if m.id == anchor), None)
                if start is not None:
                    for message in snapshot.messages[start + 1:]:
                        if message.role == "user":
                            break
                        if message.role == "assistant":
                            target = message
                            break
            if any(q["run_id"] == run_id for q in snapshot.questions) or (target and target.run_id and target.run_id != run_id):
                self.finish(key)
            return
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
        if request.status == MessageStatus.CANCELLED or (request.status == MessageStatus.QUEUED and snapshot.run and snapshot.run.active):
            self.finish(key)
        elif following and following[0].role in ("assistant", "reasoning"):
            self.finish(key)
