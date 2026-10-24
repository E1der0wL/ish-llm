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

    class Field:
        PHASE = "phase"
        ERROR_CODE = "error_code"

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
    def failure(cls, phase, code):
        return {cls.Field.PHASE: phase, cls.Field.ERROR_CODE: code}
