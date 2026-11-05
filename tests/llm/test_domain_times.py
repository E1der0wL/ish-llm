"""Record dates describe owned changes, never execution authority or child activity."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from llm.core.models import RunStatus
from llm.core.record_times import RecordTimes
from llm.services.infrastructure import storage
from tests.llm import test_repositories, test_agent_domain, test_agent_storage


class DomainTimeTests(unittest.TestCase):
    setUp = test_repositories.RepositoryTests.setUp

    def owners(self):
        return ((self.project, self.projects.repository.save),
                (self.session, self.session_repository.save),
                (self.run, self.runs.save), (self.step, self.steps.repository.save))

    def test_creation_noop_and_actual_changes_share_one_contract(self):
        for model, save in self.owners():
            with self.subTest(owner=type(model).__name__):
                before = json.loads(model.paths.record.read_bytes())
                self.assertEqual(model.created_at, model.updated_at)
                with patch.object(RecordTimes, 'touch') as touch:
                    save(model)
                    self.assertEqual(json.loads(model.paths.record.read_bytes()), before)
                    touch.assert_not_called()
                if model is self.project:
                    model.title += ' changed'
                else:
                    model.metadata['application'] = {'value': True}
                changed = RecordTimes(model.created_at, '2030-01-01T00:00:00+00:00')
                with patch.object(RecordTimes, 'touch', return_value=changed), patch.object(
                        storage, 'atomic_domain_json', wraps=storage.atomic_domain_json) as write:
                    save(model)
                    self.assertEqual(write.call_count, 1)
                self.assertEqual(RecordTimes.from_record(json.loads(model.paths.record.read_bytes())), changed)
                self.assertEqual(model.updated_at, changed.updated_at)
                with patch.object(RecordTimes, 'touch') as touch:
                    save(model)
                    touch.assert_not_called()

    def test_child_write_does_not_touch_ancestor_records(self):
        parents = [self.project, self.session, self.run]
        original = [item.paths.record.read_bytes() for item in parents]
        self.step.metadata['progress'] = 'changed'
        self.steps.save(self.step)
        self.assertEqual([item.paths.record.read_bytes() for item in parents], original)

    def test_execution_times_remain_independent(self):
        created = self.step.created_at
        self.steps.start(self.step)
        started = self.step.started_at
        self.steps.complete(self.step)
        ended = self.step.ended_at
        self.step.metadata['observation'] = 'after completion'
        with patch.object(RecordTimes, 'touch', return_value=RecordTimes(created, '2030-01-01T00:00:00+00:00')):
            self.steps.save(self.step)
        self.assertEqual((self.step.created_at, self.step.started_at, self.step.ended_at),
                         (created, started, ended))
        self.run.status = RunStatus.RUNNING
        self.run.started_at = self.run.created_at
        self.runs.save(self.run)
        self.run.status = RunStatus.COMPLETED
        self.run.ended_at = self.run.created_at
        self.runs.save(self.run)
        before = deepcopy(self.run)
        self.runs.save(self.run)
        self.assertEqual(self.run, before)

    def test_missing_null_or_non_utc_times_are_rejected_without_repair(self):
        for model, _ in self.owners():
            original = model.paths.record.read_bytes()
            value = json.loads(original)
            invalid = [{key: item for key, item in value.items() if key != name}
                       for name in ('created_at', 'updated_at')]
            invalid += [{**value, 'updated_at': stamp} for stamp in
                        (None, '', '2026-01-01T00:00:00', '2026-01-01T00:00:00+09:00')]
            for bad in invalid:
                with self.subTest(owner=type(model).__name__, data=bad), self.assertRaises(ValueError):
                    type(model).from_record(bad, model.paths)
            self.assertEqual(model.paths.record.read_bytes(), original)

    def test_storage_failure_and_transaction_rollback_preserve_dates(self):
        for model, save in self.owners():
            with self.subTest(owner=type(model).__name__):
                before = model.paths.record.read_bytes()
                times = model.created_at, model.updated_at
                if model is self.project:
                    model.title += ' modified'
                else:
                    model.metadata['changed'] = True
                with patch.object(storage, 'atomic_domain_json', side_effect=OSError('write failed')):
                    with self.assertRaises(OSError):
                        save(model)
                self.assertEqual(model.paths.record.read_bytes(), before)
                self.assertEqual((model.created_at, model.updated_at), times)
                with self.assertRaisesRegex(RuntimeError, 'rollback'):
                    with self.projects.ownership.scope():
                        save(model)
                        raise RuntimeError('rollback')
                self.assertEqual(model.paths.record.read_bytes(), before)
                self.assertEqual((model.created_at, model.updated_at), times)

    def test_creation_time_cannot_be_rewritten(self):
        for model, save in self.owners():
            with self.subTest(owner=type(model).__name__):
                before = model.paths.record.read_bytes()
                original = model.created_at
                model.created_at = '2000-01-01T00:00:00+00:00'
                with self.assertRaisesRegex(ValueError, 'immutable'):
                    save(model)
                self.assertEqual(model.paths.record.read_bytes(), before)
                model.created_at = original


class AgentTimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_agent_domain.AgentDomainTests.asyncSetUp

    async def test_public_dates_are_detached_and_do_not_change_definition_or_cas(self):
        first = RecordTimes('2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
        changed = RecordTimes(first.created_at, '2026-01-02T00:00:00+00:00')
        with patch.object(RecordTimes, 'create', return_value=first):
            await self.project.agents.acreate({'engine': 'loop'}, identifier='worker')
        before = await self.project.agents.asnapshot('worker')
        parent = self.project.paths.record.read_bytes()
        self.assertEqual(before['definition'], {'engine': 'loop'})
        self.assertEqual(await self.project.agents.aget_times('worker'), first.to_dict())
        with patch.object(RecordTimes, 'touch') as touch:
            await self.project.agents.arevise('worker', before['definition'], expected_revision=before['revision'])
            touch.assert_not_called()
        self.assertEqual(await self.project.agents.asnapshot('worker'), before)
        with patch.object(RecordTimes, 'touch', return_value=changed):
            await self.project.agents.aupdate('worker', {'description': 'changed'}, expected_revision=before['revision'])
        self.assertEqual(await self.project.agents.aget_times('worker'), changed.to_dict())
        with self.assertRaisesRegex(ValueError, 'edit_conflict'):
            await self.project.agents.arevise('worker', before['definition'], expected_revision=before['revision'])
        self.assertEqual(self.project.paths.record.read_bytes(), parent)
        result = await self.project.agents.aget_times('worker')
        result['updated_at'] = 'changed externally'
        self.assertEqual(await self.project.agents.aget_times('worker'), changed.to_dict())


class AgentTimeStorageTests(unittest.TestCase):
    setUp = test_agent_storage.AgentStorageTests.setUp
    new_project = test_agent_storage.AgentStorageTests.new_project

    def test_failed_manifest_commit_restores_source_and_times(self):
        from llm.services.lifecycle import agents
        self.repository.create(self.project, {'engine': 'test'}, identifier='worker')
        snapshot = self.repository.snapshot(self.project, 'worker')
        times = self.repository.get_times(self.project, 'worker')
        with patch.object(agents, 'atomic_json', side_effect=OSError('manifest failed')):
            with self.assertRaises(OSError):
                self.repository.save(self.project, 'worker', {'engine': 'changed'},
                                     expected_revision=snapshot['revision'])
        self.assertEqual(self.repository.snapshot(self.project, 'worker'), snapshot)
        self.assertEqual(self.repository.get_times(self.project, 'worker'), times)

    def test_agent_times_are_strict_and_not_part_of_revision(self):
        from llm.core.agents import AgentRecord
        value = AgentRecord.create(self.project.id, 'worker', {'engine': 'test'}).to_dict()
        for stamp in (None, '', '2026-01-01T00:00:00+09:00'):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                AgentRecord.read({**value, 'updated_at': stamp}, project_id=self.project.id, identifier='worker')
        other = AgentRecord.read({**value, 'updated_at': '2000-01-01T00:00:00+00:00'},
                                 project_id=self.project.id, identifier='worker')
        self.assertEqual(other.revision, value['revision'])
        self.assertEqual(other.definition, value['definition'])

    def test_clone_gets_new_times_and_backup_reads_preserve_source(self):
        first = RecordTimes('2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
        second = RecordTimes('2026-02-01T00:00:00+00:00', '2026-02-01T00:00:00+00:00')
        with patch.object(RecordTimes, 'create', return_value=first):
            self.repository.create(self.project, {'engine': 'test'}, identifier='worker')
        destination = self.new_project()
        self.repository.initialize_new(destination)
        with patch.object(RecordTimes, 'create', return_value=second):
            self.repository.clone(self.project, destination)
        self.assertEqual(self.repository.get_times(self.project, 'worker'), first.to_dict())
        self.assertEqual(self.repository.get_times(destination, 'worker'), second.to_dict())
        self.assertEqual(self.repository.read_backup(self.project)['worker'].times, first)
