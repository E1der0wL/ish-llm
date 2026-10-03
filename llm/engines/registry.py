"""Engine 등록과 실행 전 설정 검증을 담당한다. 구체적인 Engine 구현이나 저장소에 의존하지 않는다."""

from llm.engines.base import Engine, required_capabilities, steering_mode
from llm.core.steering import SteeringMode


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
        from llm.core.schema import checked_schema
        from jsonschema import Draft202012Validator
        from copy import deepcopy
        import inspect
        config = ProjectConfig(config)
        session = deepcopy(session_config) if session_config is not None else {}
        ProjectConfig.validate_session(session)
        for name, engine in self._engines.items():
            schema = getattr(engine, "configuration_schema", None)
            if callable(schema):
                spec = checked_schema(schema())
                key = spec.get("x-settings-key", name) if isinstance(spec, dict) else name
                if not isinstance(key, str) or not key.strip():
                    raise ValueError("Engine configuration key must be nonempty text")
                # 호스트 고정값이 잘못된 입력을 가리지 않게 UI와 같은 입력 스키마부터 검사한다.
                for source, owner in (("project", config), ("session", session)):
                    supplied = owner.get("engines", {})
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
