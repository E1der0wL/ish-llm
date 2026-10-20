"""저장 설정의 적용 순서와 UI 출처 표시. 실행 객체나 저장소는 소유하지 않는다."""

from copy import deepcopy
from jsonschema import Draft202012Validator

from .models import ProjectConfig


def resolve_config(layers, *, schema=None, host=None):
    """뒤의 계층이 우선한다. 호스트 지정 필드는 JSON 경로별로 편집 불가를 표시한다."""
    values, sources, overridden = {}, {}, {}

    def merge(target, changes, source, path=""):
        for key, value in changes.items():
            pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if isinstance(value, dict):
                if not isinstance(target.get(key), dict):
                    if pointer in sources:
                        overridden.setdefault(pointer, []).append({"source": sources[pointer], "value": deepcopy(target[key])})
                    target[key] = {}
                    sources.pop(pointer, None)
                merge(target[key], value, source, pointer)
            else:
                if pointer in sources:
                    overridden.setdefault(pointer, []).append({"source": sources[pointer], "value": deepcopy(target[key])})
                target[key], sources[pointer] = deepcopy(value), source
                for child in tuple(sources):
                    if child.startswith(pointer + "/"):
                        del sources[child]

    for source, data in [*layers, ("host", {} if host is None else host)]:
        ProjectConfig.validate_json(data)
        merge(values, data, source)
    if schema is not None:
        error = next(Draft202012Validator(schema).iter_errors(values), None)
        if error is not None:
            raise ValueError("Invalid configuration: " + error.message)
    return {"values": values, "sources": sources, "overridden": overridden,
            "editable": {path: source != "host" for path, source in sources.items()}}


def resolve_engine_config(config, name, *, session_config=None, agent=None, schema=None):
    """Project → Session → Agent. child는 schema가 지정한 authority를 넓힐 수 없다."""
    config = ProjectConfig.from_dict(config)
    session = {} if session_config is None else session_config
    ProjectConfig.validate_session(session)
    agent = {} if agent is None else agent
    ProjectConfig.validate_json(agent)
    layers = [
        ("project", config.parameters.get("engines", {}).get(name, {})),
        ("session", session.get("parameters", {}).get("engines", {}).get(name, {})),
        ("agent", agent)]
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
                raise ValueError("Invalid configuration: " + error.message)
        for path in limits:
            parent, child = read(prior, path), read(merged, path)
            if parent is not None and (child is None or child > parent):
                raise ValueError(f"{source} configuration widens parent limit: /{'/'.join(path)}")
        prior = merged
    view = resolve_config(layers, schema=schema)
    view["configuration_key"] = name
    return view


def resolve_component_config(component, project):
    """선택적인 설정 조회 계약. 사용자 컴포넌트에 새 필수 메서드를 강요하지 않는다."""
    describe = getattr(component, "resolve_config", None)
    if callable(describe):
        return describe(project)
    return resolve_config([("project", component.get_config(project))])


# Public/configuration boundaries use this only where explicit None must override inheritance.
UNSET = object()


def require_config(values, key, *, scope="configuration"):
    if key not in values or values[key] is None:
        raise ValueError(f"Missing required setting: {scope}.{key}")
    return values[key]
