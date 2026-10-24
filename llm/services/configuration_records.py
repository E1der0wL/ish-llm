"""Configuration form and snapshot projections; source validation stays with its Owner."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from llm.core.models import ConversationStorage, ProjectConfig, Project
from llm.core.schema import SchemaKeyword, SchemaType, SchemaBoolean
from llm.core.configuration import resolve_component_config


class ConfigurationValidator:
    ABSENT = None
    INVALID = "Configuration validator must be callable"

    @classmethod
    def require(cls, validator):
        if validator is not cls.ABSENT and not callable(validator):
            raise TypeError(cls.INVALID)


class FormSchema:
    KEY = "schema"


@dataclass(slots=True)
class ProjectConfigForm:
    """Editable values and their schema share one field declaration.

    Effective Engine/Component values are observations, never editable defaults.
    """

    title: str = field(
        metadata={FormSchema.KEY: lambda config, choices: {SchemaKeyword.TYPE: SchemaType.STRING}}
    )
    conversation_storage: str | None = field(
        metadata={FormSchema.KEY: lambda config, choices: ConversationStorage.schema()}
    )
    components: list[str] = field(
        metadata={
            FormSchema.KEY: lambda config, choices: {
                SchemaKeyword.TYPE: SchemaType.ARRAY,
                SchemaKeyword.UNIQUE_ITEMS: SchemaBoolean.ACCEPT,
                SchemaKeyword.ITEMS: (
                    {SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.ENUM: list(choices)}
                    if choices
                    else SchemaBoolean.REJECT
                ),
            }
        }
    )
    config: dict = field(metadata={FormSchema.KEY: lambda config, choices: config})

    @classmethod
    def schema(cls, config, choices):
        return {item.name: item.metadata[FormSchema.KEY](config, choices) for item in fields(cls)}

    @classmethod
    def from_project(cls, project):
        return cls(
            project.title, project.conversation_storage, list(project.components), project.config.to_dict()
        )

    @classmethod
    def from_snapshot(cls, project, components):
        values = {**project, cls.components.__name__: list(components)}
        return cls.from_dict(values)

    @classmethod
    def from_dict(cls, values):
        return cls(**{item.name: deepcopy(values[item.name]) for item in fields(cls)})

    @classmethod
    def store_components(cls, value, components):
        value[cls.components.__name__] = components[:]

    @classmethod
    def omit_engine(cls, value, name):
        if cls.config.__name__ in value:
            value[cls.config.__name__] = ProjectConfig.without_engine_parameters(value[cls.config.__name__], name)

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

    def edit_values(self):
        result = self.values.to_dict()
        result[ProjectConfigForm.config.__name__] = self.stored_config
        return result

    def component_effective(self, name):
        return ComponentConfigurationObservation.optional_effective(self.components.get(name, {}))


@dataclass(frozen=True)
class SessionConfigurationView:
    """Detached Session overrides and effective Engine observations, never stored defaults."""

    config: dict
    effective_engines: dict

    @classmethod
    def describe(cls, app, project, session):
        from .schema import effective_engines

        return cls(
            deepcopy(session.config), effective_engines(app, project.config, session_config=session.config)
        )

    def to_dict(self):
        return asdict(self)


class ProjectRecordSnapshot:
    """Borrowed observation of a Project already read by its lifecycle Owner."""

    def __init__(self, value):
        self._value = value

    @property
    def id(self):
        return self._value[Project.id.__name__]

    def config_or(self, form):
        return self._value.get(ProjectConfigForm.config.__name__, deepcopy(form.config))

    def store_config(self, form):
        self._value[ProjectConfigForm.config.__name__] = deepcopy(form.config)

    @classmethod
    def from_project(cls, project):
        values = asdict(ProjectConfigForm.from_project(project))
        values.pop(ProjectConfigForm.components.__name__)
        return cls({Project.id.__name__: project.id, **values})

    def to_dict(self):
        return self._value


@dataclass(frozen=True, slots=True)
class ComponentConfigurationObservation:
    directory: str
    configuration: dict
    effective: dict

    @classmethod
    def describe(cls, registry, name, configurations, project):
        return cls(
            registry.get(name).directory,
            deepcopy(configurations[name]),
            resolve_component_config(registry.get(name), deepcopy(project)),
        )

    @classmethod
    def effective_or_empty(cls, value):
        return value.get(cls.effective.__name__) or {}

    @classmethod
    def optional_effective(cls, value):
        return value.get(cls.effective.__name__)

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class ComponentSchemaObservation:
    directory: str
    capabilities: list
    required_components: list
    record_schema: dict | bool
    project_configuration: bool

    @classmethod
    def requirements(cls, value):
        return value.get(cls.required_components.__name__, [])

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


class ProjectConfigurationSnapshot:
    """One projection of the existing public map, including opaque observation extensions.

    The caller retains its original copy/borrow boundary. No stored record is read,
    defaulted or repaired here; this class never retains a Project/Component object.
    """

    class Field:
        PROJECT = "project"
        VALUES = "values"
        SCHEMA = "schema"
        POLICY_SCHEMA = "policy_schema"
        CONFIG_VERSION = "config_version"
        COMPONENT_VERSIONS = "component_versions"
        COMPONENTS = "components"
        EFFECTIVE_ENGINES = "effective_engines"

    def __init__(self, value):
        self._value = value

    @property
    def project(self):
        return ProjectRecordSnapshot(self._value[self.Field.PROJECT])

    @property
    def form(self):
        return ProjectConfigForm.from_dict(self._value[self.Field.VALUES])

    @property
    def values(self):
        return self._value[self.Field.VALUES]

    @property
    def schema(self):
        return self._value[self.Field.SCHEMA]

    @property
    def optional_schema(self):
        return self._value.get(self.Field.SCHEMA, {})

    @property
    def config_version(self):
        return self._value[self.Field.CONFIG_VERSION]

    @property
    def effective_engines(self):
        return self._value.get(self.Field.EFFECTIVE_ENGINES, {})

    @property
    def components(self):
        return self._value.get(self.Field.COMPONENTS, {})

    @property
    def required_components(self):
        return self._value[self.Field.COMPONENTS]

    def store_form(self, form):
        self._value[self.Field.VALUES] = asdict(form)

    def store_schema(self, schema):
        self._value[self.Field.SCHEMA] = schema

    def store_engines(self, values):
        self._value[self.Field.EFFECTIVE_ENGINES] = values

    @classmethod
    def with_schema(cls, snapshot, schema):
        return {**deepcopy(snapshot), cls.Field.SCHEMA: deepcopy(schema)}

    @classmethod
    def create(cls, project, *, registry, configurations, version, component_versions):
        record = ProjectRecordSnapshot.from_project(project)
        return cls(
            {
                cls.Field.PROJECT: record.to_dict(),
                cls.Field.POLICY_SCHEMA: ProjectConfig.describe_policies(),
                cls.Field.CONFIG_VERSION: version,
                cls.Field.COMPONENT_VERSIONS: deepcopy(component_versions),
                cls.Field.COMPONENTS: {
                    name: ComponentConfigurationObservation.describe(
                        registry, name, configurations, project
                    ).to_dict()
                    for name in project.components
                },
            }
        )

    def to_dict(self):
        return self._value
