"""Explicit file roots select actual effects and imports before any default root."""

import asyncio
from functools import partial
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from prompt_toolkit.input import create_pipe_input

from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.tools.builtin import HubBuiltinTools
from hub.backend.worker import BackendWorker
from hub.ui.application import create_application
from tests.hub.support import SizedOutput
from tests.hub.test_settings import settings_backend
from tests.hub.test_workspace_presence import FalseyPath


class FalseyString(str):
    def __bool__(self):
        return False


class FileRootPresenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / 'selected'
        self.project_root = self.root / 'project'
        self.work.mkdir()
        self.project_root.mkdir()
        self.record = SimpleNamespace(id='project', paths=SimpleNamespace(root=self.project_root))

    async def create_file(self, value, name):
        component = HubBuiltinTools(value)
        try:
            toolkit = component.for_project(self.record)
            await toolkit.registry.get('file_create').handler({'path': name, 'content': 'actual effect'})
        finally:
            await component.close()

    async def test_falsey_path_selects_explicit_tool_effect_root(self):
        await self.create_file(FalseyPath(self.work), 'path.txt')
        self.assertEqual(((self.work / 'path.txt').exists(), (self.project_root / 'path.txt').exists()),
                         (True, False), 'Actual file effect used the wrong root')
        self.assertEqual((self.work / 'path.txt').read_text(), 'actual effect')

    async def test_falsey_string_selects_explicit_tool_effect_root(self):
        await self.create_file(FalseyString(str(self.work)), 'string.txt')
        self.assertEqual(((self.work / 'string.txt').exists(), (self.project_root / 'string.txt').exists()),
                         (True, False), 'Actual file effect used the wrong root')

    async def test_invalid_falsey_roots_fail_before_any_tool_effect(self):
        for index, value in enumerate((False, 0, [], {})):
            with self.subTest(value=repr(value)):
                name = f'invalid-{index}.txt'
                error, stage = None, 'config'
                try:
                    config = HubConfig(self.root / 'workspace', file_root=value, auto_title=False)
                    stage = 'component'
                    await self.create_file(config.file_root, name)
                except TypeError as caught:
                    error = caught
                self.assertFalse((self.project_root / name).exists(), 'Invalid root caused an actual file effect')
                self.assertFalse((self.work / name).exists())
                self.assertIsInstance(error, TypeError)
                self.assertEqual(stage, 'config')

    async def test_none_and_ordinary_roots_keep_existing_file_effect_locations(self):
        await self.create_file(None, 'default.txt')
        await self.create_file(str(self.work), 'explicit.txt')
        self.assertTrue((self.project_root / 'default.txt').exists())
        self.assertFalse((self.work / 'default.txt').exists())
        self.assertTrue((self.work / 'explicit.txt').exists())
        self.assertFalse((self.project_root / 'explicit.txt').exists())

    async def test_runtime_snapshot_and_skill_import_share_explicit_file_root(self):
        config = HubConfig(self.root / 'workspace', file_root=FalseyPath(self.work), auto_title=False)
        runtime = HubRuntime(config)
        try:
            await runtime.start()
            record = await runtime.project.aget_data()
            selected, wrong = self.work / 'guide.md', record.paths.root / 'guide.md'
            selected.write_text('Selected source instructions')
            wrong.write_text('Wrong source instructions')
            snapshot = await runtime.snapshot()
            await runtime.manage_records(runtime.project.id, 'skills', 'import', 'guide.md')
            imported = await runtime.manage_records(runtime.project.id, 'skills', 'read', 'guide')
            self.assertEqual((snapshot.file_root, json.loads(imported['text'])['instructions']),
                             (str(self.work), 'Selected source instructions'))
            self.assertEqual(selected.read_text(), 'Selected source instructions')
            self.assertEqual(wrong.read_text(), 'Wrong source instructions')
        finally:
            await runtime.close()

    async def test_initial_ui_root_preserves_explicit_path(self):
        with create_pipe_input() as pipe:
            app, controller = create_application(
                HubConfig(self.root / 'workspace', file_root=FalseyPath(self.work), auto_title=False),
                input=pipe, output=SizedOutput(),
                worker_factory=partial(BackendWorker, backend_factory=settings_backend))
            try:
                self.assertEqual(Path(controller.view.file_root), self.work)
            finally:
                await asyncio.to_thread(controller.close)

    def test_empty_string_uses_current_directory_like_an_explicit_cli_path(self):
        # Use a child cwd so other async/UI activity never observes a global chdir.
        code = '''import asyncio, sys
from pathlib import Path
from types import SimpleNamespace
from hub.backend.tools.builtin import HubBuiltinTools
async def main():
    component = HubBuiltinTools("")
    try:
        toolkit = component.for_project(SimpleNamespace(id="child", paths=SimpleNamespace(root=Path(sys.argv[1]))))
        await toolkit.registry.get("file_create").handler({"path":"empty.txt","content":"actual effect"})
    finally:
        await component.close()
asyncio.run(main())
'''
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
        result = subprocess.run([sys.executable, '-c', code, str(self.project_root)],
                                cwd=self.work, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(((self.work / 'empty.txt').exists(), (self.project_root / 'empty.txt').exists()),
                         (True, False), 'Empty explicit path was replaced with the Project root')
