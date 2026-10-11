"""Pure localized display transformations, executed on the UI thread."""

from dataclasses import dataclass, replace
from datetime import datetime
from ..backend.approval import review_text

from ..model import ChatMessage, HubSnapshot, SessionSummary


@dataclass(frozen=True, slots=True)
class Presentation:
    sessions: tuple[SessionSummary, ...]
    messages: tuple[ChatMessage, ...]
    notice: str
    activity: str


def present_message(item: ChatMessage, t) -> ChatMessage:
    text = item.text
    if item.role == "assistant" and item.status == "failed":
        text = (text.rstrip() + "\n\n" if text.strip() else "") + t("response_failed")
    elif not text:
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
                cached = (item, present_message(item, t))
                self.transformed += 1
            retained[key] = cached
            messages.append(cached[1])
        self.context, self.items = context, retained
        return tuple(messages)


def present(snapshot: HubSnapshot, t, cache=None) -> Presentation:
    sessions = tuple(replace(item, status=t("queued", status=t.status(item.status), count=item.queued_count)) for item in snapshot.sessions)
    messages = cache.messages(snapshot, t) if cache else tuple(present_message(item, t) for item in snapshot.messages)
    # Keep the complete action reviewable with the existing output scrollbar
    # and tag viewer, even when it cannot fit above the answer field.
    for item in snapshot.questions:
        request = item["request"]
        if request.get("action"):
            text = item.get("review_text") or review_text(request)
            messages += (ChatMessage("assistant", text, id="interaction:" + request["id"]),)
    if not sessions:
        return Presentation((), messages, t("session_required"), "")
    notice = t("queued", status=t.status(snapshot.status), count=snapshot.queued_count)
    activity = ""
    run = snapshot.run
    if run:
        if run.active:
            activity = t("run_progress", status=t.status(run.status), engine=run.engine,
                         seconds=int(run.elapsed_seconds or 0), count=snapshot.queued_count)
            if run.steps:
                step = run.steps[-1]
                activity = t("phase", name=step.name, status=t.status(step.status))
        if run.error:
            notice = f"{t.status(run.status)}: {run.error}"
        elif run.status == "paused":
            notice = t("paused")
    if snapshot.status in ("waiting_input", "waiting_approval"):
        activity = t.status(snapshot.status)
        notice = t("queued", status=activity, count=snapshot.queued_count)
    if snapshot.storage == "memory":
        notice += " · " + t("memory")
    if snapshot.title_error:
        notice = t("title_failed", error=snapshot.title_error)
    if snapshot.model_required:
        notice = t("model_required")
    return Presentation(sessions, messages, notice, activity)
