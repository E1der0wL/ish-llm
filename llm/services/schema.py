"""등록 객체가 선언한 Project 편집 스키마를 조합한다. 저장 파일이나 모델은 읽지 않는다."""

from llm.core.configuration_views import ConfigurationView, EngineConfigurationKey
from llm.core.configuration import ConfigurationPointer

from copy import deepcopy
import json
from dataclasses import asdict, dataclass, field, fields

from llm.core.configuration import resolve_component_config
from llm.core.models import ConversationStorage, ProjectConfig
from llm.core.policies import PolicySection, UsagePolicy
from llm.core.schema import checked_schema, object_schema, implementation_schema, checked_implementation_schema, metadata_schema, schema_nodes


@dataclass
class ProjectConfigForm:
    """Editable values and their schema share one field declaration.

    Effective Engine/Component values are observations, never editable defaults.
    """
    title: str = field(metadata={"schema": lambda config, choices: {"type": "string"}})
    conversation_storage: str | None = field(metadata={"schema": lambda config, choices: ConversationStorage.schema()})
    components: list[str] = field(metadata={"schema": lambda config, choices: {
        "type": "array", "uniqueItems": True,
        "items": {"type": "string", "enum": list(choices)} if choices else False}})
    config: dict = field(metadata={"schema": lambda config, choices: config})

    @classmethod
    def schema(cls, config, choices):
        return {item.name: item.metadata["schema"](config, choices) for item in fields(cls)}

    @classmethod
    def from_project(cls, project):
        return cls(project.title, project.conversation_storage, list(project.components), project.config.to_dict())

    @classmethod
    def from_snapshot(cls, project, components):
        values = {**project, "components": list(components)}
        return cls.from_dict(values)

    @classmethod
    def from_dict(cls, values):
        return cls(**{item.name: deepcopy(values[item.name]) for item in fields(cls)})

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ProjectConfigurationView:
    """Detached UI observations; mutation still requires the captured version."""
    project_id: str
    values: ProjectConfigForm
    stored_config: dict
    schema: dict
    config_version: str
    effective_engines: dict
    components: dict


@dataclass(frozen=True)
class SessionConfigurationView:
    """Detached Session overrides and effective Engine observations, never stored defaults."""

    config: dict
    effective_engines: dict

    @classmethod
    def describe(cls, app, project, session):
        return cls(deepcopy(session.config),
                   effective_engines(app, project.config, session_config=session.config))

    def to_dict(self):
        return asdict(self)


class ProjectConfiguration:
    """Owner of the public configuration description, detached from stored state."""

    @staticmethod
    def view(snapshot):
        record = deepcopy(snapshot)
        values = ProjectConfigForm.from_dict(record["values"])
        return ProjectConfigurationView(record["project"]["id"], values,
            record["project"].get("config", deepcopy(values.config)), record["schema"],
            record["config_version"], record.get("effective_engines", {}), record.get("components", {}))

    @staticmethod
    def with_schema(snapshot, schema):
        return {**deepcopy(snapshot), "schema": deepcopy(schema)}

    @staticmethod
    def with_values(snapshot, values):
        """Build a detached edit preview, retaining the original CAS token."""
        record = deepcopy(snapshot)
        form = ProjectConfigForm.from_dict(values)
        record["values"] = form.to_dict()
        record["project"]["config"] = deepcopy(form.config)
        return record

    @staticmethod
    def snapshot(project, *, registry, configurations, version, component_versions):
        values = asdict(ProjectConfigForm.from_project(project))
        values.pop("components")
        return {"project": {"id": project.id, **values},
                "policy_schema": ProjectConfig.describe_policies(),
                "config_version": version, "component_versions": deepcopy(component_versions),
                "components": {name: {
                    "directory": registry.get(name).directory,
                    "configuration": deepcopy(configurations[name]),
                    "effective": resolve_component_config(registry.get(name), deepcopy(project))}
                    for name in project.components}}

    @staticmethod
    def describe(app, snapshot):
        value = deepcopy(snapshot)
        form = ProjectConfigForm.from_snapshot(value["project"], value["components"])
        value["schema"] = app.describe_project_config(components=form.components)
        value["values"] = asdict(form)
        value["effective_engines"] = effective_engines(app, form.config)
        return value


    @staticmethod
    def validate_edit(record, candidate):
        # Effective settings are observations. Never materialize host-fixed
        # leaves by importing a preset from another project.
        missing = object()
        def at(value, path):
            for part in path:
                if not isinstance(value, dict) or part not in value:
                    return missing
                value = value[part]
            return value
        fixed = set()
        def schema_locks(schema, path=()):
            if not isinstance(schema, dict):
                return
            if schema.get("x-host-override"):
                fixed.add(path)
            for key, child in schema.get("properties", {}).items():
                schema_locks(child, (*path, key))
            for child in schema.get("allOf", ()):
                schema_locks(child, path)
        schema_locks(record.get("schema", {}))
        engine_keys = record.get("schema", {}).get("x-engines", {})
        engines = [(EngineConfigurationKey.from_view(engine_keys.get(name, {}), default=name).configuration_key, view)
                   for name, view in record.get("effective_engines", {}).items()]
        components = [(name, spec.get("effective") or {}) for name, spec in record.get("components", {}).items()]
        for kind, entries in (("engines", engines), ("components", components)):
            for name, view in entries:
                for pointer, editable in ConfigurationView.read(view).editable.items():
                    if editable:
                        continue
                    path = ("config", "parameters", kind, name,
                            *ConfigurationPointer.parts(pointer))
                    fixed.add(path)
        for path in fixed:
            before, after = at(record["values"], path), at(candidate, path)
            if before is missing and after is missing:
                continue
            if (before is missing or after is missing or
                    json.dumps(before, sort_keys=True, allow_nan=False) != json.dumps(after, sort_keys=True, allow_nan=False)):
                raise ValueError("Host-fixed setting: " + ".".join(path))

def effective_engines(app, config, *, session_config=None):
    """등록 엔진의 공개 설정 계약을 사용한다. 조회 중 모델/준비 함수를 호출하지 않는다."""
    result = {}
    for name in app.engines.names():
        engine = app.engines.get(name)
        describe = getattr(engine, ConfigurationView.RESOLVE_METHOD, None)
        result[name] = describe(config, name, session_config=session_config) if describe else ConfigurationView.unavailable().to_dict()
    return result


def describe_project_config(app, components=None):
    registry = app.project_manager.components
    selected = registry.names() if components is None else registry.validate(components)
    catalog = {}
    for name in selected:
        component = registry.get(name)
        catalog[name] = {"directory": component.directory, "capabilities": list(component.capabilities),
                         "required_components": list(getattr(component, "required_components", ())),
                         "record_schema": checked_schema(getattr(component, "schema", object_schema())),
                         "project_configuration": callable(getattr(component, "validate_project_config", None))}
    # 선언한 런타임 자원의 선택지만 보강한다. 정책값이나 Engine별 기본값은 생성하지 않는다.
    def resources(spec):
        for node in schema_nodes(spec):
            if node.get("x-resource") == "token_counters":
                names = sorted(getattr(app.policy_resolver, "token_counters", {}))
                # A UI availability projection may narrow, never replace the owner's enum.
                if "enum" in node:
                    names = [name for name in names if name in node["enum"]]
                if names:
                    node["enum"] = names
                else:
                    node["allOf"] = [*node.get("allOf", []), False]

    engines, engine_catalog = {}, {}
    for name in app.engines.names():
        engine = app.engines.get(name)
        describe = getattr(engine, "describe_config", None)
        spec = checked_implementation_schema(describe() if describe else implementation_schema())
        resources(spec)
        key = spec.get("x-parameter-key", name) if isinstance(spec, dict) else name
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Engine configuration key must be nonempty text")
        if isinstance(spec, dict):
            spec["$id"] = "urn:ish:engine:" + name + ":configuration"
        engines[key] = {"allOf": [engines[key], spec]} if key in engines else spec
        engine_catalog[name] = EngineConfigurationKey(key).to_dict()
    policies = ProjectConfig.describe_policies()
    names = sorted(getattr(app.policy_resolver, "token_counters", {}))
    if names:
        policies["properties"][PolicySection.USAGE] = UsagePolicy.schema(counters=names)
    # 비활성 컴포넌트의 설정도 보관할 수 있지만 선택 전에는 초기화/실행하지 않는다.
    project_components = {}
    for name in registry.names():
        component = registry.get(name)
        if not callable(getattr(component, "validate_project_config", None)):
            project_components[name] = False
            continue
        describe = getattr(component, "describe_config", None)
        spec = checked_implementation_schema(describe() if describe else implementation_schema())
        resources(spec)
        if isinstance(spec, dict):
            spec["$id"] = "urn:ish:project-component:" + name + ":configuration"
            # UI values에는 명시적으로 설정한 값만 제공한다. 중첩/배열/ref의 조건을 보존한다.
        project_components[name] = spec
    config = object_schema({
        "policies": policies, "data": metadata_schema(),
        "parameters": object_schema({
            "engines": object_schema(engines),
            "components": object_schema(project_components, additionalProperties=False,
                description="컴포넌트 설정의 유일한 저장 위치. 대상 구현체가 검증·해석한다."),
        }, description="대상별 전달 인자. 같은 이름의 인자를 다른 대상으로 자동 전달하지 않는다."),
    }, **{"not": {"anyOf": [{"required": [name]} for name in
          ("completion", "engines", "component_configurations", "session_defaults", "default_engine")]}})
    config.update({"x-owner": "project", "x-scope": "execution-policy"})
    return checked_schema(object_schema(ProjectConfigForm.schema(config, registry.names()),
        additionalProperties=False, **{"$schema": "https://json-schema.org/draft/2020-12/schema",
         "x-components": catalog, "x-engines": engine_catalog, "x-selected-components": list(selected)}))
