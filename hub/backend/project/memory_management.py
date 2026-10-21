"""Memory editor adapter preserving public scope and revision contracts."""

import json

from llm.components.memory.component import MemoryComponent

from .record_management import RecordManagement


class MemoryManagement(RecordManagement):
    fields = frozenset(("content", "kind", "scope", "session_id", "status", "tags",
                        "expires_at", "metadata", "replaces"))

    def __init__(self, data, name, root, project):
        super().__init__(data, name, root)
        self.project = project

    async def _records(self):
        records = await self.data.alist(status=MemoryComponent.ALL)
        for session in await self.project.sessions.alist(include_deleted=True):
            records.update(await self.data.alist(status=MemoryComponent.ALL, session_id=session.id))
        return records

    async def list(self):
        return [{"name": identifier, "label": record.get("metadata", {}).get("title") or identifier,
                 "description": f"{record['scope']} · {record['status']} · {record['content'][:160]}",
                 "modified": record["updated_at"], "version": self._version(record)}
                for identifier, record in (await self._records()).items()]

    @staticmethod
    def _version(record):
        return {key: record.get(key) for key in ("revision", "generation", "session_id")}

    async def read(self, identifier):
        record = (await self._records())[identifier]
        return {"text": json.dumps({key: value for key, value in record.items() if key in self.fields},
                                   ensure_ascii=False, indent=2), "version": self._version(record)}

    async def save(self, identifier, values, version):
        await self.data.asave(identifier, values, expected_revision=version["revision"],
                              expected_generation=version["generation"], session_id=version["session_id"])

    async def delete(self, identifier, version):
        await self.data.adelete(identifier, expected_revision=version["revision"],
                                expected_generation=version["generation"], session_id=version["session_id"])
