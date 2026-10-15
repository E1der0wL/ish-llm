"""Read-only projection through llm services; no scheduling or localized strings."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from time import monotonic

from ...model import ChatMessage, HubSnapshot, SessionSummary, RunSummary, TraceItem
from ..execution.trace import merge_trace, tool_trace
from ..execution.steps import display_steps
from ..execution.engine_selection import model_name, requires_model, selected_engines
from ..interactions.questions import waiting_status
from ..execution.stopping import display_run, STOPPED


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
        self.tool_history = {}
        self.trace_history = {}
        from ..execution.thinking import ThinkingPhases
        self.thinking = ThinkingPhases()

    def clear(self):
        self.cache.clear()
        self.observations.clear()
        self.tool_history.clear()
        self.trace_history.clear()

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
        from ..execution.thinking import reasoning_text
        completions = run.metadata.get("completions", [])
        reasoning = "\n\n".join(text for item in completions
                                if (text := reasoning_text(item.get("reasoning_content", ""))))
        self.trace_history[run_id] = tuple(TraceItem("thought:" + item.get("id", f"{run_id}:{index}"),
            "reasoning", text, started_at=item.get("started_at") or run.started_at or "")
            for index, item in enumerate(completions)
            if (text := reasoning_text(item.get("reasoning_content", ""))))
        if len(self.trace_history) > 1024:
            self.trace_history.pop(next(iter(self.trace_history)))
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

    async def messages(self, session, messages, author="", *, latest=None, latest_steps=(), stopped_runs=()):
        visible = []
        requests = {message.run_id: self.submissions.get(message.id, message.created_at) for message in messages
                    if str(message.role) == "user" and message.run_id and not message.metadata.get("steering")}
        for message in messages:
            role = str(message.role)
            if role not in ("user", "assistant"):
                continue
            elapsed = None
            if role == "assistant" and message.run_id:
                active = str(message.status) in ("streaming", "committed", "paused")
                reasoning, elapsed = await self.run_display(session, message.run_id,
                    run=latest if latest is not None and latest.id == message.run_id else None,
                    requested_at=requests.get(message.run_id))
                reasoning = self.thinking.visible(message.run_id, reasoning,
                    active=active, content=message.content)
                tools = ()
                if active:
                    if latest is not None and latest.id == message.run_id:
                        tools = tuple(step for step in display_steps(latest_steps) if step.kind == "tool")
                        if str(latest.status) == "paused":
                            self.tool_history[message.run_id] = tools
                    elif message.run_id in self.tool_history:
                        tools = self.tool_history[message.run_id]
                    elif session is not None:
                        try:
                            steps = await (await session.run.aload(message.run_id)).steps.alist()
                            tools = tuple(step for step in display_steps(steps) if step.kind == "tool")
                            if str(message.status) == "paused":
                                self.tool_history[message.run_id] = tools
                        except FileNotFoundError:
                            pass
                    if len(self.tool_history) > 1024:
                        self.tool_history.pop(next(iter(self.tool_history)))
                if reasoning or tools:
                    trace = merge_trace(self.thinking.trace(message.run_id,
                        self.trace_history.get(message.run_id, ())), tool_trace(tools))
                    visible.append(ChatMessage("reasoning", reasoning, id=message.id + ":reasoning", tools=tools,
                                               trace=trace))
            status = str(message.metadata.get("steering", {}).get("status", message.status))
            if status == "paused" and message.run_id in stopped_runs:
                status = "interrupted"
            phase = (self.thinking.activity(latest)
                     if role == "assistant" and latest is not None and latest.id == message.run_id
                     and status in ("streaming", "committed") else "")
            timestamp = message.created_at if role == "user" else ""
            visible.append(ChatMessage(role, message.content or "", timestamp,
                                       status=status, id=message.id,
                                       author=author if role == "user" else "",
                                       elapsed_seconds=elapsed, phase=phase, run_id=message.run_id or ""))
        from ..execution.turns import TurnIndex
        return TurnIndex(messages).display(visible)

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
                run = display_run(record, await handle.aget_data()) if handle else None
                pending = (tuple({"run_id": run.id, "live": False, "request": request.to_dict()}
                           for request in await handle.ainteractions(pending_only=True))
                           if run and str(run.status) == "paused" else ())
                item = SessionObservation(identifier, record, runtime, run, handle, pending)
                self.observations[identifier] = (monotonic(), item)
            observations.append(item)
            status = waiting_status(item.questions) if item.questions else str(item.runtime.status)
            if not item.runtime.active_run_id and item.run and item.run.id in item.record.metadata.get(STOPPED, {}):
                status = str(item.run.status)
            summaries.append(SessionSummary(identifier, item.record.title, status, item.runtime.queued_count))
        selected = next((item for item in observations if item.session_id == selected_id), None)
        messages, summary, questions = (), None, ()
        session_config = selected.record.config if selected else None
        if selected:
            session = sessions[selected_id]
            steps = await selected.handle.steps.alist() if selected.run else ()
            messages = await self.messages(session, await session.aconversation(), config.user_profile.display_name,
                                           latest=selected.run, latest_steps=steps,
                                           stopped_runs=selected.record.metadata.get(STOPPED, {}))
            if selected.run:
                run = selected.run
                if str(run.status) == "paused":
                    questions = selected.questions
                _, elapsed = await self.run_display(session, run.id, run=run)
                summary = RunSummary(run.id, str(run.status), run.engine, elapsed,
                    bool(selected.runtime.active_run_id),
                    display_steps(steps),
                    str(run.error) if run.error else "")
        snapshot = HubSnapshot(
            project_id=data.id, project_title=data.title,
            model=model_name(engines, data.config, config.engine, session_config),
            storage=data.conversation_storage, sessions=tuple(summaries), selected_id=selected_id,
            messages=messages, run=summary,
            status=next((s.status for s in summaries if s.id == selected_id), "idle"),
            queued_count=selected.runtime.queued_count if selected else 0,
            model_required=requires_model(engines, data.config, config.engine, session_config) if selected else False,
            title_error=title_errors.get(selected_id, ""),
            engines=selected_engines(data.config, engines.names()),
            file_root=str(config.file_root or project.paths.root), components=tuple(data.components), questions=questions)
        return snapshot, tuple(observations)
