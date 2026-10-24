"""Run storage projections and live read scope; no alternate persisted records."""

from dataclasses import dataclass, fields
from typing import Any
from llm.core.models import Run


class RunRecordIdentity:
    """Required identity before the existing complete Run Reader."""

    def __init__(self, value):
        self._value = value

    @property
    def id(self):
        return self._value[Run.id.__name__]

    @property
    def session_id(self):
        return self._value[Run.session_id.__name__]


@dataclass(frozen=True, slots=True)
class RunInteractionReadScope:
    NAME = "run_interaction_reads"
    ABSENT = None

    session_root: Any
    run_id: str
    interactions: Any
    resolve: Any

    @property
    def key(self):
        return self.session_root, self.run_id

    @property
    def readers(self):
        return self.interactions, self.resolve


@dataclass(frozen=True, slots=True)
class RunCheckpointLineage:
    checkpoints: list

    def to_dict(self):
        return {type(self).checkpoints.__name__: self.checkpoints}


@dataclass(frozen=True, slots=True)
class RunCheckpointPlanEvidence:
    """Shallow hash input after existing Readers; never persisted or execution authority."""

    run: dict
    session: dict
    runs: list
    messages: list
    payloads: list
    files: dict
    interaction_files: dict
    responses: list
    attached: bool

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class RunObservedFailure:
    """Original diagnostic code retained when the Run finalizer selects its stable code."""

    observed_code: str

    def details(self, original):
        return {**original, type(self).observed_code.__name__: self.observed_code}


class RunLogEvent:
    SAVED = "run.saved"
    LOADED = "run.loaded"


class RuntimeLogEvent:
    OBSERVER_FAILED = "observer.failed"
    STARTED = "runtime.started"
    STOPPED = "runtime.stopped"
    INTERRUPTED = "runtime.interrupted"
    QUEUED = "request.queued"
    INTERACTION_POLICY_FAILED = "interaction.policy_failed"
