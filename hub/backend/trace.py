"""Chronological display of provider-exposed thoughts and observed Tool calls."""

from dataclasses import replace

from ..model import TraceItem


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
    return tuple(TraceItem(step.id, "tool", step.name, step.status, step.started_at) for step in steps)
