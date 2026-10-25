"""Logical conversation positions over durable edit and checkpoint attempts."""

from llm.core.continuations import RestartRecord, ResumeRecord

from .turn_values import HubTurnContext
from ...model import ReasoningMessage
from llm.core.models import MessageStatus
from dataclasses import dataclass, field, replace

from llm.services.history.turns import conversation_turns, ConversationTurns
from llm.services.runtime.requests import RequestAdmission
from .continuations import continued_messages

TURN_OPTION = HubTurnContext.KEY


def edit_source(message):
    return HubTurnContext.source(message)


@dataclass(slots=True)
class LogicalTurn:
    FIRST_ATTEMPT = 0

    attempts: list = field(default_factory=list)

    @property
    def initial(self):
        return ConversationTurns.request(self.attempts[self.FIRST_ATTEMPT])

    @property
    def active_request(self):
        return ConversationTurns.request(self.active[self.FIRST_ATTEMPT])

    @property
    def id(self):
        return self.initial.id

    @property
    def active(self):
        attempts = [
            group
            for index, group in enumerate(self.attempts)
            if index == self.FIRST_ATTEMPT
            or str(ConversationTurns.request(group).status) != MessageStatus.CANCELLED
        ]
        start = self.FIRST_ATTEMPT
        for index, group in enumerate(attempts):
            if (
                RestartRecord.from_metadata(ConversationTurns.request(group).metadata)
                or edit_source(ConversationTurns.request(group))
                and not ResumeRecord.from_metadata(
                    ConversationTurns.request(group).metadata
                )
            ):
                start = index
        return attempts[start:]

    @property
    def latest(self):
        return ConversationTurns.request(self.active[-1])


class TurnIndex:
    NO_TURN = None

    def __init__(self, records):
        self.records = records
        self.turns, self.by_id = [], {}
        by_run = {}
        for group in conversation_turns(records):
            request = ConversationTurns.request(group)
            parent = ResumeRecord.source_id(
                request.metadata
            ) or RestartRecord.source_id(request.metadata)
            turn = by_run.get(parent) or self.by_id.get(edit_source(request))
            if turn is self.NO_TURN:
                turn = LogicalTurn()
                self.turns.append(turn)
            turn.attempts.append(group)
            self.by_id[request.id] = turn
            for run_id in RequestAdmission.run_ids(request):
                by_run[run_id] = turn

    def display(self, messages):
        if not any(
            edit_source(m)
            or ResumeRecord.from_metadata(m.metadata)
            or RestartRecord.from_metadata(m.metadata)
            for m in self.records
        ):
            return continued_messages(self.records, messages)
        by_id = {message.id: message for message in messages}
        result = []
        for turn in self.turns:
            records = [m for group in turn.active for m in group]
            selected = [
                by_id[key]
                for m in records
                for key in (ReasoningMessage.identifier(m.id), m.id)
                if key in by_id
            ]
            visible = continued_messages(records, selected)
            first_request = turn.active_request
            result.extend(
                replace(m, id=turn.id) if m.id == first_request.id else m
                for m in visible
            )
        return tuple(result)
