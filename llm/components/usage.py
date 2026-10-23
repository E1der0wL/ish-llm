"""Component-owned model call receipts, independent of Run completion storage."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from enum import StrEnum
import re
from types import MappingProxyType

from llm.core.json_data import JsonObject
from llm.core.usage import TokenUsage


class ModelUsageStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ModelUsageRecord:
    SCHEMA_VERSION = 1
    DIRECTORY = "usage"
    MANIFEST = "manifest.json"
    INVALID_DIAGNOSTIC = "Model error classification requires a code"
    INVALID_TOKEN_COUNT = "Invalid complete model usage token count"
    UNKNOWN_TOTAL = "Invalid complete model usage"
    schema_version: int
    id: str
    project_id: str
    component: str
    operation: str
    model: str | None
    session_id: str | None
    run_id: str | None
    started_at: str
    ended_at: str | None = None
    status: str = ModelUsageStatus.STARTED
    usage: dict = field(default_factory=dict)
    usage_complete: bool = False
    reserved_tokens: int | None = None
    error: str | None = None
    diagnostic_code: str | None = None

    @classmethod
    def reserve(cls, *, project_id, component, invocation, source, reservation):
        from llm.core.models import new_id, now
        return cls(schema_version=cls.SCHEMA_VERSION, id=new_id(), project_id=project_id,
            component=component, operation=invocation.operation, model=invocation.model,
            session_id=source.session_id if source is not None else None,
            run_id=source.run_id if source is not None else None, started_at=now(), reserved_tokens=reservation)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError("Component model usage requires all current fields")
        JsonObject.validate(value)
        return cls(**deepcopy(value))

    def to_dict(self):
        return asdict(self)

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
        TokenUsage(self.usage).validate(complete=self.usage_complete,
            invalid_count=self.INVALID_TOKEN_COUNT, unknown_total=self.UNKNOWN_TOTAL)
        if not self.usage_complete and self.usage:
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

    def finish(self, *, ended_at, usage, diagnostic_code=None):
        """Preserve the reservation while acquiring validated terminal evidence."""
        observed = TokenUsage(usage)
        known = diagnostic_code is None and observed.total_tokens is not None
        if diagnostic_code is not None and (not isinstance(diagnostic_code, str) or not diagnostic_code):
            raise TypeError(self.INVALID_DIAGNOSTIC)
        result = replace(self, ended_at=ended_at,
            status=ModelUsageStatus.COMPLETED if diagnostic_code is None else ModelUsageStatus.FAILED,
            usage=observed.to_dict() if known else {}, usage_complete=known,
            error=diagnostic_code, diagnostic_code=diagnostic_code)
        result.validate_update(self)
        return result


@dataclass(frozen=True)
class ModelUsageManifest:
    """Required receipt inventory; only explicit initialization may create an empty one."""

    schema_version: int
    project_id: str
    component: str
    records: dict

    def __post_init__(self):
        from llm.components.base import validate_name
        if type(self.schema_version) is not int or self.schema_version != ModelUsageRecord.SCHEMA_VERSION:
            raise ValueError("Invalid Component model usage manifest version")
        validate_name(self.project_id)
        validate_name(self.component)
        if not isinstance(self.records, (dict, MappingProxyType)):
            raise ValueError("Invalid Component model usage manifest records")
        for identifier, revision in self.records.items():
            validate_name(identifier)
            if (identifier + ".json" == ModelUsageRecord.MANIFEST or not isinstance(revision, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", revision)):
                raise ValueError("Invalid Component model usage revision")
        object.__setattr__(self, "records", MappingProxyType(dict(self.records)))

    @classmethod
    def empty(cls, *, project_id, component):
        return cls(ModelUsageRecord.SCHEMA_VERSION, project_id, component, {})

    @classmethod
    def from_dict(cls, value, *, project_id, component):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError("Invalid Component model usage manifest fields")
        result = cls(**value)
        if result.project_id != project_id or result.component != component:
            raise ValueError("Component model usage manifest owner mismatch")
        return result

    def with_record(self, identifier, revision):
        return replace(self, records={**self.records, identifier: revision})

    def to_dict(self):
        # Fields are the declaration; the mapping is dynamic receipt IDs, not another schema.
        return {item.name: dict(value) if isinstance(value, MappingProxyType) else value
                for item in fields(self) for value in (getattr(self, item.name),)}
