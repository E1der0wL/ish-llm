"""Engine 등록과 실행 전 설정 검증을 담당한다. 구체적인 Engine 구현이나 저장소에 의존하지 않는다."""

from llm.engines.base import Engine, required_capabilities, steering_mode
from llm.core.steering import SteeringMode
from llm.core.models import ProjectConfig
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
        if steering_mode(engine) == SteeringMode.CONSUME:
            name_of_checkpoint = getattr(engine, "checkpoint_name", None)
            if not isinstance(name_of_checkpoint, str) or not name_of_checkpoint:
                raise ValueError("Instruction consumers must declare checkpoint_name")
        if name in self._engines:
            raise ValueError(f"Engine already registered: {name}")
        self._engines[name] = engine

    def names(self) -> tuple[str, ...]:
        return tuple(self._engines)

    def validate_configuration(self, config, *, session_config=None) -> None:
        """저장 전 공개 설정 계약만 호출한다. execute/capability/모델 함수는 호출하지 않는다."""
        from llm.core.models import ProjectConfig
        from llm.core.schema import checked_implementation_schema
        from jsonschema import Draft202012Validator
        from copy import deepcopy
        import inspect
        config = ProjectConfig(config)
        session = deepcopy(session_config) if session_config is not None else {}
        ProjectConfig.validate_session(session)
        for name, engine in self._engines.items():
            schema = getattr(engine, "configuration_schema", None)
            if callable(schema):
                spec = checked_implementation_schema(schema())
                key = spec.get("x-settings-key", name) if isinstance(spec, dict) else name
                if not isinstance(key, str) or not key.strip():
                    raise ValueError("Engine configuration key must be nonempty text")
                # 호스트 고정값이 잘못된 입력을 가리지 않게 UI와 같은 입력 스키마부터 검사한다.
                for source, owner in (("project", config), ("session", session)):
                    supplied = owner.get("parameters", {}).get("engines", {})
                    if key in supplied:
                        error = next(Draft202012Validator(spec).iter_errors(supplied[key]), None)
                        if error:
                            raise ValueError(f"Invalid Engine configuration ({name}, {source}): {error.message}")
            describe = getattr(engine, "configuration", None)
            if callable(describe):
                try:
                    result = describe(ProjectConfig(config), name, session_config=deepcopy(session))
                    if inspect.isawaitable(result):
                        if inspect.iscoroutine(result):
                            result.close()
                        raise TypeError("Engine configuration must be synchronous")
                except (ValueError, TypeError) as error:
                    raise ValueError(f"Invalid Engine configuration ({name}): {error}") from error

    def resolve(self, name: str) -> Engine:
        return self._engines[name]

    def resolve_request(self, name: str, options: dict) -> Engine:
        """JSON 요청 인자를 실행별 Engine에 연결한다. 등록 인스턴스는 변경하지 않는다.

        for_request는 동기·무효과 factory다. 옵션을 지원하지 않는 Engine은 빈 요청만
        허용하며, 반환된 실행기가 capability 탐색과 실제 실행을 함께 담당한다.
        """
        if not isinstance(options, dict):
            raise TypeError("engine_options must be a JSON object")
        ProjectConfig.validate_settings(options)
        engine = self.resolve(name)
        factory = getattr(engine, "for_request", None)
        if factory is None:
            if options:
                raise ValueError("Engine does not support request options")
            return engine
        bound = factory(deepcopy(options))
        if inspect.isawaitable(bound):
            if inspect.iscoroutine(bound):
                bound.close()
            raise TypeError("Engine for_request must be synchronous")
        if not callable(getattr(bound, "execute", None)):
            raise TypeError("Engine for_request must return an Engine")
        required_capabilities(bound)
        if steering_mode(bound) != steering_mode(engine):
            raise ValueError("Request binding must preserve Engine steering mode")
        if getattr(bound, "checkpoint_name", None) != getattr(engine, "checkpoint_name", None):
            raise ValueError("Request binding must preserve Engine checkpoint name")
        return bound
