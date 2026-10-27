"""Hub creation chooses stored Agents; submissions never synthesize one."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.defaults import HubDefaults
from llm.engines.base import BaseEngine


class HubAgentSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    async def test_explicit_creation_definition_and_request_omission_are_preserved(self):
        effects = []
        async def action(context):
            effects.append(context.run.agent_id)
            yield 'done'
        definition = {'engine': 'host', 'description': '', 'metadata': {'explicit': None}}
        original = deepcopy(definition)
        runtime = HubRuntime(HubConfig(self.root, auto_title=False, agent_id='writer',
            agent_definition=definition, engine_factories={'host': lambda: BaseEngine(action=action)}))
        self.addAsyncCleanup(runtime.close)
        definition['description'] = 'caller mutation'
        await runtime.start()
        await runtime.new_session()
        self.assertEqual(await runtime.project.agents.aload('writer'), original)
        session_id = next(iter(runtime.sessions))
        request = await runtime.submit(session_id, 'hello', 'writer')
        run = (await runtime.sessions[session_id].run.request(request).wait(timeout=15))[0]
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(run.data.agent_id, 'writer')
        self.assertNotIn('request_context', run.data.metadata)
        self.assertNotIn('tools', await runtime.project.agents.aload('writer'))
        self.assertEqual(effects, ['writer'])
        before = await runtime.project.agents.alist()
        with self.assertRaises((ValueError, FileNotFoundError)):
            await runtime.submit(session_id, 'missing', 'host')
        self.assertEqual(await runtime.project.agents.alist(), before)
        self.assertEqual(effects, ['writer'])

    async def test_default_creation_has_actual_public_and_title_agents(self):
        runtime = HubRuntime(HubConfig(self.root))
        self.addAsyncCleanup(runtime.close)
        await runtime.start()
        await runtime.new_session()
        definitions = await runtime.project.agents.alist()
        self.assertEqual(definitions['_hub_title'], {'engine': '_hub_title'})
        self.assertEqual(definitions['assistant']['engine'], 'loop')
        self.assertIn('agent_run', definitions['assistant']['tools'])
        self.assertIn('file_read', definitions['assistant']['tools'])
        catalog = await runtime.execution_catalog(next(iter(runtime.sessions)))
        self.assertEqual(set(catalog), {'agents'})
        self.assertEqual(set(catalog['agents']), {'assistant', 'planner'})
        self.assertEqual(definitions['planner'], HubDefaults.PLANNER_AGENT)
        with self.assertRaisesRegex(ValueError, 'Internal Agent'):
            await runtime.submit(next(iter(runtime.sessions)), 'hello', '_hub_title')

    async def test_existing_project_is_not_reseeded_after_explicit_agent_deletion(self):
        config = HubConfig(self.root, auto_title=False)
        first = HubRuntime(config)
        await first.start()
        await first.new_session()
        for session in first.sessions.values():
            await session.run.shutdown()
            await session.asave(agent_ids=[])
        planner = await first.project.agents.asnapshot('planner')
        await first.project.agents.adelete('planner', expected_revision=planner['revision'])
        await first.project.components.workflows.adelete(HubDefaults.CODING_WORKFLOW_ID)
        snapshot = await first.project.agents.asnapshot('assistant')
        await first.project.agents.adelete('assistant', expected_revision=snapshot['revision'])
        project_id = first.project.id
        await first.close()
        second = HubRuntime(config)
        self.addAsyncCleanup(second.close)
        await second.start()
        self.assertEqual(second.project.id, project_id)
        self.assertEqual(await second.project.agents.alist(), {})
        with self.assertRaises((ValueError, FileNotFoundError)):
            await second.submit(next(iter(second.sessions)), 'hello')
        self.assertEqual(await second.project.agents.alist(), {})
