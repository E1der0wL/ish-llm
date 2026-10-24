"""선언된 capability를 Run별로 구성한다. Tool 제공자는 하나의 ToolRegistry로 합치고 다른 capability는 제공자별 튜플로 전달한다.

Adapt generic component exports to one Run's tool snapshot."""

from llm.core.agents import AgentDefinition
from llm.core.agent_views import AgentDefinitions
from llm.components.processing import COMPLETION_PROCESSORS

from typing import Protocol
from llm.core.models import Project
from llm.components.registry import ComponentRegistry
from llm.components.processing import ordered_processors
from .registry import ToolRegistry


class CapabilityResolver(Protocol):
    """resolve is required; quota-enabled callers also require model_usage.

    model_usage returns all Component receipts in this Project, including disabled
    components. Implementations without any Component calls explicitly return [].
    validate is an optional, synchronous admission check owned by the resolver.
    """
    def resolve(self, project: Project, names: tuple[str, ...]) -> dict: ...
    def model_usage(self, project: Project) -> list[dict]: ...


class CapabilityResolverHooks:
    ABSENT = None

    @classmethod
    def resolver(cls, value):
        return getattr(value, CapabilityResolver.resolve.__name__, cls.ABSENT)

    @classmethod
    def usage_reader(cls, value):
        return getattr(value, CapabilityResolver.model_usage.__name__, cls.ABSENT)

    @classmethod
    def validator(cls, value):
        return getattr(value, ComponentToolResolver.validate.__name__, cls.ABSENT)


class ComponentToolResolver:
    UNBOUND = None
    INVALID_TOOL_EXPORT = "Tool capability must export a ToolRegistry"
    UNBOUND_AGENT_DOMAIN = "Agent domain is not bound"
    UNAVAILABLE_CAPABILITY = "Required capability unavailable: {name}"

    def __init__(self, components: ComponentRegistry, *, data_factory=UNBOUND, agents=UNBOUND, engines=UNBOUND) -> None:
        self.components = components
        self.data_factory = data_factory
        self.agents, self.engines = agents, engines

    def validate(self, project: Project) -> None:
        self.components.validate(project.components)

    def model_usage(self, project: Project) -> list[dict]:
        from llm.services.runtime.usage import component_usage
        return component_usage(self.components, project)

    def resolve_tools(self, project: Project) -> ToolRegistry:
        """전달된 Project 사본의 설정을 사용한다. 서비스 호출자는 최신 Project를 전달한다."""
        tools = ToolRegistry()
        for exported in self.components.resolve(project, ToolRegistry.CAPABILITY, data_factory=self.data_factory):
            if not isinstance(exported, ToolRegistry):
                raise TypeError(self.INVALID_TOOL_EXPORT)
            tools.extend(exported)
        if self.agents is not None:
            from llm.engines.agent_tools import agent_tools
            tools.extend(agent_tools(self.engines))
        return tools

    def resolve(self, project: Project, names: tuple[str, ...]) -> dict:
        """명시적으로 요청한 기능만 구성한다. 일반 기능은 제공자별 값의 튜플이다."""
        values = {}
        for name in names:
            if name == ToolRegistry.CAPABILITY:
                values[name] = self.resolve_tools(project)
            elif name == AgentDefinition.CAPABILITY:
                if self.agents is None:
                    raise ValueError(self.UNBOUND_AGENT_DOMAIN)
                values[name] = (AgentDefinitions(self.agents.list(project)).to_dict(),)
            else:
                exports = self.components.resolve(project, name, data_factory=self.data_factory)
                # 처리기 컬렉션은 제공자가 없어도 유효하다. 개별 도메인 이름을 알지 않는다.
                if name == COMPLETION_PROCESSORS:
                    exports = ordered_processors(exports)
                elif not exports:
                    raise ValueError(self.UNAVAILABLE_CAPABILITY.format(name=name))
                values[name] = exports
        return values
