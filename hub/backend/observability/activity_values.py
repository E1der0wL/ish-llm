"""Hub activity view fields over Backend lifecycle and Provider diagnostic observations."""

from llm.core.contracts import ProjectActivityEvent, ResourceRef, Diagnostic
from ...config.notifications import NotificationKind


class ActivitySource:
    ABSENT = None
    UNAVAILABLE = "—"

    def __init__(self, value):
        self._value = value

    @property
    def identity(self):
        return self._value.get(ResourceRef.session_id.__name__), self._value.get(
            ResourceRef.run_id.__name__
        )

    @property
    def session_id(self):
        return self._value.get(ResourceRef.session_id.__name__, self.UNAVAILABLE)

    @property
    def run_id(self):
        return self._value.get(ResourceRef.run_id.__name__, self.UNAVAILABLE)

    @property
    def step_id(self):
        return self._value.get(ResourceRef.step_id.__name__)

    @classmethod
    def provider(cls, line):
        return {
            ResourceRef.session_id.__name__: line.session_id or cls.UNAVAILABLE,
            ResourceRef.run_id.__name__: line.run_id or cls.UNAVAILABLE,
            **({ResourceRef.step_id.__name__: line.step_id} if line.step_id else {}),
        }


class ActivityRecord:
    TIME = ProjectActivityEvent.time.__name__
    EVENT = ProjectActivityEvent.event.__name__
    SOURCE = ProjectActivityEvent.source.__name__
    STATUS = ProjectActivityEvent.status.__name__
    CODE = ProjectActivityEvent.code.__name__
    DETAILS = Diagnostic.details.__name__
    PROVIDER = "provider"
    SCOPE = "scope"
    LOG_PATH = "log_path"
    PROVIDER_DIAGNOSTICS = "provider_diagnostics"
    PROVIDER_EVENT = PROVIDER
    PROJECT_SCOPE = "project"
    WORKSPACE_SCOPE = "workspace"
    IS_PROVIDER = True
    READ_FAILURE_STATUS = NotificationKind.WARNING.value
    READ_FAILURE_CODE = "provider_log_read_failed"
    READ_FAILURE_ERROR = "error"
    NO_TIME = ""

    def __init__(self, value):
        self._value = value

    @classmethod
    def provider(cls, line, path):
        return {
            cls.TIME: line.timestamp,
            cls.EVENT: cls.PROVIDER_EVENT,
            cls.STATUS: line.status,
            cls.SOURCE: ActivitySource.provider(line),
            cls.CODE: line.code,
            cls.PROVIDER: cls.IS_PROVIDER,
            cls.SCOPE: cls.PROJECT_SCOPE if line.project_id else cls.WORKSPACE_SCOPE,
            cls.DETAILS: line.value,
            cls.LOG_PATH: str(path),
        }

    @classmethod
    def read_error(cls, error):
        return {
            cls.TIME: cls.NO_TIME,
            cls.EVENT: cls.PROVIDER_EVENT,
            cls.STATUS: cls.READ_FAILURE_STATUS,
            cls.SOURCE: {},
            cls.CODE: cls.READ_FAILURE_CODE,
            cls.PROVIDER: cls.IS_PROVIDER,
            cls.SCOPE: cls.WORKSPACE_SCOPE,
            cls.DETAILS: {cls.READ_FAILURE_ERROR: str(error)},
        }

    @property
    def source(self):
        return ActivitySource(self._value[self.SOURCE])

    @property
    def is_provider(self):
        return self._value.get(self.PROVIDER)

    @property
    def code(self):
        return self._value.get(self.CODE)

    @property
    def log_path(self):
        return self._value.get(self.LOG_PATH)

    @property
    def provider_diagnostics(self):
        return self._value.get(self.PROVIDER_DIAGNOSTICS)

    def attach_provider(self, row):
        self._value.setdefault(self.PROVIDER_DIAGNOSTICS, []).append(row)

    @property
    def time(self):
        return self._value[self.TIME]

    @property
    def event(self):
        return self._value[self.EVENT]

    @property
    def status(self):
        return self._value[self.STATUS]

    @property
    def details(self):
        return self._value[self.DETAILS]

    @property
    def scope(self):
        return self._value[self.SCOPE]
