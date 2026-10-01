"""등록 객체가 선언한 Project 편집 스키마를 조합한다. 저장 파일이나 모델은 읽지 않는다."""

from copy import deepcopy
from llm.core.models import ProjectConfig
from llm.core.schema import checked_schema, completion_schema, object_schema


def effective_engines(app, config, *, session_config=None):
    """등록 엔진의 공개 설정 계약을 사용한다. 조회 중 모델/준비 함수를 호출하지 않는다."""
    result = {}
    for name in app.engines.names():
        engine = app.engines.resolve(name)
        describe = getattr(engine, "configuration", None)
        result[name] = describe(config, name, session_config=session_config) if describe else {"runtime_only": True}
    return result


def project_schema(app, components=None):
    registry = app.project_manager.components
    selected = registry.names() if components is None else registry.validate(components)
    catalog = {}
    for name in selected:
        component = registry.get(name)
        catalog[name] = {"directory": component.directory, "capabilities": list(component.capabilities),
                         "record_schema": checked_schema(getattr(component, "schema", object_schema())),
                         "project_configuration": callable(getattr(component, "validate_project_configuration", None))}
    engines, engine_catalog = {}, {}
    for name in app.engines.names():
        engine = app.engines.resolve(name)
        describe = getattr(engine, "configuration_schema", None)
        spec = checked_schema(describe() if describe else object_schema())
        key = spec.get("x-settings-key", name) if isinstance(spec, dict) else name
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Engine configuration key must be nonempty text")
        if isinstance(spec, dict):
            spec["$id"] = "urn:ish:engine:" + name + ":configuration"
        engines[key] = {"allOf": [engines[key], spec]} if key in engines else spec
        engine_catalog[name] = {"configuration_key": key}
    policies = ProjectConfig.policy_schema()
    for section in ("completion", "retention"):
        names = sorted(getattr(app.policy_resolver, "token_counters", {}))
        if names:
            policies["properties"][section]["properties"]["counter"]["enum"] = names
    # 비활성 컴포넌트의 설정도 보관할 수 있지만 선택 전에는 초기화/실행하지 않는다.
    project_components = {}
    for name in registry.names():
        component = registry.get(name)
        if not callable(getattr(component, "validate_project_configuration", None)):
            project_components[name] = False
            continue
        describe = getattr(component, "configuration_schema", None)
        spec = checked_schema(describe() if describe else object_schema())
        if isinstance(spec, dict):
            spec["$id"] = "urn:ish:project-component:" + name + ":configuration"
            # UI values에는 명시적으로 설정한 값만 제공한다. 중첩/배열/ref의 조건을 보존한다.
        project_components[name] = spec
    session = object_schema({"completion": completion_schema(), "engines": object_schema(deepcopy(engines)), "data": object_schema()},
                         **{"not": {"anyOf": [{"required": ["policies"]}, {"required": ["component_configurations"]}]}})
    config = object_schema({"completion": completion_schema(), "engines": object_schema(engines),
        "policies": policies, "session_defaults": session, "data": object_schema(),
        "component_configurations": object_schema(project_components, additionalProperties=False,
            description="컴포넌트 설정의 유일한 저장 위치. 명시된 값만 저장하며 등록만으로 활성화하지 않는다."),
        "default_engine": {"type": "string", **({"enum": list(app.engines.names())} if app.engines.names() else {}),
                           "description": "UI의 실행 선택 기본값. submit은 engine을 명시해야 한다."}})
    return checked_schema(object_schema({
        "title": {"type": "string"},
        "conversation_storage": {"type": ["string", "null"], "enum": ["file", "memory", None],
            "description": "null은 주입 저장소 전용. Session이 존재하면 변경할 수 없다."},
        "components": {"type": "array", "uniqueItems": True,
                       "items": {"type": "string", "enum": list(registry.names())} if registry.names() else False},
        "config": config,
    }, additionalProperties=False, **{"$schema": "https://json-schema.org/draft/2020-12/schema",
         "x-components": catalog, "x-engines": engine_catalog, "x-selected-components": list(selected)}))
