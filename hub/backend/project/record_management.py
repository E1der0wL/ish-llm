"""Component manager operations through lifecycle-checked public data handles."""

import asyncio
import json
from pathlib import Path
import re
from uuid import uuid4

from ...components import component_spec


class RecordManagement:
    def __init__(self, data, name, root):
        root = str(root) if isinstance(root, str) else root
        self.data, self.name, self.root = data, name, Path(root)
        self.spec = component_spec(name)

    @classmethod
    async def for_project(cls, project, name, *, root=None, rag_jobs=None):
        spec = component_spec(name)
        if spec.manager == "tools":
            raise ValueError("Unsupported component manager")
        data = await project.components.aget(name)
        if not isinstance(data, spec.data_type()):
            raise ValueError("Unsupported component data contract")
        record = await project.aget_data()
        root = record.paths.root if root is None else root
        if spec.manager == "document":
            from .rag_management import RAGManagement
            return RAGManagement(data, name, root, project.id, rag_jobs, project=project)
        if spec.manager == "memory":
            from .memory_management import MemoryManagement
            return MemoryManagement(data, name, root, project)
        manager = {"record": cls, "agent": AgentManagement}[spec.manager]
        return manager(data, name, root)

    def source_path(self, argument):
        source = Path(argument).expanduser()
        if not source.is_absolute():
            source = self.root / source
        return source

    @staticmethod
    def import_identifier(source):
        stem = source.parent.name if source.name.lower() == "skill.md" else source.stem
        return re.sub(r"[^A-Za-z0-9_-]", "_", stem)[:64].strip("_") or uuid4().hex

    def _read_import(self, argument):
        source = self.source_path(argument)
        # Only caller-selected files are read. Imported data is validated and
        # persisted by the component, never copied into its internal directories.
        with source.open("r", encoding="utf-8-sig") as stream:
            text = stream.read(8 * 1024 * 1024 + 1)
        if len(text) > 8 * 1024 * 1024:
            raise ValueError("Import file exceeds 8 MiB of text")
        stem = source.parent.name if source.name.lower() == "skill.md" else source.stem
        identifier = self.import_identifier(source)
        if self.spec.import_kind == "text":
            values = {"title": source.name, "content": text, "metadata": {"source": str(source)}}
        elif self.spec.import_kind == "skill" and source.suffix.lower() in (".md", ".txt"):
            values = {"title": stem, "instructions": text}
        else:
            values = json.loads(text)
            if not isinstance(values, dict):
                raise ValueError("Component import must contain a JSON object")
        return identifier, values

    @staticmethod
    def _entry(identifier, values, version):
        metadata = values.get("metadata") or {}
        return {"name": identifier, "label": values.get("title") or metadata.get("title") or identifier,
                "description": str(values.get("description") or values.get("purpose") or metadata.get("description") or
                                   values.get("instructions") or values.get("content") or "")[:200],
                "modified": metadata.get("updated_at", ""), "version": version}

    async def list(self):
        entries = []
        for identifier in await self.data.alist():
            snapshot = await self.snapshot(identifier)
            entries.append(self._entry(identifier, snapshot["data"], snapshot["version"]))
        return entries

    async def read(self, identifier):
        snapshot = await self.snapshot(identifier)
        return {"text": json.dumps(snapshot["data"], ensure_ascii=False, indent=2), "version": snapshot["version"]}

    async def snapshot(self, identifier):
        return await self.data.asnapshot(identifier)

    async def create(self, identifier, values):
        await self.data.acreate(values, identifier=identifier)

    async def save(self, identifier, values, version):
        await self.data.asave(identifier, values, expected_version=version)

    async def delete(self, identifier, version):
        await self.data.adelete(identifier, expected_version=version)

    async def execute(self, action, argument="", expected_version=None):
        if action == "list":
            return await self.list()
        if action == "read":
            return await self.read(argument)
        if action == "import":
            identifier, values = await asyncio.to_thread(self._read_import, argument)
            await self.create(identifier, values)
        elif action == "create" and self.name in ("prompts", "agents", "workflows", "memory"):
            values = json.loads(argument["text"])
            if not isinstance(values, dict):
                raise ValueError("Component data must be a JSON object")
            await self.create(argument["name"], values)
        elif action == "save":
            if expected_version is None:
                raise ValueError("Reload the item before saving")
            values = json.loads(argument["text"])
            if not isinstance(values, dict):
                raise ValueError("Component data must be a JSON object")
            await self.save(argument["name"], values, expected_version)
        elif action == "delete":
            if expected_version is None:
                raise ValueError("Reload the item before deleting")
            await self.delete(argument, expected_version)
        else:
            raise ValueError("Unknown component management operation")
        return await self.list()


class AgentManagement(RecordManagement):
    async def snapshot(self, identifier):
        snapshot = await self.data.asnapshot(identifier)
        return {"data": snapshot["definition"], "version": snapshot["revision"]}

    async def save(self, identifier, values, version):
        await self.data.arevise(identifier, values, expected_revision=version)


class DocumentManagement(RecordManagement):
    """RAG changes must update its corpus generation, not generic JSON records."""

    async def list(self):
        entries, cursor = [], None
        while True:
            page = await self.data.alist_catalog(limit=100, cursor=cursor)
            entries.extend(self._entry(item["id"], item,
                           {"generation": item["generation"], "revision": item["revision"]})
                           for item in page["documents"])
            cursor = page["next_cursor"]
            if cursor is None:
                return entries

    async def read(self, identifier):
        item = await self.data.aget_document(identifier)
        values = {key: item[key] for key in ("title", "content", "metadata")}
        values["description"] = item.get("description", "")
        return {"text": json.dumps(values, ensure_ascii=False, indent=2),
                "version": {"generation": item["generation"], "revision": item["revision"]}}

    async def create(self, identifier, values):
        await self.data.aadd_document(identifier=identifier, **values)

    async def save(self, identifier, values, version):
        if (not {"title", "content", "metadata"} <= values.keys() or
                values.keys() - {"title", "content", "metadata", "description"}):
            raise ValueError("RAG documents require title, content, metadata and optional description")
        await self.data.aupdate_document(identifier, **values, expected_revision=version["revision"],
                                         expected_generation=version["generation"])

    async def delete(self, identifier, version):
        await self.data.adelete_document(identifier, expected_revision=version["revision"],
                                         expected_generation=version["generation"])
