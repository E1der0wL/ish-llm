"""Bounded, tool-free auxiliary calls observed inside the owning Run."""

from llm.core.model_protocol import ModelMessage, ModelRequest, ModelRole

from contextlib import aclosing
from copy import deepcopy
import json

from llm.core.results import EngineOutput
from llm.engines.base import BaseEngine, EngineEventType


class InvalidRecoveryResponse(ValueError):
    pass


def parse_object(text):
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise InvalidRecoveryResponse("Recovery model returned invalid JSON") from error
    if not isinstance(value, dict):
        raise InvalidRecoveryResponse("Expected a JSON object")
    return value


async def model_events(engine, frame, request, instruction, payload, result, *, kind, maximum,
                       metadata=None, name=None):
    # These are a different logical request, with no inherited SDK/provider retry.
    adapter = engine.completion_adapter
    invocation = ModelRequest([ModelMessage(ModelRole.SYSTEM, instruction),
        ModelMessage(ModelRole.USER, json.dumps(payload, ensure_ascii=False, allow_nan=False))],
        adapter.auxiliary_parameters(request))
    response = []
    model = BaseEngine("Hub recovery", completion_adapter=adapter)

    async def complete(context):
        async with aclosing(model.stream_completion(invocation, response=response, provider={},
                                                    max_output_chars=maximum)) as stream:
            async for event in stream:
                if getattr(event, "type", None) == EngineEventType.COMPLETION:
                    yield event
        if response[0].message.calls:
            raise InvalidRecoveryResponse("Recovery model cannot request Tools")
        result.update(parse_object(response[0].message.text))
        yield EngineOutput(data=deepcopy(result), visibility="internal")

    async with aclosing(model.step(frame.context, complete, name=name or "Hub " + kind, kind="llm",
            metadata=metadata if metadata is not None else {"hub_recovery": kind},
            error_message="Hub recovery helper failed")) as events:
        async for event in events:
            yield event
