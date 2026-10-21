"""Component-owned model call receipts, independent of Run completion storage."""

from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from enum import StrEnum
import re

from llm.core.models import ProjectConfig


class ModelUsageStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ModelUsageRecord:
    SCHEMA_VERSION = 1
    DIRECTORY = "usage"
    MANIFEST = "manifest.json"
    schema_version: int
    id: str
    project_id: str
    component: str
    operation: str
    model: str | None
    session_id: str | None
    run_id: str | None
    started_at: str
    ended_at: str | None
    status: str
    usage: dict
    usage_complete: bool
    reserved_tokens: int | None
    error: str | None
    diagnostic_code: str | None

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError("Component model usage requires all current fields")
        ProjectConfig.validate_json(value)
        return cls(**deepcopy(value))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def validate_manifest(cls, value, *, project_id, component):
        from llm.components.base import validate_name
        if (not isinstance(value, dict) or value.keys() != {"schema_version", "project_id", "component", "records"}
                or type(value["schema_version"]) is not int or value["schema_version"] != cls.SCHEMA_VERSION
                or value["project_id"] != project_id or value["component"] != component
                or not isinstance(value["records"], dict)):
            raise ValueError("Invalid Component model usage manifest")
        for identifier, revision in value["records"].items():
            validate_name(identifier)
            if (identifier + ".json" == cls.MANIFEST or not isinstance(revision, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", revision)):
                raise ValueError("Invalid Component model usage revision")

    @staticmethod
    def timestamp(value):
        if not isinstance(value, str):
            raise ValueError("Model usage timestamp must be text")
        stamp = datetime.fromisoformat(value)
        if stamp.utcoffset() is None:
            raise ValueError("Model usage timestamps require a timezone")
        return stamp

    def __post_init__(self):
        from llm.components.base import validate_name
        if type(self.schema_version) is not int or self.schema_version != self.SCHEMA_VERSION:
            raise ValueError("Invalid component model usage version")
        for value in (self.id, self.project_id, self.component):
            validate_name(value)
        if self.id + ".json" == self.MANIFEST:
            raise ValueError("Reserved model usage identity")
        if not isinstance(self.operation, str) or not self.operation.strip():
            raise ValueError("Invalid component model operation")
        if self.model is not None and not isinstance(self.model, str):
            raise ValueError("Invalid component model identity")
        if (self.session_id is None) != (self.run_id is None):
            raise ValueError("Component model usage requires paired Session and Run ownership")
        if self.session_id is not None:
            validate_name(self.session_id)
            validate_name(self.run_id)
        status = ModelUsageStatus(self.status)
        started = self.timestamp(self.started_at)
        if type(self.usage_complete) is not bool or not isinstance(self.usage, dict):
            raise ValueError("Invalid component model usage")
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = self.usage.get(key)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Invalid complete model usage token count")
        if self.usage_complete:
            if type(self.usage.get("total_tokens")) is not int:
                raise ValueError("Invalid complete model usage")
        elif self.usage:
            raise ValueError("Unknown component usage must remain an explicit empty object")
        if self.reserved_tokens is not None and (type(self.reserved_tokens) is not int or self.reserved_tokens < 0):
            raise ValueError("Invalid model token reservation")
        if status == ModelUsageStatus.STARTED:
            if (self.ended_at is not None or self.usage_complete or self.usage
                    or self.error is not None or self.diagnostic_code is not None):
                raise ValueError("Started model usage cannot claim terminal evidence")
        else:
            if self.timestamp(self.ended_at) < started:
                raise ValueError("Model usage ended before it started")
            if status == ModelUsageStatus.COMPLETED:
                if self.error is not None or self.diagnostic_code is not None:
                    raise ValueError("Completed model usage cannot claim a failure")
            elif (not isinstance(self.error, str) or not self.error
                    or self.diagnostic_code != self.error or self.usage_complete or self.usage):
                raise ValueError("Failed model usage requires its diagnostic and unknown usage")

    def validate_update(self, previous):
        """Only a current reservation may acquire a terminal result."""
        if previous.status != ModelUsageStatus.STARTED or self.status == ModelUsageStatus.STARTED:
            raise ValueError("Model usage requires a started-to-terminal transition")
        for name in ("schema_version", "id", "project_id", "component", "operation", "model",
                     "session_id", "run_id", "started_at", "reserved_tokens"):
            if getattr(self, name) != getattr(previous, name):
                raise ValueError("Model usage reservation identity changed")
