"""Stored Tool Step identity shared by its execution Writer and evidence readers."""


from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from typing import ClassVar
from .step_event_metadata import StepEventMetadata


class ToolStepMetadata:
    CALL_ID = "tool_call_id"
    OPERATION_RECEIPT = "operation_receipt"
    ARGUMENTS = "arguments"
    OPERATION_KEY = "operation_key"
    IDEMPOTENCY_KEY = "idempotency_key"
    AUTHORIZATION = "authorization"
    ALLOWED = "allowed"
    RETRY_ATTEMPT = "retry_attempt"
    EFFECT = "effect"
    RETRY_ERROR = "retry_error"
    RETRY_FAILURES = "retry_failures"
    REUSED = "reused"

    @classmethod
    def call_id(cls, metadata):
        return metadata.get(cls.CALL_ID)


    @classmethod
    def attach_operation(cls, metadata, receipt):
        metadata[cls.OPERATION_RECEIPT] = receipt.to_dict()

    @classmethod
    def started(cls, metadata, arguments, phase, call):
        return {**deepcopy(metadata or {}), cls.ARGUMENTS: deepcopy(arguments),
                **StepEventMetadata.phase(phase), cls.OPERATION_KEY: call.operation_key,
                cls.IDEMPOTENCY_KEY: call.idempotency_key}

    @classmethod
    def authorized(cls, phase):
        return {**StepEventMetadata.phase(phase), cls.AUTHORIZATION: cls.ALLOWED}

    @classmethod
    def repaired(cls, phase, arguments, call):
        return {**cls.authorized(phase), cls.ARGUMENTS: deepcopy(arguments),
                cls.OPERATION_KEY: call.operation_key, cls.IDEMPOTENCY_KEY: call.idempotency_key}

    @classmethod
    def retry_progress(cls, phase, attempt):
        return {**StepEventMetadata.phase(phase), cls.RETRY_ATTEMPT: attempt}

    @classmethod
    def retrying(cls, phase, attempt, effect, error, failures):
        return {**StepEventMetadata.phase(phase), cls.RETRY_ATTEMPT: attempt,
                cls.EFFECT: effect, cls.RETRY_ERROR: error, cls.RETRY_FAILURES: deepcopy(failures)}

    @classmethod
    def waiting(cls, phase, arguments):
        return {**StepEventMetadata.phase(phase), cls.ARGUMENTS: deepcopy(arguments)}

    @classmethod
    def completed(cls, phase, reused):
        return {**StepEventMetadata.phase(phase), cls.REUSED: reused}


@dataclass(frozen=True, slots=True)
class StepOperationReceipt:
    """Derived audit projection; the Session operation ledger remains authoritative."""

    NO_OVERRIDE: ClassVar[None] = None

    key: str
    status: str
    run_id: str
    step_id: str
    ended_at: str | None
    evidence: str | None

    def to_dict(self):
        return asdict(self)


class OperationReceiptView:
    """Required field projection after the operation Owner's complete read."""

    def __init__(self, value):
        self._value = value

    @property
    def run_id(self):
        return self._value[StepOperationReceipt.run_id.__name__]

    @property
    def step_id(self):
        return self._value[StepOperationReceipt.step_id.__name__]

    def snapshot(self):
        return StepOperationReceipt(**{item.name: self._value[item.name] for item in fields(StepOperationReceipt)})


class ToolRetryProgress:
    """Borrowed UI observations, separate from the persisted Tool retry state."""

    def __init__(self, value):
        self._value = value

    @property
    def phase(self):
        return StepEventMetadata.observed_phase(self._value)

    @property
    def attempt(self):
        return self._value.get(ToolStepMetadata.RETRY_ATTEMPT)
