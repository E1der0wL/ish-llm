"""Steering stays durable and separate while one request owns its displayed bubble."""

from copy import deepcopy
from dataclasses import replace
import unittest

from hub.backend.execution.continuations import continued_messages
from hub.backend.observability.projection import ConversationProjection
from hub.model import ChatMessage
from llm.core.instruction_records import InstructionMessageRecord, InstructionReceipt
from llm.core.models import Message
from llm.core.steering import SteeringTarget, SteeringMode
from tests.llm.support.admission import admitted_message, uid
from tests.llm.support.continuation import resume_record


def request(name, run, **metadata):
    return admitted_message(uid(name), 'user', 'committed', uid(run), metadata)


def instruction(name, owner, run):
    target = SteeringTarget('root', uid(run), 'loop', SteeringMode.CONSUME, accepting=True)
    receipt = InstructionMessageRecord.create(uid(run), owner.id, [InstructionReceipt.active(target)])
    return Message(uid(name), 'user', name, receipt.message_status, run_id=uid(run),
                   metadata=receipt.metadata())


def displayed(records):
    return tuple(ChatMessage(str(m.role), m.content, id=m.id, run_id=m.run_id or '',
                             status='pending' if InstructionMessageRecord.present(m.metadata) else str(m.status))
                 for m in records)


class InstructionDisplayTests(unittest.TestCase):
    def test_interleaved_queued_request_does_not_capture_another_runs_instruction(self):
        first, queued = request('First', 'r1'), request('Queued', 'r2')
        steer = instruction('Only inspect files', first, 'r1')
        response = Message(uid('answer'), 'assistant', 'Working', 'streaming', run_id=uid('r1'))
        records = [first, queued, response, steer]
        saved = deepcopy(records)
        result = continued_messages(records, displayed(records))
        self.assertEqual([m.id for m in result], [first.id, queued.id, response.id])
        self.assertEqual(result[0].text, first.content)
        self.assertEqual([(i.id, i.text, i.status) for i in result[0].instructions],
                         [(steer.id, steer.content, 'pending')])
        self.assertEqual(result[1].instructions, ())
        self.assertEqual(records, saved)

    def test_resumed_instruction_joins_original_request_and_reopen_keeps_order(self):
        first = request('First', 'r1')
        resumed = request('Resumed', 'r2', resume=resume_record(run_id=uid('r1')))
        before = instruction('Before approval', first, 'r1')
        after = instruction('After approval', resumed, 'r2')
        a1 = Message(uid('a1'), 'assistant', 'Before', 'paused', run_id=uid('r1'))
        a2 = Message(uid('a2'), 'assistant', 'After', 'streaming', run_id=uid('r2'))
        records = [first, a1, before, resumed, a2, after]
        for data in (records, deepcopy(records)):
            result = continued_messages(data, displayed(data))
            self.assertEqual([m.id for m in result], [first.id, a1.id])
            self.assertEqual([i.id for i in result[0].instructions], [before.id, after.id])
            self.assertEqual(result[1].text, 'Before\n\nAfter')

    def test_orphan_instruction_is_never_silently_hidden(self):
        first = request('First', 'r1')
        steer = instruction('Outside loaded history', first, 'r1')
        source = displayed([steer])
        self.assertEqual(continued_messages([steer], source), source)

    def test_incremental_receipt_updates_parent_without_rebuilding_settled_response(self):
        first = request('First', 'r1')
        steer = instruction('Do not modify', first, 'r1')
        answer = Message(uid('a'), 'assistant', 'Working', 'streaming', run_id=uid('r1'))
        records = [first, answer, steer]
        projection = ConversationProjection()
        projection.index(records, (), {}, {})
        projection.parts = {item.id: (item,) for item in displayed(records)}
        initial = projection.display(projection.parts)
        projection.full_refresh = False
        projection.parts[steer.id] = (replace(projection.parts[steer.id][0], status='applied'),)
        updated = projection.display([steer.id])
        self.assertEqual(updated[0].instructions[0].status, 'applied')
        self.assertIs(updated[1], initial[1])
        self.assertEqual(len(updated), 2)
        self.assertIs(projection.display(), updated)
