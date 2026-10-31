"""Immediate local submission feedback, separate from durable chat history."""

from hub.model import ReasoningMessage

from hub.model import ResponseActivity

from hub.locales.keys import MessageKey

from llm.core.models import MessageStatus, MessageRole
from typing import NamedTuple
from prompt_toolkit.application.current import get_app
import asyncio
from dataclasses import replace
from time import monotonic

from ...model import HubSnapshot, ChatMessage, QuestionPresentation, SessionIdentity


class ResponseClock(NamedTuple):
    message_id: str
    elapsed_seconds: float
    observed_at: float


class ResumingResponse(NamedTuple):
    run_id: str
    anchor: str | None
    assistant: str | None


class PreparingResponse:
    ABSENT = None
    EMPTY_TEXT = ""
    MODEL_SEPARATOR = " • "
    AUTHOR_SEPARATOR = ", "
    DRAFT_ID = "draft:{session_id}"
    PREPARING_ID = "preparing:{session_id}"
    FOLLOW_TAIL = True
    ACTIVE_STATES = (MessageStatus.STREAMING, MessageStatus.COMMITTED)

    def __init__(self, view):
        self.view = view
        self.pending = {}
        self.started = {}
        self.drafts = {}
        self.authors = {}
        self.clocks = {}
        self.resuming = {}
        self._ticker = self.ABSENT
        self._painted_clock = self.ABSENT

    def visible(self):
        view = self.view
        return SessionIdentity.current(view).to_tuple() in self.pending

    def text(self):
        return (
            self.view.t(MessageKey.PREPARING_RESPONSE)
            + self.MODEL_SEPARATOR
            + self.view.model
        )

    def start(self, key, text=EMPTY_TEXT, *, resume_run_id=ABSENT, turn_id=ABSENT,
              recipients=ABSENT):
        self.pending[key] = self.ABSENT
        self.started[key] = monotonic()
        # Capture the actual recipients at submission, not a later sidebar
        # selection. This is local feedback, never durable Agent identity.
        self.authors[key] = self.AUTHOR_SEPARATOR.join(
            self.view.agent_titles.get(identifier, identifier)
            for identifier in (self.view.recipients if recipients is self.ABSENT else recipients)
        )
        if resume_run_id:
            snapshot = HubSnapshot.last_observed(self.view)
            anchor, assistant = turn_id, self.ABSENT
            if snapshot and key == (snapshot.project_id, snapshot.selected_id):
                preceding = self.ABSENT
                for message in snapshot.messages:
                    if message.role == MessageRole.USER:
                        preceding = message.id
                    if (
                        message.role == MessageRole.ASSISTANT
                        and message.run_id == resume_run_id
                    ):
                        anchor, assistant = anchor or preceding, message.id
                        break
            self.resuming[key] = ResumingResponse(resume_run_id, anchor, assistant)
        if text:
            self.drafts[key] = ChatMessage(
                MessageRole.USER.value,
                text,
                status=ChatMessage.PREPARING,
                id=self.DRAFT_ID.format(session_id=SessionIdentity(key).session_id),
                author=self.view.user_profile.display_name,
            )
        self.view.composer.buffer.suggestion = self.ABSENT
        self.paint()
        if (
            not resume_run_id
            and key == SessionIdentity.current(self.view).to_tuple()
            and self.view.general.auto_scroll
        ):
            self.view.output.set_follow_tail(self.FOLLOW_TAIL)

    async def _tick(self):
        while self.pending or self.clocks:
            await asyncio.sleep(self._next_clock_change())
            if self.view.visible and self._clock_label() != self._painted_clock:
                self.paint()

    def _next_clock_change(self):
        """Generate a signal at the next displayed second, not every frame."""
        key = SessionIdentity.current(self.view).to_tuple()
        now = monotonic()
        delays = [1.0]
        if key in self.pending:
            delays.append(1.0 - (now - self.started[key]) % 1.0)
        for clock in self.clocks.get(key, {}).values():
            delays.append(1.0 - (clock.elapsed_seconds + now - clock.observed_at) % 1.0)
        snapshot = HubSnapshot.last_observed(self.view)
        run = snapshot.run if snapshot and key == (snapshot.project_id, snapshot.selected_id) else None
        if run and run.active and run.retry and run.retry.retry_at is not None:
            remaining = run.retry.retry_at - now
            if remaining > 0:
                delays.append(remaining % 1.0 or 1.0)
        return max(self.view.theme.output_refresh_interval, min(delays))

    def _clock_label(self):
        """Only a changed displayed duration needs a timer-driven redraw.

        The locale owns duration precision. Submission/lifecycle changes still
        call paint directly; this never delays a backend observation.
        """
        key = SessionIdentity.current(self.view).to_tuple()
        now = monotonic()
        clocks = self.clocks.get(key, {})
        return (
            key,
            self.view.t.elapsed(now - self.started[key]) if key in self.pending else self.ABSENT,
            tuple((clock.message_id, self.view.t.elapsed(clock.elapsed_seconds + now - clock.observed_at))
                  for clock in clocks.values()),
            self._activity(key),
        )

    def _activity(self, key):
        snapshot = HubSnapshot.last_observed(self.view)
        run = (
            snapshot.run
            if snapshot and key == (snapshot.project_id, snapshot.selected_id)
            else self.ABSENT
        )
        from .retry_status import retry_text

        return retry_text(
            run.retry if run and run.active else self.ABSENT, self.view.t
        )

    def project(self, key, messages):
        messages = self.view.question.project(key, messages)
        clocks = dict(self.clocks.get(key, {}))
        activity = self._activity(key)
        updates = {}
        last = True
        now = monotonic()
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if (
                message.role == MessageRole.ASSISTANT
                and message.status in self.ACTIVE_STATES
            ):
                clock = clocks.pop(message.id, None)
                elapsed = (
                    clock.elapsed_seconds + now - clock.observed_at
                    if clock
                    else message.elapsed_seconds
                )
                updates[index] = replace(message, elapsed_seconds=elapsed,
                                         activity=activity if last else message.activity)
                last = False
                if not clocks:
                    break
        if updates:
            messages = tuple(updates.get(index, message) for index, message in enumerate(messages))
        if key in self.pending:
            if key in self.resuming:
                # Resume changes the existing response header, never its position
                # or partial body. Keep this bridge until durable output arrives.
                run_id, _, assistant = self.resuming[key]
                has_body = any(
                    m.role == MessageRole.ASSISTANT and m.run_id == run_id
                    for m in messages
                )
                prepared = []
                for message in messages:
                    if message.run_id == run_id and message.role == MessageRole.SYSTEM:
                        if has_body:
                            continue
                        message = replace(
                            message,
                            role=MessageRole.ASSISTANT.value,
                            id=assistant or message.id,
                            text=self.EMPTY_TEXT,
                        )
                    if (
                        message.role == MessageRole.ASSISTANT
                        and message.run_id == run_id
                    ):
                        message = replace(
                            message,
                            status=ChatMessage.PREPARING,
                            phase=ResponseActivity.RESUME_PREPARING,
                            activity=self.EMPTY_TEXT,
                        )
                    prepared.append(message)
                messages = tuple(prepared)
            else:
                if key in self.drafts:
                    messages = (*messages, self.drafts[key])
                messages = (
                    *messages,
                    ChatMessage(
                        MessageRole.ASSISTANT.value,
                        self.EMPTY_TEXT,
                        status=ChatMessage.PREPARING,
                        id=self.PREPARING_ID.format(
                            session_id=SessionIdentity(key).session_id
                        ),
                        elapsed_seconds=monotonic() - self.started[key],
                        author=self.authors.get(key, self.EMPTY_TEXT),
                    ),
                )
        return self.view.deleting.project(
            key, self.view.interrupting.project(key, messages)
        )

    def paint(self):
        self._painted_clock = self._clock_label()
        view = self.view
        session = view.sessions[view.selected]
        key = (HubSnapshot.project_identifier(view), session.id)
        view.output.update_messages(
            self.project(key, session.messages),
            auto_scroll=(
                self.FOLLOW_TAIL
                if key in self.pending
                and key not in self.resuming
                and view.general.auto_scroll
                else self.ABSENT
            ),
        )
        app = get_app()
        if (
            (self.pending or self.clocks)
            and app.is_running
            and (self._ticker is self.ABSENT or self._ticker.done())
        ):
            self._ticker = app.create_background_task(self._tick())
        app.invalidate()

    def finish(self, key):
        self.pending.pop(key, self.ABSENT)
        self.started.pop(key, self.ABSENT)
        self.drafts.pop(key, self.ABSENT)
        self.authors.pop(key, self.ABSENT)
        self.resuming.pop(key, self.ABSENT)
        self.paint()

    def accepted(self, key, message_id):
        self.drafts.pop(key, self.ABSENT)
        self.pending[key] = message_id
        snapshot = HubSnapshot.last_observed(self.view)
        if snapshot:
            self.observe(snapshot)

    def observe(self, snapshot):
        key = (snapshot.project_id, snapshot.selected_id)
        self.clocks = {
            k: v
            for k, v in self.clocks.items()
            if SessionIdentity(k).project_id == snapshot.project_id
            and any(s.id == SessionIdentity(k).session_id for s in snapshot.sessions)
        }
        observed_at = monotonic()
        clocks = {
            message.id: ResponseClock(message.id, message.elapsed_seconds, observed_at)
            for message in snapshot.messages
            if message.role == MessageRole.ASSISTANT and message.status in self.ACTIVE_STATES
            and message.elapsed_seconds is not None
        }
        if clocks:
            self.clocks[key] = clocks
        else:
            self.clocks.pop(key, self.ABSENT)
        if key in self.resuming:
            run_id, anchor, assistant = self.resuming[key]
            target = next(
                (
                    m
                    for m in snapshot.messages
                    if m.id == assistant and m.role == MessageRole.ASSISTANT
                ),
                self.ABSENT,
            )
            if target is self.ABSENT and anchor:
                start = next(
                    (i for i, m in enumerate(snapshot.messages) if m.id == anchor),
                    self.ABSENT,
                )
                if start is not self.ABSENT:
                    for message in snapshot.messages[start + 1 :]:
                        if message.role == MessageRole.USER:
                            break
                        if message.role == MessageRole.ASSISTANT:
                            target = message
                            break
            if any(
                QuestionPresentation.observe(q).run_id == run_id
                for q in snapshot.questions
            ) or (target and target.run_id and target.run_id != run_id):
                self.finish(key)
            return
        request_id = self.pending.get(key)
        if not request_id:
            return
        messages = snapshot.messages
        index = next(
            (i for i, message in enumerate(messages) if message.id == request_id),
            self.ABSENT,
        )
        if index is self.ABSENT:
            return
        request = messages[index]
        following = messages[index + 1 :]
        # Queue admission during another Run is already represented as queued.
        # For a new Run retain preparation until its own assistant is observed.
        if request.status == MessageStatus.CANCELLED or (
            request.status == MessageStatus.QUEUED
            and snapshot.run
            and snapshot.run.active
        ):
            self.finish(key)
        elif following and following[0].role in (
            MessageRole.ASSISTANT,
            ReasoningMessage.ROLE,
        ):
            self.finish(key)
