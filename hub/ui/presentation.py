"""Pure localized display transformations, executed on the UI thread."""

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
        text, _ = split_followup(text, final=item.status not in ("streaming", "committed"))
    if item.role == "assistant" and not text and item.status in ("streaming", "committed"):
        return replace(item, text="", phase=item.phase or "preparing")
    if item.role == "assistant" and item.status == "failed":
        text = t("response_failed")
        if item.failure is not None:
            text += "\n\n" + failure_text(item.failure, t)
        item = replace(item, role="system", id=item.id + ":failure" if item.id else "",
                       tools=(), trace=(), phase="", activity="")
    elif not text and not item.tools:
        text = t("status_" + item.status) if item.status in ("interrupted", "cancelled", "paused", "completed") else t("waiting")
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
                    if item.role == "assistant" and item.status == "failed" and item.text.strip():
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
    sessions = tuple(replace(item, status=t("queued", status=t.status(item.status), count=item.queued_count)) for item in snapshot.sessions)
    messages = (cache or MessagePresentationCache()).messages(snapshot, t)
    if not sessions:
        return Presentation((), messages, t("session_required"))
    notice = t("queued", status=t.status(snapshot.status), count=snapshot.queued_count)
    run = snapshot.run
    if run:
        if run.error:
            notice = f"{t.status(run.status)}: {run.error}"
        elif run.status == "paused":
            notice = t("paused")
    if snapshot.status in ("waiting_input", "waiting_approval"):
        status = t.status(snapshot.status)
        if snapshot.questions:
            status += " · " + snapshot.questions[0]["request"]["title"]
        notice = t("queued", status=status, count=snapshot.queued_count)
    if snapshot.storage == "memory":
        notice += " · " + t("memory")
    if snapshot.title_error:
        notice = t("title_failed", error=error_text(snapshot.title_error))
    if snapshot.model_required:
        notice = t("model_required")
    return Presentation(sessions, messages, notice)
