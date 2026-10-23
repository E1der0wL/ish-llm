"""Stored Run/Step observations; payload meaning belongs to its value type.

These accessors do not persist files or grant execution authority. Run results
reject an explicitly null final output; optional Step observations retain their
existing null semantics. Both paths use the same payload Reader.
"""

from copy import deepcopy
from enum import StrEnum


class ExecutionObservations:
    class Field(StrEnum):
        OUTPUT = "output"
        DIAGNOSTIC = "diagnostic"
        PROGRESS = "progress"

    @classmethod
    def has_output(cls, metadata):
        return cls.Field.OUTPUT in metadata

    @classmethod
    def run_output(cls, metadata):
        from .results import EngineOutput
        return EngineOutput.from_dict(metadata[cls.Field.OUTPUT]) if cls.has_output(metadata) else None

    @classmethod
    def step_output(cls, metadata):
        from .results import EngineOutput
        value = metadata.get(cls.Field.OUTPUT)
        return None if value is None else EngineOutput.from_dict(value)

    @classmethod
    def has_diagnostic(cls, metadata):
        return cls.Field.DIAGNOSTIC in metadata

    @classmethod
    def diagnostic(cls, metadata):
        from .contracts import Diagnostic
        value = metadata.get(cls.Field.DIAGNOSTIC)
        return None if value is None else Diagnostic.from_dict(value)

    @classmethod
    def diagnostic_record(cls, metadata):
        return deepcopy(metadata.get(cls.Field.DIAGNOSTIC, {}))

    @classmethod
    def progress(cls, metadata):
        from .contracts import OperationProgress
        value = metadata.get(cls.Field.PROGRESS)
        return None if value is None else OperationProgress.from_dict(value)

    @classmethod
    def store_output(cls, metadata, output):
        metadata[cls.Field.OUTPUT] = output.to_dict()

    @classmethod
    def store_diagnostic(cls, metadata, diagnostic):
        metadata[cls.Field.DIAGNOSTIC] = diagnostic.to_dict()

    @classmethod
    def store_progress(cls, metadata, progress):
        metadata[cls.Field.PROGRESS] = progress.to_dict()
