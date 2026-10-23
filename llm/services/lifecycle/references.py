"""Project resource references composed from their authoritative owners."""

from llm.core.resource_references import ResourceTarget, StoredResourceReference
from llm.core.resource_references import ReferenceFields
from llm.core.agents import AgentDefinition


class ProjectReferences:
    UNAVAILABLE = None
    MISSING_AGENT_OWNER = "Agent reference contracts are unavailable"
    REFERENCED = "Resource is referenced; remove references first: {references}"
    SELECTED_REFERENCED = "Component is referenced by saved Agents: {references}"

    def __init__(self, components, agents, *, selected_references=UNAVAILABLE):
        self.components, self.agents = components, agents
        self.selected_references = selected_references

    def dependents(self, project, name, identifier=ResourceTarget.WHOLE_COMPONENT, *, path=ReferenceFields.NO_PATH):
        result = [ResourceTarget.from_dict(value)
                  for value in self.components.dependents(project, name, identifier, path=path)]
        if self.selected_references is self.UNAVAILABLE:
            if self.agents.list(project):
                raise ValueError(self.MISSING_AGENT_OWNER)
            agent_references = ()
        else:
            agent_references = self.selected_references(project)
        for value in agent_references:
            reference = StoredResourceReference.read(value)
            if reference.matches(name, identifier, path=path):
                result.append(reference.dependent(AgentDefinition.REFERENCE_COMPONENT))
        return [value.to_dict() for value in result]

    def require_unreferenced(self, project, name, identifier=ResourceTarget.WHOLE_COMPONENT, *, path=ReferenceFields.NO_PATH):
        references = self.dependents(project, name, identifier, path=path)
        if references:
            raise ValueError(self.REFERENCED.format(references=references))

    def validate_selection(self, project, names):
        self.components.validate_selection(project, names)
        for name in set(project.components) - set(names):
            references = [reference for reference in self.dependents(project, name)
                          if ResourceTarget.from_dict(reference).component == AgentDefinition.REFERENCE_COMPONENT]
            if references:
                raise ValueError(self.SELECTED_REFERENCED.format(references=references))
