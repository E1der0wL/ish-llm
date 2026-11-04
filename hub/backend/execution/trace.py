"""Chronological display of provider-exposed thoughts and observed Tool calls."""

from dataclasses import replace
from llm.core.models import StepStatus

from ...model import TraceItem, SessionSummary
from ..interactions.question_values import QuestionTool


def merge_trace(*groups):
    items = {}
    for group in groups:
        for item in group:
            previous = items.get(item.id)
            # An approval resumed in another Run updates the same invocation,
            # retaining its original position in the thought/Tool sequence.
            if previous and previous.started_at:
                item = replace(item, started_at=previous.started_at)
            items[item.id] = item
    return tuple(sorted(items.values(), key=lambda item: item.started_at))


def tool_trace(steps):
    # Durable question views carry the reply even after the Tool returned.
    # Do not duplicate them with a second Tool-derived question bubble.
    return tuple(TraceItem(step.id, TraceItem.TOOL, step.name, step.status, step.started_at,
                          details=step.details) for step in steps
                 if step.name not in (QuestionTool.NAME, QuestionTool.NONBLOCKING_NAME))
