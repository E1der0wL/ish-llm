"""Read-only projection through llm services; no scheduling or localized strings."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..model import ChatMessage, HubSnapshot, SessionSummary, RunSummary, StepSummary
from .engine_selection import model_name, requires_model, selected_engines


@dataclass(slots=True)
class SessionObservation:
    session_id: str
    record: Any
    runtime: Any
    run: Any
    handle: Any


class SnapshotReader:
    def __init__(self):
        self.cache = {}
        from .thinking import ThinkingPhases
        self.thinking = ThinkingPhases()

    def clear(self):
        self.cache.clear()

    async def run_display(self, session, run_id: str, *, run=None) -> tuple[str, float | None]:
        cached = self.cache.get(run_id)
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
        if run.started_at and (run.ended_at or str(run.status) == "running"):
            end = datetime.fromisoformat(run.ended_at) if run.ended_at else datetime.now(timezone.utc)
            elapsed = max(0.0, (end - datetime.fromisoformat(run.started_at)).total_seconds())
        result = reasoning, elapsed
        if not active:
            self.cache[run_id] = result
            if len(self.cache) > 1024:
                self.cache.pop(next(iter(self.cache)))
        return result

    async def messages(self, session, messages, author="", *, latest=None):
        visible = []
        for message in messages:
            role = str(message.role)
            if role not in ("user", "assistant"):
                continue
            elapsed = None
            if role == "assistant" and message.run_id:
                reasoning, elapsed = await self.run_display(session, message.run_id,
                    run=latest if latest is not None and latest.id == message.run_id else None)
                reasoning = self.thinking.visible(message.run_id, reasoning,
                    active=str(message.status) in ("streaming", "committed"), content=message.content)
                if reasoning:
                    visible.append(ChatMessage("reasoning", reasoning, id=message.id + ":reasoning"))
            status = str(message.metadata.get("steering", {}).get("status", message.status))
            timestamp = message.created_at if role == "user" else ""
            visible.append(ChatMessage(role, message.content or "", timestamp,
                                       status=status, id=message.id,
                                       author=author if role == "user" else "",
                                       elapsed_seconds=elapsed))
        return tuple(visible)

    async def read(self, project, sessions, selected_id, *, config, engines, title_errors):
        from llm.services.query import Query
        data = await project.aget_data()
        observations, summaries = [], []
        for identifier, session in tuple(sessions.items()):
            record = await session.aget_data()
            runtime = await session.run.astatus(queued_limit=0)
            latest = await session.run.alist(query=Query(descending=True, limit=1))
            handle = latest[0] if latest else None
            run = await handle.aget_data() if handle else None
            observations.append(SessionObservation(identifier, record, runtime, run, handle))
            summaries.append(SessionSummary(identifier, record.title, str(runtime.status), runtime.queued_count))
        selected = next((item for item in observations if item.session_id == selected_id), None)
        messages, summary = (), None
        session_config = selected.record.config if selected else None
        if selected:
            session = sessions[selected_id]
            messages = await self.messages(session, await session.aconversation(), config.user_profile.display_name,
                                           latest=selected.run)
            if selected.run:
                run = selected.run
                steps = await selected.handle.steps.alist()
                completions = run.metadata.get("completions", [])
                last = completions[-1] if completions else {}
                _, elapsed = await self.run_display(session, run.id, run=run)
                summary = RunSummary(run.id, str(run.status), run.engine, elapsed,
                    bool(selected.runtime.active_run_id),
                    tuple(StepSummary(step.name, str(step.status)) for step in steps),
                    any(item.get("reasoning_content") for item in completions), len(completions),
                    last.get("model") or config.model or "", last.get("usage", {}).get("total_tokens"),
                    str(run.error) if run.error else "")
        snapshot = HubSnapshot(
            project_id=data.id, project_title=data.title,
            model=model_name(engines, data.config, config.engine, session_config),
            storage=data.conversation_storage, sessions=tuple(summaries), selected_id=selected_id,
            messages=messages, run=summary,
            status=str(selected.runtime.status) if selected else "idle",
            queued_count=selected.runtime.queued_count if selected else 0,
            model_required=requires_model(engines, data.config, config.engine, session_config) if selected else False,
            title_error=title_errors.get(selected_id, ""),
            engines=selected_engines(data.config, engines.names()),
            file_root=str(config.file_root or project.paths.root), components=tuple(data.components))
        return snapshot, tuple(observations)
