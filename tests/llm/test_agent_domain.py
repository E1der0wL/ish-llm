"""The independent Project Agent API preserves owner, selection and reference evidence."""

import json
from pathlib import Path
import tempfile
import unittest
from jsonschema.exceptions import ValidationError

from llm.components.workflows import WorkflowGraph
from llm.core.agents import AgentDefinition
from llm.engines.graph import GraphEngine
from llm.llm import LargeLanguageModel


class AgentDomainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.app = LargeLanguageModel(self.root / 'workspace')
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate()

    def stored(self):
        root = self.project.paths.agents
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}

    async def test_engine_only_crud_has_no_component_selection_or_implicit_values(self):
        self.assertNotIn('agents', self.app.project_manager.components.names())
        self.assertNotIn('agents', self.project.data.components)
        self.assertEqual(await self.project.agents.alist(), {})
        await self.project.agents.acreate({'engine': 'loop'}, identifier='worker')
        snapshot = await self.project.agents.asnapshot('worker')
        self.assertEqual(snapshot['definition'], {'engine': 'loop'})
        self.assertNotIn('system_prompt', await self.project.agents.aprompt('worker'))
        for value in (None, ''):
            await self.project.agents.aupdate_prompt('worker', value, expected_revision=snapshot['revision'])
            snapshot = await self.project.agents.asnapshot('worker')
            self.assertEqual(snapshot['definition'], {'engine': 'loop', 'system_prompt': value})
        await self.project.agents.adelete('worker', expected_revision=snapshot['revision'])
        self.assertEqual(await self.project.agents.alist(), {})

    async def test_unknown_or_invalid_selected_engine_is_rejected_before_writing(self):
        self.app.engines.register('graph', GraphEngine(handlers={}))
        before = self.stored()
        for definition in ({'engine': 'missing'}, {'engine': 'graph'},
                           {'engine': 'graph', 'engine_options': {'workflow': 'flow'}, 'tools': []},
                           {'engine': 'loop', 'engine_options': {'policy': {'max_iterations': 0}}}):
            with self.subTest(definition=definition), self.assertRaises((ValueError, ValidationError)):
                await self.project.agents.acreate(definition, identifier='worker')
            self.assertEqual(self.stored(), before)
        self.assertEqual(await self.project.sessions.alist(), [])

    async def test_stale_cas_and_deleted_or_closed_project_handles_cannot_write(self):
        agents = self.project.agents
        await agents.acreate({'engine': 'loop'}, identifier='worker')
        stale = await agents.asnapshot('worker')
        updated = await agents.arevise('worker', {'engine': 'loop', 'purpose': 'new'},
                                      expected_revision=stale['revision'])
        before = self.stored()
        with self.assertRaisesRegex(ValueError, 'edit_conflict'):
            await agents.arevise('worker', stale['definition'], expected_revision=stale['revision'])
        with self.assertRaisesRegex(ValueError, 'edit_conflict'):
            await agents.adelete('worker', expected_revision=stale['revision'])
        self.assertEqual(self.stored(), before)
        await self.project.adelete()
        with self.assertRaisesRegex(ValueError, 'deleted'):
            await agents.adelete('worker', expected_revision=updated['revision'])
        self.assertEqual(self.stored(), before)
        await self.app.shutdown()
        with self.assertRaises(RuntimeError):
            await agents.alist()

    async def test_missing_domain_records_block_project_load_clone_and_backup(self):
        await self.project.agents.acreate({'engine': 'loop'}, identifier='worker')
        record = self.project.paths.agents / 'records/worker.json'
        record.unlink()
        before = self.stored()
        for operation in (lambda: self.app.projects.aload(self.project.id), self.project.aclone,
                          lambda: self.project.abackup(self.root / 'backup'), self.project.agents.alist):
            with self.assertRaisesRegex(ValueError, 'missing'):
                await operation()
            self.assertEqual(self.stored(), before)
        self.assertFalse(record.exists())
        self.assertFalse((self.root / 'backup').exists())

    async def test_clone_and_portable_backup_keep_identity_and_explicit_values(self):
        definition = {'engine': 'loop', 'system_prompt': None, 'resources': {'rag': False},
                      'metadata': {'zero': 0, 'empty': []}}
        await self.project.agents.acreate(definition, identifier='worker')
        clone = await self.project.aclone()
        self.assertEqual(await clone.agents.aload('worker'), definition)
        raw = json.loads((clone.paths.agents / 'records/worker.json').read_bytes())
        self.assertEqual(raw['project_id'], clone.id)
        self.assertEqual(raw['revision'], AgentDefinition.revision(definition))
        archive = await self.project.abackup(self.root / 'backup')
        async with LargeLanguageModel(self.root / 'destination') as other:
            restored = await other.projects.arestore_backup(archive)
            self.assertEqual(await restored.agents.aload('worker'), definition)
            self.assertEqual(restored.id, self.project.id)

    async def test_retained_workflow_blocks_agent_deletion_without_component_activation(self):
        await self.project.agents.acreate({'engine': 'loop'}, identifier='worker')
        revision = (await self.project.agents.asnapshot('worker'))['revision']
        await self.project.components.aselect(['workflows'])
        workflows = self.project.components.workflows
        graph = (WorkflowGraph(entry='agent').node('agent', 'agent', agent='worker')
                 .node('end', 'end').connect('agent', 'end').to_dict())
        await workflows.acreate(graph, identifier='flow')
        await self.project.components.aselect([])
        before = self.stored()
        with self.assertRaisesRegex(ValueError, 'handler owner is unavailable'):
            await self.project.agents.adelete('worker', expected_revision=revision)
        self.assertEqual(self.stored(), before)
        from llm.engines.graph.agent import AgentNode
        self.app.engines.register('graph', GraphEngine(handlers={'agent': AgentNode(engines=self.app.engines)}))
        with self.assertRaisesRegex(ValueError, 'referenced'):
            await self.project.agents.adelete('worker', expected_revision=revision)
        self.assertEqual(self.stored(), before)
        await self.project.components.aselect(['workflows'])
        snapshot = await workflows.asnapshot('flow')
        await workflows.adelete('flow', expected_version=snapshot['version'])
        await self.project.agents.adelete('worker', expected_revision=revision)
        self.assertEqual(await self.project.agents.alist(), {})

    async def test_retained_loop_uses_registered_handler_reference_owner_and_preserves_unrelated_agents(self):
        from llm.engines.graph.agent import AgentNode
        from unittest.mock import Mock
        owner = AgentNode(engines=self.app.engines)
        owner.agent_references = Mock(wraps=owner.agent_references)
        owner.__call__ = Mock(side_effect=AssertionError('reference checks must not execute'))
        self.app.engines.register('graph', GraphEngine(handlers={'delegate': owner}))
        for identifier in ('worker', 'unrelated'):
            await self.project.agents.acreate({'engine': 'loop'}, identifier=identifier)
        body = (WorkflowGraph(entry='delegate').node('delegate', 'delegate', agent='worker')
                .node('end', 'end').connect('delegate', 'end').to_dict())
        flow = (WorkflowGraph(entry='loop').node('loop', 'loop', body=body, max_iterations=1, on_limit='continue')
                .node('end', 'end').connect('loop', 'end').to_dict())
        await self.project.components.aselect(['workflows'])
        await self.project.components.workflows.acreate(flow, identifier='retained')
        await self.project.components.aselect([])
        before = self.stored()
        revision = (await self.project.agents.asnapshot('worker'))['revision']
        with self.assertRaisesRegex(ValueError, 'referenced by workflows/retained'):
            await self.project.agents.adelete('worker', expected_revision=revision)
        self.assertEqual(self.stored(), before)
        revision = (await self.project.agents.asnapshot('unrelated'))['revision']
        await self.project.agents.adelete('unrelated', expected_revision=revision)
        self.assertEqual(set(await self.project.agents.alist()), {'worker'})
        self.assertEqual(owner.agent_references.call_count, 2)
        owner.__call__.assert_not_called()

    async def test_checkpoint_references_ignore_application_payload_and_keep_nested_selections(self):
        from llm.core.checkpoints import build_envelope, CheckpointStatus
        from llm.engines.agents import AgentExecution
        self.assertEqual(AgentExecution.referenced_agents({'metadata': {'agent_id': 'unrelated'}}), set())
        for record in ({'agent_binding': {'agent_id': 'worker'}},
                       {'handler_selection': {'binding': {'agent_id': 'worker'}}}):
            for _ in range(3):
                self.assertEqual(AgentExecution.referenced_agents(record), {'worker'})
                record = {**build_envelope(engine_scope='parent', checkpoint_name='graph', local_key='child',
                    status=CheckpointStatus.COMPLETED, container=True, requires_retry=False), 'payload': record}

    async def test_selected_engine_owns_workflow_reference_and_deletion_protection(self):
        from llm.components.workflows import WorkflowGraph
        self.app.engines.register('orchestrator', GraphEngine(handlers={}))
        await self.project.components.aselect(['workflows'])
        workflows = await self.project.components.aget('workflows')
        await workflows.acreate(WorkflowGraph(entry='end').node('end', 'end').to_dict(), identifier='flow')
        await self.project.agents.acreate({'engine': 'orchestrator', 'engine_options': {'workflow': 'flow'}},
                                         identifier='coordinator')
        path = self.project.paths.root / 'workflows/records/flow.json'
        before = path.read_bytes()
        for operation in (lambda: workflows.adelete('flow'),
                          lambda: self.project.components.aremove('workflows', permanent=True)):
            with self.assertRaisesRegex(ValueError, 'referenced'):
                await operation()
            self.assertEqual(path.read_bytes(), before)
        value = await self.project.agents.asnapshot('coordinator')
        await self.project.agents.adelete('coordinator', expected_revision=value['revision'])
        await workflows.adelete('flow')
        self.assertFalse(path.exists())
