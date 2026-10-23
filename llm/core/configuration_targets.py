"""Project-owned target selection; implementations own each selected envelope."""

from copy import deepcopy
from dataclasses import dataclass

from .implementation_parameters import ImplementationParameters
from .json_data import JsonObject


@dataclass(frozen=True, init=False, slots=True)
class ParameterTargets:
    ENGINES = "engines"
    COMPONENTS = "components"
    CATEGORIES = (ENGINES, COMPONENTS)
    INVALID_OBJECT = "parameters must be a dictionary"
    UNKNOWN_TARGETS = "Unsupported parameter targets: {unknown}"
    INVALID_TARGETS = "parameters.{category} must map target names to dictionaries"
    SCOPE = "parameters.{category}.{name}"
    SESSION_COMPONENTS = "Component parameters belong to ProjectConfig, not Session configuration"
    PROJECT_SCOPE = True
    SESSION_SCOPE = False

    _value: dict

    @classmethod
    def validate(cls, value, *, project=PROJECT_SCOPE):
        if not isinstance(value, dict):
            raise TypeError(cls.INVALID_OBJECT)
        unknown = value.keys() - set(cls.CATEGORIES)
        if unknown:
            raise ValueError(cls.UNKNOWN_TARGETS.format(unknown=sorted(unknown)))
        if not project and cls.COMPONENTS in value:
            raise ValueError(cls.SESSION_COMPONENTS)
        for category in cls.CATEGORIES:
            targets = value.get(category, {})
            if not isinstance(targets, dict) or any(not isinstance(options, dict) for options in targets.values()):
                raise TypeError(cls.INVALID_TARGETS.format(category=category))
            for name, options in targets.items():
                ImplementationParameters.validate(options, scope=cls.SCOPE.format(category=category, name=name))

    @classmethod
    def read(cls, value):
        cls.validate(value)
        JsonObject.validate(value)
        result = object.__new__(cls)
        object.__setattr__(result, cls._value.__name__, deepcopy(value))
        return result

    @property
    def engine_names(self):
        return tuple(self._value.get(self.ENGINES, {}))

    @property
    def component_names(self):
        return tuple(self._value.get(self.COMPONENTS, {}))

    def has_engine(self, name):
        return name in self._value.get(self.ENGINES, {})

    def engine(self, name):
        return ImplementationParameters.read(self._value.get(self.ENGINES, {}).get(name, {}))

    def component(self, name):
        return ImplementationParameters.read(self._value.get(self.COMPONENTS, {}).get(name, {}))

    def _with_target(self, category, name, value):
        result = self.to_dict()
        parameters = value if isinstance(value, ImplementationParameters) else ImplementationParameters.read(value)
        result.setdefault(category, {})[name] = parameters.to_dict()
        return self.read(result)

    def with_engine(self, name, value):
        return self._with_target(self.ENGINES, name, value)

    def with_component(self, name, value):
        return self._with_target(self.COMPONENTS, name, value)

    def _without_target(self, category, name):
        result = self.to_dict()
        result.get(category, {}).pop(name, None)
        return self.read(result)

    def without_engine(self, name):
        return self._without_target(self.ENGINES, name)

    def without_component(self, name):
        return self._without_target(self.COMPONENTS, name)

    def to_dict(self):
        return deepcopy(self._value)


@dataclass(frozen=True, init=False, slots=True)
class SessionConfiguration:
    _value: dict

    @classmethod
    def read(cls, value):
        from .models import ProjectConfig
        ProjectConfig.validate_session(value)
        result = object.__new__(cls)
        object.__setattr__(result, cls._value.__name__, deepcopy(value))
        return result

    @property
    def parameter_targets(self):
        from .models import ProjectConfig
        return ParameterTargets.read(self._value.get(ProjectConfig.PARAMETERS, {}))

    def with_engine(self, name, parameters):
        from .models import ProjectConfig
        value = self.to_dict()
        value[ProjectConfig.PARAMETERS] = self.parameter_targets.with_engine(name, parameters).to_dict()
        return self.read(value)

    def to_dict(self):
        return deepcopy(self._value)


@dataclass(frozen=True, init=False, slots=True)
class ResolvedEngineConfiguration:
    ENGINE = "engine"
    INVALID_OBJECT = "Resolved Engine configuration must be an object"

    _value: dict

    @classmethod
    def read(cls, value):
        from .models import ProjectConfig
        if not isinstance(value, dict):
            raise TypeError(cls.INVALID_OBJECT)
        ImplementationParameters.validate(value[cls.ENGINE])
        ProjectConfig.from_dict({key: item for key, item in value.items() if key != cls.ENGINE})
        result = object.__new__(cls)
        object.__setattr__(result, cls._value.__name__, deepcopy(value))
        return result

    @classmethod
    def create(cls, config, name):
        from .models import ProjectConfig
        value = deepcopy(config)
        value[cls.ENGINE] = ParameterTargets.read(value.get(ProjectConfig.PARAMETERS, {})).engine(name).to_dict()
        return cls.read(value)

    @property
    def engine(self):
        return ImplementationParameters.read(self._value[self.ENGINE])

    def to_dict(self):
        return deepcopy(self._value)
