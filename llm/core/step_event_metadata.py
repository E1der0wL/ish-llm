"""Step event display metadata; these observations never authorize a state transition."""

from enum import StrEnum


class StepEventPhase(StrEnum):
    STARTED = "started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class StepEventMetadata:
    PARENT_STEP_ID = "parent_step_id"
    CHECKPOINT_NAME = "checkpoint_name"

    class Field:
        PHASE = "phase"
        ERROR_CODE = "error_code"

    @classmethod
    def observed_phase(cls, metadata):
        return metadata.get(cls.Field.PHASE)

    @classmethod
    def read_phase(cls, metadata, default):
        value = metadata.get(cls.Field.PHASE)
        return value if isinstance(value, str) and value.strip() else default

    @classmethod
    def read_error_code(cls, metadata, default):
        value = metadata.get(cls.Field.ERROR_CODE)
        return value if isinstance(value, str) and value.strip() else default

    @classmethod
    def phase(cls, value):
        return {cls.Field.PHASE: value}

    @classmethod
    def checkpoint_name(cls, metadata):
        return (metadata or {}).get(cls.CHECKPOINT_NAME)

    @classmethod
    def failure(cls, phase, code):
        return {cls.Field.PHASE: phase, cls.Field.ERROR_CODE: code}
