"""Starter Graph data and real tool effects through the public Run contract."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from hub.asset.coding_workflow import CodingWorkflow
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.defaults import HubDefaults
from hub.config.environment import ENVIRONMENT_OPTION
from llm.engines.loop import LoopEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.hub.test_shell_environment import snapshot
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class StarterWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_project_has_model_free_planner_and_editable_workflow(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False))
            try:
                await runtime.start()
                project = runtime.project
                project_id = project.id
                planner = await project.agents.aload(HubDefaults.PLANNER_AGENT_ID)
                self.assertEqual(planner, HubDefaults.PLANNER_AGENT)
                self.assertNotIn('completion', planner)
                self.assertNotIn('completion', await project.agents.aload('assistant'))
                workflows = await project.components.aget('workflows')
                definition = await workflows.aload(HubDefaults.CODING_WORKFLOW_ID)
                self.assertEqual(definition, CodingWorkflow.definition('assistant'))
                for stage in CodingWorkflow.STAGES:
                    self.assertEqual(definition['nodes'][stage]['agent'], 'assistant')
                    self.assertEqual(definition['nodes'][stage]['emit_text'], stage == 'report')
                changed = deepcopy(definition)
                changed['initial_state']['instructions']['inspect'] = 'Custom inspection'
                await workflows.aupdate(HubDefaults.CODING_WORKFLOW_ID, changed)
                record = await project.agents.asnapshot('planner')
                await project.agents.adelete('planner', expected_revision=record['revision'])
            finally:
                await runtime.close()
            runtime = HubRuntime(HubConfig(root, project_id=project_id, auto_title=False))
            try:
                await runtime.start()
                self.assertNotIn('planner', await runtime.project.agents.alist())
                workflows = await runtime.project.components.aget('workflows')
                self.assertEqual(await workflows.aload(HubDefaults.CODING_WORKFLOW_ID), changed)
            finally:
                await runtime.close()

    async def test_explicit_primary_identity_and_component_scope_are_preserved(self):
        for worker_id in ('coder', 'planner'):
            with self.subTest(worker_id=worker_id), tempfile.TemporaryDirectory() as root:
                runtime = HubRuntime(HubConfig(root, auto_title=False, agent_id=worker_id,
                    agent_definition={'engine': 'loop', 'tools': []}))
                try:
                    await runtime.start()
                    self.assertEqual(await runtime.project.agents.aload(worker_id), {'engine': 'loop', 'tools': []})
                    if worker_id == 'coder':
                        definition = await runtime.project.components.workflows.aload(HubDefaults.CODING_WORKFLOW_ID)
                        self.assertEqual(definition['nodes']['inspect']['agent'], 'coder')
                    elif worker_id == 'planner':
                        self.assertEqual(await runtime.project.components.workflows.alist(), {})
                    created = await runtime.settings_service.create_project(
                        {'title': 'Without workflows', 'components': ['tools'], 'config': {}})
                    other = await runtime.backend.projects.aload(created['project']['id'])
                    self.assertEqual(set(await other.agents.alist()), {worker_id})
                finally:
                    await runtime.close()

    async def test_graph_modifies_runs_tests_repairs_and_emits_final_answer(self):
        def invoke(name, arguments, identifier):
            return [chunk(calls=[call(json.dumps(arguments), name=name, call_id=identifier)], finish='tool_calls')]

        # Script the provider, not tools: files and Python/Bash checks actually execute.
        provider = ScriptedCompletion(
            [chunk('Inspect: create a fixture and verify that it prints 42.', finish='stop')],
            invoke('shell_execute', {'command': "printf 'print(41)\\n' > answer.py"}, 'write'),
            [chunk('Implement: answer.py created; verify the expected value.', finish='stop')],
            invoke('shell_execute', {'command': 'python3 answer.py'}, 'check-failure'),
            invoke('shell_execute', {'command': "printf 'print(42)\\n' > answer.py"}, 'repair'),
            invoke('shell_execute', {'command': "test \"$(python3 answer.py)\" = 42 && printf verified"}, 'check-pass'),
            [chunk('Verify: repaired 41 to 42; Python and Bash checks passed.', finish='stop')],
            [chunk('Created answer.py, corrected it to print 42, and verified the output.', finish='stop')],
        )
        with tempfile.TemporaryDirectory() as root:
            work = Path(root) / 'work'
            work.mkdir()
            runtime = HubRuntime(HubConfig(Path(root) / 'storage', model='test/coder', auto_title=False,
                agent_definition={'engine': 'loop', 'tools': ['shell_execute']},
                engine_factories={'loop': lambda: LoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            try:
                await runtime.start()
                session = await runtime.project.sessions.acreate(agent_ids=['assistant', 'planner'])
                pending = await session.run.submit('Create answer.py that prints 42 and test it.', recipients=['planner'],
                    request_context={ENVIRONMENT_OPTION: snapshot(work, 'workflow').to_dict()})
                approvals = 0
                for _ in range(6):
                    run, = await asyncio.wait_for(pending.wait(), 30)
                    if str(run.data.status) != 'paused':
                        break
                    interactions = await run.ainteractions(pending_only=True)
                    self.assertTrue(interactions)
                    for interaction in interactions:
                        await run.arespond(interaction.respond('approve'))
                        approvals += 1
                    pending = await session.run.resume(run.id, agent_id='planner')
                self.assertEqual(str(run.data.status), 'completed', run.data.error)
                self.assertEqual(approvals, 4)
                self.assertEqual((work / 'answer.py').read_text(), 'print(42)\n')
                self.assertEqual((await run.aresponse()).content,
                                 'Created answer.py, corrected it to print 42, and verified the output.')
                self.assertEqual(len(provider.requests), 8)
                requests = [str(request['messages']) for request in provider.requests]
                self.assertIn('inspect', requests[0])
                self.assertIn('Inspect: create a fixture', requests[1])
                self.assertIn('Implement: answer.py created', requests[3])
                self.assertIn('Verify: repaired 41 to 42', requests[-1])
                self.assertTrue(any('verified' in request for request in requests))
            finally:
                await runtime.close()

    async def test_custom_worker_outside_graph_handler_is_not_seeded(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False,
                agent_definition={'engine': 'special', 'tools': []},
                engine_factories={'special': LoopEngine}))
            try:
                await runtime.start()
                self.assertNotIn('planner', await runtime.project.agents.alist())
                self.assertEqual(await runtime.project.components.workflows.alist(), {})
            finally:
                await runtime.close()
