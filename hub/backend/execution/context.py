"""Only completed prior turns enter new requests; checkpoint context stays intact."""

from llm.core.continuations import RestartRecord, ResumeRecord

from llm.services.history.turns import ConversationTurns
from .turn_values import HubTurnContext
from llm.core.models import Message, MessageRole, MessageStatus
from llm.core.policies import ContextMode
from llm.services.history.context import ConversationContextBuilder, ContextPolicy
from dataclasses import replace
from .followup import split_followup


class HubContextBuilder(ConversationContextBuilder):
    PARAGRAPH = "\n\n"
    UNSELECTED_POLICY = None

    def for_clone(self, messages):
        from .turns import TurnIndex, edit_source

        if not any(
            edit_source(m)
            or ResumeRecord.from_metadata(m.metadata)
            or RestartRecord.from_metadata(m.metadata)
            for m in messages
        ):
            return super().for_clone(messages)
        records = [m for m in messages if m.role in ConversationTurns.CONTEXT_ROLES]
        for turn in TurnIndex(messages).turns:
            active = [m for group in turn.active for m in group]
            records.append(ConversationTurns.request(active))
            assistants = [m for m in active if m.role == MessageRole.ASSISTANT]
            if assistants:
                records.append(
                    replace(
                        assistants[-1],
                        content=self.PARAGRAPH.join(m.content for m in assistants),
                    )
                )
        snapshot = super().for_clone(records)
        for message in snapshot:
            ResumeRecord.remove(message.metadata)
            RestartRecord.remove(message.metadata)
            HubTurnContext.remove(message.metadata)
        return snapshot

    def for_run(
        self,
        messages: list[Message],
        input_message_id: str,
        *,
        policy: ContextPolicy | None = UNSELECTED_POLICY
    ) -> tuple[Message, ...]:
        from .turns import TurnIndex

        index = TurnIndex(messages)
        current = index.by_id.get(input_message_id)
        records = [m for m in messages if m.role in ConversationTurns.CONTEXT_ROLES]
        for turn in index.turns:
            assistants = [m for m in turn.active[-1] if m.role == MessageRole.ASSISTANT]
            completed = bool(assistants) and all(
                m.status == MessageStatus.COMPLETED for m in assistants
            )
            if turn is current or completed:
                records.extend(m for group in turn.active for m in group)
        # Budget only after selecting complete logical turns. The current resume
        # lineage contains saved tool receipts, not unrelated previous requests.
        history = super().for_run(
            records, input_message_id, policy=ContextPolicy(mode=ContextMode.FULL.value)
        )
        kept = [
            (
                replace(item, content=split_followup(item.content).content)
                if item.role == MessageRole.ASSISTANT
                else item
            )
            for item in history[:-1]
        ]
        return self._select([*kept, history[-1]], policy)
