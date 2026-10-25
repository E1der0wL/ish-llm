"""Group requests and their responses by Run, including interleaved queued input."""

from llm.core.steering import is_instruction
from llm.core.models import MessageRole
from llm.core.model_protocol import ModelRole
from llm.services.runtime.requests import RequestAdmission


class ConversationTurns:
    NO_GROUP = None
    NO_INDEX = None
    CONTEXT_ROLES = (MessageRole.SYSTEM.value, ModelRole.DEVELOPER.value)
    MISSING_TURN = "Conversation turn does not exist"

    @classmethod
    def request(cls, group):
        return group[0]

    @classmethod
    def group(cls, messages):
        groups, by_run = [], {}
        for message in messages:
            if message.conversation_deleted:
                continue
            if message.role == MessageRole.USER and not is_instruction(message):
                group = [message]
                groups.append(group)
                for run_id in RequestAdmission.run_ids(message):
                    by_run[run_id] = group
        by_id = {cls.request(group).id: group for group in groups}
        current = ConversationTurns.NO_GROUP
        for message in messages:
            if message.conversation_deleted:
                continue
            if message.id in by_id:
                current = by_id[message.id]
            else:
                group = by_run.get(message.run_id) if message.run_id else current
                if group is not ConversationTurns.NO_GROUP:
                    group.append(message)
        return groups

    @classmethod
    def through(cls, messages, request_id):
        groups = conversation_turns(messages)
        index = next(
            (i for i, group in enumerate(groups) if cls.request(group).id == request_id), ConversationTurns.NO_INDEX
        )
        if index is ConversationTurns.NO_INDEX:
            raise ValueError(ConversationTurns.MISSING_TURN)
        selected = {message.id for group in groups[: index + 1] for message in group}
        return [m for m in messages if m.id in selected or m.role in ConversationTurns.CONTEXT_ROLES]


def conversation_turns(messages):
    return ConversationTurns.group(messages)


def through_turn(messages, request_id):
    return ConversationTurns.through(messages, request_id)
