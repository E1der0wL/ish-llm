"""Checksums never replace owner evidence; restore cannot execute approved effects."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from llm.core.interactions import approval_request
from llm.llm import BackendServices, LargeLanguageModel
from llm.services.infrastructure.backups import DirectoryBackups
from llm.services.runtime.tools import ToolApprovalRequired, ToolRuntime
from tests.llm import test_long_running as long_running
from tests.llm.test_loop import call, chunk


def all_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


class BackupOwnerIntegrityTests(unittest.IsolatedAsyncioTestCase):
    setup_app = long_running.LongRunningTests.setup_app

    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.effect = self.root / 'effects.txt'
        async def act(arguments):
            with self.effect.open('a') as stream:
                stream.write('approved effect\n')
            return 'done'
        async def authorize(call):
            raise ToolApprovalRequired(request=approval_request('Review the file effect'))
        self.runtime = ToolRuntime(authorize=authorize)
        self.app, self.session, self.model = await self.setup_app(act, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')], [chunk('done', finish='stop')]], self.runtime)
        self.project = self.session.project
        self.run = (await (await self.session.run.submit('go', recipients=['loop'])).wait())[0]
        self.assertEqual(self.run.data.status, 'paused', self.run.data.error)
        self.request, = await self.run.ainteractions()
        await self.run.arespond(self.request.respond('approve'))
        await self.session.run.shutdown()
        self.archive = await self.project.abackup(self.root / 'valid-archive')
        self.assertEqual(len(self.model.requests), 1)
        self.assertFalse(self.effect.exists())

    def target(self, name):
        return LargeLanguageModel(self.root / name, engines={'loop': self.app.engines.get('loop')},
            components=[self.app.project_manager.components.get('tools')],
            services=BackendServices(tool_runtime=self.runtime))

    async def _assert_semantic_damage_is_rejected(self, damage):
        relative = (self.run.data.paths.state / 'interactions' / (self.request.id + '.json')).relative_to(self.project.paths.root)
        original_archive = all_bytes(self.archive)
        original_source = all_bytes(self.project.paths.root)
        working = self.root / 'damaged-project'
        shutil.copytree(self.archive / 'project', working)
        path = working / relative
        if damage == 'missing':
            path.unlink()
        else:
            value = json.loads(path.read_text())
            if damage == 'actor':
                del value['response']['actor']
            else:
                del value['response']
            path.write_text(json.dumps(value))
        # Deliberately build a checksum-consistent archive of damaged owner evidence.
        archive = DirectoryBackups().create(working, self.root / 'damaged-archive', metadata={'project_id': self.project.id})
        DirectoryBackups().verify(archive)
        before = all_bytes(archive)
        async with self.target('destination') as target:
            existing = await target.projects.acreate('existing target')
            destination_before = all_bytes(target.workspace)
            with self.assertRaises((ValueError, TypeError, FileNotFoundError)):
                await target.projects.arestore_backup(archive)
            self.assertEqual(all_bytes(target.workspace), destination_before)
            self.assertFalse((target.workspace / 'projects' / self.project.id).exists())
            self.assertEqual((await existing.aget_data()).title, 'existing target')
        self.assertEqual(all_bytes(archive), before)
        self.assertEqual(all_bytes(self.archive), original_archive)
        self.assertEqual(all_bytes(self.project.paths.root), original_source)
        self.assertEqual(len(self.model.requests), 1)
        self.assertFalse(self.effect.exists())

    async def test_restore_rejects_checksum_consistent_corrupt_approved_response(self):
        await self._assert_semantic_damage_is_rejected('actor')

    async def test_restore_rejects_missing_response_without_reconstructing_pending(self):
        await self._assert_semantic_damage_is_rejected('response')

    async def test_restore_rejects_missing_control_even_with_a_matching_inventory(self):
        await self._assert_semantic_damage_is_rejected('missing')

    async def test_restore_preserves_approval_and_requires_explicit_resume_for_one_effect(self):
        before = all_bytes(self.archive)
        async with self.target('destination') as target:
            restored = await target.projects.arestore_backup(self.archive)
            self.assertEqual(len(self.model.requests), 1)
            self.assertFalse(self.effect.exists())
            session = await restored.sessions.aload(self.session.id)
            run = await session.run.aload(self.run.id)
            responses = await run.ainteraction_responses()
            self.assertEqual(len(responses), 1)
            resumed = (await (await session.run.resume(run.id, agent_id='loop')).wait())[0]
            self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
            self.assertEqual(self.effect.read_text(), 'approved effect\n')
            self.assertEqual(len(self.model.requests), 2)
        self.assertEqual(all_bytes(self.archive), before)

    async def test_staged_restore_calls_the_actual_owner_and_preserves_existing_target_on_failure(self):
        component = self.app.project_manager.components.get('tools')
        validate = component.validate_backup
        roots = []
        def reject_staging(project):
            validate(project)
            roots.append(project.paths.root)
            if project.paths.root != self.archive / 'project':
                raise ValueError('owner rejected staged evidence')
        before = all_bytes(self.archive)
        async with self.target('destination') as target:
            await target.projects.acreate('existing target')
            destination_before = all_bytes(target.workspace)
            with patch.object(component, 'validate_backup', side_effect=reject_staging):
                with self.assertRaisesRegex(ValueError, 'owner rejected staged evidence'):
                    await target.projects.arestore_backup(self.archive)
            self.assertEqual(len(roots), 2)
            self.assertEqual(roots[0], self.archive / 'project')
            self.assertNotEqual(roots[1], roots[0])
            self.assertEqual(all_bytes(target.workspace), destination_before)
            self.assertEqual(list((target.workspace / 'projects' / '.transactions').iterdir()), [])
        self.assertEqual(all_bytes(self.archive), before)
        self.assertEqual(len(self.model.requests), 1)
        self.assertFalse(self.effect.exists())
