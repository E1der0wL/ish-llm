"""저장 설정의 적용 순서와 UI 출처 표시. 실행 객체나 저장소는 소유하지 않는다."""

from copy import deepcopy
from enum import StrEnum
from jsonschema import Draft202012Validator

from .models import ProjectConfig
from llm.core.json_data import JsonObject
from .configuration_views import ConfigurationView, ConfigurationOverride


# Explicit None must override inheritance at the existing public boundaries.
UNSET = object()


class ConfigurationSource(StrEnum):
    PROJECT = "project"
    SESSION = "session"
    AGENT = "agent"
    HOST = "host"
    CLIENT = "client"


class ConfigurationPointer:
    ROOT = ""
    SEPARATOR = "/"
    ESCAPE = "~"
    ESCAPED_ESCAPE = "~0"
    ESCAPED_SEPARATOR = "~1"

    @classmethod
    def append(cls, path, key):
        return path + cls.SEPARATOR + key.replace(cls.ESCAPE, cls.ESCAPED_ESCAPE).replace(
            cls.SEPARATOR, cls.ESCAPED_SEPARATOR)

    @classmethod
    def parts(cls, path):
        return tuple(part.replace(cls.ESCAPED_SEPARATOR, cls.SEPARATOR).replace(
            cls.ESCAPED_ESCAPE, cls.ESCAPE) for part in path.lstrip(cls.SEPARATOR).split(cls.SEPARATOR))




class ConfigurationError:
    INVALID = "Invalid configuration: {message}"
    WIDENING = "{source} configuration widens parent limit: /{path}"
    MISSING = "Missing required setting: {scope}.{key}"
    DEFAULT_SCOPE = "configuration"


def resolve_config(layers, *, schema=None, host=None):
    """뒤의 계층이 우선한다. 호스트 지정 필드는 JSON 경로별로 편집 불가를 표시한다."""
    values, sources, overridden = {}, {}, {}

    def merge(target, changes, source, path=ConfigurationPointer.ROOT):
        for key, value in changes.items():
            pointer = ConfigurationPointer.append(path, key)
            if isinstance(value, dict):
                if not isinstance(target.get(key), dict):
                    if pointer in sources:
                        overridden.setdefault(pointer, []).append(ConfigurationOverride(sources[pointer], target[key]).to_dict())
                    target[key] = {}
                    sources.pop(pointer, None)
                merge(target[key], value, source, pointer)
            else:
                if pointer in sources:
                    overridden.setdefault(pointer, []).append(ConfigurationOverride(sources[pointer], target[key]).to_dict())
                target[key], sources[pointer] = deepcopy(value), source
                for child in tuple(sources):
                    if child.startswith(pointer + ConfigurationPointer.SEPARATOR):
                        del sources[child]

    for source, data in [*layers, (ConfigurationSource.HOST, {} if host is None else host)]:
        JsonObject.validate(data)
        merge(values, data, source)
    if schema is not None:
        error = next(Draft202012Validator(schema).iter_errors(values), None)
        if error is not None:
            raise ValueError(ConfigurationError.INVALID.format(message=error.message))
    return ConfigurationView.create(values, sources, overridden,
        {path: source != ConfigurationSource.HOST for path, source in sources.items()}).to_dict()


def resolve_engine_config(config, name, *, session_config=None, agent=None, schema=None):
    """Project → Session → Agent. child는 schema가 지정한 authority를 넓힐 수 없다."""
    config = ProjectConfig.from_dict(config)
    session = {} if session_config is None else session_config
    ProjectConfig.validate_session(session)
    agent = {} if agent is None else agent
    JsonObject.validate(agent)
    layers = [
        (ConfigurationSource.PROJECT, config.parameters.get("engines", {}).get(name, {})),
        (ConfigurationSource.SESSION, session.get("parameters", {}).get("engines", {}).get(name, {})),
        (ConfigurationSource.AGENT, agent)]
    from .schema import narrowing_paths
    limits = narrowing_paths(schema)

    def read(value, path):
        for key in path:
            if not isinstance(value, dict) or key not in value:
                return None
            value = value[key]
        return value

    prior = {}
    for source, value in layers:
        merged = ProjectConfig.merge(prior, value)
        if schema is not None:
            error = next(Draft202012Validator(schema).iter_errors(merged), None)
            if error is not None:
                raise ValueError(ConfigurationError.INVALID.format(message=error.message))
        for path in limits:
            parent, child = read(prior, path), read(merged, path)
            if parent is not None and (child is None or child > parent):
                raise ValueError(ConfigurationError.WIDENING.format(source=source, path=ConfigurationPointer.SEPARATOR.join(path)))
        prior = merged
    return ConfigurationView.read(resolve_config(layers, schema=schema)).with_engine_key(name).to_dict()


def resolve_component_config(component, project):
    """선택적인 설정 조회 계약. 사용자 컴포넌트에 새 필수 메서드를 강요하지 않는다."""
    describe = getattr(component, ConfigurationView.RESOLVE_METHOD, None)
    if callable(describe):
        return describe(project)
    return resolve_config([(ConfigurationSource.PROJECT, component.get_config(project))])


def require_config(values, key, *, scope=ConfigurationError.DEFAULT_SCOPE):
    if key not in values or values[key] is None:
        raise ValueError(ConfigurationError.MISSING.format(scope=scope, key=key))
    return values[key]
