"""Stored execution states require their original start, end and active Run evidence."""

import json
import unittest
from copy import deepcopy
import shutil
from dataclasses import asdict

from llm.core.models import Run, RunStatus, SessionStatus, StepStatus
from llm.core.results import CompletionResult
from tests.llm import test_repositories as repositories


class DomainStateTests(unittest.TestCase):
    setUp = repositories.RepositoryTests.setUp

    def test_stale_objects_cannot_reopen_terminal_execution_records(self):
        old_step = deepcopy(self.step)
        self.steps.start(self.step)
        self.steps.complete(self.step)
        old_run = deepcopy(self.run)
        self.run.status = RunStatus.RUNNING
        self.run.started_at = self.run.created_at
        self.runs.save(self.run)
        self.run.status = RunStatus.COMPLETED
        self.run.ended_at = self.run.created_at
        self.runs.save(self.run)
        for model, filename, save in ((old_step, 'step.json', self.steps.start),
                                      (old_run, 'run.json', self.runs.save)):
            with self.subTest(kind=type(model).__name__):
                path = model.paths.root / filename
                before = path.read_bytes()
                rejected = False
                try:
                    save(model)
                except ValueError:
                    rejected = True
                self.assertEqual(path.read_bytes(), before, 'stale object reopened a terminal record')
                self.assertTrue(rejected)

    def test_stale_run_cannot_remove_a_persisted_model_call_receipt(self):
        stale = deepcopy(self.run)
        self.run.metadata['completions'].append(asdict(CompletionResult()))
        self.runs.save(self.run)
        path = self.run.paths.root / 'run.json'
        before = path.read_bytes()
        rejected = False
        try:
            self.runs.save(stale)
        except ValueError:
            rejected = True
        self.assertEqual(path.read_bytes(), before, 'stale Run erased a model call reservation')
        self.assertTrue(rejected)

    def test_stale_receipt_cannot_erase_final_usage_evidence(self):
        self.run.metadata['completions'].append(asdict(CompletionResult()))
        self.runs.save(self.run)
        stale = deepcopy(self.run)
        result = self.run.metadata['completions'][0]
        result.update(status=RunStatus.COMPLETED, ended_at=result['started_at'],
                      usage_complete=True, usage={'total_tokens': 7})
        self.runs.save(self.run)
        path = self.run.paths.root / 'run.json'
        before = path.read_bytes()
        rejected = False
        try:
            self.runs.save(stale)
        except ValueError:
            rejected = True
        self.assertEqual(path.read_bytes(), before, 'stale receipt erased finalized token usage')
        self.assertTrue(rejected)

    def assert_invalid_record(self, model, filename, load, changes):
        path = model.paths.root / filename
        original = path.read_bytes()
        data = json.loads(original)
        data.update(changes)
        path.write_text(json.dumps(data))
        damaged = path.read_bytes()
        try:
            with self.assertRaises(ValueError):
                load()
            self.assertEqual(path.read_bytes(), damaged)
        finally:
            path.write_bytes(original)

    def test_running_session_requires_its_active_run_declarations(self):
        for status, current in ((SessionStatus.RUNNING, None), (SessionStatus.RUNNING, ''),
                                (SessionStatus.RUNNING, []),
                                (SessionStatus.RUNNING, [self.run.id, self.run.id]),
                                (SessionStatus.IDLE, [self.run.id]), (SessionStatus.DELETED, [self.run.id])):
            with self.subTest(status=status, current=current):
                self.assert_invalid_record(self.session, 'session.json',
                    lambda: self.session_repository.load(self.project, self.session.id),
                    {'status': status, 'active_run_ids': current})

    def test_run_state_requires_its_start_and_terminal_evidence(self):
        stamp = self.run.created_at
        cases = [dict(status=RunStatus.RUNNING),
                 dict(status=RunStatus.PENDING, started_at=stamp),
                 dict(status=RunStatus.RUNNING, started_at=stamp, ended_at=stamp)]
        for status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.INTERRUPTED,
                       RunStatus.CANCELLED, RunStatus.PAUSED):
            cases.append(dict(status=status, started_at=stamp, ended_at=None))
        for status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED):
            cases.append(dict(status=status, started_at=None, ended_at=stamp))
        for changes in cases:
            with self.subTest(changes=changes):
                self.assert_invalid_record(self.run, 'run.json', lambda: self.runs.load(self.session, self.run.id), changes)

    def test_step_state_cannot_lose_start_or_terminal_evidence(self):
        stamp = self.step.created_at
        cases = [dict(status=StepStatus.RUNNING), dict(status=StepStatus.PENDING, started_at=stamp),
                 dict(status=StepStatus.RUNNING, started_at=stamp, ended_at=stamp),
                 dict(status=StepStatus.COMPLETED, started_at=None, ended_at=stamp)]
        for status in (StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.INTERRUPTED, StepStatus.CANCELLED):
            cases.append(dict(status=status, started_at=stamp, ended_at=None))
        for changes in cases:
            with self.subTest(changes=changes):
                self.assert_invalid_record(self.step, 'step.json', lambda: self.steps.load(self.run, self.step.id), changes)

    def test_pending_recovery_and_prestart_step_termination_remain_valid(self):
        for finish in (self.steps.cancel, self.steps.interrupt, lambda step: self.steps.fail(step, 'before start')):
            step = self.steps.create(self.run, 'test', 'Prestart termination')
            finish(step)
            current = self.steps.load(self.run, step.id)
            self.assertIsNone(current.started_at)
            self.assertIsNotNone(current.ended_at)
        path = self.run.paths.root / 'run.json'
        data = json.loads(path.read_bytes())
        data.update(status=RunStatus.INTERRUPTED, ended_at=self.run.created_at)
        current = Run.from_record(data, self.run.paths)
        self.assertIsNone(current.started_at)
        self.assertEqual(current.status, RunStatus.INTERRUPTED)

    def test_writers_reject_invalid_current_state_before_changing_original_files(self):
        for model, filename, save, changes in (
                (self.project, 'project.json', self.projects.repository.save, {'deleted': None}),
                (self.session, 'session.json', self.session_repository.save, {'status': SessionStatus.RUNNING}),
                (self.run, 'run.json', self.runs.save, {'status': RunStatus.COMPLETED}),
                (self.step, 'step.json', self.steps.save, {'status': StepStatus.COMPLETED})):
            with self.subTest(filename=filename):
                path = model.paths.root / filename
                before = path.read_bytes()
                changed = deepcopy(model)
                for key, value in changes.items():
                    setattr(changed, key, value)
                with self.assertRaises(ValueError):
                    save(changed)
                self.assertEqual(path.read_bytes(), before)

    def test_backup_uses_the_same_terminal_state_evidence(self):
        self.assert_invalid_record(self.run, 'run.json', lambda: self.runs.read_backup(self.session),
            {'status': RunStatus.COMPLETED, 'started_at': self.run.created_at, 'ended_at': None})
        self.assert_invalid_record(self.step, 'step.json', lambda: self.steps.repository.read_backup(self.run),
            {'status': StepStatus.COMPLETED, 'started_at': self.step.created_at, 'ended_at': None})

    def test_old_handles_cannot_recreate_missing_required_records(self):
        for model, filename, save in (
                (self.step, 'step.json', self.steps.save), (self.run, 'run.json', self.runs.save),
                (self.session, 'session.json', self.session_repository.save),
                (self.project, 'project.json', self.projects.repository.save)):
            with self.subTest(filename=filename):
                path = model.paths.root / filename
                original = path.read_bytes()
                path.unlink()
                rejected = False
                try:
                    try:
                        save(model)
                    except (ValueError, OSError):
                        rejected = True
                    self.assertFalse(path.exists(), 'an old handle recreated missing required evidence')
                    self.assertTrue(rejected)
                finally:
                    path.write_bytes(original)

    def test_old_handles_cannot_initialize_an_erased_record_tree(self):
        # Work from leaf to root so every object still represents a stored record.
        for model, save in ((self.step, self.steps.save), (self.run, self.runs.save),
                            (self.session, self.session_repository.save),
                            (self.project, self.projects.repository.save)):
            with self.subTest(kind=type(model).__name__):
                shutil.rmtree(model.paths.root)
                rejected = False
                try:
                    save(model)
                except (ValueError, OSError):
                    rejected = True
                self.assertFalse(model.paths.root.exists(), 'an old handle initialized an erased record tree')
                self.assertTrue(rejected)

    def test_create_never_adopts_an_existing_or_partially_lost_record_tree(self):
        for model, filename, create in (
                (self.step, 'step.json', self.steps.repository.create),
                (self.run, 'run.json', self.runs.create),
                (self.session, 'session.json', self.session_repository.create),
                (self.project, 'project.json', self.projects.repository.create)):
            with self.subTest(kind=type(model).__name__):
                path = model.paths.root / filename
                before = path.read_bytes()
                with self.assertRaises(FileExistsError):
                    create(model)
                self.assertEqual(path.read_bytes(), before)
                path.unlink()
                try:
                    with self.assertRaises(FileExistsError):
                        create(model)
                    self.assertFalse(path.exists())
                finally:
                    path.write_bytes(before)

    def test_saved_objects_cannot_repair_current_corruption(self):
        for model, filename, save in (
                (self.step, 'step.json', self.steps.save), (self.run, 'run.json', self.runs.save),
                (self.session, 'session.json', self.session_repository.save),
                (self.project, 'project.json', self.projects.repository.save)):
            with self.subTest(kind=type(model).__name__):
                path = model.paths.root / filename
                before = path.read_bytes()
                value = json.loads(before)
                del value['created_at']
                path.write_text(json.dumps(value))
                damaged = path.read_bytes()
                try:
                    with self.assertRaises(ValueError):
                        save(model)
                    self.assertEqual(path.read_bytes(), damaged)
                finally:
                    path.write_bytes(before)
