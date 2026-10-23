"""Pure localized display transformations, executed on the UI thread."""

from hub.locales.keys import MessageKey, MessagePattern

from llm.core.models import MessageStatus, RunStatus
from dataclasses import dataclass, replace
from datetime import datetime

from ..model import ChatMessage, HubSnapshot, SessionSummary
from ..backend.execution.followup import split_followup
from .error_presentation import error_text, failure_text


@dataclass(frozen=True, slots=True)
class Presentation:
    sessions: tuple[SessionSummary, ...]
    messages: tuple[ChatMessage, ...]
    notice: str


def present_message(item: ChatMessage, t) -> ChatMessage:
    text = item.text
    if item.role == "assistant":
        text, _ = split_followup(text, final=item.status not in (MessageStatus.STREAMING, MessageStatus.COMMITTED))
    if item.role == "assistant" and not text and item.status in (MessageStatus.STREAMING, MessageStatus.COMMITTED):
        return replace(item, text="", phase=item.phase or "preparing")
    if item.role == "assistant" and item.status == MessageStatus.FAILED:
        text = t(MessageKey.RESPONSE_FAILED)
        if item.failure is not None:
            text += "\n\n" + failure_text(item.failure, t)
        item = replace(item, role="system", id=item.id + ":failure" if item.id else "",
                       tools=(), trace=(), phase="", activity="")
    elif not text and not item.tools:
        text = t(MessagePattern.status(item.status)) if item.status in (MessageStatus.INTERRUPTED, MessageStatus.CANCELLED, MessageStatus.PAUSED, MessageStatus.COMPLETED) else t(MessageKey.WAITING)
    return replace(item, text=text,
        time=datetime.fromisoformat(item.time).astimezone().strftime("%Y-%m-%d %H:%M:%S") if item.time else "")


class MessagePresentationCache:
    """Only retain projections for the currently selected conversation."""
    def __init__(self):
        self.context = None
        self.items = {}
        self.transformed = 0

    def messages(self, snapshot, t):
        context = (snapshot.project_id, snapshot.selected_id, id(t), t.icons)
        previous = self.items if context == self.context else {}
        retained, messages = {}, []
        for index, item in enumerate(snapshot.messages):
            key = item.id or index
            cached = previous.get(key)
            if cached is None or cached[0] != item:
                # A clock tick does not change Markdown/metadata parsing.
                if cached and replace(cached[0], elapsed_seconds=item.elapsed_seconds) == item:
                    projected = replace(cached[1], elapsed_seconds=item.elapsed_seconds)
                    partial = replace(cached[2], elapsed_seconds=item.elapsed_seconds) if cached[2] else None
                else:
                    projected, partial = present_message(item, t), None
                    if item.role == "assistant" and item.status == MessageStatus.FAILED and item.text.strip():
                        # Keep model output separate; parse it only when changed.
                        text, _ = split_followup(item.text, final=True)
                        partial = replace(item, text=text, failure=None)
                cached = (item, projected, partial)
                self.transformed += 1
            retained[key] = cached
            if cached[2] is not None:
                messages.append(cached[2])
            messages.append(cached[1])
        self.context, self.items = context, retained
        return tuple(messages)


def present(snapshot: HubSnapshot, t, cache=None) -> Presentation:
    sessions = tuple(replace(item, status=t(MessageKey.QUEUED, status=t.status(item.status), count=item.queued_count)) for item in snapshot.sessions)
    messages = (cache or MessagePresentationCache()).messages(snapshot, t)
    if not sessions:
        return Presentation((), messages, t(MessageKey.SESSION_REQUIRED))
    notice = t(MessageKey.QUEUED, status=t.status(snapshot.status), count=snapshot.queued_count)
    run = snapshot.run
    if run:
        if run.error:
            notice = f"{t.status(run.status)}: {run.error}"
        elif run.status == RunStatus.PAUSED:
            notice = t(MessageKey.PAUSED)
    if snapshot.status in (SessionSummary.WAITING_INPUT, SessionSummary.WAITING_APPROVAL):
        status = t.status(snapshot.status)
        if snapshot.questions:
            status += " · " + snapshot.questions[0]["request"]["title"]
        notice = t(MessageKey.QUEUED, status=status, count=snapshot.queued_count)
    if snapshot.storage == "memory":
        notice += " · " + t(MessageKey.MEMORY)
    if snapshot.title_error:
        notice = t(MessageKey.TITLE_FAILED, error=error_text(snapshot.title_error))
    if snapshot.model_required:
        notice = t(MessageKey.MODEL_REQUIRED)
    return Presentation(sessions, messages, notice)
