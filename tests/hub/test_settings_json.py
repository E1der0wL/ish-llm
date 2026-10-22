"""JSON editor preserves draft/save boundaries, hidden engines and host locks."""

import asyncio
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.backend.runtime import HubConfig
from hub.ui.application import create_application
from hub.backend.project.naming import TITLE_ENGINE
from tests.hub.support import SizedOutput, eventually, auxiliary, settings_button
from tests.hub.test_live import until


class SettingsJsonTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.pipe = self.enterContext(create_pipe_input())
        self.app, self.controller = create_application(HubConfig(directory, auto_title=True),
            input=self.pipe, output=SizedOutput())
        self.view = self.controller.view
        self.screen = self.view.settings
        self.task = asyncio.create_task(self.app.run_async(pre_run=lambda: self.view.show(self.app)))
        await until(lambda: self.view.connected and not self.view.projects.busy)
        await asyncio.wrap_future(self.controller.worker.call('manage_records', self.view.project_id,
            'agents', 'create', {'name': 'settings-graph', 'text': json.dumps({
                'engine': 'graph', 'engine_options': {'workflow': 'settings-only'}})}))
        await until(lambda: 'settings-graph' in self.view.agents)
        self.view.choose_agent('settings-graph')
        await auxiliary(self.app, self.pipe, self.view, 'settings')
        await until(lambda: not self.screen.busy and self.screen.page is not None and self.view.settings_open)

    async def asyncTearDown(self):
        self.app.exit()
        await self.task
        await asyncio.to_thread(self.controller.close)

    async def edit(self, transform):
        async def editor(text, settings, *, suffix):
            self.assertEqual(suffix, '.json')
            self.assertIs(settings, self.view.general)
            return transform(text)
        with patch('hub.ui.host.edit_text', new=AsyncMock(side_effect=editor)) as mock:
            await auxiliary(self.app, self.pipe, self.view, 'json')
            await until(lambda: mock.await_count == 1 and not self.screen.busy)
        return mock

    async def test_home_settings_targets_loop_even_when_graph_is_selected(self):
        self.assertEqual(self.screen.page.section, 'engine:loop')
        self.assertEqual(self.screen.query, '')
        self.assertEqual(self.screen.page.query, '')
        self.assertEqual(self.view.engine, 'graph')
        self.assertIs(self.app.layout.current_control, self.screen.page.fields()[0].input.control)

    async def test_json_project_edit_save_cancel_unknown_keys_and_hidden_engine(self):
        screen = self.screen
        before = screen.page.values()
        hidden = {'config': {'completion': {'model': 'private/title'}}}
        def completion(values):
            return values['config']['parameters']['engines']['loop']['config']['completion']
        for form in screen.page.forms():
            form.original.setdefault('config', {}).setdefault('parameters', {}).setdefault('engines', {})[TITLE_ENGINE] = deepcopy(hidden)
        def modify(text):
            values = json.loads(text)
            self.assertNotIn(TITLE_ENGINE, values['config']['parameters']['engines'])
            values['config']['parameters']['engines']['loop']['config'].setdefault('completion', {})['model'] = 'edited/model'
            completion(values)['extra_body'] = {'vendor_extension': None}
            return json.dumps(values)
        await self.edit(modify)
        self.assertTrue(screen.page.dirty, screen.status)
        values = screen.page.values()
        self.assertEqual(completion(values)['extra_body'], {'vendor_extension': None})
        self.assertEqual(values['config']['parameters']['engines'][TITLE_ENGINE], hidden)
        self.assertNotEqual(values, before)
        self.assertEqual(screen.page.record['values']['config'], before['config'])
        self.assertEqual(screen.page.section, 'engine:loop')
        settings_button(self.app, self.pipe, self.view, 'save')
        await until(lambda: not screen.busy and not screen.page.dirty)
        self.assertEqual(completion(screen.page.record['values'])['extra_body'], {'vendor_extension': None})
        def remove(text):
            values = json.loads(text)
            completion(values).pop('extra_body')
            return json.dumps(values)
        await self.edit(remove)
        self.assertNotIn('extra_body', completion(screen.page.values()))
        settings_button(self.app, self.pipe, self.view, 'cancel')
        await until(lambda: not screen.page.dirty)
        self.assertEqual(completion(screen.page.values())['extra_body'], {'vendor_extension': None})

    async def test_invalid_json_is_retained_for_repair_and_wrong_types_rejected(self):
        screen = self.screen
        with set_app(self.app):
            screen.choose('profile')
        old = screen.page
        await self.edit(lambda _: '{invalid')
        self.assertIs(screen.page, old)
        self.assertTrue(screen.status)
        self.assertEqual(screen.json_editor.recovery, (old, '{invalid'))
        def repair(text):
            self.assertEqual(text, '{invalid')
            values = old.form.values()
            values['email'] = 'editor@example.test'
            return json.dumps(values)
        await self.edit(repair)
        self.assertIsNone(screen.json_editor.recovery)
        self.assertEqual(screen.page.form.values()['email'], 'editor@example.test')
        self.assertTrue(screen.page.dirty)
        saved = screen.drafts.preferences['profile']['email']
        self.assertNotEqual(saved, 'editor@example.test')
        draft = screen.page
        await self.edit(lambda _: '{"email": 5}')
        self.assertIs(screen.page, draft)
        self.assertTrue(screen.status)
        settings_button(self.app, self.pipe, self.view, 'cancel')
        await until(lambda: screen.page is not draft)
        self.assertEqual(screen.page.form.values()['email'], saved)

    async def test_host_fixed_json_leaves_cannot_be_changed(self):
        screen = self.screen
        page = screen.page
        path = ('config', 'parameters', 'engines', 'loop', 'config', 'completion', 'model')
        page.engine_forms['loop'].locked.add(path)
        def modify(text):
            values = json.loads(text)
            values['config']['parameters']['engines']['loop']['config'].setdefault('completion', {})['model'] = 'forbidden/model'
            return json.dumps(values)
        await self.edit(modify)
        self.assertIs(screen.page, page)
        self.assertIn(screen.t('settings_host_locked'), screen.status)

    async def test_removing_current_component_in_json_keeps_navigation_consistent(self):
        screen = self.screen
        with set_app(self.app):
            screen.choose('component:goals')
        def remove(text):
            values = json.loads(text)
            values['components'].remove('goals')
            return json.dumps(values)
        await self.edit(remove)
        self.assertEqual(screen.page.section, 'project:general')
        self.assertEqual(screen.selected, 'project:general')
        self.assertEqual(screen.navigation.left_key, 'project:general')
        self.assertNotIn('goals', screen.page.component_forms)
        self.assertTrue(screen.page.dirty)

    async def test_hidden_engine_preservation_never_coerces_invalid_parents(self):
        page = self.screen.page
        before = page.values()
        self.assertIn(TITLE_ENGINE, before['config']['parameters']['engines'])
        for value in (None, 'invalid'):
            with self.subTest(value=value):
                edited = deepcopy(before)
                edited['config'] = value
                with self.assertRaises(ValueError):
                    self.screen.json_editor.candidate(page, before, json.dumps(edited))
                self.assertEqual(page.values(), before)
