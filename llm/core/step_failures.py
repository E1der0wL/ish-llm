"""Common evidence that an Engine consumed a failed Step; no Tool/Engine policy."""

from dataclasses import dataclass
from typing import ClassVar
from llm.core.checkpoint_records import CheckpointWrite, CheckpointRevision
from llm.core.contracts import JsonValue
from llm.core.checkpoints import CheckpointStatus, CheckpointLifecycle


@dataclass(frozen=True, slots=True)
class FailureHandling(JsonValue):
    METADATA: ClassVar[str] = "handled_failure"
    KEY_PREFIX: ClassVar[str] = "handled/"
    STEP_FIELD: ClassVar[str] = "failed_step_id"
    STATUS_FIELD: ClassVar[str] = CheckpointLifecycle.status.__name__
    checkpoint: str
    key: str
    revision: str

    @classmethod
    def record(cls, step_id):
        """Completion of handling, never a replacement for the failed Step."""
        if not isinstance(step_id, str) or not step_id:
            raise ValueError("Failure handling requires a Step identity")
        return cls.KEY_PREFIX + step_id, {
            cls.STATUS_FIELD: CheckpointStatus.COMPLETED, cls.STEP_FIELD: step_id}

    def __post_init__(self):
        super(FailureHandling, self).__post_init__()
        if not self.checkpoint or not self.key:
            raise ValueError("Invalid failed Step handling identity")
        CheckpointRevision.validate(self.revision)

    @classmethod
    def receipt(cls, checkpoint, digest):
        write = CheckpointWrite(checkpoint.metadata)
        if not write.is_record:
            raise ValueError("Failure handling requires a checkpoint record")
        return cls(write.name, write.record.key, digest(write.record.value))

    @classmethod
    def read(cls, metadata):
        if cls.METADATA not in metadata:
            return None
        return cls.from_dict(metadata[cls.METADATA])
