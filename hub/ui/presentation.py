"""Pure localized display transformations, executed on the UI thread."""

from dataclasses import dataclass, replace
from datetime import datetime
import json
import re

from ..model import ChatMessage, HubSnapshot, SessionSummary


@dataclass(frozen=True, slots=True)
class Presentation:
    sessions: tuple[SessionSummary, ...]
    messages: tuple[ChatMessage, ...]
    detail: str
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


def present(snapshot: HubSnapshot, t) -> Presentation:
    sessions = tuple(replace(item, status=t("queued", status=t.status(item.status), count=item.queued_count)) for item in snapshot.sessions)
    messages = tuple(present_message(item, t) for item in snapshot.messages)
    # Keep the complete action reviewable with the existing output scrollbar
    # and tag viewer, even when it cannot fit above the answer field.
    for item in snapshot.questions:
        request = item["request"]
        if request.get("action"):
            action = json.dumps(request["action"], ensure_ascii=False, indent=2)
            fence = "`" * max(3, max((len(part) for part in re.findall(r"`+", action)), default=0) + 1)
            text = request["title"] + "\n\n" + request.get("description", "") + f"\n\n{fence}json\n{action}\n{fence}"
            messages += (ChatMessage("assistant", text, id="interaction:" + request["id"]),)
    if not sessions:
        return Presentation((), messages, "", t("session_required"), "")
    detail = f"  {t('workspace')}\n  {snapshot.project_id}\n\n  {t('session')}\n  {snapshot.selected_id}\n"
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
            if not run.has_reasoning:
                detail += "\n" + t("no_reasoning")
        detail += f"\n\n  {t('run')} / {t.status(run.status)}\n  {run.id}\n\n  {t('steps')}\n"
        detail += "\n".join(f"  {t.status(step.status)} · {step.name}" for step in run.steps)
        if run.completion_count:
            detail += "\n\n" + t("completion_detail", model=run.model or "—", count=run.completion_count,
                                     tokens=run.tokens if run.tokens is not None else "—")
        if run.error:
            notice = f"{t.status(run.status)}: {run.error}"
            detail += f"\n\n  {run.error}"
        elif run.status == "paused":
            notice = t("paused")
    if snapshot.storage == "memory":
        notice += " · " + t("memory")
    if snapshot.title_error:
        notice = t("title_failed", error=snapshot.title_error)
    if snapshot.model_required:
        notice = t("model_required")
    return Presentation(sessions, messages, detail, notice, activity)
