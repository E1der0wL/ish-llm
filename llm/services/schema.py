"""등록 객체가 선언한 Project 편집 스키마를 조합한다. 저장 파일이나 모델은 읽지 않는다."""

from llm.services.runtime.policies import TokenCounters
from llm.core.configuration_views import ConfigurationView, EngineConfigurationKey, ConfigurationPresentation
from llm.core.json_data import JsonPointer, JsonValue

from copy import deepcopy
from dataclasses import dataclass

from llm.components.base import Component, ComponentHook
from llm.core.models import ProjectConfig
from llm.core.configuration_targets import ParameterTargets
from llm.core.policies import PolicySection, UsagePolicy
from llm.core.schema import (
    checked_schema,
    object_schema,
    implementation_schema,
    checked_implementation_schema,
    metadata_schema,
    schema_nodes,
    SchemaKeyword,
    SchemaExtension,
    SchemaResource,
    SchemaBoolean,
)


from .configuration_records import (
    ProjectConfigForm,
    ProjectConfigurationView,
    SessionConfigurationView,
    ProjectConfigurationSnapshot,
    ComponentConfigurationObservation,
    ComponentSchemaObservation,
)


class ProjectSchemaDescription:
    """Public editor catalog and IDs; implementation schemas retain their own validation."""

    UNSPECIFIED = None
    COMPONENTS = "x-components"
    ENGINES = "x-engines"
    SELECTED_COMPONENTS = "x-selected-components"
    DIALECT = "https://json-schema.org/draft/2020-12/schema"
    ENGINE_PREFIX = "urn:ish:engine:"
    COMPONENT_PREFIX = "urn:ish:project-component:"
    CONFIGURATION_SUFFIX = ":configuration"
    COMPONENT_PARAMETERS = "컴포넌트 설정의 유일한 저장 위치. 대상 구현체가 검증·해석한다."
    TARGET_PARAMETERS = "대상별 전달 인자. 같은 이름의 인자를 다른 대상으로 자동 전달하지 않는다."

    @classmethod
    def metadata(cls, components, engines, selected):
        return {
            SchemaKeyword.SCHEMA: cls.DIALECT,
            cls.COMPONENTS: components,
            cls.ENGINES: engines,
            cls.SELECTED_COMPONENTS: list(selected),
        }

    @classmethod
    def engine_id(cls, name):
        return cls.ENGINE_PREFIX + name + cls.CONFIGURATION_SUFFIX

    @classmethod
    def component_id(cls, name):
        return cls.COMPONENT_PREFIX + name + cls.CONFIGURATION_SUFFIX


@dataclass(slots=True)
class ProjectFormSchemas:
    general: dict
    engines: dict
    components: dict

    @classmethod
    def read(cls, schema):
        general = deepcopy(schema)
        config = general[SchemaKeyword.PROPERTIES][ProjectConfigForm.config.__name__][
            SchemaKeyword.PROPERTIES
        ]
        parameters = config.pop(ProjectConfig.PARAMETERS)[SchemaKeyword.PROPERTIES]
        components = parameters.pop(ParameterTargets.COMPONENTS)[SchemaKeyword.PROPERTIES]
        engines = parameters.pop(ParameterTargets.ENGINES)[SchemaKeyword.PROPERTIES]
        general[SchemaKeyword.PROPERTIES].pop(ProjectConfigForm.components.__name__)
        return cls(general, engines, components)


class ProjectConfiguration:
    """Owner of the public configuration description, detached from stored state."""

    HOST_FIXED = "Host-fixed setting: "
    PATH_SEPARATOR = "."

    @staticmethod
    def engine_parameter_schemas(schema):
        config = schema[SchemaKeyword.PROPERTIES][ProjectConfigForm.config.__name__]
        parameters = config[SchemaKeyword.PROPERTIES][ProjectConfig.PARAMETERS]
        return deepcopy(
            parameters[SchemaKeyword.PROPERTIES][ParameterTargets.ENGINES][SchemaKeyword.PROPERTIES]
        )

    @staticmethod
    def engine_form_path(name):
        return (ProjectConfigForm.config.__name__, *ProjectConfig.engine_parameter_path(name))

    @staticmethod
    def component_form_path(name):
        return ProjectConfigForm.config.__name__, ProjectConfig.PARAMETERS, ParameterTargets.COMPONENTS, name

    @staticmethod
    def view(snapshot):
        record = ProjectConfigurationSnapshot(deepcopy(snapshot))
        values = record.form
        return ProjectConfigurationView(
            record.project.id,
            values,
            record.project.config_or(values),
            record.schema,
            record.config_version,
            record.effective_engines,
            record.components,
        )

    @staticmethod
    def with_schema(snapshot, schema):
        return ProjectConfigurationSnapshot.with_schema(snapshot, schema)

    @staticmethod
    def with_values(snapshot, values):
        """Build a detached edit preview, retaining the original CAS token."""
        record = ProjectConfigurationSnapshot(deepcopy(snapshot))
        form = ProjectConfigForm.from_dict(values)
        record.store_form(form)
        record.project.store_config(form)
        return record.to_dict()

    @staticmethod
    def snapshot(project, *, registry, configurations, version, component_versions):
        return ProjectConfigurationSnapshot.create(
            project,
            registry=registry,
            configurations=configurations,
            version=version,
            component_versions=component_versions,
        ).to_dict()

    @staticmethod
    def describe(app, snapshot):
        value = ProjectConfigurationSnapshot(deepcopy(snapshot))
        form = ProjectConfigForm.from_snapshot(value.project.to_dict(), value.required_components)
        value.store_schema(app.describe_project_config(components=form.components))
        value.store_form(form)
        value.store_engines(effective_engines(app, form.config))
        return value.to_dict()

    @staticmethod
    def validate_edit(record, candidate):
        # Effective settings are observations. Never materialize host-fixed
        # leaves by importing a preset from another project.
        record = ProjectConfigurationSnapshot(record)
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
            if schema.get(SchemaExtension.HOST_OVERRIDE):
                fixed.add(path)
            for key, child in schema.get(SchemaKeyword.PROPERTIES, {}).items():
                schema_locks(child, (*path, key))
            for child in schema.get(SchemaKeyword.ALL_OF, ()):
                schema_locks(child, path)

        schema_locks(record.optional_schema)
        engine_keys = record.optional_schema.get(ProjectSchemaDescription.ENGINES, {})
        engines = [
            (
                EngineConfigurationKey.from_view(engine_keys.get(name, {}), default=name).configuration_key,
                view,
            )
            for name, view in record.effective_engines.items()
        ]
        components = [
            (name, ComponentConfigurationObservation.effective_or_empty(spec))
            for name, spec in record.components.items()
        ]
        for kind, entries in ((ParameterTargets.ENGINES, engines), (ParameterTargets.COMPONENTS, components)):
            for name, view in entries:
                for pointer, editable in ConfigurationView.read(view).editable.items():
                    if editable:
                        continue
                    path = (
                        ProjectConfigForm.config.__name__,
                        ProjectConfig.PARAMETERS,
                        kind,
                        name,
                        *JsonPointer.parts(pointer),
                    )
                    fixed.add(path)
        for path in fixed:
            before, after = at(record.values, path), at(candidate, path)
            if before is missing and after is missing:
                continue
            if (
                before is missing
                or after is missing
                or not JsonValue.same(before, after)
            ):
                raise ValueError(
                    ProjectConfiguration.HOST_FIXED + ProjectConfiguration.PATH_SEPARATOR.join(path)
                )


def effective_engines(app, config, *, session_config=ProjectSchemaDescription.UNSPECIFIED):
    """등록 엔진의 공개 설정 계약을 사용한다. 조회 중 모델/준비 함수를 호출하지 않는다."""
    result = {}
    for name in app.engines.names():
        engine = app.engines.get(name)
        describe = ConfigurationView.resolver(engine)
        result[name] = (
            describe(config, name, session_config=session_config)
            if describe
            else ConfigurationView.unavailable().to_dict()
        )
    return result


def describe_project_config(app, components=ProjectSchemaDescription.UNSPECIFIED):
    registry = app.project_manager.components
    selected = (
        registry.names()
        if components is ProjectSchemaDescription.UNSPECIFIED
        else registry.validate(components)
    )
    catalog = {}
    for name in selected:
        component = registry.get(name)
        catalog[name] = ComponentSchemaObservation(
            component.directory,
            list(component.capabilities),
            list(Component.declared_dependencies(component)),
            checked_schema(Component.declared_schema(component, object_schema())),
            callable(
                getattr(
                    component, ComponentHook.VALIDATE_PROJECT_CONFIG, ProjectSchemaDescription.UNSPECIFIED
                )
            ),
        ).to_dict()

    # 선언한 런타임 자원의 선택지만 보강한다. 정책값이나 Engine별 기본값은 생성하지 않는다.
    def resources(spec):
        for node in schema_nodes(spec):
            if node.get(SchemaExtension.RESOURCE) == SchemaResource.TOKEN_COUNTERS:
                names = sorted(TokenCounters.from_resolver(app.policy_resolver))
                # A UI availability projection may narrow, never replace the owner's enum.
                if SchemaKeyword.ENUM in node:
                    names = [name for name in names if name in node[SchemaKeyword.ENUM]]
                if names:
                    node[SchemaKeyword.ENUM] = names
                else:
                    node[SchemaKeyword.ALL_OF] = [*node.get(SchemaKeyword.ALL_OF, []), SchemaBoolean.REJECT]

    engines, engine_catalog = {}, {}
    for name in app.engines.names():
        engine = app.engines.get(name)
        from llm.engines.contracts import EngineHooks

        describe = EngineHooks.schema_describer(engine)
        spec = checked_implementation_schema(describe() if describe else implementation_schema())
        resources(spec)
        key = EngineConfigurationKey.schema_key(spec, default=name)
        if isinstance(spec, dict):
            spec[SchemaKeyword.ID] = ProjectSchemaDescription.engine_id(name)
        engines[key] = {SchemaKeyword.ALL_OF: [engines[key], spec]} if key in engines else spec
        engine_catalog[name] = EngineConfigurationKey(key).to_dict()
    policies = ProjectConfig.describe_policies()
    names = sorted(TokenCounters.from_resolver(app.policy_resolver))
    if names:
        policies[SchemaKeyword.PROPERTIES][PolicySection.USAGE] = UsagePolicy.schema(counters=names)
    # 비활성 컴포넌트의 설정도 보관할 수 있지만 선택 전에는 초기화/실행하지 않는다.
    project_components = {}
    for name in registry.names():
        component = registry.get(name)
        if not callable(
            getattr(component, ComponentHook.VALIDATE_PROJECT_CONFIG, ProjectSchemaDescription.UNSPECIFIED)
        ):
            project_components[name] = SchemaBoolean.REJECT
            continue
        describe = getattr(component, ComponentHook.DESCRIBE_CONFIG, ProjectSchemaDescription.UNSPECIFIED)
        spec = checked_implementation_schema(describe() if describe else implementation_schema())
        resources(spec)
        if isinstance(spec, dict):
            spec[SchemaKeyword.ID] = ProjectSchemaDescription.component_id(name)
            # UI values에는 명시적으로 설정한 값만 제공한다. 중첩/배열/ref의 조건을 보존한다.
        project_components[name] = spec
    config = object_schema(
        {
            ProjectConfig.POLICIES: policies,
            ProjectConfig.DATA: metadata_schema(),
            ProjectConfig.PARAMETERS: object_schema(
                {
                    ParameterTargets.ENGINES: object_schema(engines),
                    ParameterTargets.COMPONENTS: object_schema(
                        project_components,
                        additionalProperties=SchemaBoolean.REJECT,
                        description=ProjectSchemaDescription.COMPONENT_PARAMETERS,
                    ),
                },
                description=ProjectSchemaDescription.TARGET_PARAMETERS,
            ),
        },
        **{
            SchemaKeyword.NOT: {
                SchemaKeyword.ANY_OF: [
                    {SchemaKeyword.REQUIRED: [name]} for name in ProjectConfig.RETIRED_SECTIONS
                ]
            }
        },
    )
    config.update(ConfigurationPresentation.project())
    return checked_schema(
        object_schema(
            ProjectConfigForm.schema(config, registry.names()),
            additionalProperties=SchemaBoolean.REJECT,
            **ProjectSchemaDescription.metadata(catalog, engine_catalog, selected),
        )
    )
