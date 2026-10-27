"""Bounded, tool-free auxiliary calls observed inside the owning Run."""

from llm.core.models import StepKind

from llm.core.results import OutputVisibility

from llm.core.model_protocol import ModelMessage, ModelRequest, ModelRole

from contextlib import aclosing
from copy import deepcopy
import json

from llm.core.results import EngineOutput
from llm.engines.base import BaseEngine, EngineEventType
from llm.engines.events import EngineEvent
from dataclasses import dataclass, fields
from typing import ClassVar


class RecoveryModelProtocol:
    JSON_OPEN = "```json\n"
    JSON_CLOSE = "```"
    INVALID_JSON = "Recovery model returned invalid JSON"
    OBJECT_REQUIRED = "Expected a JSON object"
    TOOL_REQUEST = "Recovery model cannot request Tools"
    ENGINE_NAME = "Hub recovery"
    STEP_PREFIX = "Hub "
    HELPER_FAILED = "Hub recovery helper failed"
    NO_METADATA = None
    NO_NAME = None
    ENSURE_ASCII = False
    ALLOW_NAN = False


@dataclass(frozen=True, slots=True)
class RecoveryStepMetadata:
    """The helper Step kind annotation, separate from diagnostic recovery explanations."""

    ANALYSIS: ClassVar[str] = "error analysis"
    SUMMARY: ClassVar[str] = "input summary"

    hub_recovery: str

    @classmethod
    def kind(cls, metadata):
        return metadata.get(cls.hub_recovery.__name__)

    def to_record(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


class InvalidRecoveryResponse(ValueError):
    pass


def parse_object(text):
    text = text.strip()
    if text.startswith(RecoveryModelProtocol.JSON_OPEN) and text.endswith(
        RecoveryModelProtocol.JSON_CLOSE
    ):
        text = text[
            len(RecoveryModelProtocol.JSON_OPEN) : -len(
                RecoveryModelProtocol.JSON_CLOSE
            )
        ].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise InvalidRecoveryResponse(RecoveryModelProtocol.INVALID_JSON) from error
    if not isinstance(value, dict):
        raise InvalidRecoveryResponse(RecoveryModelProtocol.OBJECT_REQUIRED)
    return value


async def model_events(
    engine,
    frame,
    request,
    instruction,
    payload,
    result,
    *,
    kind,
    maximum,
    metadata=RecoveryModelProtocol.NO_METADATA,
    name=RecoveryModelProtocol.NO_NAME
):
    # These are a different logical request, with no inherited SDK/provider retry.
    adapter = engine.completion_adapter
    invocation = ModelRequest(
        [
            ModelMessage(ModelRole.SYSTEM, instruction),
            ModelMessage(
                ModelRole.USER,
                json.dumps(
                    payload,
                    ensure_ascii=RecoveryModelProtocol.ENSURE_ASCII,
                    allow_nan=RecoveryModelProtocol.ALLOW_NAN,
                ),
            ),
        ],
        adapter.auxiliary_parameters(request),
    )
    response = []
    model = BaseEngine(RecoveryModelProtocol.ENGINE_NAME, completion_adapter=adapter)

    async def complete(context):
        async with aclosing(
            model.stream_completion(
                invocation, response=response, provider={}, max_output_chars=maximum
            )
        ) as stream:
            async for event in stream:
                if EngineEvent.observed_type(event) == EngineEventType.COMPLETION:
                    yield event
        if response[0].message.calls:
            raise InvalidRecoveryResponse(RecoveryModelProtocol.TOOL_REQUEST)
        result.update(parse_object(response[0].message.text))
        yield EngineOutput(data=deepcopy(result), visibility=OutputVisibility.INTERNAL)

    async with aclosing(
        model.step(
            frame.context,
            complete,
            name=name or RecoveryModelProtocol.STEP_PREFIX + kind,
            kind=StepKind.LLM,
            metadata=(
                metadata
                if metadata is not RecoveryModelProtocol.NO_METADATA
                else RecoveryStepMetadata(kind).to_record()
            ),
            error_message=RecoveryModelProtocol.HELPER_FAILED,
        )
    ) as events:
        async for event in events:
            yield event
