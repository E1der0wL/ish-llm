"""Builtin registration definitions, Host adapter schemas and approval projections."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum

from llm.core.schema import SchemaKeyword, open_schema, object_schema, OpenSchemaKind
from llm.services.infrastructure.storage import revision_token
from .arguments import BuiltinArguments
from .process_values import ProcessInvocation
from .workspace_values import WorkspaceBinding


@dataclass(frozen=True, slots=True, kw_only=True)
class GitStatusArguments(BuiltinArguments):
    NAME = "git_status"
    DESCRIPTION = "Read Git working tree status."


@dataclass(frozen=True, slots=True, kw_only=True)
class GitDiffArguments(BuiltinArguments):
    NAME = "git_diff"
    DESCRIPTION = "Read a Git diff without external diff/textconv programs."

    staged: bool = field(default=False, metadata={BuiltinArguments.SCHEMA: {"type": "boolean"}})


class GitCommands:
    PREFIX = ("git", "--no-optional-locks", "-c", "core.fsmonitor=false", "--no-pager")
    STATUS = ("status", "--porcelain=v1")
    DIFF = ("diff", "--no-ext-diff", "--no-textconv")
    CACHED = ("--cached",)

    @classmethod
    def status(cls):
        return ProcessInvocation(argv=[*cls.PREFIX, *cls.STATUS])

    @classmethod
    def diff(cls, arguments):
        return ProcessInvocation(argv=[*cls.PREFIX, *cls.DIFF, *(cls.CACHED if arguments.staged else ())])


@dataclass(frozen=True, slots=True, kw_only=True)
class PromptReadArguments(BuiltinArguments):
    NAME = "prompt_read"
    DESCRIPTION = "Read a saved Agent prompt and revision."

    agent_id: str = field(metadata={BuiltinArguments.SCHEMA: {"type": "string"}})


@dataclass(frozen=True, slots=True)
class ConfiguredChecks:
    INVALID = "Checks must map names to nonempty argv sequences"

    commands: dict

    @classmethod
    def read(cls, values):
        commands = dict(values or {})
        for name, argv in commands.items():
            if (not isinstance(name, str) or not name or isinstance(argv, str) or not argv
                    or any(not isinstance(item, str) or not item for item in argv)):
                raise ValueError(cls.INVALID)
            commands[name] = list(argv)
        return cls(commands)


@dataclass(slots=True)
class BuiltinExecutionBinding:
    root: str
    shell: list[str] | None
    checks: dict
    test_workspaces: WorkspaceBinding | None = None

    def to_dict(self):
        return {item.name: value.to_dict() if isinstance(value, WorkspaceBinding) else deepcopy(value)
                for item in fields(self)
                if (value := getattr(self, item.name)) is not None or item.name != type(self).test_workspaces.__name__}

    def revision(self, contract):
        return BoundBuiltinContract(self.to_dict(), contract.to_dict()).revision()


@dataclass(frozen=True, slots=True)
class BoundBuiltinContract:
    environment: dict
    contract: dict

    def revision(self):
        return revision_token(asdict(self))


class HostAdapterName(StrEnum):
    WEB_SEARCH = "web_search"
    WEB_FETCH = "web_fetch"
    BROWSER_OPEN = "browser_open"
    BROWSER_SNAPSHOT = "browser_snapshot"
    BROWSER_ACT = "browser_act"
    KERNEL_EXECUTE = "kernel_execute"
    KERNEL_RESET = "kernel_reset"
    EXTERNAL_RAG_SEARCH = "external_rag_search"
    EXTERNAL_GRAPHRAG_SEARCH = "external_graphrag_search"
    CONTEXT_EXPAND = "context_expand"


class HostAdapterSchemas:
    """Opaque Host-owned request schemas; the backend never interprets their payloads."""

    WEB_SEARCH = object_schema({"query": {"type": "string", "minLength": 1},
        "limit": {"type": "integer", "minimum": 1}}, required=["query"])
    URL = object_schema({"url": {"type": "string", "pattern": "^https?://"}}, required=["url"])
    SESSION = object_schema({"session_id": {"type": "string"}}, required=["session_id"])
    BROWSER_ACT = object_schema({"session_id": {"type": "string"},
        "action": open_schema("host browser adapter", category=OpenSchemaKind.ADAPTER)}, required=["session_id", "action"])
    KERNEL_EXECUTE = object_schema({"session_id": {"type": "string"}, "code": {"type": "string"}}, required=["session_id", "code"])
    RAG = object_schema({"corpus": {"type": "string"}, "query": {"type": "string", "minLength": 1},
        "options": open_schema("host RAG adapter", category=OpenSchemaKind.ADAPTER)}, required=["corpus", "query"])
    GRAPH_RAG = object_schema({"corpus": {"type": "string"}, "query": {"type": "string", "minLength": 1},
        "options": open_schema("host GraphRAG adapter", category=OpenSchemaKind.ADAPTER)}, required=["corpus", "query"])
    CONTEXT = object_schema({"reference": {"type": "string"},
        "scope": {"enum": ["chunk", "section", "topic", "document"]}}, required=["reference", "scope"])


@dataclass(frozen=True, slots=True)
class HostAdapterSpec:
    name: str
    parameters: dict
    execution: bool = False


class HostAdapters:
    INVALID = "Unknown or non-callable builtin adapter"
    RETIRED_LOCAL_NAMES = frozenset(("rag_search", "graphrag_search"))
    RENAMED = ("BuiltinTools adapter {name!r} was renamed to 'external_{name}'. "
               "Update the adapter key and Project enabled tool names. "
               "Local RAG components provide the unprefixed search tools.")
    EXTERNAL_RAG = frozenset((HostAdapterName.EXTERNAL_RAG_SEARCH, HostAdapterName.EXTERNAL_GRAPHRAG_SEARCH))
    RAG_DESCRIPTION = ("Search an external corpus through the host adapter {name}. "
        "The host interprets corpus/query/options; this does not search the current Project's local RAG.")
    DESCRIPTION = "Invoke the configured host adapter: {name}."
    SPECS = (
        HostAdapterSpec(HostAdapterName.WEB_SEARCH, HostAdapterSchemas.WEB_SEARCH),
        HostAdapterSpec(HostAdapterName.WEB_FETCH, HostAdapterSchemas.URL),
        HostAdapterSpec(HostAdapterName.BROWSER_OPEN, HostAdapterSchemas.URL, execution=True),
        HostAdapterSpec(HostAdapterName.BROWSER_SNAPSHOT, HostAdapterSchemas.SESSION),
        HostAdapterSpec(HostAdapterName.BROWSER_ACT, HostAdapterSchemas.BROWSER_ACT, execution=True),
        HostAdapterSpec(HostAdapterName.KERNEL_EXECUTE, HostAdapterSchemas.KERNEL_EXECUTE, execution=True),
        HostAdapterSpec(HostAdapterName.KERNEL_RESET, HostAdapterSchemas.SESSION, execution=True),
        HostAdapterSpec(HostAdapterName.EXTERNAL_RAG_SEARCH, HostAdapterSchemas.RAG),
        HostAdapterSpec(HostAdapterName.EXTERNAL_GRAPHRAG_SEARCH, HostAdapterSchemas.GRAPH_RAG),
        HostAdapterSpec(HostAdapterName.CONTEXT_EXPAND, HostAdapterSchemas.CONTEXT),
    )

    @classmethod
    def selected(cls, name, handler):
        if name in cls.RETIRED_LOCAL_NAMES:
            raise ValueError(cls.RENAMED.format(name=name))
        value = next((spec for spec in cls.SPECS if spec.name == name), None)
        if value is None or not callable(handler):
            raise ValueError(cls.INVALID)
        return deepcopy(value)

    @classmethod
    def description(cls, name):
        return (cls.RAG_DESCRIPTION if name in cls.EXTERNAL_RAG else cls.DESCRIPTION).format(name=name)
