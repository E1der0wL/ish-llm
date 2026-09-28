"""저장 설정의 적용 순서와 UI 출처 표시. 실행 객체나 저장소는 소유하지 않는다."""

from copy import deepcopy
from jsonschema import Draft202012Validator

from .models import ProjectConfig


def resolve_configuration(defaults, layers, *, schema=None, host=None):
    """뒤의 계층이 우선한다. 호스트 지정 필드는 JSON 경로별로 편집 불가를 표시한다."""
    values, sources, overridden = {}, {}, {}

    def merge(target, changes, source, path=""):
        for key, value in changes.items():
            pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if isinstance(value, dict):
                if not isinstance(target.get(key), dict):
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

    for source, data in [("default", defaults), *layers, ("host", host or {})]:
        ProjectConfig.validate_settings(data)
        merge(values, data, source)
    if schema is not None:
        error = next(Draft202012Validator(schema).iter_errors(values), None)
        if error is not None:
            raise ValueError("Invalid configuration: " + error.message)
    return {"values": values, "sources": sources, "overridden": overridden,
            "editable": {path: source != "host" for path, source in sources.items()}}


def engine_configuration(config, name, defaults, *, task_config=None, agent=None, host=None, schema=None):
    """Project → Task → Agent → host를 Engine과 UI가 같은 함수로 해석한다."""
    config = ProjectConfig(config)
    task = task_config or {}
    ProjectConfig.validate_task(task)
    view = resolve_configuration(defaults, [
        ("project", config.engines.get(name, {})),
        ("task", task.get("engines", {}).get(name, {})),
        ("agent", agent or {})], host=host, schema=schema)
    view["configuration_key"] = name
    return view


def component_configuration(component, project):
    """선택적인 설정 조회 계약. 사용자 컴포넌트에 새 필수 메서드를 강요하지 않는다."""
    describe = getattr(component, "effective_configuration", None)
    if callable(describe):
        return describe(project)
    return resolve_configuration({}, [("component", component.configuration(project))])
