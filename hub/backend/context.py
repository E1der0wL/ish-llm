"""Hub's model context excludes failed turns without deleting their history."""

from llm.core.models import Message, MessageRole, MessageStatus
from llm.core.steering import is_instruction
from llm.services.history.context import ConversationContextBuilder, ContextPolicy


class HubContextBuilder(ConversationContextBuilder):
    def for_run(self, messages: list[Message], input_message_id: str, *,
                policy: ContextPolicy | None = None) -> tuple[Message, ...]:
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
        return self._select([*kept, history[-1]], policy)
