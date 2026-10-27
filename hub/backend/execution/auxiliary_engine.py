"""Tool-free model jobs with ordinary backend Agent/Run/Step ownership."""

import asyncio
from contextlib import aclosing
from copy import copy
from llm.core.agents import AgentDefinition
from llm.core.configuration_targets import ResolvedEngineConfiguration
from llm.core.model_protocol import ModelMessage, ModelRequest, ModelRole, copy_model
from llm.core.models import StepKind
from llm.core.schema import object_schema, implementation_schema, SchemaBoolean
from llm.engines.base import BaseEngine
from llm.engines.loop.configuration import LoopParameters
from llm.providers.requests import provider_schema


class AuxiliaryEngine(BaseEngine):
    """Subclasses own their purpose and bounds; model settings belong to their Agent."""

    name = "Hub auxiliary"
    instruction = ""
    output_chars = None

    def __init__(self, **kwargs):
        super().__init__(self.name, kind=StepKind.LLM, **kwargs)
        self.parameters = {}

    def describe_config(self):
        return implementation_schema(
            config=object_schema({}, additionalProperties=SchemaBoolean.REJECT),
            policy=object_schema({LoopParameters.PROVIDER: provider_schema()},
                                 additionalProperties=SchemaBoolean.REJECT))

    def describe_agent(self):
        return AgentDefinition.describe(
            fields=(AgentDefinition.Field.ENGINE, AgentDefinition.Field.COMPLETION,
                    AgentDefinition.Field.PURPOSE, AgentDefinition.Field.DESCRIPTION,
                    AgentDefinition.Field.METADATA),
            properties={AgentDefinition.Field.COMPLETION: self.completion_adapter.describe_config()})

    def for_agent(self, definition):
        worker = copy(self)
        worker.parameters = copy_model(definition.get(AgentDefinition.Field.COMPLETION, {}))
        self.completion_adapter.validate_request(worker.parameters)
        self.completion_adapter.validate_input_ownership(worker.parameters, auxiliary=True)
        return worker

    def resolve_config(self, config, name, *, session_config=None):
        from llm.core.configuration import resolve_engine_config
        return resolve_engine_config(config, name, session_config=session_config, schema=self.describe_config())

    async def run(self, context):
        settings = ResolvedEngineConfiguration.read(context.resolve_config()).engine
        request = ModelRequest([
            ModelMessage(ModelRole.SYSTEM, self.instruction),
            ModelMessage(ModelRole.USER, context.messages[-1].content),
        ], copy_model(self.parameters))
        if context.completion_policy is not None:
            request = await asyncio.to_thread(context.completion_policy.prepare, request)
        async with aclosing(self.stream_completion(request,
                provider=settings.policy.get(LoopParameters.PROVIDER, {}),
                max_output_chars=self.output_chars)) as events:
            async for event in events:
                yield event
