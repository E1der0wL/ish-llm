"""Memory editor adapter preserving public scope and revision contracts."""

from llm.components.memory.records import MemoryRecord, MemoryEdit
from .management_values import (
    ManagementEntry,
    EditorRecord,
    DefinitionSummary,
    ManagementOperation,
)

from llm.components.memory.component import MemoryComponent

from .record_management import RecordManagement


class MemoryManagement(RecordManagement):
    fields = MemoryEdit.FIELDS
    DESCRIPTION_LIMIT = 160
    SEPARATOR = " · "

    def __init__(self, data, name, root, project):
        super().__init__(data, name, root)
        self.project = project

    async def _records(self):
        records = await self.data.alist(status=MemoryComponent.ALL)
        for session in await self.project.sessions.alist(
            include_deleted=ManagementOperation.INCLUDE_DELETED
        ):
            records.update(
                await self.data.alist(status=MemoryComponent.ALL, session_id=session.id)
            )
        return records

    async def list(self):
        return [
            ManagementEntry.record(
                identifier,
                DefinitionSummary.metadata_title(MemoryRecord(record).metadata_or_empty)
                or identifier,
                f"{MemoryRecord(record).scope}{self.SEPARATOR}{MemoryRecord(record).status}{self.SEPARATOR}{MemoryRecord(record).content[:self.DESCRIPTION_LIMIT]}",
                MemoryRecord(record).updated_at,
                self._version(record),
            )
            for identifier, record in (await self._records()).items()
        ]

    @staticmethod
    def _version(record):
        return MemoryEdit.version(record)

    async def read(self, identifier):
        record = (await self._records())[identifier]
        return EditorRecord.create(
            EditorRecord.render(MemoryEdit.content(record, self.fields)),
            self._version(record),
        )

    async def save(self, identifier, values, version):
        await self.data.asave(
            identifier,
            values,
            expected_revision=MemoryEdit(version).revision,
            expected_generation=MemoryEdit(version).generation,
            session_id=MemoryEdit(version).session_id,
        )

    async def delete(self, identifier, version):
        await self.data.adelete(
            identifier,
            expected_revision=MemoryEdit(version).revision,
            expected_generation=MemoryEdit(version).generation,
            session_id=MemoryEdit(version).session_id,
        )
