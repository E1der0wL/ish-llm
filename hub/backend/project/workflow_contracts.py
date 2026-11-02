"""Hub authoring messages; backend inspection remains the validation Owner."""

from dataclasses import dataclass

from llm.core.contracts import JsonValue
from llm.components.workflows.inspection import WorkflowInspection


@dataclass(frozen=True, slots=True)
class WorkflowCatalogData(JsonValue):
    schema: dict
    engines: dict[str, dict[str, dict]]
    common_fields: list[str]


@dataclass(frozen=True, slots=True)
class WorkflowInspectionRequest(JsonValue):
    definition: dict
    engine: str


class WorkflowInspectionResponse:
    """Preserve the public JSON response and decode its backend-owned records."""

    VALID = WorkflowInspection.valid.fget.__name__
    INVALID_VALIDITY = 'Inspection validity does not match the diagnostics'
    LOCATION = 'path'

    @classmethod
    def write(cls, report):
        return {cls.VALID: report.valid, **report.to_dict()}

    @classmethod
    def read(cls, value):
        data = dict(value)
        valid = data.pop(cls.VALID)
        report = WorkflowInspection.from_dict(data)
        if type(valid) is not bool or valid != report.valid:
            raise ValueError(cls.INVALID_VALIDITY)
        return report

    @classmethod
    def location(cls, diagnostic):
        # This is an optional public diagnostic location, never a storage path.
        return diagnostic.details.get(cls.LOCATION, '')
