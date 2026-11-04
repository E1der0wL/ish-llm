"""Hub creation policy consumes native feedback; it never fabricates success."""

from dataclasses import replace
import json
import tempfile
import unittest

from hub.backend.runtime import HubConfig, HubRuntime
from llm.components.tools import Tool, ToolRegistry
from llm.core.tool_contracts import ToolEffect
from llm.engines.loop import LoopEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.services.runtime.tools import ToolExecutionError
from tests.llm.support.runtime_tools import RuntimeTools, TEST_CONTRACT
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class HubToolFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_creation_policy_defers_unstarted_calls_and_preserves_failure_proof(self):
        class ExplicitToolLoop(LoopEngine):
            pass
        attempts = []
        async def read(arguments):
            attempts.append(arguments)
            raise ToolExecutionError('Read permission denied', effect='none', recoverable=False)
        tool = Tool('read_example', 'Read a test document', {'type': 'object'}, read,
                    contract=replace(TEST_CONTRACT, effect=ToolEffect.READ_ONLY))
        model = ScriptedCompletion(
            [chunk(calls=[call('{}', name='read_example', call_id='first'),
                          call('{}', name='read_example', call_id='second', index=1)], finish='tool_calls')],
            [chunk('Please grant read permission or choose another document.', finish='stop')])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model='test/model', auto_title=False,
                postprocess=False, prepare_provider=False,
                agent_definition={'engine': 'loop', 'tools': ['read_example']},
                project_config={'parameters': {'components': {'tools': {'config': {'enabled': ['read_example']}}}}},
                component_factories=[lambda: RuntimeTools(ToolRegistry([tool]))],
                engine_factories={'loop': lambda: ExplicitToolLoop(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))}))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                await runtime.submit(sid, 'Read the document')
                await runtime.sessions[sid].run.wait_idle()
                run, = await runtime.sessions[sid].run.alist()
                stored = await run.aget_data()
                self.assertEqual(stored.status, 'completed', stored.error)
                self.assertEqual(attempts, [{}])
                failed, = [step for step in await run.steps.alist() if step.status == 'failed']
                self.assertIn('handled_failure', failed.metadata)
                replies = [json.loads(item['content']) for item in model.requests[-1]['messages']
                           if item['role'] == 'tool']
                self.assertEqual([item['status'] for item in replies], ['failed', 'not_executed'])
                snapshot = await runtime.snapshot()
                self.assertEqual(snapshot.run.status, 'completed')
                self.assertIn('grant read permission', snapshot.messages[-1].text)
            finally:
                await runtime.close()
