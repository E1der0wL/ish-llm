"""Completion state evidence and reservation identity survive every stored update."""

import json
import unittest
from copy import deepcopy
from dataclasses import asdict

from llm.core.models import RunStatus
from llm.core.results import CompletionResult
from tests.llm import test_repositories as repositories


class CompletionIntegrityTests(unittest.TestCase):
    def setUp(self):
        repositories.RepositoryTests.setUp(self)
        self.run.metadata['completions'].append(asdict(CompletionResult(reserved_tokens=8)))
        self.runs.save(self.run)

    def assert_bad_receipt(self, changes, *, backup=False):
        path = self.run.paths.root / 'run.json'
        original = path.read_bytes()
        data = json.loads(original)
        data['metadata']['completions'][0].update(changes)
        path.write_text(json.dumps(data))
        damaged = path.read_bytes()
        try:
            with self.assertRaises(ValueError):
                self.runs.read_backup(self.session) if backup else self.runs.load(self.session, self.run.id)
            self.assertEqual(path.read_bytes(), damaged)
        finally:
            path.write_bytes(original)

    def test_terminal_receipts_require_end_evidence(self):
        for status in RunStatus:
            if status in self.run.ACTIVE_STATES:
                continue
            with self.subTest(status=status):
                self.assert_bad_receipt({'status': status, 'ended_at': None})

    def test_active_receipts_do_not_claim_terminal_evidence(self):
        for status in self.run.ACTIVE_STATES:
            with self.subTest(status=status):
                self.assert_bad_receipt({'status': status, 'ended_at': self.run.created_at})

    def test_reservation_update_cannot_change_call_identity_time_or_budget(self):
        for key, value in (('step_id', self.step.id), ('started_at', '2000-01-01T00:00:00+00:00'),
                           ('reserved_tokens', None), ('reserved_tokens', 0)):
            with self.subTest(key=key, value=value):
                stale = deepcopy(self.run)
                stale.metadata['completions'][0][key] = value
                path = self.run.paths.root / 'run.json'
                before = path.read_bytes()
                rejected = False
                try:
                    self.runs.save(stale)
                except ValueError:
                    rejected = True
                self.assertEqual(path.read_bytes(), before)
                self.assertTrue(rejected)

    def test_writer_and_backup_share_terminal_receipt_validation(self):
        invalid = deepcopy(self.run)
        invalid.metadata['completions'][0]['status'] = RunStatus.COMPLETED
        path = self.run.paths.root / 'run.json'
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.runs.save(invalid)
        self.assertEqual(path.read_bytes(), before)
        self.run.status = RunStatus.RUNNING
        self.run.started_at = self.run.created_at
        self.runs.save(self.run)
        self.run.status = RunStatus.COMPLETED
        self.run.ended_at = self.run.created_at
        self.runs.save(self.run)
        self.assert_bad_receipt({'ended_at': None}, backup=True)

    def test_streaming_provider_model_and_partial_usage_may_advance(self):
        entry = self.run.metadata['completions'][0]
        entry.update(model='response/model', response_id='response', usage={'total_tokens': 3})
        self.runs.save(self.run)
        entry.update(status=RunStatus.COMPLETED, ended_at=entry['started_at'],
                     duration_seconds=0, usage_complete=True)
        self.runs.save(self.run)
        persisted = self.runs.load(self.session, self.run.id).metadata['completions'][0]
        self.assertEqual(persisted['model'], 'response/model')
        self.assertEqual(persisted['reserved_tokens'], 8)
        self.assertEqual(persisted['usage']['total_tokens'], 3)

    def test_interrupted_reservation_keeps_unknown_usage_and_budget(self):
        self.run.status = RunStatus.INTERRUPTED
        self.run.ended_at = self.run.created_at
        self.runs.save(self.run)
        entry = self.runs.load(self.session, self.run.id).metadata['completions'][0]
        self.assertEqual(entry['status'], RunStatus.INTERRUPTED)
        self.assertEqual(entry['reserved_tokens'], 8)
        self.assertFalse(entry['usage_complete'])
        self.assertEqual(entry['usage'], {})
