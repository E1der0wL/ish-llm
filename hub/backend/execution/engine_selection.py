"""All registered public engines are available without an activation checklist."""

from llm.core.agents import AgentDefinition
from llm.providers.request_values import ModelParameters


class HubEngineIdentity:
    LOOP = "loop"
    GRAPH = "graph"
    INTERNAL_PREFIX = "_hub_"

    @classmethod
    def internal(cls, identifier):
        return identifier.startswith(cls.INTERNAL_PREFIX)


class EngineSelection:
    REQUIRED_POSITION = 1

    @classmethod
    def model_required(cls, selection):
        return selection[cls.REQUIRED_POSITION]

    @classmethod
    def completion(cls, definition):
        return definition.get(AgentDefinition.Field.COMPLETION, {})


def selected_engines(config, registered) -> tuple[str, ...]:
    return tuple(
        name
        for name in registered
        if not HubEngineIdentity.internal(name)
    )



def agent_model(engines, definition):
    """Read a display hint; admission still validates the selected Agent."""
    from llm.engines.loop import LoopEngine
    from llm.engines.graph import GraphEngine
    from .environment import underlying_engine

    try:
        engine = engines.get(AgentDefinition.engine(definition))
    except KeyError:
        # Keep the registry's normal diagnostic for an unavailable strategy.
        engine = engines.resolve_agent(definition)
    # Standard factories preserve their strategy. Building an engine solely to
    # display its model repeats Agent/schema checks already owned by admission.
    # Custom factories can change strategy, so retain their resolution contract.
    if type(underlying_engine(engine)) not in (LoopEngine, GraphEngine):
        engine = engines.resolve_agent(definition)
    model = ModelParameters(EngineSelection.completion(definition)).model_or_empty
    required = isinstance(underlying_engine(engine), LoopEngine) and not model
    return model, required
