"""Stored absence never restores authority, quota, identity or lost messages."""

import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from llm.components.memory import MemoryComponent
from llm.components.registry import ComponentRegistry
from llm.core.models import MessageRole, MessageStatus
from llm.services.history.conversation import ConversationStore
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.lifecycle.sessions import SessionManager
from tests.llm import test_usage_deletion
from tests.llm import test_persistence
from tests.llm import test_refinement
from llm.core.models import Run, Step, new_id
from llm.services.runtime.runs import RunRepository
from llm.services.lifecycle.steps import StepRepository


class StoredIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.memory = MemoryComponent()
        registry = ComponentRegistry()
        registry.register(self.memory)
        self.projects = ProjectManager(ProjectRepository(self.root / 'projects'),
            SessionManager(), components=registry)
        self.project = self.projects.create('integrity', components=['memory'])

    def test_project_deleted_field_is_required_and_boolean(self):
        path = self.project.paths.root / 'project.json'
        original = json.loads(path.read_text())
        for value in ('missing', None, 0, 1, 'false', []):
            with self.subTest(value=value):
                data = {**original, 'deleted': value}
                if value == 'missing':
                    del data['deleted']
                path.write_text(json.dumps(data))
                before = path.read_bytes()
                for read in (lambda: self.projects.load(self.project.id),
                             lambda: self.projects.repository.read_backup(self.project.paths.root)):
                    with self.assertRaises((ValueError, TypeError, KeyError)):
                        read()
                    self.assertEqual(path.read_bytes(), before)

    def test_memory_missing_identity_is_not_reissued_on_read_or_reselection(self):
        path = self.memory.root(self.project) / 'identity.json'
        path.unlink()
        rejected = []
        for operation in (lambda: self.memory.identity(self.project),
                          lambda: self.memory.initialize(self.project),
                          lambda: self.memory.validate_backup(self.project)):
            try:
                operation()
            except (ValueError, OSError):
                rejected.append(True)
            else:
                rejected.append(False)
        self.assertEqual((rejected, path.exists()), ([True, True, True], False))

    def test_unterminated_conversation_cannot_be_read_or_destroyed_by_append(self):
        path = self.root / 'conversation.jsonl'
        store = ConversationStore(path)
        store.create(MessageRole.USER, 'preserve me', MessageStatus.COMMITTED)
        path.write_bytes(path.read_bytes()[:-1])
        original = path.read_bytes()
        rejected = []
        for operation in (lambda: ConversationStore(path).list(),
                          lambda: ConversationStore(path).create(MessageRole.USER, 'next', MessageStatus.QUEUED)):
            try:
                operation()
            except ValueError:
                rejected.append(True)
            else:
                rejected.append(False)
        self.assertEqual((rejected, path.read_bytes()), ([True, True], original))


class CompletionIntegrityTests(unittest.IsolatedAsyncioTestCase):
    create = test_usage_deletion.UsageDeletionTests.create
    run_once = test_usage_deletion.UsageDeletionTests.run_once

    async def test_completion_fields_and_token_types_are_required_before_usage_projection(self):
        app, project, session, _ = await self.create({'project_max_calls': 1})
        first = await self.run_once(session)
        await session.run.shutdown()
        path = first.data.paths.root / 'run.json'
        original = json.loads(path.read_text())
        entry = original['metadata']['completions'][0]
        corruptions = [{k: v for k, v in entry.items() if k != key} for key in entry]
        corruptions += [{**entry, 'usage_complete': value} for value in (None, 0, 1, 'true')]
        corruptions += [{**entry, 'reserved_tokens': value} for value in (-1, True, 0.5, '1')]
        corruptions += [{**entry, 'usage_complete': True, 'usage': {'total_tokens': value}}
                        for value in (-1, True, 0.5, None)]
        for index, corrupted in enumerate(corruptions):
            with self.subTest(case=index):
                value = deepcopy(original)
                value['metadata']['completions'] = [corrupted]
                path.write_text(json.dumps(value))
                before = path.read_bytes()
                with self.assertRaises((ValueError, TypeError)):
                    await project.amodel_usage()
                with self.assertRaises((ValueError, TypeError)):
                    app.run_repository.load(session.data, first.id)
                self.assertEqual(path.read_bytes(), before)

    async def test_missing_completion_container_cannot_reset_project_quota(self):
        _, project, session, model = await self.create({'project_max_calls': 1})
        first = await self.run_once(session)
        await session.run.shutdown()
        path = first.data.paths.root / 'run.json'
        data = json.loads(path.read_text())
        del data['metadata']['completions']
        path.write_text(json.dumps(data))
        before = path.read_bytes()
        rejected = False
        try:
            await project.amodel_usage()
        except (ValueError, KeyError):
            rejected = True
        other = await project.sessions.acreate()
        try:
            await self.run_once(other)
        except (ValueError, KeyError):
            pass
        self.assertEqual((rejected, len(model.requests), path.read_bytes()), (True, 1, before))

    async def test_missing_run_record_is_not_omitted_from_quota_history(self):
        app, project, session, model = await self.create({'project_max_calls': 1})
        first = await self.run_once(session)
        await session.run.shutdown()
        path = first.data.paths.root / 'run.json'
        snapshot = first.data
        path.unlink()
        with self.assertRaises(ValueError):
            app.run_repository.save(snapshot)
        rejected = False
        try:
            await project.amodel_usage()
        except (ValueError, OSError):
            rejected = True
        other = await project.sessions.acreate()
        try:
            await self.run_once(other)
        except (ValueError, OSError):
            pass
        self.assertEqual((rejected, len(model.requests), path.exists()), (True, 1, False))

    async def test_missing_session_record_is_not_omitted_from_quota_history(self):
        _, project, session, model = await self.create({'project_max_calls': 1})
        await self.run_once(session)
        await session.run.shutdown()
        path = session.data.paths.root / 'session.json'
        path.unlink()
        rejected = False
        try:
            await project.amodel_usage()
        except (ValueError, OSError):
            rejected = True
        other = await project.sessions.acreate()
        try:
            await self.run_once(other)
        except (ValueError, OSError):
            pass
        self.assertEqual((rejected, len(model.requests), path.exists()), (True, 1, False))

    async def test_missing_project_policy_container_cannot_remove_quota(self):
        _, project, session, model = await self.create({'project_max_calls': 1})
        await self.run_once(session)
        await session.run.shutdown()
        path = project.paths.root / 'project.json'
        data = json.loads(path.read_text())
        del data['config']['policies']
        path.write_text(json.dumps(data))
        before = path.read_bytes()
        rejected = False
        try:
            other = await project.sessions.acreate()
            await self.run_once(other)
        except (ValueError, OSError):
            rejected = True
        self.assertEqual((rejected, len(model.requests), path.read_bytes()), (True, 1, before))


class DomainRecordTests(unittest.TestCase):
    setUp = test_persistence.PersistenceTests.setUp

    def test_every_serialized_field_is_required_on_domain_read(self):
        runs = RunRepository()
        run_id = new_id()
        run = Run(run_id, self.session.id, new_id(), new_id(), 'loop', runs.paths(self.session, run_id))
        runs.create(run)
        steps = StepRepository()
        step_id = new_id()
        step = Step(step_id, run.id, 'tool', 'effect', steps.paths(run, step_id))
        steps.create(step)
        cases = [
            (self.project.paths.root / 'project.json', lambda: self.projects.load(self.project.id)),
            (self.session.paths.root / 'session.json', lambda: self.sessions.load(self.project, self.session.id)),
            (run.paths.root / 'run.json', lambda: runs.load(self.session, run.id)),
            (step.paths.root / 'step.json', lambda: steps.load(run, step.id)),
        ]
        for path, read in cases:
            original = path.read_bytes()
            data = json.loads(original)
            for key in data:
                with self.subTest(record=path.name, field=key):
                    path.write_text(json.dumps({k: v for k, v in data.items() if k != key}))
                    before = path.read_bytes()
                    try:
                        with self.assertRaises((ValueError, TypeError, KeyError)):
                            read()
                        self.assertEqual(path.read_bytes(), before)
                    finally:
                        path.write_bytes(original)


class RefinementIntegrityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_refinement.RefinementTests.asyncSetUp
    proposal = test_refinement.RefinementTests.proposal
    approve = test_refinement.RefinementTests.approve

    async def test_history_state_and_effect_receipt_must_agree(self):
        identifier, data, original = await self.proposal()
        await self.approve(identifier)
        path = self.project.paths.root / 'refinement' / 'records' / (identifier + '.json')
        value = json.loads(path.read_text())
        corruptions = [
            {**value, 'history': value['history'][:1]},
            {**value, 'history': value['history'][1:]},
            {**value, 'history': [*value['history'], value['history'][-1]]},
            {**value, 'applied_target': {'identifier': data['target']['identifier'], 'version': original['version']}},
            {k: v for k, v in value.items() if k != 'evaluations'},
        ]
        for index, corrupted in enumerate(corruptions):
            with self.subTest(case=index):
                path.write_text(json.dumps(corrupted))
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    await self.refine.asnapshot(identifier)
                self.assertEqual(await self.refine.atarget_snapshot(data['target']), original)
                self.assertEqual(path.read_bytes(), before)

    async def test_missing_approval_history_cannot_authorize_target_change(self):
        identifier, data, original = await self.proposal()
        await self.approve(identifier)
        path = self.project.paths.root / 'refinement' / 'records' / (identifier + '.json')
        value = json.loads(path.read_text())
        value['history'] = []
        path.write_text(json.dumps(value))
        before = path.read_bytes()
        rejected = False
        try:
            view = await self.refine.asnapshot(identifier)
            await self.refine.aapply(identifier, expected_version=view['version'])
        except ValueError:
            rejected = True
        self.assertEqual((rejected, await self.refine.atarget_snapshot(data['target']), path.read_bytes()),
                         (True, original, before))
