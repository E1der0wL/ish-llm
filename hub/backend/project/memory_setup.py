"""Explicit Hub model-save defaults; Memory/Data retain processing and storage ownership."""

from copy import deepcopy

from llm.core.models import ProjectConfig
from llm.core.implementation_parameters import ImplementationParameters
from llm.providers.request_values import LiteLLMParameters
from llm.components.memory import MemoryComponent
from llm.components.memory.configuration import MemoryProcessingConfig, MemoryToolData
from llm.components.data import DataComponent
from ...config.defaults import HubDefaults


class MemorySetup:
    @classmethod
    def prepare(cls, config, components):
        candidate = ProjectConfig.from_dict(deepcopy(config))
        selected = list(components)
        if MemoryComponent.name not in selected:
            return candidate, selected
        current = candidate.parameter_targets.component(MemoryComponent.name).to_dict()
        parameters = ImplementationParameters.read(current)
        processing = parameters.config.get(MemoryProcessingConfig.KEY, {})
        completion = processing.get(MemoryProcessingConfig.Field.COMPLETION)
        model = completion.get(LiteLLMParameters.MODEL) if isinstance(completion, dict) else None
        if not isinstance(model, str) or not model.strip():
            return candidate, selected
        defaults = deepcopy(HubDefaults.MEMORY_SUMMARY)
        policy = parameters.policy.get(MemoryProcessingConfig.KEY, {})
        # A summary is wrapped in provenance before injection. Leave room for
        # that wrapper and recalled facts within the user's existing limit.
        context_chars = policy.get(MemoryProcessingConfig.Field.CONTEXT_CHARS)
        if type(context_chars) is int and context_chars > 0:
            summary = defaults[ImplementationParameters.CONFIG][MemoryProcessingConfig.KEY]
            summary[MemoryProcessingConfig.Field.SUMMARY_CHARS] = min(
                summary[MemoryProcessingConfig.Field.SUMMARY_CHARS],
                max(1, context_chars // HubDefaults.MEMORY_SUMMARY_CONTEXT_SHARE),
            )
        if policy.get(MemoryProcessingConfig.Field.COMPRESS_TOOLS):
            # Explicit legacy preview choice remains authoritative; never select both modes.
            defaults[ImplementationParameters.CONFIG][MemoryProcessingConfig.KEY].pop(
                MemoryProcessingConfig.Field.TOOL_RESULT_MODE)
            defaults[ImplementationParameters.CONFIG][MemoryProcessingConfig.KEY].pop(
                MemoryProcessingConfig.Field.TOOL_DATA)
        merged = ProjectConfig.merge(defaults, current)
        candidate.set_component_parameters(MemoryComponent.name, merged)
        destination = merged[ImplementationParameters.CONFIG][MemoryProcessingConfig.KEY].get(
            MemoryProcessingConfig.Field.TOOL_DATA)
        if destination and destination.get(MemoryToolData.Field.COMPONENT) == DataComponent.name:
            if DataComponent.name not in selected:
                selected.append(DataComponent.name)
        return candidate, selected

    @classmethod
    async def ensure_sources(cls, project):
        data = await project.aget_data()
        if MemoryComponent.name not in data.components or DataComponent.name not in data.components:
            return
        processing = data.config.parameter_targets.component(MemoryComponent.name).config.get(
            MemoryProcessingConfig.KEY, {})
        destination = processing.get(MemoryProcessingConfig.Field.TOOL_DATA)
        expected = HubDefaults.MEMORY_SUMMARY[ImplementationParameters.CONFIG][
            MemoryProcessingConfig.KEY][MemoryProcessingConfig.Field.TOOL_DATA]
        if destination != expected:
            return
        files = await project.components.aget(DataComponent.name)
        if HubDefaults.MEMORY_SOURCE not in await files.alist():
            await files.acreate({}, identifier=HubDefaults.MEMORY_SOURCE)
