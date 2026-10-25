"""Component manager operations through lifecycle-checked public data handles."""

from llm.core.editable_snapshot import EditableSnapshot
from llm.core.agent_views import AgentSnapshot
from llm.components.rag.document_records import RAGDocumentInput, RAGDocument
from llm.components.rag.document_views import DocumentEdit
from llm.components.rag.retrieval_records import CatalogPage
from .management_values import (
    ManagementAction,
    ManagementOperation,
    ManagementEntry,
    EditorRecord,
    DefinitionSummary,
    SelectedFileImport,
    ImportProvenance,
)

import asyncio
import json
from pathlib import Path
import re
from uuid import uuid4

from ...components import component_spec, ManagerKind, ImportKind, ManagedComponents


class RecordManagement:
    def __init__(self, data, name, root):
        root = str(root) if isinstance(root, str) else root
        self.data, self.name, self.root = data, name, Path(root)
        self.spec = component_spec(name)

    @classmethod
    async def for_project(
        cls,
        project,
        name,
        *,
        root=ManagementOperation.ABSENT,
        rag_jobs=ManagementOperation.ABSENT
    ):
        spec = component_spec(name)
        if spec.manager == ManagerKind.TOOLS:
            raise ValueError(ManagementOperation.UNSUPPORTED_MANAGER)
        data = (
            project.agents
            if spec.manager == ManagerKind.AGENT
            else await project.components.aget(name)
        )
        if not isinstance(data, spec.data_type()):
            raise ValueError(ManagementOperation.UNSUPPORTED_DATA)
        record = await project.aget_data()
        root = record.paths.root if root is ManagementOperation.ABSENT else root
        if spec.manager == ManagerKind.DOCUMENT:
            from .rag_management import RAGManagement

            return RAGManagement(
                data, name, root, project.id, rag_jobs, project=project
            )
        if spec.manager == ManagerKind.MEMORY:
            from .memory_management import MemoryManagement

            return MemoryManagement(data, name, root, project)
        manager = (
            SkillManagement
            if spec.import_kind == ImportKind.SKILL
            else {ManagerKind.RECORD: cls, ManagerKind.AGENT: AgentManagement}[
                spec.manager
            ]
        )
        return manager(data, name, root)

    def source_path(self, argument):
        source = Path(argument).expanduser()
        if not source.is_absolute():
            source = self.root / source
        return source

    @staticmethod
    def import_identifier(source):
        stem = source.stem
        return (
            re.sub(
                SelectedFileImport.IDENTIFIER_PATTERN,
                SelectedFileImport.REPLACEMENT,
                stem,
            )[: SelectedFileImport.MAXIMUM_IDENTIFIER].strip(
                SelectedFileImport.REPLACEMENT
            )
            or uuid4().hex
        )

    def _read_import(self, argument):
        source = self.source_path(argument)
        # Only caller-selected files are read. Imported data is validated and
        # persisted by the component, never copied into its internal directories.
        with source.open(
            SelectedFileImport.MODE, encoding=SelectedFileImport.ENCODING
        ) as stream:
            text = stream.read(
                SelectedFileImport.MAXIMUM_TEXT + SelectedFileImport.OVERFLOW_PROBE
            )
        if len(text) > SelectedFileImport.MAXIMUM_TEXT:
            raise ValueError(SelectedFileImport.TOO_LARGE)
        identifier = self.import_identifier(source)
        if self.spec.import_kind == ImportKind.TEXT:
            values = RAGDocumentInput.content_source(
                source.name, text, ImportProvenance.create(source)
            )
        else:
            values = json.loads(text)
            if not isinstance(values, dict):
                raise ValueError(ManagementOperation.INVALID_IMPORT)
        return identifier, values

    @staticmethod
    def _entry(identifier, values, version):
        return DefinitionSummary.entry(identifier, values, version)

    async def list(self):
        entries = []
        for identifier in await self.data.alist():
            snapshot = EditableSnapshot.read(await self.snapshot(identifier))
            entries.append(self._entry(identifier, snapshot.data, snapshot.version))
        return entries

    async def read(self, identifier):
        snapshot = EditableSnapshot.read(await self.snapshot(identifier))
        return EditorRecord.create(EditorRecord.render(snapshot.data), snapshot.version)

    async def snapshot(self, identifier):
        return await self.data.asnapshot(identifier)

    async def create(self, identifier, values):
        await self.data.acreate(values, identifier=identifier)

    async def save(self, identifier, values, version):
        await self.data.asave(identifier, values, expected_version=version)

    async def delete(self, identifier, version):
        await self.data.adelete(identifier, expected_version=version)

    async def execute(
        self,
        action,
        argument=ManagementOperation.EMPTY_ARGUMENT,
        expected_version=ManagementOperation.ABSENT,
    ):
        from ..progress import OperationProgress
        from ...locales.keys import MessageKey
        OperationProgress.report(OperationProgress.management(action))
        if action == ManagementAction.LIST:
            return await self.list()
        if action == ManagementAction.READ:
            return await self.read(argument)
        if action == ManagementAction.IMPORT:
            identifier, values = await asyncio.to_thread(self._read_import, argument)
            OperationProgress.report(MessageKey.PROGRESS_BACKEND_WRITE)
            await self.create(identifier, values)
        elif (
            action == ManagementAction.CREATE
            and self.name in ManagedComponents.DEFINITION_EDITORS
        ):
            values = json.loads(EditorRecord(argument).text)
            if not isinstance(values, dict):
                raise ValueError(ManagementOperation.INVALID_DATA)
            await self.create(EditorRecord(argument).name, values)
        elif action == ManagementAction.SAVE:
            if expected_version is ManagementOperation.ABSENT:
                raise ValueError(ManagementOperation.RELOAD_SAVE)
            values = json.loads(EditorRecord(argument).text)
            if not isinstance(values, dict):
                raise ValueError(ManagementOperation.INVALID_DATA)
            await self.save(EditorRecord(argument).name, values, expected_version)
        elif action == ManagementAction.DELETE:
            if expected_version is ManagementOperation.ABSENT:
                raise ValueError(ManagementOperation.RELOAD_DELETE)
            await self.delete(argument, expected_version)
        else:
            raise ValueError(ManagementOperation.UNKNOWN)
        OperationProgress.report(MessageKey.PROGRESS_BACKEND_READ)
        return await self.list()


class SkillManagement(RecordManagement):
    """Native directory import and Markdown editing through the Skill owner."""

    async def list(self):
        from llm.components.skills import SkillRecord

        entries = []
        for identifier in await self.data.alist():
            snapshot = EditableSnapshot.read(await self.snapshot(identifier))
            document = SkillRecord.document(snapshot.data)
            entries.append(
                ManagementEntry.record(
                    identifier,
                    document.name,
                    document.description,
                    ManagementEntry.EMPTY,
                    snapshot.version,
                )
            )
        return entries

    async def read(self, identifier):
        from llm.components.skills import SkillRecord

        snapshot = EditableSnapshot.read(await self.snapshot(identifier))
        return EditorRecord.create(
            SkillRecord.document(snapshot.data).text, snapshot.version
        )

    async def execute(
        self,
        action,
        argument=ManagementOperation.EMPTY_ARGUMENT,
        expected_version=ManagementOperation.ABSENT,
    ):
        if action == ManagementAction.IMPORT:
            from llm.core.skills import SkillDocument

            source = self.source_path(argument)
            if source.name == SkillDocument.FILENAME and source.is_file():
                source = source.parent
            await self.data.aimport_directory(source)
            return await self.list()
        if action == ManagementAction.SAVE:
            if expected_version is ManagementOperation.ABSENT:
                raise ValueError(ManagementOperation.RELOAD_SAVE)
            await self.data.awrite_markdown(
                EditorRecord(argument).name,
                EditorRecord(argument).text,
                expected_revision=expected_version,
            )
            return await self.list()
        return await super().execute(action, argument, expected_version)


class AgentManagement(RecordManagement):
    async def list(self):
        from ..execution.engine_selection import HubEngineIdentity
        entries = []
        for identifier in await self.data.alist():
            if not HubEngineIdentity.internal(identifier):
                snapshot = EditableSnapshot.read(await self.snapshot(identifier))
                entries.append(self._entry(identifier, snapshot.data, snapshot.version))
        return entries

    async def snapshot(self, identifier):
        snapshot = AgentSnapshot.read(await self.data.asnapshot(identifier))
        return EditableSnapshot(snapshot.definition, snapshot.revision).to_dict()

    async def save(self, identifier, values, version):
        await self.data.arevise(identifier, values, expected_revision=version)

    async def delete(self, identifier, version):
        await self.data.adelete(identifier, expected_revision=version)


class DocumentManagement(RecordManagement):
    """RAG changes must update its corpus generation, not generic JSON records."""

    PAGE_SIZE = 100

    async def list(self):
        entries, cursor = [], ManagementOperation.ABSENT
        while ManagementOperation.ITERATE:
            page = await self.data.alist_catalog(limit=self.PAGE_SIZE, cursor=cursor)
            entries.extend(
                self._entry(
                    RAGDocument(item).identifier, item, DocumentEdit.version(item)
                )
                for item in CatalogPage(page).documents
            )
            cursor = CatalogPage(page).next_cursor
            if cursor is ManagementOperation.ABSENT:
                return entries

    async def read(self, identifier):
        item = await self.data.aget_document(identifier)
        values = DocumentEdit.content(item)
        return EditorRecord.create(
            EditorRecord.render(values), DocumentEdit.version(item)
        )

    async def create(self, identifier, values):
        await self.data.aadd_document(identifier=identifier, **values)

    async def save(self, identifier, values, version):
        DocumentEdit.require_fields(values)
        await self.data.aupdate_document(
            identifier,
            **values,
            expected_revision=DocumentEdit(version).revision,
            expected_generation=DocumentEdit(version).generation
        )

    async def delete(self, identifier, version):
        await self.data.adelete_document(
            identifier,
            expected_revision=DocumentEdit(version).revision,
            expected_generation=DocumentEdit(version).generation,
        )
