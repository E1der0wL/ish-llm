"""Stored Tool Step identity shared by its execution Writer and evidence readers."""


from dataclasses import asdict, dataclass, fields
from typing import ClassVar


class ToolStepMetadata:
    CALL_ID = "tool_call_id"
    OPERATION_RECEIPT = "operation_receipt"

    @classmethod
    def call_id(cls, metadata):
        return metadata.get(cls.CALL_ID)


    @classmethod
    def attach_operation(cls, metadata, receipt):
        metadata[cls.OPERATION_RECEIPT] = receipt.to_dict()


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
