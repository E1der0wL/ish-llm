"""Fold checkpoint attempts into one display turn without changing stored history."""

from dataclasses import replace
from collections.abc import Sequence
from llm.core.models import Message
from ..model import ChatMessage
from .trace import merge_trace


def continued_messages(records: Sequence[Message], messages: Sequence[ChatMessage]) -> tuple[ChatMessage, ...]:
    parents = {m.run_id: m.metadata.get("resume", {}).get("run_id") for m in records
               if str(m.role) == "user" and m.run_id and m.metadata.get("resume")}
    assistants = {m.run_id: m.id for m in records if str(m.role) == "assistant" and m.run_id}
    sources, hidden = {}, set()
    for record in records:
        parent = (record.metadata.get("resume", {}).get("run_id") if str(record.role) == "user"
                  else parents.get(record.run_id))
        if parent not in assistants:
            continue
        seen = {record.run_id}
        while parent not in seen and parents.get(parent) in assistants:
            seen.add(parent)
            parent = parents[parent]
        if parent in seen:
            continue
        if str(record.role) == "user" and record.metadata.get("resume"):
            hidden.add(record.id)
        elif str(record.role) == "assistant":
            sources[record.id] = assistants[parent]
    result, positions, reasoning, tools, traces = [], {}, {}, {}, {}
    for message in messages:
        if message.role == "reasoning" and message.id.endswith(":reasoning"):
            assistant = message.id.removesuffix(":reasoning")
            root = sources.get(assistant, assistant)
            traces[root] = merge_trace(traces.get(root, ()), message.trace)
            history = tools.setdefault(root, {})
            for index, step in enumerate(message.tools):
                history[step.id or (assistant, index)] = step
            previous = reasoning.get(root, "")
            if message.text and message.text not in previous:
                reasoning[root] = (message.text if message.text.startswith(previous) else
                                   previous + ("\n\n" if previous else "") + message.text)
            continue
        if message.id in hidden:
            continue
        root = sources.get(message.id)
        if root in positions:
            index = positions[root]
            previous = result[index]
            text = previous.text
            if message.text:
                text += ("\n\n" if text else "") + message.text
            elapsed = ((previous.elapsed_seconds or 0) + (message.elapsed_seconds or 0)
                       if previous.elapsed_seconds is not None or message.elapsed_seconds is not None else None)
            result[index] = replace(message, id=root, text=text, elapsed_seconds=elapsed)
        else:
            positions[message.id] = len(result)
            result.append(message)
    visible = []
    for message in result:
        if (message.id in reasoning or message.id in tools) and message.status in ("streaming", "committed", "paused"):
            visible.append(ChatMessage("reasoning", reasoning.get(message.id, ""), id=message.id + ":reasoning",
                                       tools=tuple(tools.get(message.id, {}).values()), trace=traces.get(message.id, ())))
        visible.append(message)
    return tuple(visible)
