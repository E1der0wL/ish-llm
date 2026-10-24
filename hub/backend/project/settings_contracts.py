"""Settings commands and shallow public response projections shared with the UI."""

from dataclasses import dataclass, fields
from copy import deepcopy
from enum import StrEnum
from typing import ClassVar

from llm.core.usage import UsageSummary
from ...errors.contracts import ErrorInfo


class SettingsOperation(StrEnum):
    CATALOG = "catalog"
    LIST_PROJECTS = "list_projects"
    LOAD_PROJECT = "load_project"
    SAVE_PROJECT = "save_project"
    CREATE_PROJECT = "create_project"
    SELECT_COMPONENTS = "select_components"
    CLONE_PROJECT = "clone_project"
    DELETE_PROJECT = "delete_project"
    RENAME_PROJECT = "rename_project"
    ACTIVATE_PROJECT = "activate_project"
    SAVE_GLOBAL = "save_global"
    SAVE_ALL = "save_all"
    PRESET_LIST = "preset_list"
    PRESET_EXPORT = "preset_export"
    PRESET_SAVE = "preset_save"
    PRESET_APPLY = "preset_apply"


class SettingsDispatch:
    SAVE_PROJECT_VALUES_POSITION = 1
    CREATE_PROJECT_VALUES_POSITION = 0
    PRESET_PREFIX = "preset_"
    PRESETS = (
        SettingsOperation.PRESET_LIST,
        SettingsOperation.PRESET_EXPORT,
        SettingsOperation.PRESET_SAVE,
        SettingsOperation.PRESET_APPLY,
    )
    DIRECT = frozenset(SettingsOperation) - frozenset(PRESETS)
    PROJECT_WRITES = (SettingsOperation.SAVE_PROJECT, SettingsOperation.CREATE_PROJECT)
    UNSUPPORTED_SAVE = "Unsupported settings save operation"
    UNKNOWN_OPERATION = "Unknown settings operation"

    @classmethod
    def preset_method(cls, operation):
        return operation.removeprefix(cls.PRESET_PREFIX)

    @classmethod
    def project_values(cls, operation, args):
        position = (
            cls.SAVE_PROJECT_VALUES_POSITION
            if operation == SettingsOperation.SAVE_PROJECT
            else cls.CREATE_PROJECT_VALUES_POSITION
        )
        return deepcopy(args[position])


class SettingsResponse:
    """Keep live ErrorInfo/Schema values as observations, not recursive serialization."""

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class ProjectSummary(SettingsResponse):
    id: str
    title: str

    @classmethod
    def from_data(cls, data):
        return cls(data.id, data.title)

    @classmethod
    def from_record(cls, value):
        return cls(value[cls.id.__name__], value[cls.title.__name__])


@dataclass(slots=True)
class ProjectSummaryObservation:
    """The current UI row borrows its detached public response record."""

    record: dict

    @property
    def id(self):
        return self.record[ProjectSummary.id.__name__]

    @property
    def title(self):
        return self.record[ProjectSummary.title.__name__]

    @property
    def present(self):
        return bool(self.record)

    def set_title(self, title):
        self.record[ProjectSummary.title.__name__] = title


class ProjectSummaries:
    NO_MATCH = None

    def __init__(self, records):
        self._records = records

    def to_records(self):
        """The existing public observation boundary retains its borrowed list."""
        return self._records

    def rows(self):
        return (ProjectSummaryObservation(value) for value in self._records)

    def identifiers(self):
        return {row.id for row in self.rows()}

    def find(self, identifier):
        return next((row for row in self.rows() if row.id == identifier), self.NO_MATCH)

    def title_or(self, identifier, fallback):
        return next((row.title for row in self.rows() if row.id == identifier), fallback)

    def replace(self, identifier, title):
        self._records = [row.record for row in self.rows() if row.id != identifier] + [
            ProjectSummary(identifier, title).to_dict()]

    def rename_matches(self, identifier, title):
        for row in self.rows():
            if row.id == identifier:
                row.set_title(title)


@dataclass(frozen=True, slots=True)
class ProjectUsageRecord:
    identity: ProjectSummary
    daily: list
    summary: UsageSummary

    @classmethod
    def collect(cls, data, totals):
        from .usage import daily_usage

        identity = ProjectSummary.from_data(data)
        calls = UsageSummary.calls_from_record(totals)
        daily = daily_usage(calls)
        summary = UsageSummary.from_metrics(totals, calls=calls)
        return cls(identity, daily, summary)

    def to_dict(self):
        return {
            **self.identity.to_dict(),
            type(self).daily.__name__: self.daily,
            **self.summary.metrics_record(),
        }


@dataclass(frozen=True, slots=True)
class ProjectUsageFailure(ProjectSummary):
    OPERATION: ClassVar[str] = "model_usage"
    error: ErrorInfo


@dataclass(frozen=True, slots=True)
class ProjectUsageObservation:
    """Observe a public usage row without repairing or reading stored receipts."""

    record: dict

    @property
    def has_error(self):
        return ProjectUsageFailure.error.__name__ in self.record

    @property
    def error(self):
        return self.record[ProjectUsageFailure.error.__name__]

    @property
    def title(self):
        return self.record[ProjectSummary.title.__name__]

    @property
    def period_seconds(self):
        return self.record[UsageSummary.period_seconds.__name__]


@dataclass(frozen=True, slots=True)
class SettingsCatalog(SettingsResponse):
    RISK_SCHEME_FIELD: ClassVar[str] = "x-hub-risk-scheme"
    projects: list
    schema: dict
    host: dict
    usage: list
    active_project: str
    preferences: dict

    @classmethod
    def observe(cls, value):
        return SettingsCatalogObservation(value)


@dataclass(frozen=True, slots=True)
class SettingsBatch(SettingsResponse):
    results: list
    error: ErrorInfo | None = None

    @classmethod
    def from_record(cls, value):
        return cls(value[cls.results.__name__], value[cls.error.__name__])

    @classmethod
    def error_from_record(cls, value):
        return value[cls.error.__name__]

    @classmethod
    def observe(cls, value):
        return SettingsBatchObservation(value)


@dataclass(frozen=True, slots=True)
class PresetApplication(SettingsResponse):
    batch: dict
    catalog: dict
    project: dict
    presets: dict

    @classmethod
    def observe(cls, value):
        return PresetApplicationObservation(value)


@dataclass(frozen=True, slots=True)
class SettingsCatalogObservation:
    """Borrow the public response; only requested fields are interpreted."""

    NO_ACTIVE_PROJECT: ClassVar[str] = ""
    record: dict

    @property
    def schema(self):
        return self.record[SettingsCatalog.schema.__name__]

    @property
    def projects(self):
        return ProjectSummaries(self.record[SettingsCatalog.projects.__name__])

    @property
    def active_project(self):
        return self.record.get(SettingsCatalog.active_project.__name__, self.NO_ACTIVE_PROJECT)

    @property
    def usage(self):
        return self.record.get(SettingsCatalog.usage.__name__, [])

    @property
    def preferences(self):
        from ...config.preference_records import PreferenceSnapshot

        return PreferenceSnapshot(self.record[SettingsCatalog.preferences.__name__])


@dataclass(frozen=True, slots=True)
class SettingsBatchObservation:
    record: dict

    @property
    def results(self):
        return self.record[SettingsBatch.results.__name__]

    @property
    def optional_error(self):
        return self.record.get(SettingsBatch.error.__name__)

    @property
    def required_error(self):
        return SettingsBatch.error_from_record(self.record)


@dataclass(frozen=True, slots=True)
class PresetApplicationObservation:
    """Observe the applied preset without reading the unused Project response."""

    record: dict

    @property
    def catalog(self):
        return SettingsCatalog.observe(self.record[PresetApplication.catalog.__name__])

    @property
    def presets_record(self):
        return self.record[PresetApplication.presets.__name__]

    @property
    def batch_error(self):
        return SettingsBatch.error_from_record(self.record[PresetApplication.batch.__name__])
