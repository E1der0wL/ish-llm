"""Builtin-owned completed output references; file bytes remain with the selected Component."""

from copy import deepcopy
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
import tempfile

from llm.core.configuration import UNSET
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import revision_token, async_method, drain_on_cancel
from llm.services.infrastructure.text_stream import text_blocks
from llm.core.editable_snapshot import EditableSnapshot
from llm.components.file_manifest import FileManifest
from llm.services.lifecycle.components import ComponentData
from llm.services.lifecycle.sessions import SessionRepository
from .process_values import (ProcessOutput, ProcessSnapshot, StoredOutput, OutputReference,
                             OutputDestination, ProcessOutputArguments, ProcessText)


class ProcessOrigin(StrEnum):
    RUNTIME = 'runtime'
    RETAINED = 'retained'


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    """Origin is observation provenance, never permission to restart a command."""
    INVALID = 'Invalid process observation'

    source: str
    record: StoredOutput

    def __post_init__(self):
        if self.source not in ProcessOrigin or not isinstance(self.record, StoredOutput):
            raise ValueError(self.INVALID)

    def to_dict(self):
        value = asdict(self)
        value[type(self).record.__name__] = self.record.to_dict()
        return value


class BuiltinOutputData(ComponentData):
    MISSING_DESTINATION = 'Process observation requires an explicitly configured output destination'
    PUBLICATION_REQUIRED = 'Process output records are published by the execution owner'
    IMMUTABLE_OUTPUT = 'Process output records cannot be rewritten'
    MISSING_REFERENCES = 'Output deletion requires execution reference checks'
    RESUME_REFERENCES = 'Output is retained while unfinished Run resume references exist'
    DUPLICATE_PUBLICATION = 'Process output already exists; cannot replay publication'
    CHANGED_OUTPUT = 'Stored process output changed'

    FILE_PATTERN = "process-output/{identifier}/{name}.txt"

    @workspace_locked
    def _process_binding(self):
        project, component = self._current()
        output = component.get_output(project)
        if output is None:
            raise ValueError(BuiltinOutputData.MISSING_DESTINATION)
        return component.get_observers(project), ProcessOutputStore(self, output.output.to_dict(), output.output_preview_chars)

    async def alist_processes(self):
        """List Project-owned live observations and retained output after restart."""
        from .capture import output_scope

        observers, store = await self._async_call(self._process_binding)
        records = {identifier: ProcessObservation(ProcessOrigin.RETAINED.value, StoredOutput(**record))
                   for identifier, record in (await self.alist()).items()}
        with output_scope(store):
            for processes in observers:
                records.update({identifier: ProcessObservation(ProcessOrigin.RUNTIME.value, StoredOutput(**record))
                                for identifier, record in (await processes.observe()).items()})
        return records

    async def aread_process(self, identifier, *, stdout_offset, stderr_offset, max_chars):
        """Read character ranges without spawning, writing stdin or reviving a PID."""
        from .capture import output_scope
        from .process_values import ProcessOutputArguments

        from jsonschema import Draft202012Validator

        arguments = ProcessOutputArguments(process_id=identifier, stdout_offset=stdout_offset,
            stderr_offset=stderr_offset, max_chars=max_chars).to_dict()
        Draft202012Validator(ProcessOutputArguments.schema()).validate(arguments)
        observers, store = await self._async_call(self._process_binding)
        with output_scope(store):
            for processes in observers:
                if processes.contains(identifier):
                    return await processes.output(arguments)
            return await store.read(ProcessOutputArguments.read(arguments))

    def create(self, data, *, identifier=ComponentData.UNSPECIFIED):
        raise ValueError(BuiltinOutputData.PUBLICATION_REQUIRED)

    def save(self, identifier, data, *, expected_version=ComponentData.UNSPECIFIED):
        raise ValueError(BuiltinOutputData.IMMUTABLE_OUTPUT)

    def update(self, identifier, changes, *, expected_version=ComponentData.UNSPECIFIED):
        raise ValueError(BuiltinOutputData.IMMUTABLE_OUTPUT)

    @workspace_locked
    def delete(self, identifier, *, expected_version=ComponentData.UNSPECIFIED):
        project, _ = self._current()
        if self._sessions is None or self._sessions.run_repository is None:
            raise RuntimeError(BuiltinOutputData.MISSING_REFERENCES)
        for session in self._sessions.list(project, include_deleted=SessionRepository.ALL_RECORDS):
            self._sessions.require_inactive(session)
            if self._sessions.run_repository.resume_references(session):
                raise ValueError(BuiltinOutputData.RESUME_REFERENCES)
        return super().delete(identifier, expected_version=expected_version)

    @workspace_locked
    def _publish_output(self, identifier, sources, record, *, destination):
        files = self.related(destination.component)
        snapshot = EditableSnapshot.read(files.snapshot(destination.identifier))
        paths = {self.FILE_PATTERN.format(identifier=identifier, name=name): source for name, source in sources.items()}
        if any(path in FileManifest(snapshot.data).entries for path in paths):
            raise ValueError(BuiltinOutputData.DUPLICATE_PUBLICATION)
        snapshot = EditableSnapshot.read(files.import_files(destination.identifier, paths, expected_revision=snapshot.version))
        references = {name: OutputReference(destination.component, destination.identifier, path=path,
            revision=revision_token(FileManifest(snapshot.data).require(path))).to_dict()
            for name, path in zip(sources, paths)}
        record = StoredOutput(**record).with_sources(references).to_dict()
        project, component = self._current()
        component.create(project, record, identifier=identifier)
        return references

    @workspace_locked
    def read_output(self, identifier, name, *, offset, limit):
        # ComponentData.load has already validated this retained record.
        record = StoredOutput(**self.load(identifier))
        reference = record.reference(name)
        files = self.related(reference.component)
        snapshot = EditableSnapshot.read(files.snapshot(reference.identifier))
        if revision_token(FileManifest(snapshot.data).require(reference.path)) != reference.revision:
            raise ValueError(BuiltinOutputData.CHANGED_OUTPUT)
        return files.read_text(reference.identifier, reference.path,
                               expected_revision=snapshot.version, offset=offset, limit=limit)

    aread_output = async_method(read_output)
    adelete = async_method(delete)
    acreate = async_method(create)
    asave = async_method(save)
    aupdate = async_method(update)


class ProcessOutputStore:
    INVALID_OFFSET = 'Output offset is outside the retained stream'
    WRITE_MODE = 'wb'

    def __init__(self, data, destination, preview_chars):
        self.data, self.destination, self.preview_chars = data, OutputDestination(**deepcopy(destination)), preview_chars
        self.owner = (str(data.ownership.path), data.project.id, data.name,
                      self.destination.component, self.destination.identifier)

    async def publish(self, identifier, item):
        import asyncio
        record = await asyncio.to_thread(item.snapshot, identifier)
        return await self.publish_capture(identifier, record, item.stdout, item.stderr)

    async def publish_capture(self, identifier, record, stdout, stderr):
        # Completed capture bytes are normalized incrementally to UTF-8 text.
        # No workspace lock is held while a command runs or its pipes are drained.
        import asyncio
        directory = tempfile.TemporaryDirectory()
        def stage():
            sources = {}
            for name, capture in zip(ProcessSnapshot.stream_names(), (stdout, stderr)):
                path = Path(directory.name) / name
                with capture.lock, path.open(self.WRITE_MODE) as output:
                    capture.stream.seek(0)
                    for block in text_blocks(capture.stream, errors=ProcessText.ERRORS):
                        output.write(block.encode(ProcessText.ENCODING))
                sources[name] = path
            return sources
        try:
            sources = await drain_on_cancel(asyncio.to_thread(stage))
            return await drain_on_cancel(self.data._async_call(self.data._publish_output,
                identifier, sources, record, destination=self.destination))
        finally:
            directory.cleanup()

    async def command_result(self, argv, cwd, code, stdout, stderr, *, started_at):
        from uuid import uuid4
        from datetime import datetime, timezone
        import asyncio
        identifier = uuid4().hex
        timestamp = datetime.now(timezone.utc).isoformat()
        from .process_values import ProcessStatus
        snapshot = ProcessSnapshot(identifier, ProcessStatus.COMPLETED.value, code, ProcessSnapshot.NO_PROCESS_ID,
            list(argv), str(cwd), started_at, timestamp, ProcessSnapshot.STDIN_CLOSED,
            await asyncio.to_thread(stdout.read, ProcessOutputArguments.MIN_OFFSET, self.preview_chars),
            await asyncio.to_thread(stderr.read, ProcessOutputArguments.MIN_OFFSET, self.preview_chars),
            ProcessSnapshot.COMPLETE_OUTPUT)
        record = StoredOutput.from_snapshot(snapshot, sources=StoredOutput.UNPUBLISHED, stdout_chars=stdout.characters,
                                           stderr_chars=stderr.characters, preview_chars=self.preview_chars)
        sources = await self.publish_capture(identifier, record.to_dict(), stdout, stderr)
        return record.with_sources(sources).to_dict()

    async def load(self, identifier):
        return await self.data.aload(identifier)

    async def read(self, args):
        record = StoredOutput(**await self.load(args.process_id))
        limit = None if args.max_chars is UNSET else args.max_chars
        values = {}
        for name, offset in zip(ProcessSnapshot.stream_names(), (args.stdout_offset, args.stderr_offset)):
            total = record.total(name)
            if not ProcessOutputArguments.MIN_OFFSET <= offset <= total:
                raise ValueError(ProcessOutputStore.INVALID_OFFSET)
            text = await self.data.aread_output(args.process_id, name, offset=offset, limit=limit)
            values[name] = (text, offset + len(text), total - offset - len(text))
        return ProcessOutput(args.process_id, record.status, record.returncode, record.truncated,
            *values[ProcessSnapshot.stdout.__name__], *values[ProcessSnapshot.stderr.__name__]).to_dict()
