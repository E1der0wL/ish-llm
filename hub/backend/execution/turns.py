"""Logical conversation positions over durable edit and checkpoint attempts."""

from llm.core.continuations import RestartRecord, ResumeRecord

from llm.core.agents import AgentBinding
from llm.core.models import MessageStatus
from dataclasses import dataclass, field, replace

from llm.services.history.turns import conversation_turns
from llm.services.runtime.requests import RequestAdmission
from .continuations import continued_messages

TURN_OPTION = "_hub_turn"


def edit_source(message):
    value = message.metadata.get(AgentBinding.CONTEXT_FIELD, {}).get(TURN_OPTION, {})
    return value.get("source", "") if isinstance(value, dict) else ""


@dataclass(slots=True)
class LogicalTurn:
    attempts: list = field(default_factory=list)

    @property
    def id(self):
        return self.attempts[0][0].id

    @property
    def active(self):
        attempts = [group for index, group in enumerate(self.attempts)
                    if index == 0 or str(group[0].status) != MessageStatus.CANCELLED]
        start = 0
        for index, group in enumerate(attempts):
            if RestartRecord.from_metadata(group[0].metadata) or edit_source(group[0]) and not ResumeRecord.from_metadata(group[0].metadata):
                start = index
        return attempts[start:]

    @property
    def latest(self):
        return self.active[-1][0]


class TurnIndex:
    def __init__(self, records):
        self.records = records
        self.turns, self.by_id = [], {}
        by_run = {}
        for group in conversation_turns(records):
            request = group[0]
            parent = ResumeRecord.source_id(request.metadata) or RestartRecord.source_id(request.metadata)
            turn = by_run.get(parent) or self.by_id.get(edit_source(request))
            if turn is None:
                turn = LogicalTurn()
                self.turns.append(turn)
            turn.attempts.append(group)
            self.by_id[request.id] = turn
            for run_id in RequestAdmission.run_ids(request):
                by_run[run_id] = turn

    def display(self, messages):
        if not any(edit_source(m) or ResumeRecord.from_metadata(m.metadata) or RestartRecord.from_metadata(m.metadata) for m in self.records):
            return continued_messages(self.records, messages)
        by_id = {message.id: message for message in messages}
        result = []
        for turn in self.turns:
            records = [m for group in turn.active for m in group]
            selected = [by_id[key] for m in records for key in (m.id + ":reasoning", m.id) if key in by_id]
            visible = continued_messages(records, selected)
            first_request = turn.active[0][0]
            result.extend(replace(m, id=turn.id) if m.id == first_request.id else m for m in visible)
        return tuple(result)
