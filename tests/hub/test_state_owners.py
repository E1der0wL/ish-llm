"""Notification reconciliation and snapshot coalescing keep their independent lifetimes."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest

from hub.backend.observability.notifications import NotificationBuffer
from hub.model import HubSnapshot
from hub.ui.snapshot_inbox import SnapshotInbox


class NotificationBufferTests(unittest.TestCase):
    def setUp(self):
        self.buffer = NotificationBuffer()
        self.run = NS(id="run", session_id="s", status="running", engine="loop")

    def test_initial_history_is_silent_and_reconciliation_deduplicates_events(self):
        self.buffer.observe_session("p", "s", "Session", self.run)
        self.assertEqual(self.buffer.items(), ())
        self.run.status = "completed"
        self.buffer.observe_session("p", "s", "Renamed", self.run)
        self.buffer.record_run(self.run)
        self.buffer.observe_session("p", "s", "Renamed", self.run)
        self.assertEqual([(item.sequence, item.title, item.status) for item in self.buffer.items()],
                         [(1, "Renamed", "completed")])

    def test_empty_session_observation_then_event_and_snapshot_record_once(self):
        self.buffer.observe_session("p", "s", "Session", None)
        self.buffer.record_run(self.run)
        self.buffer.observe_session("p", "s", "Session", self.run)
        self.assertEqual([item.status for item in self.buffer.items()], ["running"])
        self.run.engine = "_hub_title"
        self.run.status = "completed"
        self.buffer.record_run(self.run)
        self.assertEqual(len(self.buffer.items()), 1)

    def test_paused_state_survives_dedup_eviction_and_other_project_activity(self):
        self.buffer.register_session("old-project", "s", "Old session")
        self.run.status = "paused"
        self.buffer.record_run(self.run)
        self.buffer.record_question("old-project", "s", "Old session",
                                    {"request": {"id": "approval", "kind": "approval"}})
        self.buffer.register_session("new-project", "other", "Other")
        for index in range(130):
            self.buffer.record_run(NS(id=str(index), session_id="other", engine="loop", status="completed"))
        before = self.buffer.items()
        self.buffer.record_run(self.run)
        self.assertEqual(self.buffer.items(), before)
        self.assertEqual(len(before), 64)
        self.assertEqual([item.sequence for item in before], list(range(69, 133)))
        self.assertTrue(all(item.project_id == "new-project" for item in before))

    def test_live_question_snapshot_and_repeat_callbacks_share_identity(self):
        request = {"id": "q", "kind": "question"}
        pending = {"q": NS(project_id="p", session_id="s", request=NS(to_dict=lambda: request))}
        self.buffer.observe_questions(pending)
        self.buffer.observe_session("p", "s", "Session", None, ({"request": request},))
        self.buffer.observe_questions({})
        self.buffer.observe_questions(pending)
        self.assertEqual([(item.title, item.status) for item in self.buffer.items()], [("s", "waiting_input")])

    def test_forget_session_stops_late_events_without_erasing_published_items(self):
        self.buffer.register_session("p", "s", "Session")
        self.buffer.record_run(self.run)
        before = self.buffer.items()
        self.buffer.forget_session("s")
        self.run.status = "interrupted"
        self.buffer.record_run(self.run)
        self.assertEqual(self.buffer.items(), before)
        self.buffer.observe_session("p", "s", "Reopened", self.run)
        self.assertEqual(self.buffer.items(), before)


class SnapshotInboxTests(unittest.TestCase):
    def setUp(self):
        self.inbox = SnapshotInbox()
        self.snapshot = HubSnapshot("p", "Project", "model", "file", (), "", ())

    def test_concurrent_publications_claim_one_callback_and_keep_latest(self):
        with ThreadPoolExecutor(max_workers=8) as workers:
            scheduled = list(workers.map(lambda index: self.inbox.offer(
                replace(self.snapshot, project_title=str(index)), can_schedule=True), range(64)))
        self.assertEqual(sum(scheduled), 1)
        latest = replace(self.snapshot, project_title="Last")
        self.assertFalse(self.inbox.offer(latest, can_schedule=True))
        self.assertIs(self.inbox.take(), latest)
        self.assertIsNone(self.inbox.take())

    def test_publication_after_consumption_claims_a_new_slot(self):
        self.assertTrue(self.inbox.offer(self.snapshot, can_schedule=True))
        self.assertIs(self.inbox.take(), self.snapshot)
        next_snapshot = replace(self.snapshot, project_id="other")
        self.assertTrue(self.inbox.offer(next_snapshot, can_schedule=True))
        self.assertIs(self.inbox.take(), next_snapshot)

    def test_unavailable_and_rebound_loop_do_not_drop_pending_snapshot(self):
        self.assertFalse(self.inbox.offer(self.snapshot, can_schedule=False))
        self.inbox.rebind()
        self.assertIs(self.inbox.take(), self.snapshot)
        self.assertTrue(self.inbox.offer(self.snapshot, can_schedule=True))
        self.inbox.rebind()
        latest = replace(self.snapshot, project_title="After shell prompt changed")
        self.assertTrue(self.inbox.offer(latest, can_schedule=True))
        self.assertIs(self.inbox.take(), latest)
