"""Test-space ownership evidence and its separate incomplete-resource observation."""

from dataclasses import dataclass, fields, replace
import re

from llm.core.contracts import JsonValue


class WorkspaceIdentity:
    PATTERN = r"[0-9a-f]{32}"
    SCHEMA_PATTERN = "^" + PATTERN + "$"
    PROJECT_PATTERN = r"[A-Za-z0-9_-]+"
    INVALID = "Invalid test workspace_id"
    INVALID_PROJECT = "Invalid test workspace Project identity"

    @classmethod
    def valid(cls, value):
        return isinstance(value, str) and re.fullmatch(cls.PATTERN, value) is not None

    @classmethod
    def validate(cls, value):
        if not cls.valid(value):
            raise ValueError(cls.INVALID)

    @classmethod
    def valid_project(cls, value):
        return isinstance(value, str) and re.fullmatch(cls.PROJECT_PATTERN, value) is not None

    @classmethod
    def validate_project(cls, value):
        if not cls.valid_project(value):
            raise ValueError(cls.INVALID_PROJECT)


class WorkspaceEvidence:
    INCOMPLETE = "Test workspace creation is incomplete; explicitly delete it"
    INVALID = "Invalid test workspace ownership record"
    READY = True
    INCOMPLETE_STATE = False


@dataclass(frozen=True, slots=True)
class WorkspaceOrigin(JsonValue):
    session_id: str | None
    run_id: str | None
    step_id: str | None

    @classmethod
    def from_call(cls, call):
        return cls(call.session_id, call.run_id, call.step_id)


@dataclass(frozen=True, slots=True)
class WorkspaceRecord(JsonValue):
    VERSION = 1

    storage_version: int
    workspace_id: str
    project_id: str
    created_at: str
    created_by: WorkspaceOrigin
    source_paths: list[str]
    ready: bool = WorkspaceEvidence.INCOMPLETE_STATE

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if (self.storage_version != self.VERSION or not WorkspaceIdentity.valid(self.workspace_id)
                or not WorkspaceIdentity.valid_project(self.project_id)):
            raise ValueError(WorkspaceEvidence.INVALID)

    @classmethod
    def read(cls, value, *, project_id, workspace_id):
        # Every stored field is required even if its creation API has a default.
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(WorkspaceEvidence.INVALID)
        try:
            record = cls.from_dict(value)
        except (TypeError, ValueError):
            raise ValueError(WorkspaceEvidence.INVALID) from None
        if record.project_id != project_id or record.workspace_id != workspace_id:
            raise ValueError(WorkspaceEvidence.INVALID)
        return record

    def published(self):
        return replace(self, ready=WorkspaceEvidence.READY)

    def require_ready(self):
        if not self.ready:
            raise ValueError(WorkspaceEvidence.INCOMPLETE)


@dataclass(frozen=True, slots=True)
class MissingWorkspaceRecord(JsonValue):
    """Observation of a trusted directory only; never reconstructed execution evidence."""

    REASON = "missing_record"

    workspace_id: str
    project_id: str
    ready: bool = WorkspaceEvidence.INCOMPLETE_STATE
    reason: str = REASON

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if self.ready is not WorkspaceEvidence.INCOMPLETE_STATE or self.reason != self.REASON:
            raise ValueError(WorkspaceEvidence.INVALID)

    def require_ready(self):
        raise ValueError(WorkspaceEvidence.INCOMPLETE)


@dataclass(frozen=True, slots=True)
class WorkspaceInventory(JsonValue):
    workspaces: list[WorkspaceRecord | MissingWorkspaceRecord]


@dataclass(frozen=True, slots=True)
class WorkspaceDeleted(JsonValue):
    workspace_id: str
    deleted: bool = True
