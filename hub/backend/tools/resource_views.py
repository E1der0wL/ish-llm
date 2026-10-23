"""Hub resource-tool response projections; no stored definition or authority."""

from dataclasses import dataclass

from llm.core.editable_snapshot import EditableSnapshot, EditableSnapshotFields


@dataclass(frozen=True, slots=True)
class CreatedResource(EditableSnapshotFields):
    id: str

    @classmethod
    def from_snapshot(cls, identifier, value):
        snapshot = EditableSnapshot.read(value)
        return cls(data=snapshot.data, version=snapshot.version, id=identifier)
