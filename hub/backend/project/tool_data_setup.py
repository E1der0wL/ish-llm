"""Explicit command-output creation defaults; backend Components own all bytes."""

from copy import deepcopy

from llm.components.data import DataComponent
from llm.components.tools.builtin.component import BuiltinParameter
from llm.core.implementation_parameters import ImplementationParameters
from llm.core.models import ProjectConfig
from llm.core.policies import ToolPolicyValues, PolicySection

from ...config.defaults import HubDefaults


class ToolDataSetup:
    @staticmethod
    def destination() -> dict[str, str]:
        return {"component": DataComponent.name, "identifier": HubDefaults.TOOL_OUTPUT_SOURCE}

    @classmethod
    def creation_defaults(cls, config: ProjectConfig, components, builtins) -> ProjectConfig:
        """Only new Projects receive defaults; explicit fields stay authoritative."""
        from ..tools.builtin import HubBuiltinTools

        if not isinstance(builtins, HubBuiltinTools) or builtins.name not in components:
            return config
        parameters = config.parameter_targets.component(builtins.name)
        current = parameters.to_dict()
        current.setdefault(ImplementationParameters.POLICY, {}).setdefault(
            BuiltinParameter.READ_ACCESS, deepcopy(HubDefaults.TOOL_READ_ACCESS))
        if (DataComponent.name in components
                and BuiltinParameter.OUTPUT not in parameters.config
                and BuiltinParameter.OUTPUT_PREVIEW_CHARS not in parameters.policy):
            current.setdefault(ImplementationParameters.CONFIG, {})[BuiltinParameter.OUTPUT] = cls.destination()
            current[ImplementationParameters.POLICY][BuiltinParameter.OUTPUT_PREVIEW_CHARS] = HubDefaults.TOOL_OUTPUT_PREVIEW_CHARS
        config.set_component_parameters(builtins.name, current)
        policy = config.policies.setdefault(PolicySection.TOOLS, {})
        # A supplied constraint map, including {}, is an explicit user choice.
        policy.setdefault(ToolPolicyValues.argument_constraints.__name__, deepcopy(HubDefaults.TOOL_PAGE_CONSTRAINTS))
        return config

    @classmethod
    async def ensure_sources(cls, project) -> None:
        """Used after explicit creation/save; never during open or Tool execution."""
        from ..tools.builtin import HubBuiltinTools

        record = await project.aget_data()
        if HubBuiltinTools.NAME not in record.components or DataComponent.name not in record.components:
            return
        destination = record.config.parameter_targets.component(HubBuiltinTools.NAME).config.get(BuiltinParameter.OUTPUT)
        if destination != cls.destination():
            return
        files = await project.components.aget(DataComponent.name)
        if HubDefaults.TOOL_OUTPUT_SOURCE not in await files.alist():
            await files.acreate({"title": "Command outputs"}, identifier=HubDefaults.TOOL_OUTPUT_SOURCE)
