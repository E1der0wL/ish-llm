"""Wrapped Graph delegation retains approval, environment and actual effects."""

import json
from pathlib import Path
import tempfile
import unittest

from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.environment import ENVIRONMENT_OPTION
from llm.components.workflows import WorkflowGraph
from llm.engines.loop import LoopEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.hub.test_shell_environment import snapshot
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class WrappedGraphAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_nested_graph_approval_reopen_keeps_environment_and_single_effect(self):
        with tempfile.TemporaryDirectory() as root:
            work = Path(root) / 'work'
            work.mkdir()
            effect = work / 'effect.txt'
            model = ScriptedCompletion(
                [chunk(calls=[call(json.dumps({'agent_id': 'writer', 'input': {}}),
                    name='agent_run')], finish='tool_calls')],
                [chunk('done', finish='stop')])
            config = HubConfig(Path(root) / 'storage', model='test/model', auto_title=False,
                agent_definition={'engine': 'loop', 'tools': ['agent_run', 'shell_execute'],
                                  'policy': {'max_tool_calls': 2}},
                engine_factories={'loop': lambda: LoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                workflow = (WorkflowGraph(entry='write').node('write', 'tool', tool='shell_execute',
                    arguments={'command': 'printf "%s\\n" "$HUB_SHELL_TEST" >> effect.txt'})
                    .node('end', 'end').connect('write', 'end'))
                await runtime.project.components.workflows.acreate(workflow.to_dict(), identifier='write')
                await runtime.project.agents.acreate({'engine': 'graph',
                    'engine_options': {'workflow': 'write'}}, identifier='writer')
                session = await runtime.project.sessions.acreate(agent_ids=sorted(await runtime.project.agents.alist()))
                project_id, session_id = runtime.project.id, session.id
                paused = (await (await session.run.submit('write once', recipients=[config.agent_id],
                    request_context={ENVIRONMENT_OPTION: snapshot(work, 'captured').to_dict()})).wait())[0]
                self.assertEqual(str(paused.data.status), 'paused', paused.data.error)
                self.assertFalse(effect.exists())
                request, = await paused.ainteractions(pending_only=True)
                await paused.arespond(request.respond('approve'))
                await runtime.close()
                runtime = HubRuntime(config)
                await runtime.start()
                project = await runtime.backend.projects.aload(project_id)
                session = await project.sessions.aload(session_id)
                done = (await (await session.run.resume(paused.id, agent_id=config.agent_id)).wait())[0]
                self.assertEqual(str(done.data.status), 'completed', done.data.error)
                self.assertEqual(effect.read_text().splitlines(), ['captured'])
                self.assertEqual(len(model.requests), 2)
                self.assertEqual(len(await session.run.alist()), 2)
            finally:
                await runtime.close()
