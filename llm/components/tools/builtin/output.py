"""Builtin-owned completed output references; file bytes remain with the selected Component."""

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import tempfile

from llm.core.configuration import UNSET
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import revision_token, async_method, drain_on_cancel
from llm.services.infrastructure.text_stream import text_blocks
from llm.core.editable_snapshot import EditableSnapshot
from llm.components.file_manifest import FileManifest
from llm.services.lifecycle.components import ComponentData
from .process_values import ProcessOutput, ProcessSnapshot, StoredOutput, OutputReference


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    """Origin is observation provenance, never permission to restart a command."""
    source: str
    record: StoredOutput

    def __post_init__(self):
        if self.source not in {'runtime', 'retained'} or not isinstance(self.record, StoredOutput):
            raise ValueError('Invalid process observation')

    def to_dict(self):
        return {'source': self.source, 'record': self.record.to_dict()}


class BuiltinOutputData(ComponentData):
    @workspace_locked
    def _process_binding(self):
        project, component = self._current()
        options = component._options(project)
        if 'output' not in options:
            raise ValueError('Process observation requires an explicitly configured output destination')
        return component.get_observers(project), ProcessOutputStore(self, options['output'], options['output_preview_chars'])

    async def alist_processes(self):
        """List Project-owned live observations and retained output after restart."""
        from .capture import output_scope

        observers, store = await self._async_call(self._process_binding)
        records = {identifier: ProcessObservation('retained', StoredOutput(**record))
                   for identifier, record in (await self.alist()).items()}
        with output_scope(store):
            for processes in observers:
                records.update({identifier: ProcessObservation('runtime', StoredOutput(**record))
                                for identifier, record in (await processes.observe()).items()})
        return records

    async def aread_process(self, identifier, *, stdout_offset, stderr_offset, max_chars):
        """Read character ranges without spawning, writing stdin or reviving a PID."""
        from .capture import output_scope
        from .process_values import ProcessOutputArguments

        from jsonschema import Draft202012Validator

        arguments = {'process_id': identifier, 'stdout_offset': stdout_offset,
                     'stderr_offset': stderr_offset, 'max_chars': max_chars}
        Draft202012Validator(ProcessOutputArguments.schema()).validate(arguments)
        observers, store = await self._async_call(self._process_binding)
        with output_scope(store):
            for processes in observers:
                if processes.contains(identifier):
                    return await processes.output(arguments)
            return await store.read(ProcessOutputArguments.read(arguments))

    def create(self, data, *, identifier=ComponentData.UNSPECIFIED):
        raise ValueError("Process output records are published by the execution owner")

    def save(self, identifier, data, *, expected_version=ComponentData.UNSPECIFIED):
        raise ValueError("Process output records cannot be rewritten")

    def update(self, identifier, changes, *, expected_version=ComponentData.UNSPECIFIED):
        raise ValueError("Process output records cannot be rewritten")

    @workspace_locked
    def delete(self, identifier, *, expected_version=ComponentData.UNSPECIFIED):
        project, _ = self._current()
        if self._sessions is None or self._sessions.run_repository is None:
            raise RuntimeError("Output deletion requires execution reference checks")
        for session in self._sessions.list(project, include_deleted=True):
            self._sessions.require_inactive(session)
            if self._sessions.run_repository.resume_references(session):
                raise ValueError("Output is retained while unfinished Run resume references exist")
        return super().delete(identifier, expected_version=expected_version)

    @workspace_locked
    def _publish_output(self, identifier, sources, record, *, destination):
        files = self.related(destination["component"])
        snapshot = EditableSnapshot.read(files.snapshot(destination["identifier"]))
        paths = {f"process-output/{identifier}/{name}.txt": source for name, source in sources.items()}
        if any(path in FileManifest(snapshot.data).entries for path in paths):
            raise ValueError("Process output already exists; cannot replay publication")
        snapshot = EditableSnapshot.read(files.import_files(destination["identifier"], paths, expected_revision=snapshot.version))
        references = {name: OutputReference(**destination, path=path,
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
            raise ValueError("Stored process output changed")
        return files.read_text(reference.identifier, reference.path,
                               expected_revision=snapshot.version, offset=offset, limit=limit)

    aread_output = async_method(read_output)
    adelete = async_method(delete)
    acreate = async_method(create)
    asave = async_method(save)
    aupdate = async_method(update)


class ProcessOutputStore:
    def __init__(self, data, destination, preview_chars):
        self.data, self.destination, self.preview_chars = data, deepcopy(destination), preview_chars
        self.owner = (str(data.ownership.path), data.project.id, data.name,
                      destination["component"], destination["identifier"])

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
            for name, capture in (("stdout", stdout), ("stderr", stderr)):
                path = Path(directory.name) / name
                with capture.lock, path.open("wb") as output:
                    capture.stream.seek(0)
                    for block in text_blocks(capture.stream, errors="replace"):
                        output.write(block.encode("utf-8"))
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
        snapshot = ProcessSnapshot(identifier, ProcessStatus.COMPLETED.value, code, None,
            list(argv), str(cwd), started_at, timestamp, False,
            await asyncio.to_thread(stdout.read, 0, self.preview_chars),
            await asyncio.to_thread(stderr.read, 0, self.preview_chars), False)
        record = StoredOutput.from_snapshot(snapshot, sources=None, stdout_chars=stdout.characters,
                                           stderr_chars=stderr.characters, preview_chars=self.preview_chars)
        sources = await self.publish_capture(identifier, record.to_dict(), stdout, stderr)
        return record.with_sources(sources).to_dict()

    async def load(self, identifier):
        return await self.data.aload(identifier)

    async def read(self, args):
        record = StoredOutput(**await self.load(args.process_id))
        limit = None if args.max_chars is UNSET else args.max_chars
        values = {}
        for name, offset in (("stdout", args.stdout_offset), ("stderr", args.stderr_offset)):
            total = record.total(name)
            if not 0 <= offset <= total:
                raise ValueError("Output offset is outside the retained stream")
            text = await self.data.aread_output(args.process_id, name, offset=offset, limit=limit)
            values[name] = (text, offset + len(text), total - offset - len(text))
        return ProcessOutput(args.process_id, record.status, record.returncode, record.truncated,
            *values["stdout"], *values["stderr"]).to_dict()
