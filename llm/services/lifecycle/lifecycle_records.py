"""Derived lifecycle observations and plan evidence; original records stay with domain Owners."""

from dataclasses import dataclass, fields
from llm.core.models import Project, Session


class LifecycleDeletion:
    DEFAULT = False
    PERMANENT = True
    INVALID = "permanent must be a bool"

    @classmethod
    def validate(cls, permanent):
        if type(permanent) is not bool:
            raise TypeError(cls.INVALID)


class ProjectRecordIdentity:
    def __init__(self, value):
        self._value = value

    @property
    def id(self):
        return self._value[Project.id.__name__]


class SessionRecordIdentity:
    def __init__(self, value):
        self._value = value

    @property
    def id(self):
        return self._value[Session.id.__name__]

    @property
    def project_id(self):
        return self._value[Session.project_id.__name__]

    @property
    def config(self):
        return self._value[Session.config.__name__]


@dataclass(frozen=True, slots=True)
class ProjectBackupMetadata:
    project_id: str

    def to_dict(self):
        return {type(self).project_id.__name__: self.project_id}


@dataclass(slots=True)
class DeletionRecoveryResults:
    projects: list
    sessions: dict

    def include_session(self, project_id, result):
        self.sessions[project_id] = result

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class ResumeTurnDetails:
    status: str
    engine: str
    message_ids: list[str]

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class TurnResumeEvidence:
    run: dict
    checkpoint: str
    interactions: list

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class TurnDeletionEvidence:
    source: dict
    messages: list
    dependencies: list

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


class ProjectLogEvent:
    SAVED = "project.saved"
    LOADED = "project.loaded"
    DELETED = "project.deleted"
    CREATED = "project.created"
    RESTORED = "project.restored"
    CLONED = "project.cloned"


class SessionLogEvent:
    LOADED = "session.loaded"
    INITIALIZED = "sessions.initialized"
    SAVED = "session.saved"
    DELETED = "session.deleted"
    CREATED = "session.created"
    RESTORED = "session.restored"
    CLONED = "session.cloned"
    CONVERSATION_DELETED = "conversation.deleted"


class ComponentLogEvent:
    CONFIGURED = "component.configured"
    CREATED = "component.record_created"
    SAVED = "component.record_saved"
    UPDATED = "component.record_updated"
    DELETED = "component.record_deleted"
    REMOVED = "component.removed"
