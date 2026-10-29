"""Disposable display projection for one visited conversation."""

from dataclasses import dataclass


@dataclass(slots=True)
class MessageProjection:
    key: tuple
    run_id: str | None
    items: tuple


@dataclass(slots=True)
class SessionProjection:
    observation: object
    status: str
    summary: object


class ConversationProjection:
    def __init__(self):
        self.revision = 0
        self.clear()

    def clear(self):
        self.revision += 1
        self.records = None
        self.context = None
        self.messages = ()
        self.sources = ()
        self.settled: dict[str, MessageProjection] = {}
        self.input_records = None
        self.input_context = None
        self.by_run = {}
        self.parts = {}
        self.request_times = {}
        self.request_authors = {}
        self.displayed = {}
        self.display_positions = {}
        self.display_offsets = []
        self.display_rows = ()
        self.linked = False
        self.full_refresh = True
        self.focus_id = None
        self.detailed = set()

    def changed(self, run_id=None):
        self.revision += 1
        if run_id is None:
            self.full_refresh = True
        if run_id is not None:
            for record in self.by_run.get(run_id, ()):
                self.settled.pop(record.id, None)

    def incremental(self, records, context):
        return (not self.full_refresh and self.input_records is records
                and self.input_context == context)

    def index(self, records, context, requests, authors):
        from ..execution.turns import edit_source
        from llm.core.continuations import ResumeRecord, RestartRecord

        # The index is reusable only after all rows have been projected. A
        # failed historical read must not make the next refresh skip its rows.
        self.full_refresh = True
        self.input_records, self.input_context = records, context
        self.request_times, self.request_authors = requests, authors
        self.by_run = {}
        self.display_positions = {record.id: index for index, record in enumerate(records)}
        for record in records:
            self.by_run.setdefault(record.run_id, []).append(record)
        self.parts = {key: value for key, value in self.parts.items()
                      if key in self.settled}
        identifiers = {record.id for record in records}
        self.displayed = {key: value for key, value in self.displayed.items() if key in identifiers}
        self.linked = any(edit_source(record) or ResumeRecord.from_metadata(record.metadata)
                          or RestartRecord.from_metadata(record.metadata) for record in records)

    def display(self, changed=()):
        from ..execution.continuations import continued_messages
        from ..execution.turns import TurnIndex

        if self.linked:
            # Continuation folding keeps its single existing owner. Edits and
            # resumed attempts may change rows outside the current Run.
            return TurnIndex(self.input_records).display([
                row for record in self.input_records for row in self.parts.get(record.id, ())])
        first = 0 if self.full_refresh else min(
            (self.display_positions[key] for key in changed), default=len(self.input_records))
        if first == len(self.input_records) and not self.full_refresh:
            return self.display_rows
        # Reuse the settled prefix in C-level tuple slicing/concatenation. Only
        # the changed suffix needs Python row traversal and continuation folding.
        offset = self.display_offsets[first] if first else 0
        prefix = self.display_rows[:offset]
        offsets = self.display_offsets[:first]
        visible = []
        for record in self.input_records[first:]:
            offsets.append(offset + len(visible))
            part = self.parts.get(record.id, ())
            previous = self.displayed.get(record.id)
            if previous is None or previous[0] is not part:
                previous = part, continued_messages((record,), part)
                self.displayed[record.id] = previous
            visible.extend(previous[1])
        self.display_offsets = offsets
        self.display_rows = prefix + tuple(visible)
        return self.display_rows

    def retain(self, identifiers):
        self.detailed.intersection_update(identifiers)
        self.settled = {key: value for key, value in self.settled.items()
                        if key in identifiers}

    def reusable(self, records, context):
        return self.records is records and self.context == context

    def remember(self, records, context, messages, sources=()):
        self.records, self.context, self.messages = records, context, messages
        # Retain the exact detached observations whose identities are in context.
        # Object IDs alone can be recycled after a reconciliation read.
        self.sources = sources
