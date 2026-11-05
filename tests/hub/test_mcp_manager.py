"""Hub MCP integration uses native discovery, revision checks and Agent owners."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import tempfile
import unittest
from unittest.mock import patch

from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.execution.mcp import HubMCPConnector
from hub.backend.project.management_values import MCPOperation
from hub.components import ManagedComponents, ManagerKind
from llm.components.mcp import MCPComponent
from llm.components.tools.registry import Tool, ToolRegistry
from llm.core.agent_views import AgentSnapshot
from llm.providers.mcp import MCPConnector


class MCPManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.connections, self.effects = [], []

        @asynccontextmanager
        async def connector(definition):
            task = asyncio.current_task()
            self.connections.append(('open', task, deepcopy(definition)))
            tools = ToolRegistry()
            async def invoke(arguments):
                self.effects.append(arguments)
                return 'remote result'
            tools.register(Tool('read_notes', 'Read notes', {'type': 'object', 'properties': {}},
                                invoke, contract=MCPConnector.REMOTE_CONTRACT))
            try:
                yield tools
            finally:
                self.assertIs(asyncio.current_task(), task)
                self.connections.append(('close', task, deepcopy(definition)))

        self.runtime = HubRuntime(HubConfig(self.directory.name, prepare_provider=False,
            auto_title=False, postprocess=False, component_factories=(
                lambda: MCPComponent(connector=connector, revision='hub-test-v1'),)))
        self.addAsyncCleanup(self.runtime.close)
        await self.runtime.start()
        self.project = self.runtime.project
        self.data = await self.project.components.aget('mcp')
        await self.data.acreate({'transport': 'stdio', 'command': 'fake-server'}, identifier='notes')

    async def manage(self, action, argument='', version=None):
        return await self.runtime.manage_records(self.project.id, 'mcp', action, argument, version)

    async def discovery(self):
        record = await self.manage('read', 'notes')
        return await self.manage(MCPOperation.DISCOVER, 'notes', record['version'])

    @staticmethod
    def selection(record):
        agent = next(item for item in record['agents'] if item['name'] == 'assistant')
        return {**{key: record[key] for key in ('server', 'definition_version', 'tools_version')},
                'agent': 'assistant', 'agent_revision': agent['revision'], 'tools': ['read_notes']}

    async def test_crud_is_offline_discovery_closes_and_selection_only_revises_agent(self):
        self.assertEqual(ManagedComponents.MCP.manager, ManagerKind.MCP)
        self.assertEqual([item['name'] for item in await self.manage('list')], ['notes'])
        self.assertEqual(self.connections, [])
        before = AgentSnapshot.read(await self.project.agents.asnapshot('assistant'))
        config = deepcopy((await self.project.aget_data()).config.to_dict())
        record = await self.discovery()
        self.assertEqual([row[0] for row in self.connections], ['open', 'close'])
        self.assertEqual(self.effects, [])
        self.assertNotIn('planner', [item['name'] for item in record['agents']])
        self.assertNotIn('definition', record['agents'][0])  # No credentials in UI data.
        selected = self.selection(record)
        result = await self.manage(MCPOperation.SELECT, selected)
        self.assertEqual(result, {'agent': 'assistant', 'tools': 1})
        after = AgentSnapshot.read(await self.project.agents.asnapshot('assistant'))
        aliases = after.definition['resources']['mcp']['notes']
        self.assertEqual(list(aliases.values()), ['read_notes'])
        self.assertTrue(all(len(alias) <= 64 for alias in aliases))
        self.assertEqual(after.definition['tools'], before.definition['tools'])
        self.assertEqual((await self.project.aget_data()).config.to_dict(), config)
        self.assertEqual(len(self.connections), 2)  # Apply never connects or calls Tools.
        self.assertEqual(self.effects, [])
        descriptor, = await self.data.alist_tools('notes')
        self.assertTrue(descriptor['contract']['approval_required'])
        with self.assertRaisesRegex(ValueError, 'Agent changed'):
            await self.manage(MCPOperation.SELECT, selected)

    async def test_concurrent_definition_or_catalog_change_does_not_apply(self):
        record = await self.discovery()
        selection = self.selection(record)
        before = await self.project.agents.asnapshot('assistant')
        snapshot = await self.data.asnapshot('notes')
        await self.data.asave('notes', {'transport': 'stdio', 'command': 'changed-server'},
                              expected_version=snapshot['version'])
        with self.assertRaises(ValueError):
            await self.manage(MCPOperation.SELECT, selection)
        with self.assertRaises(ValueError):
            await self.manage(MCPOperation.DISCOVER, 'notes', record['definition_version'])
        self.assertEqual(await self.project.agents.asnapshot('assistant'), before)
        self.assertEqual(len(self.connections), 2)

    async def test_missing_tool_rejected_and_explicit_empty_selection_removes_only_server(self):
        record = await self.discovery()
        selection = self.selection(record)
        with self.assertRaisesRegex(ValueError, 'no longer available'):
            await self.manage(MCPOperation.SELECT, {**selection, 'tools': ['gone']})
        await self.manage(MCPOperation.SELECT, selection)
        record = await self.discovery()
        new_selection = self.selection(record)
        await self.manage(MCPOperation.SELECT, {**new_selection, 'tools': []})
        agent = AgentSnapshot.read(await self.project.agents.asnapshot('assistant'))
        self.assertNotIn('notes', agent.definition['resources']['mcp'])
        self.assertTrue(agent.definition['resources']['rag'])


class MCPTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_transport_omits_timeout_and_does_not_write_stderr_to_tui(self):
        options, logs = [], []
        class SDK:
            def __init__(self, **kwargs):
                options.append(kwargs)
                self.stderr = kwargs['errlog']
            @asynccontextmanager
            async def __call__(self, definition):
                self.stderr.write(b'test server message')
                yield 'client'
        with patch('hub.backend.execution.mcp.MCPConnector', SDK), patch(
                'hub.backend.execution.mcp.diagnostic', side_effect=lambda *a, **k: logs.append((a, k))):
            connector = HubMCPConnector()
            self.assertEqual(options, [])
            async with connector({'transport': 'stdio', 'command': 'test'}) as client:
                self.assertEqual(client, 'client')
            self.assertNotIn('timeout_seconds', options[0])
            self.assertTrue(options[0]['errlog'].closed)
            self.assertEqual(logs[0][1]['stderr_tail'], 'test server message')


class MCPSelectionUITests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_drafts_and_late_apply_keep_current_popup_and_public_revision(self):
        from prompt_toolkit.application.current import set_app
        from prompt_toolkit.input import DummyInput
        from examples.hub.preview import create_preview
        from tests.hub.support import SizedOutput
        from hub.widget.forms.mcp_manager import MCPManager, MCPToolSelection

        app, view, _ = create_preview(input=DummyInput(), output=SizedOutput())
        calls = []
        def request(*args, **kwargs):
            calls.append((args, kwargs))
        try:
            with set_app(app):
                manager = MCPManager(view, request, name='mcp', edit_text=None)
                record = {'server': 'notes', 'definition_version': 'server-v1', 'tools_version': 'catalog-v1',
                    'agents': [{'name': 'a', 'label': 'First', 'revision': 'a-v1', 'bindings': {}},
                               {'name': 'b', 'label': 'Second', 'revision': 'b-v1', 'bindings': {'alias': 'read_notes'}}],
                    'tools': [{'name': 'read_notes', 'description': 'Read notes',
                               'parameters': {'type': 'object', 'properties': {}},
                               'contract': MCPConnector.REMOTE_CONTRACT.to_dict(), 'classification': None}]}
                popup = MCPToolSelection(manager, record)
                view.dialogs.show(popup, popup.agent.button)
                popup.tools.current_values = ['read_notes']
                popup.select_agent('b')
                popup.tools.current_values = []
                popup.select_agent('a')
                self.assertEqual(popup.tools.current_values, ['read_notes'])
                popup.apply()
                popup.apply()
                self.assertEqual(len(calls), 1)
                args, _ = calls[0]
                self.assertEqual(args[0], MCPOperation.SELECT)
                self.assertEqual(args[1]['agent_revision'], 'a-v1')
                self.assertEqual(args[1]['tools'], ['read_notes'])
                popup.close()
                args[3]({'agent': 'a', 'tools': 1})
                self.assertIs(view._dialog, manager)
                self.assertFalse(popup.busy)
                self.assertTrue(manager.status)
        finally:
            view.close()
