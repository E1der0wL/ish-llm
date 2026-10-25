"""Fold checkpoint attempts into one display turn without changing stored history."""

from llm.core.continuations import ResumeRecord

from dataclasses import replace
from collections.abc import Sequence
from llm.core.models import MessageStatus, MessageRole, Message
from llm.services.runtime.requests import RequestAdmission
from ...model import ChatMessage, ReasoningMessage
from .trace import merge_trace


class ContinuationDisplay:
    EMPTY = ""
    PARAGRAPH = "\n\n"
    NO_ELAPSED = None
    ZERO_SECONDS = 0
    VISIBLE_REASONING = (
        MessageStatus.STREAMING,
        MessageStatus.COMMITTED,
        MessageStatus.PAUSED,
    )


def continued_messages(
    records: Sequence[Message], messages: Sequence[ChatMessage]
) -> tuple[ChatMessage, ...]:
    parents = {
        run_id: ResumeRecord.require(m.metadata).source_run_id
        for m in records
        if str(m.role) == MessageRole.USER and ResumeRecord.from_metadata(m.metadata)
        for run_id in RequestAdmission.run_ids(m)
    }
    assistants = {
        m.run_id: m.id
        for m in records
        if str(m.role) == MessageRole.ASSISTANT and m.run_id
    }
    sources, hidden = {}, set()
    for record in records:
        parent = (
            ResumeRecord.source_id(record.metadata)
            if str(record.role) == MessageRole.USER
            else parents.get(record.run_id)
        )
        if parent not in assistants:
            continue
        seen = {record.run_id}
        while parent not in seen and parents.get(parent) in assistants:
            seen.add(parent)
            parent = parents[parent]
        if parent in seen:
            continue
        if str(record.role) == MessageRole.USER and ResumeRecord.from_metadata(
            record.metadata
        ):
            hidden.add(record.id)
        elif str(record.role) == MessageRole.ASSISTANT:
            sources[record.id] = assistants[parent]
    result, positions, reasoning, tools, traces, graphs = [], {}, {}, {}, {}, {}
    for message in messages:
        if ReasoningMessage.owns(message):
            assistant = ReasoningMessage.assistant_id(message.id)
            root = sources.get(assistant, assistant)
            if message.workflow:
                graphs[root] = (message.workflow, message.workflow_states)
            traces[root] = merge_trace(traces.get(root, ()), message.trace)
            history = tools.setdefault(root, {})
            for index, step in enumerate(message.tools):
                history[step.id or (assistant, index)] = step
            previous = reasoning.get(root, ContinuationDisplay.EMPTY)
            if message.text and message.text not in previous:
                reasoning[root] = (
                    message.text
                    if message.text.startswith(previous)
                    else previous
                    + (
                        ContinuationDisplay.PARAGRAPH
                        if previous
                        else ContinuationDisplay.EMPTY
                    )
                    + message.text
                )
            continue
        if message.id in hidden:
            continue
        root = sources.get(message.id)
        if root in positions:
            index = positions[root]
            previous = result[index]
            text = previous.text
            if message.text:
                text += (
                    ContinuationDisplay.PARAGRAPH if text else ContinuationDisplay.EMPTY
                ) + message.text
            elapsed = (
                (previous.elapsed_seconds or ContinuationDisplay.ZERO_SECONDS)
                + (message.elapsed_seconds or ContinuationDisplay.ZERO_SECONDS)
                if previous.elapsed_seconds is not ContinuationDisplay.NO_ELAPSED
                or message.elapsed_seconds is not ContinuationDisplay.NO_ELAPSED
                else ContinuationDisplay.NO_ELAPSED
            )
            result[index] = replace(
                message, id=root, text=text, elapsed_seconds=elapsed
            )
        else:
            positions[message.id] = len(result)
            result.append(message)
    visible = []
    for message in result:
        show_reasoning = (
            message.id in reasoning or message.id in tools
        ) and message.status in ContinuationDisplay.VISIBLE_REASONING
        if show_reasoning or message.id in graphs:
            workflow, states = graphs.get(message.id, (ContinuationDisplay.EMPTY, ()))
            visible.append(
                ReasoningMessage.create(
                    message.id,
                    reasoning.get(message.id, ContinuationDisplay.EMPTY) if show_reasoning else ContinuationDisplay.EMPTY,
                    tools=tuple(tools.get(message.id, {}).values()) if show_reasoning else (),
                    trace=traces.get(message.id, ()) if show_reasoning else (),
                    author=message.author,
                    workflow=workflow,
                    workflow_states=states,
                )
            )
        visible.append(message)
    return tuple(visible)
