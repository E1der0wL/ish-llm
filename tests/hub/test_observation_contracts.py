"""Display observations cannot invent evidence or rescan settled history."""

import asyncio
import copy
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from hub.backend.observability.snapshot import SnapshotReader
from hub.backend.observability.workflow import WorkflowObservation
from hub.backend.progress import OperationProgress
from hub.backend.runtime import HubConfig, HubRuntime
from hub.locales.keys import MessageKey
from llm.core.models import Message
from llm.engines.base import BaseEngine
from llm.components.workflows import WorkflowGraph


class ObservationContracts(unittest.IsolatedAsyncioTestCase):
    async def test_reverse_order_invitation_is_canonical_and_survives_reopen(self):
        async def action(context):
            yield 'done'
        with tempfile.TemporaryDirectory() as root:
            config = HubConfig(root, auto_title=False, agent_id='z',
                agent_definition={'engine': 'test'},
                engine_factories={'test': lambda: BaseEngine(action=action)})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                await runtime.project.agents.acreate({'engine': 'test'}, identifier='a')
                sid = await runtime.new_session()
                result = await runtime.invite_agent(runtime.project.id, sid, 'a')
                self.assertEqual(result, ('a', 'z'))
                self.assertEqual(await runtime.invite_agent(runtime.project.id, sid, 'a'), result)
                runs = await (await runtime.sessions[sid].run.submit('hello', recipients='all')).wait(timeout=10)
                self.assertEqual({run.data.agent_id for run in runs}, {'a', 'z'})
            finally:
                await runtime.close()
            reopened = HubRuntime(config)
            try:
                await reopened.start()
                self.assertEqual((await reopened.sessions[sid].aget_data()).agent_ids, ['a', 'z'])
            finally:
                await reopened.close()

    async def test_history_larger_than_old_cache_does_not_repeat_io(self):
        graph = WorkflowGraph(entry='done').node('done', 'end').to_dict()
        graph_steps = [NS(kind='graph', metadata={'workflow_id': 'flow', 'definition': graph})]
        for engine, expected_steps in (('loop', 0), ('graph-alias', 1025)):
            with self.subTest(engine=engine):
                runs = {f'r{i}': NS(id=f'r{i}', engine=engine, status='completed',
                    started_at=None, ended_at=None, metadata={'completions': []}) for i in range(1025)}
                handles = {key: NS(aget_data=AsyncMock(return_value=run),
                    steps=NS(alist=AsyncMock(return_value=graph_steps))) for key, run in runs.items()}
                session = NS(run=NS(aload=AsyncMock(side_effect=lambda key: handles[key])))
                records = [Message(id=f'a{i}', role='assistant', content='answer',
                    status='completed', created_at='', run_id=f'r{i}', metadata={}) for i in range(1025)]
                reader = SnapshotReader()
                first = await reader.messages(session, records, graph_engines=('graph-alias',))
                loads = session.run.aload.await_count
                scans = sum(h.steps.alist.await_count for h in handles.values())
                self.assertEqual(scans, expected_steps)
                self.assertEqual(await reader.messages(session, records, graph_engines=('graph-alias',)), first)
                self.assertEqual(session.run.aload.await_count, loads)
                self.assertEqual(sum(h.steps.alist.await_count for h in handles.values()), scans)
                await reader.messages(session, records[-1:], graph_engines=('graph-alias',))
                self.assertEqual(set(reader.run_engines), {'r1024'})
                self.assertEqual(set(reader.workflows), {'r1024'})
                await reader.messages(session, [])
                self.assertFalse(reader.cache or reader.workflows or reader.authors)

    def test_graph_projection_requires_original_evidence_and_keeps_source_immutable(self):
        graph = WorkflowGraph(entry='work').node('work', 'action').node('done', 'end').connect('work', 'done').to_dict()
        root = NS(kind='graph', name='not-a-workflow-id', metadata={'workflow_id':'flow', 'definition':graph})
        node = NS(kind='graph_node', status='running', metadata={'node_id':'work', 'path':'flow/work'})
        before = copy.deepcopy(root.metadata)
        observation = WorkflowObservation.read([root, node])
        self.assertEqual(observation.states, (('work', 'running'),))
        self.assertEqual(root.metadata, before)
        for field in ('workflow_id', 'definition'):
            broken = copy.deepcopy(root)
            del broken.metadata[field]
            with self.subTest(field=field), self.assertRaises(KeyError):
                WorkflowObservation.read([broken, node])
        for field in ('node_id', 'path'):
            broken = copy.deepcopy(node)
            del broken.metadata[field]
            with self.subTest(field=field), self.assertRaises(KeyError):
                WorkflowObservation.read([root, broken])
        self.assertEqual(WorkflowObservation.read([]), WorkflowObservation())

    def test_operation_stages_use_declared_actions_not_name_prefixes(self):
        self.assertEqual(OperationProgress.management('import'), MessageKey.PROGRESS_BACKEND_WRITE)
        self.assertEqual(OperationProgress.management('retry'), MessageKey.PROGRESS_BACKEND_WRITE)
        self.assertEqual(OperationProgress.management('job_delete_plan'), MessageKey.PROGRESS_BACKEND_READ)
        self.assertEqual(OperationProgress.settings('preset_apply'), MessageKey.PROGRESS_BACKEND_WRITE)
        self.assertEqual(OperationProgress.settings('preset_export'), MessageKey.PROGRESS_BACKEND_READ)
        self.assertEqual(OperationProgress.management('delete_unknown'), MessageKey.PROGRESS_BACKEND_WORK)
        self.assertEqual(OperationProgress.settings('save_unknown'), MessageKey.PROGRESS_BACKEND_WORK)
