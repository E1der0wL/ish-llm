"""Project resource references composed from their authoritative owners."""


class ProjectReferences:
    def __init__(self, components, agents, *, selected_references=None):
        self.components, self.agents = components, agents
        self.selected_references = selected_references

    def dependents(self, project, name, identifier=None, *, path=None):
        result = self.components.dependents(project, name, identifier, path=path)
        if self.selected_references is None:
            if self.agents.list(project):
                raise ValueError("Agent reference contracts are unavailable")
            agent_references = ()
        else:
            agent_references = self.selected_references(project)
        for reference in agent_references:
            if (reference["component"] == name
                    and (identifier is None or reference["identifier"] == identifier)
                    and (path is None or reference.get("path") in (None, path))):
                result.append({"component": "agents", "identifier": reference["source"]})
        return result

    def require_unreferenced(self, project, name, identifier=None, *, path=None):
        references = self.dependents(project, name, identifier, path=path)
        if references:
            raise ValueError(f"Resource is referenced; remove references first: {references}")

    def validate_selection(self, project, names):
        self.components.validate_selection(project, names)
        for name in set(project.components) - set(names):
            references = [reference for reference in self.dependents(project, name)
                          if reference["component"] == "agents"]
            if references:
                raise ValueError(f"Component is referenced by saved Agents: {references}")
