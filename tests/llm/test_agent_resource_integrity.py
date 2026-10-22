"""Stored Agent resources protect the same originals through direct and parent deletion."""

from pathlib import Path
import tempfile
import unittest

from llm.core.agents import AgentDefinition
from llm.components.mcp import MCPComponent
from llm.components.prompts import PromptComponent
from llm.llm import LargeLanguageModel
from tests.llm import test_agent_workflow as workflow


def source_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*')
            if path.is_file() and 'logs' not in path.relative_to(root).parts}


class AgentResourceDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.parts = [PromptComponent(), MCPComponent()]
        self.app = LargeLanguageModel(self.root, components=self.parts)
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=[part.name for part in self.parts])
        self.prompts = self.project.components.prompts
        self.mcp = self.project.components.mcp
        await self.prompts.acreate({'messages': [{'role': 'system', 'content': 'Original instructions'}]}, identifier='instructions')
        await self.mcp.acreate({'transport': 'streamable_http', 'url': 'https://example.invalid/mcp'}, identifier='remote')
        await self.project.agents.acreate({'purpose': 'Read original resources', 'engine': 'loop',
            'resources': {'prompt': 'instructions', 'mcp': {'remote': {'read_remote': 'read'}}}}, identifier='reader')

    async def _assert_protected(self, name, identifier, *, parent=False, disabled=False, expected_error=ValueError):
        if disabled:
            self.assertNotIn('agents', self.project.data.components)
            await self.app.shutdown()
            self.app = LargeLanguageModel(self.root, components=self.parts)
            self.addAsyncCleanup(self.app.shutdown)
            self.project = await self.app.projects.aload(self.project.id)
        before = source_bytes(self.project.paths.root)
        rejected = False
        try:
            if parent:
                await self.project.components.aremove(name, permanent=True)
            else:
                target = await self.project.components.aget(name)
                review = await target.asnapshot(identifier)
                await target.adelete(identifier, expected_version=review['version'])
        except expected_error:
            rejected = True
        self.assertEqual((rejected, source_bytes(self.project.paths.root) == before), (True, True))

    async def test_prompt_reference_blocks_actual_record_deletion(self):
        await self._assert_protected('prompts', 'instructions')

    async def test_mcp_reference_blocks_actual_record_deletion(self):
        await self._assert_protected('mcp', 'remote')

    async def test_prompt_reference_blocks_component_deletion(self):
        await self._assert_protected('prompts', 'instructions', parent=True)

    async def test_mcp_reference_blocks_component_deletion(self):
        await self._assert_protected('mcp', 'remote', parent=True)

    async def test_disabled_agent_after_restart_still_protects_prompt(self):
        await self._assert_protected('prompts', 'instructions', disabled=True)

    async def test_disabled_agent_after_restart_still_protects_mcp_parent(self):
        await self._assert_protected('mcp', 'remote', parent=True, disabled=True)

    async def test_selected_agent_blocks_disabling_its_required_resources(self):
        before = source_bytes(self.project.paths.root)
        for name in ('prompts', 'mcp'):
            with self.assertRaisesRegex(ValueError, 'referenced'):
                await self.project.components.aremove(name)
            with self.assertRaisesRegex(ValueError, 'referenced'):
                await self.project.components.aselect([part.name for part in self.parts if part.name != name])
            self.assertEqual(source_bytes(self.project.paths.root), before)

    async def test_each_persisted_consumer_must_release_before_explicit_deletion(self):
        agents = self.project.agents
        original = await agents.aload('reader')
        await agents.acreate(original, identifier='second')
        await agents.aupdate('reader', {'resources': {}},
                             expected_revision=(await agents.asnapshot('reader'))['revision'])
        await self._assert_protected('prompts', 'instructions')
        await self._assert_protected('mcp', 'remote', parent=True)
        await agents.aupdate('second', {'resources': {}},
                             expected_revision=(await agents.asnapshot('second'))['revision'])
        for target, identifier in ((self.prompts, 'instructions'), (self.mcp, 'remote')):
            review = await target.asnapshot(identifier)
            await target.adelete(identifier, expected_version=review['version'])
            self.assertEqual(await target.alist(), {})
        self.assertEqual(set(await agents.alist()), {'reader', 'second'})

    async def test_missing_disabled_agent_inventory_is_not_an_empty_reference_set(self):
        self.assertNotIn('agents', self.project.data.components)
        (self.project.paths.root / 'agents' / 'records.manifest.json').unlink()
        await self._assert_protected('prompts', 'instructions', expected_error=FileNotFoundError)
        await self._assert_protected('mcp', 'remote', parent=True, expected_error=FileNotFoundError)

    async def test_valid_project_termination_can_remove_both_sides_of_internal_references(self):
        await self.project.adelete(permanent=True)
        self.assertFalse(self.project.paths.root.exists())
        self.assertEqual(await self.app.projects.alist(), [])

    async def test_clone_and_disabled_backup_restore_keep_their_copied_agent_references(self):
        clone = await self.project.aclone()
        self.assertNotIn('agents', self.project.data.components)
        archive = await self.project.abackup(self.root / 'archive')
        async with LargeLanguageModel(self.root / 'destination', components=self.parts) as app:
            restored = await app.projects.arestore_backup(archive)
            for project in (clone, restored):
                self.assertNotIn('agents', (await project.aget_data()).components)
                self.assertIn('reader', await project.agents.alist())
                self.assertTrue((project.paths.root / 'agents' / 'records' / 'reader.json').is_file())
                before = source_bytes(project.paths.root)
                with self.assertRaisesRegex(ValueError, 'referenced'):
                    await project.components.prompts.adelete('instructions')
                with self.assertRaisesRegex(ValueError, 'referenced'):
                    await project.components.aremove('mcp', permanent=True)
                self.assertEqual(source_bytes(project.paths.root), before)

    async def test_rag_resource_protects_the_component_without_selecting_document_ids(self):
        from llm.components.rag import RAGComponent
        from tests.llm.configuration_fixtures import rag_project
        async with LargeLanguageModel(self.root / 'rag-case', components=[RAGComponent()]) as app:
            project = await app.projects.acreate(components=['rag'], config=rag_project())
            await project.agents.acreate({'purpose': 'Search corpus', 'engine': 'loop',
                'resources': {'rag': True}}, identifier='searcher')
            before = source_bytes(project.paths.root)
            for permanent in (False, True):
                with self.assertRaisesRegex(ValueError, 'referenced'):
                    await project.components.aremove('rag', permanent=permanent)
                self.assertEqual(source_bytes(project.paths.root), before)
            await project.agents.aupdate('searcher', {'resources': {'rag': False}},
                expected_revision=(await project.agents.asnapshot('searcher'))['revision'])
            await project.components.aremove('rag', permanent=True)
            self.assertFalse((project.paths.root / 'rag').exists())


class AgentResourceConsumerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = workflow.AgentWorkflowTests.asyncSetUp
    setup_graph = workflow.AgentWorkflowTests.setup_graph
    run_graph = workflow.AgentWorkflowTests.run_graph

    async def test_rejected_prompt_deletion_preserves_real_model_input_and_call_count(self):
        effect = self.root / 'model-effects.txt'
        model = workflow.ScriptedCompletion(workflow.answer('first'), workflow.answer('second'))
        def completion(**kwargs):
            with effect.open('a') as stream:
                stream.write('model\n')
            yield from model(**kwargs)
        self.profile.update(tools=[], resources={'prompt': 'instructions'})
        await self.setup_graph(workflow.agent_graph(), completion, extra_components=(PromptComponent(),))
        prompts = self.project.components.prompts
        await prompts.acreate({'messages': [{'role': 'system', 'content': 'Original persisted instruction'}]}, identifier='instructions')
        first = await self.run_graph()
        self.assertEqual(first.data.status, 'completed', first.data.error)
        self.assertIn('Original persisted instruction', model.requests[0]['messages'][0]['content'])
        self.assertEqual(effect.read_text(), 'model\n')
        await self.session.run.shutdown()
        before = source_bytes(self.project.paths.root)
        for action in (lambda: prompts.adelete('instructions'),
                       lambda: self.project.components.aremove('prompts', permanent=True)):
            with self.assertRaisesRegex(ValueError, 'referenced'):
                await action()
            self.assertEqual(source_bytes(self.project.paths.root), before)
            self.assertEqual(effect.read_text(), 'model\n')
        self.session = await self.project.sessions.acreate('second request')
        second = await self.run_graph()
        self.assertEqual(second.data.status, 'completed', second.data.error)
        self.assertEqual(model.requests[0]['messages'][0], model.requests[1]['messages'][0])
        self.assertEqual(effect.read_text(), 'model\nmodel\n')

    async def test_rejected_mcp_deletion_cannot_connect_or_repeat_actual_tool_effect(self):
        from contextlib import asynccontextmanager
        from llm.components.tools import Tool, ToolRegistry
        from tests.llm.support.runtime_tools import TEST_CONTRACT
        effect = self.root / 'mcp-effects.txt'
        connected = []
        async def act(arguments):
            with effect.open('a') as stream:
                stream.write('remote effect\n')
            return 'done'
        @asynccontextmanager
        async def connector(definition):
            connected.append(definition)
            try:
                yield ToolRegistry((Tool('read', 'read', {'type': 'object'}, act, contract=TEST_CONTRACT),))
            finally:
                connected.append('closed')
        self.profile.update(tools=[], resources={'mcp': {'remote': {'read_remote': 'read'}}})
        model = workflow.ScriptedCompletion([workflow.chunk(calls=[workflow.call('{}', name='read_remote')],
                                           finish='tool_calls')], workflow.answer('done'))
        await self.setup_graph(workflow.agent_graph(), model, extra_components=(MCPComponent(connector=connector),))
        mcp = self.project.components.mcp
        await mcp.acreate({'transport': 'streamable_http', 'url': 'https://example.invalid/mcp'}, identifier='remote')
        with self.assertRaisesRegex(ValueError, 'referenced'):
            await mcp.adelete('remote')
        self.assertFalse(effect.exists())
        self.assertEqual(connected, [])
        run = await self.run_graph()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(effect.read_text(), 'remote effect\n')
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(len(connected), 2)
        self.assertEqual(connected[-1], 'closed')
        await self.session.run.shutdown()
        before = source_bytes(self.project.paths.root)
        with self.assertRaisesRegex(ValueError, 'referenced'):
            await self.project.components.aremove('mcp', permanent=True)
        self.assertEqual(source_bytes(self.project.paths.root), before)
        self.assertEqual(effect.read_text(), 'remote effect\n')
        self.assertEqual(len(connected), 2)
