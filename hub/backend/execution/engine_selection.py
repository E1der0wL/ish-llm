"""All registered public engines are available without an activation checklist."""

from llm.core.implementation_parameters import ImplementationParameters

from llm.core.configuration_views import ConfigurationView


def selected_engines(config, registered) -> tuple[str, ...]:
    return tuple(name for name in registered if not name.startswith("_hub_"))


def model_name(engines, config, engine, session_config=None):
    if engine not in engines.names():
        return ""
    implementation = engines.get(engine)
    describe = getattr(implementation, ConfigurationView.RESOLVE_METHOD, None)
    if describe:
        return ImplementationParameters.read(ConfigurationView.read(describe(config, engine, session_config=session_config)).values).config.get("completion", {}).get("model", "")
    return ""

def requires_model(engines, config, engine, session_config=None):
    from llm.engines.loop import LoopEngine
    if engine not in engines.names():
        return False
    from .environment import underlying_engine
    implementation = underlying_engine(engines.get(engine))
    # Graph/사용자 엔진에는 LLM 모델이 필요하다고 추정하지 않는다.
    if not isinstance(implementation, LoopEngine):
        return False
    view = ConfigurationView.read(implementation.resolve_config(config, engine, session_config=session_config))
    # JSON으로 표현하지 않은 host client/factory는 조회만으로 모델 누락을 판정하지 않는다.
    if "completion" in view.runtime:
        return False
    return not ImplementationParameters.read(view.values).config.get("completion", {}).get("model")


def agent_model(engines, config, definition, session_config=None):
    """Resolve the actual Agent's parameters through its selected Engine."""
    from llm.engines.loop import LoopEngine
    from .environment import underlying_engine
    engine = engines.resolve_agent(definition)
    describe = getattr(engine, ConfigurationView.RESOLVE_METHOD, None)
    view = ConfigurationView.read(describe(config, definition["engine"], session_config=session_config) if callable(describe) else {})
    model = ImplementationParameters.read(view.values).config.get("completion", {}).get("model", "")
    required = (isinstance(underlying_engine(engine), LoopEngine)
                and "completion" not in view.runtime and not model)
    return model, required

