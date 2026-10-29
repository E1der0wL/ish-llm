"""Application names and explicit Session invitations through public services."""

from llm.core.agents import AgentDefinition
from typing import Any, TYPE_CHECKING
from collections.abc import Sequence

from ..execution.engine_selection import HubEngineIdentity
from ...locales.keys import MessageKey

if TYPE_CHECKING:
    from ..runtime import HubRuntime


class AgentProfile:
    TITLE = "title"
    INVALID_METADATA = "Agent metadata must be a JSON object."

    @classmethod
    def title(cls, identifier: str, definition: dict[str, Any]) -> str:
        title = definition.get(AgentDefinition.Field.METADATA, {}).get(cls.TITLE)
        return title.strip() if isinstance(title, str) and title.strip() else identifier

    @classmethod
    def rename(cls, definition: dict[str, Any], title: str) -> dict[str, Any]:
        metadata = definition.setdefault(AgentDefinition.Field.METADATA, {})
        if not isinstance(metadata, dict):
            raise ValueError(cls.INVALID_METADATA)
        metadata[cls.TITLE] = title.strip()
        return definition


async def invite(
    runtime: "HubRuntime", project_id: str, session_id: str, agent_id: str | Sequence[str]
) -> tuple[str, ...]:
    """Validate the complete selection, then persist membership in one save."""
    if not isinstance(agent_id, (str, list, tuple)):
        raise TypeError("Agent invitation requires an ID or a list of IDs")
    requested = [agent_id] if isinstance(agent_id, str) else list(agent_id)
    if not requested or any(not isinstance(value, str) or not value for value in requested):
        raise ValueError(runtime.t(MessageKey.PARTICIPANTS_REQUIRED))
    selected = tuple(dict.fromkeys(requested))
    if any(HubEngineIdentity.internal(value) for value in selected):
        raise ValueError(runtime.INTERNAL_RECIPIENT)
    project = await runtime.backend.projects.aload(project_id)
    for identifier in selected:
        await project.agents.aload(identifier)
    session = await project.sessions.aload(session_id)
    record = await session.aget_data()
    participants = list(record.agent_ids)
    if not set(selected).issubset(participants):
        status = await session.run.astatus()
        if status.active_runs or status.queued_count or session_id in runtime.namer.tasks:
            raise ValueError(runtime.t(MessageKey.PARTICIPANTS_BUSY))
        participants = sorted(set(participants).union(selected))
        await session.run.shutdown()
        try:
            await session.asave(agent_ids=participants)
        finally:
            if runtime.session_is_open(session_id):
                await session.run.start()
    runtime.snapshot_reader.invalidate(session_id)
    runtime.dirty.set()
    return tuple(participants)


async def change(runtime, project_id, session_id, action, identifier, value):
    from .participant_groups import ParticipantGroups
    project = await runtime.backend.projects.aload(project_id)
    session = await project.sessions.aload(session_id)
    status = await session.run.astatus()
    if status.active_runs or status.queued_count or session_id in runtime.namer.tasks:
        raise ValueError(runtime.t(MessageKey.PARTICIPANTS_BUSY))
    await session.run.shutdown()
    try:
        record = await session.aget_data()
        metadata, participants = ParticipantGroups.change(
            record.metadata, record.agent_ids, action, identifier, value)
        await session.asave(metadata=metadata, agent_ids=participants)
    finally:
        if runtime.session_is_open(session_id):
            await session.run.start()
    runtime.snapshot_reader.invalidate(session_id)
    runtime.dirty.set()
    return True
