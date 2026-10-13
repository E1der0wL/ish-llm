"""Hub's model context excludes failed turns without deleting their history."""

from llm.core.models import Message, MessageRole, MessageStatus
from llm.core.steering import is_instruction
from llm.services.history.context import ConversationContextBuilder, ContextPolicy
from dataclasses import replace
from .followup import split_followup


class HubContextBuilder(ConversationContextBuilder):
    def for_clone(self, messages):
        from .turns import TurnIndex, TURN_OPTION, edit_source
        if not any(edit_source(m) or m.metadata.get("resume") for m in messages):
            return super().for_clone(messages)
        records = [m for m in messages if m.role in ("system", "developer")]
        for turn in TurnIndex(messages).turns:
            active = [m for group in turn.active for m in group]
            records.append(active[0])
            assistants = [m for m in active if m.role == MessageRole.ASSISTANT]
            if assistants:
                records.append(replace(assistants[-1], content="\n\n".join(m.content for m in assistants)))
        snapshot = super().for_clone(records)
        for message in snapshot:
            message.metadata.pop("resume", None)
            message.metadata.get("engine_options", {}).pop(TURN_OPTION, None)
        return snapshot

    def for_run(self, messages: list[Message], input_message_id: str, *,
                policy: ContextPolicy | None = None) -> tuple[Message, ...]:
        from .turns import TurnIndex, edit_source
        if any(edit_source(message) or message.metadata.get("resume") for message in messages):
            messages = TurnIndex(messages).context_records()
        # Let llm order queued turns and attach resumed steering first. Selection
        # budgets must apply after failed pairs have been removed. Clone history
        # deliberately stays intact, including records whose run_id was cleared.
        history = super().for_run(messages, input_message_id, policy=ContextPolicy(mode="full"))
        groups: list[list[Message]] = []
        for message in history[:-1]:
            if not groups or (message.role == MessageRole.USER and not is_instruction(message)):
                groups.append([])
            groups[-1].append(message)
        kept = [message for group in groups
                if not any(item.status == MessageStatus.FAILED for item in group)
                for message in group]
        kept = [replace(item, content=split_followup(item.content)[0])
                if item.role == MessageRole.ASSISTANT else item for item in kept]
        return self._select([*kept, history[-1]], policy)
