"""An explicit launch anchor remains authoritative regardless of truthiness."""

from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.project.settings import SettingsService
from hub.backend.project.presets import PresetService
from hub.config.preferences import PreferencesStore
from hub.config.presets import PresetStore
from hub.config.restart import restart_config
from tests.hub.test_settings import settings_backend


class FalseyPath(type(Path())):
    def __bool__(self):
        return False


class WorkspacePresenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.anchor = FalseyPath(self.root / 'launch')
        self.data = self.root / 'selected'
        self.config = HubConfig(self.data, _workspace_anchor=self.anchor, auto_title=False)

    def files(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob('*') if path.is_file()}

    def preset(self, store):
        return {'version': store.VERSION, 'global': store.globals(),
                'project': {'config': {}, 'components': [], 'conversation_storage': 'memory'}}

    def test_settings_and_presets_write_only_below_explicit_anchor(self):
        settings = SettingsService(SimpleNamespace(config=self.config))
        presets = PresetService(settings)
        settings.preferences.save('profile', {'display_name': 'Chosen anchor'})
        presets.store.save('Chosen', self.preset(presets.store))
        presets.store.select('Chosen')
        self.assertTrue((self.anchor / '.hub/preferences.json').exists(),
                        'Explicit anchor was discarded before the actual preference write')
        self.assertEqual(PreferencesStore(self.anchor).load()['profile']['display_name'], 'Chosen anchor')
        self.assertEqual(PresetStore(self.anchor).selected_project()['components'], [])
        self.assertFalse(self.data.exists(), 'Settings wrote under the active data root')

    async def test_startup_and_catalog_use_anchor_without_overwriting_preferences(self):
        PreferencesStore(self.anchor).save('general', {'editor': 'nano', 'workspace_root': ''})
        presets = PresetStore(self.anchor)
        presets.save('Empty components', self.preset(presets))
        presets.select('Empty components')
        before = {name: value for name, value in self.files().items() if name.startswith('launch/')}
        runtime = HubRuntime(self.config, settings_backend)
        await runtime.start()
        try:
            record = await runtime.project.aget_data()
            catalog = await runtime.settings_service.catalog()
            self.assertEqual(tuple(record.components), (), 'Startup ignored the selected launch preset')
            self.assertEqual(record.conversation_storage, 'memory')
            self.assertEqual(runtime.general.editor, 'nano')
            self.assertEqual(catalog['preferences']['general']['workspace_root'], str(self.anchor))
            self.assertEqual({name: value for name, value in self.files().items()
                              if name.startswith('launch/')}, before)
        finally:
            await runtime.close()

    def test_restart_reads_same_anchor_and_preserves_all_files(self):
        next_root = self.root / 'next'
        PreferencesStore(self.anchor).save('general', {'workspace_root': str(next_root)})
        before = self.files()
        launch = HubConfig(self.root / 'other-launch', auto_title=False)
        current = replace(self.config, project_id='old-project', session_id='old-session')
        result = restart_config(launch, current)
        self.assertEqual(result.workspace, next_root, 'Restart discarded the explicit launch anchor')
        self.assertEqual(result._workspace_anchor, self.anchor)
        self.assertIsNone(result.project_id)
        self.assertIsNone(result.session_id)
        self.assertEqual(self.files(), before)
        self.assertFalse(next_root.exists())

    def test_missing_anchor_uses_original_launch_workspace(self):
        launch_root = self.root / 'ordinary-launch'
        config = HubConfig(launch_root, auto_title=False)
        settings = SettingsService(SimpleNamespace(config=config))
        settings.preferences.save('profile', {'display_name': 'Ordinary anchor'})
        self.assertEqual(PreferencesStore(launch_root).load()['profile']['display_name'], 'Ordinary anchor')
        self.assertEqual(restart_config(config, replace(config, workspace=self.data)).workspace, launch_root)
