"""Attach steering observations to their admitted request, without rewriting history."""

from dataclasses import replace

from llm.core.continuations import ResumeRecord
from llm.core.instruction_records import InstructionMessageRecord
from llm.core.models import MessageRole
from llm.core.steering import is_instruction
from llm.services.runtime.requests import RequestAdmission

from ...model import InstructionDisplay


class UserInstructions:
    def __init__(self, records=()):
        self.parents, self.children = {}, {}
        instructions = [record for record in records if is_instruction(record)]
        if not instructions:
            return
        requests = {record.id: record for record in records
                    if record.role == MessageRole.USER and not is_instruction(record)}
        by_run = {run_id: record.id for record in requests.values()
                  for run_id in RequestAdmission.run_ids(record)}
        for record in instructions:
            owner = InstructionMessageRecord.from_message(record).input_message_id
            parent = owner if owner in requests else None
            seen = set()
            while parent is not None and parent not in seen:
                seen.add(parent)
                source = by_run.get(ResumeRecord.source_id(requests[parent].metadata))
                if source is None:
                    break
                parent = source
            if parent is not None:
                self.parents[record.id] = parent
                self.children.setdefault(parent, []).append(record.id)

    def attach(self, identifier, part, parts):
        if identifier in self.parents:
            # An incomplete/windowed projection must not silently lose a receipt.
            if parts.get(self.parents[identifier]):
                return ()
        children = self.children.get(identifier, ())
        if not children:
            return part
        instructions = tuple(
            InstructionDisplay(item.id, item.text, status=item.status, time=item.time)
            for child in children for item in parts.get(child, ())
        )
        return tuple(replace(item, instructions=instructions)
                     if item.id == identifier else item for item in part)

    def display(self, messages):
        if not self.parents:
            return messages
        parts = {item.id: (item,) for item in messages}
        return tuple(row for item in messages
                     for row in self.attach(item.id, (item,), parts))
