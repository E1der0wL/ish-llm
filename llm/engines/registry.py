"""Engine 등록과 실행 전 설정 검증을 담당한다. 구체적인 Engine 구현이나 저장소에 의존하지 않는다."""

from llm.core.configuration_views import ConfigurationView

from llm.engines.base import Engine, required_capabilities, steering_mode, agent_mode
from llm.core.steering import SteeringMode
from copy import deepcopy
import inspect


class EngineRegistry:
    def __init__(self) -> None:
        self._engines: dict[str, Engine] = {}

    def register(self, name: str, engine: Engine) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Engine name must be a nonempty string")
        if not callable(getattr(engine, "execute", None)):
            raise TypeError("Engine must implement execute(context)")
        required_capabilities(engine)
        agent_mode(engine)
        if steering_mode(engine) == SteeringMode.CONSUME:
            name_of_checkpoint = getattr(engine, "checkpoint_name", None)
            if not isinstance(name_of_checkpoint, str) or not name_of_checkpoint:
                raise ValueError("Instruction consumers must declare checkpoint_name")
        if name in self._engines:
            raise ValueError(f"Engine already registered: {name}")
        self._engines[name] = engine

    def names(self) -> tuple[str, ...]:
        return tuple(self._engines)

    def validate_config(self, config, *, session_config=None) -> None:
        """저장 전 공개 설정 계약만 호출한다. execute/capability/모델 함수는 호출하지 않는다."""
        from llm.core.models import ProjectConfig
        from llm.core.schema import checked_implementation_schema
        from jsonschema import Draft202012Validator
        from copy import deepcopy
        import inspect
        config = ProjectConfig.from_dict(config)
        session = deepcopy(session_config) if session_config is not None else {}
        ProjectConfig.validate_session(session)
        for name, engine in self._engines.items():
            schema = getattr(engine, "describe_config", None)
            if callable(schema):
                spec = checked_implementation_schema(schema())
                key = spec.get("x-parameter-key", name) if isinstance(spec, dict) else name
                if not isinstance(key, str) or not key.strip():
                    raise ValueError("Engine configuration key must be nonempty text")
                # 호스트 고정값이 잘못된 입력을 가리지 않게 UI와 같은 입력 스키마부터 검사한다.
                for source, owner in (("project", config), ("session", session)):
                    supplied = owner.get("parameters", {}).get("engines", {})
                    if key in supplied:
                        error = next(Draft202012Validator(spec).iter_errors(supplied[key]), None)
                        if error:
                            raise ValueError(f"Invalid Engine configuration ({name}, {source}): {error.message}")
            describe = getattr(engine, ConfigurationView.RESOLVE_METHOD, None)
            if callable(describe):
                try:
                    result = describe(ProjectConfig.from_dict(config), name, session_config=deepcopy(session))
                    if inspect.isawaitable(result):
                        if inspect.iscoroutine(result):
                            result.close()
                        raise TypeError("Engine configuration must be synchronous")
                except (ValueError, TypeError) as error:
                    raise ValueError(f"Invalid Engine configuration ({name}): {error}") from error

    def get(self, name: str) -> Engine:
        return self._engines[name]

    def resolve_agent(self, definition: dict) -> Engine:
        """Validate one selected, synchronous and effect-free Agent factory."""
        from llm.core.agents import AgentDefinition
        AgentDefinition.validate("selected", definition)
        try:
            engine = self.get(definition["engine"])
        except KeyError:
            raise ValueError(f"Unregistered Agent engine: {definition['engine']}") from None
        AgentDefinition.validate_mode(definition, agent_mode(engine))
        factory = getattr(engine, "for_agent", None)
        if not callable(factory):
            raise ValueError("Agent engines must implement for_agent(definition)")
        bound = factory(deepcopy(definition))
        if inspect.isawaitable(bound):
            if inspect.iscoroutine(bound):
                bound.close()
            raise TypeError("Engine for_agent must be synchronous")
        if not callable(getattr(bound, "execute", None)):
            raise TypeError("for_agent must return an Engine")
        required_capabilities(bound)
        if agent_mode(bound) != agent_mode(engine):
            raise ValueError("Agent binding must preserve Engine Agent mode")
        if steering_mode(bound) != steering_mode(engine):
            raise ValueError("Agent binding must preserve Engine steering mode")
        if getattr(bound, "checkpoint_name", None) != getattr(engine, "checkpoint_name", None):
            raise ValueError("Agent binding must preserve Engine checkpoint name")
        if steering_mode(bound) == SteeringMode.CONSUME:
            checkpoint = getattr(bound, "checkpoint_name", None)
            if (not isinstance(checkpoint, str) or not checkpoint.strip()
                    or not callable(getattr(bound, "validate_resume", None))):
                raise ValueError("Steerable Agent engine requires checkpoint_name and validate_resume")
        return bound
