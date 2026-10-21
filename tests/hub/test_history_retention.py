"""Application selects retention; reviewed requests still obey current owner evidence."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from hub.backend.project.history_retention import HistoryRetention, HistoryRetentionPolicy
from llm.core.models import ProjectConfig
from tests.llm import test_history_retention_integrity as history_fixtures


class HistoryRetentionTests(unittest.IsolatedAsyncioTestCase):
    app = history_fixtures.HistoryRetentionIntegrityTests.app
    completed = history_fixtures.HistoryRetentionIntegrityTests.completed
    history = history_fixtures.HistoryRetentionIntegrityTests.history
    originals = staticmethod(history_fixtures.HistoryRetentionIntegrityTests.originals)

    async def test_application_selects_recent_runs_only_when_explicit_limits_are_supplied(self):
        app, model, project, session, runs = await self.history(3)
        retained = HistoryRetention(project, HistoryRetentionPolicy(unit='run'))
        before = self.originals(session)
        self.assertEqual((await retained.preview()).candidates, [])
        self.assertEqual(len((await project.aretention_plan('run')).candidates), 3)
        self.assertEqual(self.originals(session), before)
        selected = HistoryRetention(project, HistoryRetentionPolicy(unit='run', keep_runs=1, max_bytes=1))
        review = await selected.preview()
        self.assertEqual({row['run_id'] for row in review.candidates}, {run.id for run in runs[:2]})
        self.assertIn(runs[-1].id, {row['run_id'] for row in review.protected})
        await selected.apply(review)
        self.assertEqual([run.id for run in await session.run.alist()], [runs[-1].id])
        self.assertEqual(len(model.requests), 3)

    async def test_age_is_application_selection_and_never_a_backend_policy(self):
        app, model, project, session, runs = await self.history()
        class Later:
            now = staticmethod(lambda zone: datetime.now(timezone.utc) + timedelta(seconds=120))
            fromisoformat = staticmethod(datetime.fromisoformat)
        service = HistoryRetention(project, HistoryRetentionPolicy(unit='session', max_age_seconds=60))
        before = self.originals(session)
        self.assertEqual((await service.preview()).candidates, [])
        with patch('hub.backend.project.history_retention.datetime', Later):
            review = await service.preview()
        self.assertEqual([row['session_id'] for row in review.candidates], [session.id])
        self.assertEqual(self.originals(session), before)
        with self.assertRaises(ValueError):
            ProjectConfig(policies={'retention': {'unit': 'session', 'max_age_seconds': 60}})
        with patch('hub.backend.project.history_retention.datetime', Later):
            await service.apply(review)
        self.assertFalse(session.paths.root.exists())
        self.assertEqual(len(model.requests), 2)

    async def test_selected_counter_limits_tokens_and_keeps_latest_run(self):
        app, model, project, session, runs = await self.history(3)
        calls = []
        policy = HistoryRetentionPolicy(unit='run', keep_runs=1, max_tokens=7,
                                        counter_params={'model': 'explicit/counter'})
        with self.assertRaisesRegex(ValueError, 'explicitly selected counter'):
            HistoryRetention(project, policy)
        service = HistoryRetention(project, policy, counter=lambda request: calls.append(request) or 7)
        review = await service.preview()
        self.assertEqual((review.tokens_before, review.tokens_after), (21, 7))
        self.assertEqual({row['run_id'] for row in review.candidates}, {run.id for run in runs[:2]})
        self.assertTrue(all(request['model'] == 'explicit/counter' and request['messages'] for request in calls))
        await service.apply(review)
        self.assertEqual([run.id for run in await session.run.alist()], [runs[-1].id])
        self.assertEqual(len(model.requests), 3)

    async def test_changed_application_policy_or_current_protection_refuses_review(self):
        app, model, project, session, runs = await self.history()
        policy = HistoryRetentionPolicy(unit='run', keep_runs=1, max_bytes=1)
        service = HistoryRetention(project, policy)
        review = await service.preview()
        service.policy = HistoryRetentionPolicy(unit='run', max_bytes=2)
        before = self.originals(session)
        with self.assertRaisesRegex(ValueError, 'conflict'):
            await service.apply(review)
        self.assertEqual(self.originals(session), before)
        service.policy = policy
        await session.asave(metadata={'retain': True})
        before = self.originals(session)
        with self.assertRaisesRegex(ValueError, 'conflict'):
            await service.apply(review)
        self.assertEqual(self.originals(session), before)
        self.assertEqual(len(model.requests), 2)

    def test_invalid_policy_values_cannot_become_defaults(self):
        for changes in ({'unit': None}, {'unit': False}, {'keep_runs': True}, {'keep_runs': -1},
                        {'max_bytes': 0}, {'max_tokens': True}, {'max_age_seconds': float('nan')},
                        {'max_age_seconds': 0}, {'counter_params': None}, {'counter_params': {'messages': []}}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                HistoryRetentionPolicy(**{'unit': 'run', **changes})

    async def test_editing_review_cannot_expand_the_application_selection(self):
        app, model, project, session, runs = await self.history()
        service = HistoryRetention(project, HistoryRetentionPolicy(unit='run', keep_runs=1, max_bytes=1))
        review = await service.preview()
        review.candidates.extend(row for row in review.source.candidates if row['run_id'] == runs[-1].id)
        before = self.originals(session)
        with self.assertRaisesRegex(ValueError, 'conflict'):
            await service.apply(review)
        self.assertEqual(self.originals(session), before)
        self.assertEqual(len(model.requests), 2)
