"""호스트 BuiltinTools를 명시적으로 선택한 Project에 연결한다. Python Tool 저장소와 독립적이다."""

from dataclasses import replace
from enum import StrEnum

from llm.components.base import Component, validate_name
from llm.core.parameters import ParameterLayout
from llm.components.tools import Tool, ToolRegistry
from llm.components.tools.selection import ToolSelection
from .path_access import PathAccess, path_scope
from .output import BuiltinOutputData, OutputDestination, ProcessOutputStore
from .capture import output_scope
from llm.core.schema import object_schema
from llm.services.infrastructure.storage import revision_token
from jsonschema import Draft202012Validator


class BuiltinParameter(StrEnum):
    OUTPUT = "output"
    READ_ACCESS = "read_access"
    WRITE_ACCESS = "write_access"
    CWD_ACCESS = "cwd_access"
    OUTPUT_PREVIEW_CHARS = "output_preview_chars"


class BuiltinToolComponent(Component):
    """Toolkit 수명은 호스트의 async with가 소유하고, 선택 목록은 ProjectConfig가 소유한다."""

    capabilities = (ToolRegistry.CAPABILITY,)
    data_class = BuiltinOutputData
    access_fields = (BuiltinParameter.READ_ACCESS, BuiltinParameter.WRITE_ACCESS, BuiltinParameter.CWD_ACCESS)
    parameter_layout = ParameterLayout(config=(ToolSelection.enabled.__name__, BuiltinParameter.OUTPUT),
                                      policy=(*access_fields, BuiltinParameter.OUTPUT_PREVIEW_CHARS))

    def __init__(self, toolkit, *, name: str):
        self.name = self.directory = validate_name(name)
        self.toolkit = toolkit

    def describe_config(self):
        schema = ToolSelection.schema(choices=self.toolkit.registry.names())
        for name in self.access_fields:
            schema["properties"][name] = PathAccess.schema()
        schema["properties"]["output"] = OutputDestination.schema()
        schema["properties"]["output_preview_chars"] = {"type": "integer", "minimum": 0}
        return self.parameter_layout.schema(schema)

    def validate_config(self, value):
        super().validate_config(value)
        options = self.parameter_layout.unpack(value)
        for name in self.access_fields:
            if name in options:
                PathAccess.read(options[name])
        if ("output" in options) != ("output_preview_chars" in options):
            raise ValueError("Stored output requires explicit output and output_preview_chars")

    def references(self, project):
        refs = []
        output = self._options(project).get("output")
        if output is not None:
            refs.append({**output, "source": f"{self.name}/config.output"})
        if not self._has_storage(project):
            return refs
        for identifier, record in self.list(project).items():
            for reference in record["output_sources"].values():
                refs.append({**reference, "source": f"{self.name}/{identifier}"})
        return refs

    def validate_record(self, identifier, value):
        # The source record is a retained observation, never an execution request.
        keys = ("process_id", "status", "returncode", "pid", "argv", "cwd", "started_at",
                "ended_at", "stdin_open", "stdout", "stderr", "truncated", "output_sources",
                "stdout_chars", "stderr_chars", "preview_chars")
        reference = {**OutputDestination.schema(), "required": ["component", "identifier", "path", "revision"]}
        reference["properties"].update(path={"type": "string", "minLength": 1},
                                       revision={"type": "string", "pattern": "^[a-f0-9]{64}$"})
        properties = {key: {"type": "string"} for key in keys}
        for key in ("returncode", "pid"):
            properties[key] = {"type": ["integer", "null"]}
        for key in ("stdout_chars", "stderr_chars", "preview_chars"):
            properties[key] = {"type": "integer", "minimum": 0}
        for key in ("stdin_open", "truncated"):
            properties[key] = {"type": "boolean"}
        properties["argv"] = {"type": "array", "items": {"type": "string"}}
        properties["ended_at"] = {"type": ["string", "null"]}
        properties["output_sources"] = object_schema({"stdout": reference, "stderr": reference}, required=["stdout", "stderr"])
        Draft202012Validator(object_schema(properties, required=list(keys))).validate(value)
        if value["process_id"] != identifier:
            raise ValueError("Output record identity mismatch")

    def resolve_runtime(self, project, capability, *, data_factory):
        options = self._options(project)
        if capability != ToolRegistry.CAPABILITY or "output" not in options:
            return self.resolve(project, capability)
        store = None
        if capability == ToolRegistry.CAPABILITY and "output" in options:
            destination = options["output"]
            files = data_factory(destination["component"])
            files.snapshot(destination["identifier"])
            if not callable(getattr(files, "import_files", None)) or not callable(getattr(files, "read_text", None)):
                raise ValueError("Output destination requires file import and text range APIs")
            store = ProcessOutputStore(data_factory(self.name), destination, options["output_preview_chars"])
        return self._resolve(project, capability, store)

    def resolve(self, project, capability):
        if capability == ToolRegistry.CAPABILITY and "output" in self._options(project):
            raise ValueError("Stored output requires lifecycle-bound Component handles")
        return self._resolve(project, capability, None)

    def _resolve(self, project, capability, store):
        if capability != ToolRegistry.CAPABILITY:
            return super().resolve(project, capability)
        if self.toolkit.closed:
            raise RuntimeError(self.toolkit.CLOSED)
        options = self._options(project)
        access = {name: options.pop(name) for name in self.access_fields if name in options}
        output = options.pop("output", None)
        preview = options.pop("output_preview_chars", None)
        selected = self.toolkit.registry.select(tuple(ToolSelection.read(options, scope=self.name).names))
        def bound_contract(tool):
            contract = tool.contract
            # Tool 고유 불변식과 호스트 작업 환경을 모두 기존 revision에 묶는다.
            # 명시 contract가 있어도 작업 루트/명령 변경으로 옛 승인을 재사용할 수 없다.
            revision = self.toolkit.binding_revision(contract, **access)
            if output is not None:
                revision = revision_token({"contract": revision, "output": output, "output_preview_chars": preview})
            return replace(contract, revision=revision)
        def handler(tool):
            selected = tool.handler
            if store is None and not access:
                return selected
            from .workspaces import TestWorkspaces
            if isinstance(selected, TestWorkspaces):
                return selected.with_output(store)
            async def invoke(arguments):
                with output_scope(store), path_scope(access):
                    return await selected(arguments)
            return invoke
        return selected.derive(tuple(Tool(tool.name, tool.description, tool.parameters,
                                       handler(tool),
                                       definition=tool.definition, contract=bound_contract(tool),
                                       classification=tool.classification)
                                  for tool in (selected.get(name) for name in selected.names())))
