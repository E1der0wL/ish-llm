"""Group requests and their responses by Run, including interleaved queued input."""

from llm.core.steering import is_instruction
from llm.services.runtime.requests import RequestAdmission


def conversation_turns(messages):
    groups, by_run = [], {}
    for message in messages:
        if message.conversation_deleted:
            continue
        if message.role == "user" and not is_instruction(message):
            group = [message]
            groups.append(group)
            for run_id in RequestAdmission.run_ids(message):
                by_run[run_id] = group
    by_id = {group[0].id: group for group in groups}
    current = None
    for message in messages:
        if message.conversation_deleted:
            continue
        if message.id in by_id:
            current = by_id[message.id]
        else:
            group = by_run.get(message.run_id) if message.run_id else current
            if group is not None:
                group.append(message)
    return groups


def through_turn(messages, request_id):
    groups = conversation_turns(messages)
    index = next((i for i, group in enumerate(groups) if group[0].id == request_id), None)
    if index is None:
        raise ValueError("Conversation turn does not exist")
    selected = {message.id for group in groups[:index + 1] for message in group}
    return [m for m in messages if m.id in selected or m.role in ("system", "developer")]
