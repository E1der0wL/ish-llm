"""Read-only projection through llm services; no scheduling or localized strings."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from time import monotonic

from ..model import ChatMessage, HubSnapshot, SessionSummary, RunSummary, StepSummary
from .engine_selection import model_name, requires_model, selected_engines
from .questions import waiting_status


@dataclass(slots=True)
class SessionObservation:
    session_id: str
    record: Any
    runtime: Any
    run: Any
    handle: Any
    questions: tuple = ()


class SnapshotReader:
    def __init__(self):
        self.cache = {}
        self.observations = {}
        self.submissions = {}
        from .thinking import ThinkingPhases
        self.thinking = ThinkingPhases()

    def clear(self):
        self.cache.clear()
        self.observations.clear()

    def invalidate(self, session_id):
        self.observations.pop(session_id, None)

    async def run_display(self, session, run_id: str, *, run=None, requested_at=None) -> tuple[str, float | None]:
        key = (run_id, requested_at)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        try:
            if run is None:
                run = await (await session.run.aload(run_id)).aget_data()
        except FileNotFoundError:
            return "", None  # Retention can remove old execution history.
        from .thinking import reasoning_text
        completions = run.metadata.get("completions", [])
        reasoning = reasoning_text(completions[-1].get("reasoning_content", "")) if completions else ""
        elapsed = None
        active = str(run.status) in ("pending", "running")
        started_at = requested_at or run.started_at
        if started_at and (run.ended_at or active):
            end = datetime.fromisoformat(run.ended_at) if run.ended_at else datetime.now(timezone.utc)
            elapsed = max(0.0, (end - datetime.fromisoformat(started_at)).total_seconds())
        result = reasoning, elapsed
        if not active:
            self.cache[key] = result
            if len(self.cache) > 1024:
                self.cache.pop(next(iter(self.cache)))
        return result

    async def messages(self, session, messages, author="", *, latest=None):
        visible = []
        requests = {message.run_id: self.submissions.get(message.id, message.created_at) for message in messages
                    if str(message.role) == "user" and message.run_id and not message.metadata.get("steering")}
        for message in messages:
            role = str(message.role)
            if role not in ("user", "assistant"):
                continue
            elapsed = None
            if role == "assistant" and message.run_id:
                reasoning, elapsed = await self.run_display(session, message.run_id,
                    run=latest if latest is not None and latest.id == message.run_id else None,
                    requested_at=requests.get(message.run_id))
                reasoning = self.thinking.visible(message.run_id, reasoning,
                    active=str(message.status) in ("streaming", "committed"), content=message.content)
                if reasoning:
                    visible.append(ChatMessage("reasoning", reasoning, id=message.id + ":reasoning"))
            status = str(message.metadata.get("steering", {}).get("status", message.status))
            phase = (self.thinking.activity(latest)
                     if role == "assistant" and latest is not None and latest.id == message.run_id
                     and status in ("streaming", "committed") else "")
            timestamp = message.created_at if role == "user" else ""
            visible.append(ChatMessage(role, message.content or "", timestamp,
                                       status=status, id=message.id,
                                       author=author if role == "user" else "",
                                       elapsed_seconds=elapsed, phase=phase))
        from .continuations import continued_messages
        return continued_messages(messages, visible)

    async def read(self, project, sessions, selected_id, *, config, engines, title_errors):
        from llm.services.query import Query
        data = await project.aget_data()
        observations, summaries = [], []
        for identifier, session in tuple(sessions.items()):
            cached = self.observations.get(identifier)
            if identifier != selected_id and cached and monotonic() - cached[0] < 2:
                item = cached[1]
            else:
                record = await session.aget_data()
                runtime = await session.run.astatus(queued_limit=0)
                latest = await session.run.alist(query=Query(descending=True, limit=1))
                handle = latest[0] if latest else None
                run = await handle.aget_data() if handle else None
                pending = (tuple({"run_id": run.id, "live": False, "request": request.to_dict()}
                           for request in await handle.ainteractions(pending_only=True))
                           if run and str(run.status) == "paused" else ())
                item = SessionObservation(identifier, record, runtime, run, handle, pending)
                self.observations[identifier] = (monotonic(), item)
            observations.append(item)
            status = waiting_status(item.questions) if item.questions else str(item.runtime.status)
            summaries.append(SessionSummary(identifier, item.record.title, status, item.runtime.queued_count))
        selected = next((item for item in observations if item.session_id == selected_id), None)
        messages, summary, questions = (), None, ()
        session_config = selected.record.config if selected else None
        if selected:
            session = sessions[selected_id]
            messages = await self.messages(session, await session.aconversation(), config.user_profile.display_name,
                                           latest=selected.run)
            if selected.run:
                run = selected.run
                if str(run.status) == "paused":
                    questions = selected.questions
                steps = await selected.handle.steps.alist()
                _, elapsed = await self.run_display(session, run.id, run=run)
                summary = RunSummary(run.id, str(run.status), run.engine, elapsed,
                    bool(selected.runtime.active_run_id),
                    tuple(StepSummary(step.name, str(step.status), str(step.kind)) for step in steps),
                    str(run.error) if run.error else "")
        snapshot = HubSnapshot(
            project_id=data.id, project_title=data.title,
            model=model_name(engines, data.config, config.engine, session_config),
            storage=data.conversation_storage, sessions=tuple(summaries), selected_id=selected_id,
            messages=messages, run=summary,
            status=(waiting_status(questions) if questions else str(selected.runtime.status)) if selected else "idle",
            queued_count=selected.runtime.queued_count if selected else 0,
            model_required=requires_model(engines, data.config, config.engine, session_config) if selected else False,
            title_error=title_errors.get(selected_id, ""),
            engines=selected_engines(data.config, engines.names()),
            file_root=str(config.file_root or project.paths.root), components=tuple(data.components), questions=questions)
        return snapshot, tuple(observations)
