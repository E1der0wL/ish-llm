"""Explicit Vision settings, backend registration and configuration observations."""

from copy import deepcopy
from llm.components.base import validate_name
from llm.core.configuration import UNSET, require_config
from llm.core.configuration_views import ConfigurationEnforcement
from llm.core.implementation_parameters import ImplementationParameters, ParameterLookup
from llm.core.json_data import JsonObject
from llm.core.model_protocol import ModelOperation
from llm.core.parameters import ParameterLayout
from llm.core.schema import SchemaKeyword, SchemaType, SchemaBoolean, object_schema, checked_schema
from llm.providers.requests import provider_schema, resolve_provider_options
from llm.services.infrastructure.storage import revision_token
from .image_contracts import ImageLimits
from .records import VisionImageIdentity


class OCRBackendContract:
    DEFAULT_REVISION = "1"
    AUTOMATIC = "auto"
    ABSENT = None
    INVALID_VISION_REVISION = "Vision backend revision is required"
    INVALID_BACKEND = "Register explicit OCR backends; auto is unsupported"
    INVALID_REVISION = "OCR backend revision is required"

    @classmethod
    def validate_revision(cls, revision):
        if not isinstance(revision, str) or not revision:
            raise ValueError(cls.INVALID_VISION_REVISION)

    @classmethod
    def validate(cls, name, backend):
        validate_name(name)
        if name == cls.AUTOMATIC or not callable(cls.recognizer(backend)):
            raise ValueError(cls.INVALID_BACKEND)
        if not isinstance(cls.revision(backend), str) or not backend.revision:
            raise ValueError(cls.INVALID_REVISION)
        checked_schema(backend.describe_config())

    @classmethod
    def recognizer(cls, backend):
        try:
            return backend.recognize
        except AttributeError:
            return cls.ABSENT

    @classmethod
    def revision(cls, backend):
        try:
            return backend.revision
        except AttributeError:
            return cls.ABSENT


class OCRSettings:
    NAME = "ocr"
    ABSENT = None
    BACKEND = "backend"
    BACKENDS = "backends"
    TIMEOUT = "timeout"
    SCOPE = VisionImageIdentity.COMPONENT + ParameterLookup.SEPARATOR + NAME
    POSITIVE_TIMEOUT = 0
    INVALID_BACKEND = "Select a registered OCR backend; auto is unsupported"

    def __init__(self, value):
        self._value = value

    def selected(self, backend, registered):
        if backend is UNSET:
            backend = require_config(self._value, self.BACKEND, scope=self.SCOPE)
        if not isinstance(backend, str) or backend not in registered:
            raise ValueError(self.INVALID_BACKEND)
        return backend

    def parameters(self, backend, options):
        return {
            **self._value.get(self.BACKENDS, {}).get(backend, {}),
            **({} if options is self.ABSENT else options),
        }

    @property
    def timeout(self):
        return self._value.get(self.TIMEOUT)

    @classmethod
    def schema(cls, backends):
        selected = {SchemaKeyword.ENUM: list(backends)} if backends else {SchemaKeyword.NOT: {}}
        return object_schema(
            {
                cls.BACKEND: {SchemaKeyword.TYPE: SchemaType.STRING, **selected},
                cls.TIMEOUT: {
                    SchemaKeyword.TYPE: [SchemaType.NUMBER, SchemaType.NULL],
                    SchemaKeyword.EXCLUSIVE_MINIMUM: cls.POSITIVE_TIMEOUT,
                },
                cls.BACKENDS: object_schema(
                    {key: value.describe_config() for key, value in backends.items()},
                    additionalProperties=SchemaBoolean.REJECT,
                ),
            },
            additionalProperties=SchemaBoolean.REJECT,
        )


class VisionSettings:
    COMPLETION = "completion"
    PROVIDER = "provider"
    LIMITS = "limits"
    OCR = OCRSettings.NAME
    OCR_BACKEND_PATH = OCR + ParameterLookup.SEPARATOR + OCRSettings.BACKEND
    OCR_BACKENDS_PATH = OCR + ParameterLookup.SEPARATOR + OCRSettings.BACKENDS
    OCR_TIMEOUT_PATH = OCR + ParameterLookup.SEPARATOR + OCRSettings.TIMEOUT
    LAYOUT = ParameterLayout(
        config=(COMPLETION,),
        policy=(PROVIDER, LIMITS),
        paths={
            OCR_BACKEND_PATH: ParameterLayout.CONFIG_PREFIX + OCR_BACKEND_PATH,
            OCR_BACKENDS_PATH: ParameterLayout.CONFIG_PREFIX + OCR_BACKENDS_PATH,
            OCR_TIMEOUT_PATH: ParameterLayout.POLICY_PREFIX + OCR_TIMEOUT_PATH,
        },
    )

    def __init__(self, value):
        self._value = value

    @property
    def limits(self):
        return self._value.get(self.LIMITS, {})

    @property
    def completion(self):
        return self._value.get(self.COMPLETION, {})

    @property
    def provider(self):
        return self._value.get(self.PROVIDER, {})

    @property
    def ocr(self):
        return OCRSettings(self._value.get(self.OCR, {}))

    def changed(self, current):
        return current._value != self._value

    @classmethod
    def schema(cls, component):
        return component.parameter_layout.schema(
            object_schema(
                {
                    cls.LIMITS: ImageLimits.schema(),
                    cls.OCR: OCRSettings.schema(component.ocr_backends),
                    cls.COMPLETION: component.model.adapter.describe_config(ModelOperation.COMPLETION),
                    cls.PROVIDER: provider_schema(),
                }
            )
        )

    @classmethod
    def validate(cls, component, values):
        JsonObject.validate(values)
        value = cls(component.parameter_layout.unpack(values))
        resolve_provider_options(value.provider)
        params = value.completion
        component.model.adapter.validate_options(params, operation=ModelOperation.COMPLETION)
        component.model.adapter.validate_vision(params)

    @classmethod
    def required_backend(cls, config):
        return (
            []
            if OCRSettings.BACKEND in ImplementationParameters.read(config).config.get(cls.OCR, {})
            else [OCRSettings.BACKEND]
        )


class VisionConfigurationView:
    ASSET_FORMAT = "asset_format"

    @classmethod
    def enforce(cls, view, component):
        ConfigurationEnforcement.apply(
            view,
            {
                ImplementationParameters.CONFIG: {
                    VisionSettings.COMPLETION: deepcopy(component.model.adapter.vision_enforced)
                },
                cls.ASSET_FORMAT: component.FORMAT_VERSION,
            },
        )


class VisionBinding:
    CONFIGURATION = "configuration"
    REVISION = "revision"
    BACKENDS = OCRSettings.BACKENDS

    @classmethod
    def create(cls, component, project):
        return revision_token(
            {
                cls.CONFIGURATION: component.get_config(project),
                cls.REVISION: component.revision,
                cls.BACKENDS: {key: value.revision for key, value in component.ocr_backends.items()},
            }
        )
