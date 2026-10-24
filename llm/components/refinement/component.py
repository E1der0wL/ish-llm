"""Refinement capability adapter; proposal meaning belongs to its record Owner."""

from llm.components.tools.registry import ToolRegistry
from llm.core.implementation_parameters import ImplementationParameters
from llm.components.definitions import DefinitionComponent
from .data import RefinementData
from .records import RefinementRecord, RefinementPolicy

IDENTIFIER = RefinementRecord.IDENTIFIER
TARGET = RefinementRecord.TARGET
EVIDENCE = RefinementRecord.EVIDENCE
PARENT = RefinementRecord.PARENT
EVALUATION = RefinementRecord.EVALUATION
SOURCE = RefinementRecord.SOURCE
HISTORY = RefinementRecord.HISTORY
STORED_EVALUATION = RefinementRecord.STORED_EVALUATION


class RefinementComponent(DefinitionComponent):
    name = directory = RefinementRecord.CAPABILITY
    capabilities = (name, ToolRegistry.CAPABILITY)
    data_class = RefinementData
    schema = RefinementRecord.SCHEMA
    INVALID_RUNTIME = "Refinement capabilities require a lifecycle-bound component data factory"

    def validate_record(self, identifier, data):
        super().validate_record(identifier, data)
        RefinementRecord.validate_history(data)

    def describe_config(self):
        return RefinementPolicy.schema()

    def clone(self, source, destination):
        # Proposals retain original Project authority; clones require new approval.
        self.initialize(destination)

    def history_references(self, project):
        records = self.list(project).values()
        return RefinementRecord.history_references(records)

    def references(self, project):
        return RefinementRecord.references(self.list(project))

    def resolve(self, project, capability):
        raise ValueError(self.INVALID_RUNTIME)

    def resolve_runtime(self, project, capability, *, data_factory):
        data = data_factory(self.name)
        if capability == RefinementRecord.CAPABILITY:
            return data
        if capability == ToolRegistry.CAPABILITY:
            from .tools import refinement_tools

            policy = ImplementationParameters.read(self.get_config(project)).policy
            return refinement_tools(
                data,
                apply=RefinementPolicy.apply_tools(policy),
                operations=RefinementPolicy.proposal_operations(policy),
            )
        return super().resolve_runtime(project, capability, data_factory=data_factory)
