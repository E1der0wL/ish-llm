"""Resolve the same admitted Project Agent for root execution and resume."""

from copy import deepcopy
import inspect

from llm.core.agents import AgentBinding
from llm.core.configuration import UNSET
from llm.core.models import ProjectConfig


class RunAgentResolver:
    def __init__(self, engines, repository):
        self.engines, self.repository = engines, repository

    def selected(self, project, identifier):
        return AgentBinding.create(project.id, identifier, self.repository.load(project, identifier))

    def current(self, project, binding):
        binding = AgentBinding.read(binding)
        if binding["project_id"] != project.id or self.selected(project, binding["agent_id"]) != binding:
            raise ValueError("Agent definition changed since request admission")
        return binding

    def strategy(self, binding, *, request_context=UNSET):
        from llm.engines.agents import AgentInvocation
        binding = AgentBinding.read(binding)
        engine = self.engines.resolve_agent(binding["definition"])
        if request_context is not UNSET:
            if not isinstance(request_context, dict):
                raise TypeError("request_context must be a JSON object")
            ProjectConfig.validate_json(request_context)
            factory = getattr(engine, "for_context", None)
            if not callable(factory):
                raise ValueError("Selected Engine does not accept request context")
            bound = factory(deepcopy(request_context))
            if inspect.isawaitable(bound):
                if inspect.iscoroutine(bound):
                    bound.close()
                raise TypeError("Engine for_context must be synchronous")
            from llm.engines.base import required_capabilities, steering_mode, agent_mode
            if not callable(getattr(bound, "execute", None)):
                raise TypeError("for_context must return an Engine")
            required_capabilities(bound)
            if (agent_mode(bound) != agent_mode(engine)
                    or steering_mode(bound) != steering_mode(engine)
                    or getattr(bound, "checkpoint_name", None) != getattr(engine, "checkpoint_name", None)):
                raise ValueError("Request context must preserve Engine execution contracts")
            engine = bound
        return AgentInvocation(binding, engine, self.engines)

    def source(self, project, run):
        return self.current(project, AgentBinding.from_run(run))
