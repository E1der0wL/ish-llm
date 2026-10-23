"""Resolve the same admitted Project Agent for root execution and resume."""

from llm.core.agents import AgentBinding
from llm.core.configuration import UNSET
from llm.engines.base import EngineContextBinding


class RunAgentResolver:
    CHANGED_DEFINITION = "Agent definition changed since request admission"

    def __init__(self, engines, repository):
        self.engines, self.repository = engines, repository

    def selected(self, project, identifier):
        return AgentBinding.create(project.id, identifier, self.repository.load(project, identifier))

    def current(self, project, binding):
        binding = AgentBinding.read(binding.to_dict())
        if binding.project_id != project.id or self.selected(project, binding.agent_id) != binding:
            raise ValueError(self.CHANGED_DEFINITION)
        return binding

    def strategy(self, binding, *, request_context=UNSET):
        from llm.engines.agents import AgentInvocation
        binding = AgentBinding.read(binding.to_dict())
        engine = self.engines.resolve_agent(binding.definition)
        engine = EngineContextBinding.bind(engine, request_context)
        return AgentInvocation(binding, engine, self.engines)

    def source(self, project, run):
        return self.current(project, AgentBinding.from_run(run))
