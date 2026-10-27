"""Builtin-owned completed output references; file bytes remain with the selected Component."""

from copy import deepcopy
from pathlib import Path
import tempfile

from llm.core.configuration import UNSET
from llm.core.schema import object_schema
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import revision_token, async_method, drain_on_cancel
from llm.services.infrastructure.text_stream import text_blocks
from llm.core.editable_snapshot import EditableSnapshot
from llm.components.file_manifest import FileManifest
from llm.services.lifecycle.components import ComponentData
from .process_values import ProcessOutput


class OutputDestination:
    @staticmethod
    def schema():
        return object_schema({"component": {"type": "string", "minLength": 1},
                              "identifier": {"type": "string", "minLength": 1}},
                             required=["component", "identifier"])


class BuiltinOutputData(ComponentData):
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
        references = {name: {**destination, "path": path,
            "revision": revision_token(FileManifest(snapshot.data).require(path))}
            for name, path in zip(sources, paths)}
        record = {**record, "output_sources": references}
        project, component = self._current()
        component.create(project, record, identifier=identifier)
        return references

    @workspace_locked
    def read_output(self, identifier, name, *, offset, limit):
        record = self.load(identifier)
        reference = record["output_sources"][name]
        files = self.related(reference["component"])
        snapshot = EditableSnapshot.read(files.snapshot(reference["identifier"]))
        if revision_token(FileManifest(snapshot.data).require(reference["path"])) != reference["revision"]:
            raise ValueError("Stored process output changed")
        return files.read_text(reference["identifier"], reference["path"],
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
        record = {"process_id": identifier, "status": ProcessStatus.COMPLETED.value, "returncode": code,
            "pid": None, "argv": list(argv), "cwd": str(cwd), "started_at": started_at,
            "ended_at": timestamp, "stdin_open": False, "truncated": False,
            "stdout": await asyncio.to_thread(stdout.read, 0, self.preview_chars),
            "stderr": await asyncio.to_thread(stderr.read, 0, self.preview_chars),
            "stdout_chars": stdout.characters, "stderr_chars": stderr.characters,
            "preview_chars": self.preview_chars}
        record["output_sources"] = await self.publish_capture(identifier, record, stdout, stderr)
        return record

    async def load(self, identifier):
        return await self.data.aload(identifier)

    async def read(self, args):
        record = await self.load(args.process_id)
        limit = None if args.max_chars is UNSET else args.max_chars
        values = {}
        for name, offset in (("stdout", args.stdout_offset), ("stderr", args.stderr_offset)):
            total = record[name + "_chars"]
            if not 0 <= offset <= total:
                raise ValueError("Output offset is outside the retained stream")
            text = await self.data.aread_output(args.process_id, name, offset=offset, limit=limit)
            values[name] = (text, offset + len(text), total - offset - len(text))
        return ProcessOutput(args.process_id, record["status"], record["returncode"], record["truncated"],
            *values["stdout"], *values["stderr"]).to_dict()
