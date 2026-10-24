"""Invalid conversation mutations must never poison durable or in-memory history."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.core.models import MessageRole, MessageStatus
from llm.services.history.conversation import ConversationStore, MemoryConversationStore
from llm.services.infrastructure.storage import tree_snapshot


class ConversationMutationIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "conversation.jsonl"

    @staticmethod
    def seed(store):
        message = store.create(
            MessageRole.ASSISTANT,
            "original",
            MessageStatus.STREAMING,
            message_id="assistant",
            run_id="run",
            metadata={"preserve": [1]},
        )
        store.delta(message.id, " buffered")
        return message.id

    def assert_rejected(self, mutation):
        for factory in (lambda: ConversationStore(self.path), MemoryConversationStore):
            with self.subTest(storage=factory):
                store = factory()
                identifier = self.seed(store)
                before, files = store.list(), tree_snapshot(self.root)
                with patch.object(store, "_append", wraps=store._append) as write:
                    with self.assertRaises((TypeError, ValueError)):
                        mutation(store, identifier)
                    self.assertEqual(write.call_count, 0)
                self.assertEqual(store.list(), before)
                self.assertEqual(tree_snapshot(self.root), files)
                self.assertEqual(store.count(status=MessageStatus.STREAMING), 1)
                if isinstance(store, ConversationStore):
                    self.assertEqual(ConversationStore(self.path).list(), before)
                store.delta(identifier, " continued")
                self.assertEqual(store.get(identifier).content, "original buffered continued")

    def test_invalid_delta_preserves_bytes_and_reopens(self):
        self.assert_rejected(lambda store, identifier: store.delta(identifier, 12))

    def test_invalid_status_preserves_bytes_and_reopens(self):
        self.assert_rejected(lambda store, identifier: store.set_status(identifier, "bad-status"))

    def test_invalid_run_binding_preserves_bytes_and_reopens(self):
        self.assert_rejected(lambda store, identifier: store.bind_run(identifier, 12))

    def test_null_metadata_preserves_bytes_and_reopens(self):
        self.assert_rejected(lambda store, identifier: store.update_metadata(identifier, None))

    def test_partly_valid_metadata_cannot_partly_change_state(self):
        self.assert_rejected(
            lambda store, identifier: store.update_metadata(
                identifier, [["changed", "must not survive"], ["malformed"]]
            )
        )

    def test_invalid_creation_never_creates_a_journal(self):
        defaults = dict(
            role=MessageRole.ASSISTANT,
            content="original",
            status=MessageStatus.STREAMING,
            message_id="assistant",
            run_id="run",
            metadata={"preserve": [1]},
        )
        for field, value in (
            ("role", "bad-role"),
            ("content", 12),
            ("status", "bad-status"),
            ("message_id", 12),
            ("run_id", 12),
            ("metadata", [1]),
        ):
            for factory in (lambda: ConversationStore(self.path), MemoryConversationStore):
                with self.subTest(field=field, storage=factory):
                    store = factory()
                    files = tree_snapshot(self.root)
                    with patch.object(store, "_append", wraps=store._append) as write:
                        with self.assertRaises((TypeError, ValueError)):
                            store.create(**{**defaults, field: value})
                        self.assertEqual(write.call_count, 0)
                    self.assertEqual(tree_snapshot(self.root), files)
                    self.assertEqual(store.list(), [])
                    self.assertFalse(self.path.exists())

    def test_invalid_batch_does_not_append_its_valid_prefix(self):
        self.assert_rejected(
            lambda store, identifier: store.deltas(identifier, [("valid prefix", "append"), (12, "append")])
        )

    def test_invalid_durable_events_remain_rejected_without_repair(self):
        store = ConversationStore(self.path)
        identifier = self.seed(store)
        original = self.path.read_bytes()
        events = [
            {"type": "message.delta", "id": identifier, "text": 12},
            {"type": "message.delta", "id": identifier, "text": "x", "operation": "bad-operation"},
            {"type": "message.status", "id": identifier, "status": "bad-status"},
            {"type": "message.run", "id": identifier, "run_id": 12},
            {"type": "message.metadata", "id": identifier, "metadata": [["x", 1], ["bad"]]},
            {"type": "unknown-event", "id": identifier},
        ]
        for event in events:
            with self.subTest(event=event):
                self.path.write_bytes(original + (json.dumps(event) + "\n").encode())
                damaged = tree_snapshot(self.root)
                reopened = ConversationStore(self.path)
                for _ in range(2):
                    with self.assertRaises((TypeError, ValueError)):
                        reopened.list()
                    self.assertEqual(tree_snapshot(self.root), damaged)

    def test_current_optional_operation_and_explicit_null_binding_are_preserved(self):
        store = ConversationStore(self.path)
        identifier = self.seed(store)
        with self.path.open("a") as stream:
            stream.write(json.dumps({"type": "message.delta", "id": identifier, "text": " append"}) + "\n")
        store = ConversationStore(self.path)
        self.assertEqual(store.get(identifier).content, "original buffered append")
        store.bind_run(identifier, None)
        store.update_metadata(identifier, [["added", [2]]])
        store.set_status(identifier, "completed")
        result = ConversationStore(self.path).get(identifier)
        self.assertIsNone(result.run_id)
        self.assertEqual(result.metadata, {"preserve": [1], "added": [2]})
        self.assertEqual(result.status, MessageStatus.COMPLETED)

    def test_write_failure_leaves_existing_state_and_bytes(self):
        store = ConversationStore(self.path)
        identifier = self.seed(store)
        before, files = store.list(), tree_snapshot(self.root)
        with patch(
            "llm.services.history.conversation.append_bytes", side_effect=OSError("write failed")
        ) as write:
            with self.assertRaisesRegex(OSError, "write failed"):
                store.delta(identifier, "not committed")
            self.assertEqual(write.call_count, 1)
        self.assertEqual(store.list(), before)
        self.assertEqual(ConversationStore(self.path).list(), before)
        self.assertEqual(tree_snapshot(self.root), files)
