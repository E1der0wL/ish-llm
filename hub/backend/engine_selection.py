"""All registered public engines are available without an activation checklist."""


def selected_engines(config, registered) -> tuple[str, ...]:
    return tuple(name for name in registered if not name.startswith("_hub_"))


def model_name(engines, config, engine, session_config=None):
    if engine not in engines.names():
        return ""
    implementation = engines.get(engine)
    describe = getattr(implementation, "resolve_config", None)
    if describe:
        return describe(config, engine, session_config=session_config).get("values", {}).get("config", {}).get("completion", {}).get("model", "")
    return ""

def requires_model(engines, config, engine, session_config=None):
    from llm.engines.loop import LoopEngine
    if engine not in engines.names():
        return False
    implementation = engines.get(engine)
    # Graph/사용자 엔진에는 LLM 모델이 필요하다고 추정하지 않는다.
    if not isinstance(implementation, LoopEngine):
        return False
    view = implementation.resolve_config(config, engine, session_config=session_config)
    # JSON으로 표현하지 않은 host client/factory는 조회만으로 모델 누락을 판정하지 않는다.
    if "completion" in view.get("runtime", ()):
        return False
    return not view["values"].get("config", {}).get("completion", {}).get("model")

