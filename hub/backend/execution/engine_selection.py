"""All registered public engines are available without an activation checklist."""

from llm.core.agents import AgentDefinition
from llm.core.implementation_parameters import ImplementationParameters
from llm.core.configuration_views import ConfigurationView
from llm.engines.loop.configuration import LoopParameters
from llm.providers.request_values import ModelParameters


class HubEngineIdentity:
    LOOP = "loop"
    GRAPH = "graph"
    INTERNAL_PREFIX = "_hub_"


class EngineSelection:
    NO_SESSION_CONFIG = None
    NO_DESCRIPTION = None
    NO_MODEL = ModelParameters.NO_MODEL_NAME
    MODEL_NOT_REQUIRED = False

    @classmethod
    def completion(cls, values):
        return LoopParameters(ImplementationParameters.read(values).config).completion

    @classmethod
    def model_name(cls, values):
        return ModelParameters(cls.completion(values)).model_or_empty


def selected_engines(config, registered) -> tuple[str, ...]:
    return tuple(
        name
        for name in registered
        if not name.startswith(HubEngineIdentity.INTERNAL_PREFIX)
    )


def model_name(
    engines, config, engine, session_config=EngineSelection.NO_SESSION_CONFIG
):
    if engine not in engines.names():
        return EngineSelection.NO_MODEL
    implementation = engines.get(engine)
    describe = getattr(
        implementation, ConfigurationView.RESOLVE_METHOD, EngineSelection.NO_DESCRIPTION
    )
    if describe:
        return EngineSelection.model_name(
            ConfigurationView.read(
                describe(config, engine, session_config=session_config)
            ).values
        )
    return EngineSelection.NO_MODEL


def requires_model(
    engines, config, engine, session_config=EngineSelection.NO_SESSION_CONFIG
):
    from llm.engines.loop import LoopEngine

    if engine not in engines.names():
        return EngineSelection.MODEL_NOT_REQUIRED
    from .environment import underlying_engine

    implementation = underlying_engine(engines.get(engine))
    # Graph/custom Engines do not implicitly require a model.
    if not isinstance(implementation, LoopEngine):
        return EngineSelection.MODEL_NOT_REQUIRED
    view = ConfigurationView.read(
        implementation.resolve_config(config, engine, session_config=session_config)
    )
    # Non-JSON Host clients/factories must not be diagnosed as missing from a view.
    if LoopParameters.has_completion(view.runtime):
        return EngineSelection.MODEL_NOT_REQUIRED
    return not ModelParameters(EngineSelection.completion(view.values)).optional_model


def agent_model(
    engines, config, definition, session_config=EngineSelection.NO_SESSION_CONFIG
):
    """Resolve the actual Agent's parameters through its selected Engine."""
    from llm.engines.loop import LoopEngine
    from .environment import underlying_engine

    engine = engines.resolve_agent(definition)
    describe = getattr(
        engine, ConfigurationView.RESOLVE_METHOD, EngineSelection.NO_DESCRIPTION
    )
    view = ConfigurationView.read(
        describe(
            config, AgentDefinition.engine(definition), session_config=session_config
        )
        if callable(describe)
        else {}
    )
    model = EngineSelection.model_name(view.values)
    required = (
        isinstance(underlying_engine(engine), LoopEngine)
        and not LoopParameters.has_completion(view.runtime)
        and not model
    )
    return model, required
