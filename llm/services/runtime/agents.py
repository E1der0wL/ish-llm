"""Selected Project Agent bindings for the single Run dispatch path."""

from copy import deepcopy

from llm.core.agents import AgentDefinition
from llm.services.lifecycle.agents import AgentRepository


class RunAgentResolver:
    version = 1

    def __init__(self, engines, repository=None):
        self.engines = engines
        self.repository = repository if repository is not None else AgentRepository()

    @staticmethod
    def adapter(definition):
        return ("engine-request-v1" if definition.get("metadata", {}).get("compatibility") == "engine-request-v1"
                else "agent")

    @classmethod
    def snapshot(cls, identifier, definition):
        AgentDefinition.validate(identifier, definition)
        return {"storage_version": cls.version, "agent_id": identifier,
                "definition": deepcopy(definition), "revision": AgentDefinition.revision(definition),
                "engine": definition["engine"], "adapter": cls.adapter(definition)}

    def selected(self, project, identifier):
        return self.snapshot(identifier, self.repository.load(project, identifier))

    def legacy(self, project, engine, options, *, create=False):
        """Stable Project record for the v1 engine= admission adapter.

        The compatibility record is content-addressed; editing one cannot
        silently change the meaning of later engine= calls. Plain history reads
        never create these records. Admission/recovery owns any actual write.
        """
        definition = {"engine": engine, "engine_options": deepcopy(options),
                      "metadata": {"compatibility": "engine-request-v1"}}
        identifier = "legacy_" + AgentDefinition.revision(definition)[:56]
        binding = self.snapshot(identifier, definition)
        try:
            stored = self.repository.load(project, identifier)
        except FileNotFoundError:
            if create:
                self.repository.create(project, definition, identifier=identifier)
        else:
            if stored != definition:
                raise ValueError("Legacy Agent definition changed; restore it or select an explicit Agent")
        return binding

    @classmethod
    def validate(cls, binding):
        if (not isinstance(binding, dict) or set(binding) != {
                "storage_version", "agent_id", "definition", "revision", "engine", "adapter"}
                or type(binding["storage_version"]) is not int or binding["storage_version"] != cls.version):
            raise ValueError("Unsupported Agent execution binding")
        expected = cls.snapshot(binding["agent_id"], binding["definition"])
        if expected != binding:
            raise ValueError("Agent execution binding identity or revision mismatch")
        return deepcopy(binding)

    def current(self, project, binding):
        binding = self.validate(binding)
        if self.selected(project, binding["agent_id"]) != binding:
            raise ValueError("Agent definition changed since request admission")
        return binding

    def strategy(self, binding):
        binding = self.validate(binding)
        from llm.engines.agents import AgentInvocation
        engine = self.engines.resolve_agent(binding["definition"], adapter=binding["adapter"])
        return AgentInvocation(binding, engine, self.engines)

    def source(self, project, run, *, create=False):
        binding = run.metadata.get("agent_binding")
        if binding is None:
            if run.agent_id is not None:
                raise ValueError("Run Agent reference is missing its binding")
            return self.legacy(project, run.engine, run.metadata.get("engine_options", {}), create=create)
        binding = self.validate(binding)
        if binding["agent_id"] != run.agent_id or binding["engine"] != run.engine:
            raise ValueError("Run Agent and Engine binding mismatch")
        return self.current(project, binding)
