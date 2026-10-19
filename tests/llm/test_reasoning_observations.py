"""Provider-supplied reasoning stays separate from answer text and obeys limits."""
from llm.core.model_protocol import ModelMessage, ModelRequest, ModelResponse, ToolCall, ToolResult, ContentPart, ToolDefinition
from llm.providers.litellm import LiteLLMCompletionAdapter

import unittest

from llm.engines.base import BaseEngine, EngineEvent


def chunk(delta, finish=None):
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class ReasoningObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_reasoning_is_a_completion_observation_not_answer_text(self):
        def provider(**kwargs):
            yield chunk({"reasoning_content": "Consider "})
            yield chunk({"reasoning_content": "the options."})
            yield chunk({"content": "Answer"}, "stop")
        engine = BaseEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))
        response = []
        values = [v async for v in engine.stream_completion(ModelRequest([], {'model': "test"}), response=response)]
        self.assertEqual("".join(v for v in values if isinstance(v, str)), "Answer")
        self.assertEqual(response[0].message.text, "Answer")
        self.assertEqual(response[0].message.role, "assistant")
        self.assertEqual(response[0].message.opaque["litellm"]["reasoning_content"], "Consider the options.")
        observations = [v.completion for v in values if isinstance(v, EngineEvent)]
        self.assertEqual(observations[-1].reasoning_content, "Consider the options.")
        self.assertEqual(observations[0].reasoning_content, "")
        plain = [v async for v in engine.stream_completion(ModelRequest([], {}), include_events=False)]
        self.assertEqual(plain, ["Answer"])

    async def test_reasoning_limit_and_partial_failure_observation(self):
        def provider(**kwargs):
            yield chunk({"reasoning_content": "12345"})
            yield chunk({"reasoning_content": "6"})
        engine = BaseEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))
        values = []
        with self.assertRaisesRegex(ValueError, "limit"):
            async for value in engine.stream_completion(ModelRequest([], {}), max_output_chars=5):
                values.append(value)
        self.assertEqual(values[-1].completion.reasoning_content, "12345")
        self.assertEqual(str(values[-1].completion.status), "failed")

    async def test_reasoning_after_termination_is_rejected(self):
        def provider(**kwargs):
            yield chunk({"content": "Done"}, "stop")
            yield chunk({"reasoning_content": "too late"})
        with self.assertRaisesRegex(ValueError, "termination"):
            async for _ in BaseEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider)).stream_completion(ModelRequest([], {})):
                pass
