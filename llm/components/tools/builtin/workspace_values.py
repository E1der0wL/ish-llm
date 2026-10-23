"""Test-space inputs, Host runtime settings and execution projections."""

from copy import deepcopy
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from llm.core.configuration import UNSET
from llm.core.schema import SchemaKeyword
from llm.core.tool_contracts import ToolIsolation
from llm.services.infrastructure.process_environment import ProcessEnvironment
from llm.services.infrastructure.process_result import ProcessResult
from llm.services.infrastructure.process_command import ProcessCommand
from llm.services.runtime.processes import ProcessToolRunner
from .arguments import BuiltinArguments
from .workspace_records import WorkspaceIdentity


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkspaceSelection(BuiltinArguments):
    FILE_DESCRIPTION = "Operate on this Project's test workspace instead of the source tree."

    workspace_id: object = field(default=UNSET, metadata={BuiltinArguments.SCHEMA:
        {"type": "string", "pattern": WorkspaceIdentity.SCHEMA_PATTERN}})

    @property
    def selected(self):
        return self.workspace_id is not UNSET

    @classmethod
    def add_to_file_schema(cls, schema):
        value = deepcopy(schema)
        selected = cls.schema()[SchemaKeyword.PROPERTIES][cls.workspace_id.__name__]
        value[SchemaKeyword.PROPERTIES][cls.workspace_id.__name__] = {
            **selected, SchemaKeyword.DESCRIPTION: cls.FILE_DESCRIPTION}
        return value


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkspaceReference(WorkspaceSelection):
    workspace_id: str = field(metadata={BuiltinArguments.SCHEMA:
        WorkspaceSelection.schema()[SchemaKeyword.PROPERTIES][WorkspaceSelection.workspace_id.__name__]})


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkspaceCreateArguments(BuiltinArguments):
    NAME = "test_workspace_create"
    DESCRIPTION = "Create an empty test workspace with source_paths=[], or independently copy selected relative files/directories. Never changes source files."

    source_paths: list[str] = field(metadata={BuiltinArguments.SCHEMA:
        {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True}})


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkspaceListArguments(BuiltinArguments):
    NAME = "test_workspace_list"
    DESCRIPTION = "List this Project's retained test workspaces, including incomplete creations. Does not replay commands."


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkspaceDeleteArguments(WorkspaceReference):
    NAME = "test_workspace_delete"
    DESCRIPTION = "Permanently delete a test workspace and its results. Rejects a busy workspace; source files are untouched."


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkspaceExecuteArguments(WorkspaceReference):
    NAME = "test_workspace_execute"
    DESCRIPTION = "Execute argv in a Linux OS sandbox inside a retained test workspace. Requires an absolute executable. Returns stdout, stderr and returncode; use file tools with workspace_id to inspect results. No host fallback."

    argv: list[str] = field(metadata={BuiltinArguments.SCHEMA:
        {"type": "array", "minItems": 1, "items": {"type": "string"}}})
    env: object = field(default=UNSET, metadata={BuiltinArguments.SCHEMA: ProcessEnvironment.schema()})

    @classmethod
    def valid_command(cls, arguments):
        return ProcessCommand.valid(arguments.get(cls.argv.__name__))


@dataclass(slots=True, kw_only=True)
class WorkspaceSettings:
    """Host-selected fields shared by runtime settings and approval projection."""

    UNCONFIGURED = None
    NO_MOUNTS = ()

    allow_network: bool
    read_only_paths: tuple[Path, ...] = NO_MOUNTS
    env: object = UNCONFIGURED
    max_output_bytes: int | None = UNCONFIGURED
    memory_bytes: int | None = UNCONFIGURED
    python_executable: object = UNCONFIGURED
    isolation: str = ToolIsolation.SANDBOX

    def arguments(self):
        return {item.name: deepcopy(getattr(self, item.name)) for item in fields(self)}


@dataclass(slots=True, kw_only=True)
class WorkspaceRuntime(WorkspaceSettings):
    """Validation and process construction stay with the runner."""

    def runner(self, cwd):
        return ProcessToolRunner({}, cwd=cwd, **self.arguments())

    def with_environment(self, value):
        return self if value is UNSET else replace(self, env=ProcessEnvironment.copy(value))

    def binding(self, root, source_root):
        return WorkspaceBinding(root=str(root), source_root=str(source_root), **self.arguments())


@dataclass(slots=True, kw_only=True)
class WorkspaceBinding(WorkspaceSettings):
    root: str
    source_root: str

    def to_dict(self):
        value = self.arguments()
        value[type(self).read_only_paths.__name__] = [str(path) for path in self.read_only_paths]
        return {name: str(item) if isinstance(item, Path) else item for name, item in value.items()}


@dataclass(frozen=True, slots=True)
class WorkspaceExecutionResult(ProcessResult):
    workspace_id: str

    @classmethod
    def from_result(cls, workspace_id, result):
        value = ProcessResult.from_dict(result)
        return cls(value.returncode, value.stdout, value.stderr, workspace_id)
