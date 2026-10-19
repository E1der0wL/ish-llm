"""Compatibility exports; Project Agent definitions are owned by AgentRepository."""

from llm.components.definitions import DefinitionComponent
from llm.core.agents import AgentDefinition
from llm.services.lifecycle.agents import AgentRepository
from .data import AgentData


class AgentComponent(DefinitionComponent):
    """Legacy configuration/Tool facade over the independent Agent catalogue."""

    name = "agents"
    directory = "agents"
    capabilities = ("agents",)
    data_class = AgentData
    # Existing Component clients use this input contract for forms and Tool
    # admission. Project.agents exposes the new engine-only domain contract.
    schema = {**AgentDefinition.schema, "required": ["purpose", "engine"]}
    revision = staticmethod(AgentDefinition.revision)

    def __init__(self, *, engines=None):
        self.engines = engines
        self.repository = AgentRepository()
        self.capabilities = ("agents", "tools") if engines is not None else ("agents",)

    def resolve(self, project, capability):
        if capability == "tools" and self.engines is not None:
            from .tools import agent_tools
            return agent_tools(self.engines)
        if capability == "agents":
            return self.repository.resolve(project)
        return super().resolve(project, capability)

    def validate_record(self, identifier, data):
        super().validate_record(identifier, data)
        AgentDefinition.validate(identifier, data)

    def root(self, project):
        return self.repository.root(project)

    def _record_path(self, project, identifier):
        return self.repository.path(project, identifier)

    def initialize(self, project):
        self.get_config(project)
        self.repository.initialize(project)

    def create(self, project, data, *, identifier=None):
        self.validate_record(identifier or "pending", data)
        return self.repository.create(project, data, identifier=identifier)

    def load(self, project, identifier):
        return self.repository.load(project, identifier)

    def list(self, project):
        return self.repository.list(project)

    def save(self, project, identifier, data):
        self.validate_record(identifier, data)
        self.repository.save(project, identifier, data)

    def update(self, project, identifier, changes):
        self.validate_record(identifier, {**self.repository.load(project, identifier), **changes})
        return self.repository.update(project, identifier, changes)

    def delete(self, project, identifier):
        self.repository.delete(project, identifier)

    def clone(self, source, destination):
        # ProjectManager clones the domain exactly once, including when this
        # optional Tool/configuration compatibility facade is disabled.
        pass

    def delete_directory(self, project):
        raise ValueError("Agent definitions belong to Project.agents; delete them explicitly")
