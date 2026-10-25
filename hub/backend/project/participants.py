"""Application names and explicit Session invitations through public services."""

from llm.core.agents import AgentDefinition
from typing import Any, TYPE_CHECKING

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
    runtime: "HubRuntime", project_id: str, session_id: str, agent_id: str
) -> tuple[str, ...]:
    if HubEngineIdentity.internal(agent_id):
        raise ValueError(runtime.INTERNAL_RECIPIENT)
    project = await runtime.backend.projects.aload(project_id)
    await project.agents.aload(agent_id)
    session = await project.sessions.aload(session_id)
    record = await session.aget_data()
    participants = list(record.agent_ids)
    if agent_id not in participants:
        status = await session.run.astatus()
        if status.active_runs or status.queued_count or session_id in runtime.namer.tasks:
            raise ValueError(runtime.t(MessageKey.PARTICIPANTS_BUSY))
        participants = sorted((*participants, agent_id))
        await session.run.shutdown()
        try:
            await session.asave(agent_ids=participants)
        finally:
            if runtime.session_is_open(session_id):
                await session.run.start()
    runtime.snapshot_reader.invalidate(session_id)
    runtime.dirty.set()
    return tuple(participants)
