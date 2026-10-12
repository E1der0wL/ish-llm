"""Pure localized display transformations, executed on the UI thread."""

from dataclasses import dataclass, replace
from datetime import datetime

from ..model import ChatMessage, HubSnapshot, SessionSummary
from ..backend.followup import split_followup


@dataclass(frozen=True, slots=True)
class Presentation:
    sessions: tuple[SessionSummary, ...]
    messages: tuple[ChatMessage, ...]
    notice: str
    activity: str


def present_message(item: ChatMessage, t) -> ChatMessage:
    text = item.text
    if item.role == "assistant":
        text, _ = split_followup(text, final=item.status not in ("streaming", "committed"))
    if item.role == "assistant" and not text and item.status in ("streaming", "committed"):
        return replace(item, text="", phase=item.phase or "preparing")
    if item.role == "assistant" and item.status == "failed":
        text = (text.rstrip() + "\n\n" if text.strip() else "") + t("response_failed")
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
                projected = (replace(cached[1], elapsed_seconds=item.elapsed_seconds)
                    if cached and replace(cached[0], elapsed_seconds=item.elapsed_seconds) == item
                    else present_message(item, t))
                cached = (item, projected)
                self.transformed += 1
            retained[key] = cached
            messages.append(cached[1])
        self.context, self.items = context, retained
        return tuple(messages)


def present(snapshot: HubSnapshot, t, cache=None) -> Presentation:
    sessions = tuple(replace(item, status=t("queued", status=t.status(item.status), count=item.queued_count)) for item in snapshot.sessions)
    messages = cache.messages(snapshot, t) if cache else tuple(present_message(item, t) for item in snapshot.messages)
    if not sessions:
        return Presentation((), messages, t("session_required"), "")
    notice = t("queued", status=t.status(snapshot.status), count=snapshot.queued_count)
    activity = ""
    run = snapshot.run
    if run:
        if run.active:
            activity = t("run_progress", status=t.status(run.status), engine=run.engine,
                         seconds=int(run.elapsed_seconds or 0), count=snapshot.queued_count)
            if not run.steps and any(m.phase == "resuming" for m in snapshot.messages):
                activity = t.status("resuming")
            if run.steps:
                step = run.steps[-1]
                activity = f"{t.status(step.status)} · {step.name}"
                if step.kind == "llm" and step.status == "running":
                    phase = next((m.phase for m in reversed(snapshot.messages)
                                  if m.role == "assistant" and m.phase), "thinking")
                    activity = f"{t.status(phase if phase != 'preparing' else 'thinking')} · {snapshot.model}"
        if run.error:
            notice = f"{t.status(run.status)}: {run.error}"
        elif run.status == "paused":
            notice = t("paused")
    if snapshot.status in ("waiting_input", "waiting_approval"):
        activity = f"{t.status(snapshot.status)} · {snapshot.questions[0]['request']['title']}" if snapshot.questions else t.status(snapshot.status)
        notice = t("queued", status=activity, count=snapshot.queued_count)
    if snapshot.storage == "memory":
        notice += " · " + t("memory")
    if snapshot.title_error:
        notice = t("title_failed", error=snapshot.title_error)
    if snapshot.model_required:
        notice = t("model_required")
    return Presentation(sessions, messages, notice, activity)
