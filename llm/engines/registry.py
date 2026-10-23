"""Engine 등록과 실행 전 설정 검증을 담당한다. 구체적인 Engine 구현이나 저장소에 의존하지 않는다."""

from llm.core.configuration_views import ConfigurationView, EngineConfigurationKey

from llm.engines.base import Engine, required_capabilities, steering_mode, agent_mode
from llm.core.steering import SteeringMode
from .contracts import EngineAgentBinding, EngineHooks
from copy import deepcopy
import inspect


class EngineRegistry:
    NO_SESSION_CONFIG = None
    INVALID_NAME = "Engine name must be a nonempty string"
    INVALID_ENGINE = "Engine must implement execute(context)"
    MISSING_CHECKPOINT = "Instruction consumers must declare checkpoint_name"
    DUPLICATE_NAME = "Engine already registered: {name}"
    INVALID_SOURCE_CONFIG = "Invalid Engine configuration ({name}, {source}): {message}"
    ASYNC_CONFIGURATION = "Engine configuration must be synchronous"
    INVALID_CONFIGURATION = "Invalid Engine configuration ({name}): {error}"
    UNREGISTERED_AGENT = "Unregistered Agent engine: {name}"

    def __init__(self) -> None:
        self._engines: dict[str, Engine] = {}

    def register(self, name: str, engine: Engine) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError(self.INVALID_NAME)
        if not callable(EngineHooks.executor(engine)):
            raise TypeError(self.INVALID_ENGINE)
        required_capabilities(engine)
        agent_mode(engine)
        if steering_mode(engine) == SteeringMode.CONSUME:
            name_of_checkpoint = EngineHooks.checkpoint_name(engine)
            if not isinstance(name_of_checkpoint, str) or not name_of_checkpoint:
                raise ValueError(self.MISSING_CHECKPOINT)
        if name in self._engines:
            raise ValueError(self.DUPLICATE_NAME.format(name=name))
        self._engines[name] = engine

    def names(self) -> tuple[str, ...]:
        return tuple(self._engines)

    def validate_config(self, config, *, session_config=NO_SESSION_CONFIG) -> None:
        """저장 전 공개 설정 계약만 호출한다. execute/capability/모델 함수는 호출하지 않는다."""
        from llm.core.models import ProjectConfig
        from llm.core.configuration_targets import SessionConfiguration
        from llm.core.configuration import ConfigurationSource
        from llm.core.schema import checked_implementation_schema
        from jsonschema import Draft202012Validator
        from copy import deepcopy
        import inspect
        config = ProjectConfig.from_dict(config)
        session = deepcopy(session_config) if session_config is not self.NO_SESSION_CONFIG else {}
        session_view = SessionConfiguration.read(session)
        for name, engine in self._engines.items():
            schema = EngineHooks.schema_describer(engine)
            if callable(schema):
                spec = checked_implementation_schema(schema())
                key = EngineConfigurationKey.schema_key(spec, default=name)
                # 호스트 고정값이 잘못된 입력을 가리지 않게 UI와 같은 입력 스키마부터 검사한다.
                for source, owner in ((ConfigurationSource.PROJECT, config), (ConfigurationSource.SESSION, session_view)):
                    supplied = owner.parameter_targets
                    if supplied.has_engine(key):
                        error = next(Draft202012Validator(spec).iter_errors(supplied.engine(key).to_dict()), None)
                        if error:
                            raise ValueError(self.INVALID_SOURCE_CONFIG.format(name=name, source=source, message=error.message))
            describe = ConfigurationView.resolver(engine)
            if callable(describe):
                try:
                    result = describe(ProjectConfig.from_dict(config), name, session_config=deepcopy(session))
                    if inspect.isawaitable(result):
                        if inspect.iscoroutine(result):
                            result.close()
                        raise TypeError(self.ASYNC_CONFIGURATION)
                except (ValueError, TypeError) as error:
                    raise ValueError(self.INVALID_CONFIGURATION.format(name=name, error=error)) from error

    def get(self, name: str) -> Engine:
        return self._engines[name]

    def resolve_agent(self, definition: dict) -> Engine:
        """Validate one selected, synchronous and effect-free Agent factory."""
        from llm.core.agents import AgentDefinition
        AgentDefinition.validate_selected(definition)
        try:
            engine = self.get(AgentDefinition.engine(definition))
        except KeyError:
            raise ValueError(self.UNREGISTERED_AGENT.format(name=AgentDefinition.engine(definition))) from None
        return EngineAgentBinding.bind(engine, definition)
