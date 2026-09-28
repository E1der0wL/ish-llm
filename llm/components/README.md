# Project components

Components own Project-root directories and persistent definitions. They do not
execute Runs. `Component` in `base.py` provides directory initialization/removal,
open JSON serialization/deserialization, configuration, definition CRUD and clone.
`ComponentRegistry` registers identities/directories and collects generic optional
capability resolution; neither module imports Tools or requires `resolve_tools`.

| Package | Persisted data | Runtime responsibility |
| --- | --- | --- |
| tools | Enabled names and optional native function-tool definitions | Bind definitions to explicitly registered Python handlers |
| agents | Purpose, completion model/options and optional prompt/resource references | Purpose-specific model calls belong to Engines |
| skills | Instructions and optional resource descriptions | The consumer decides how to apply instructions |
| mcp | stdio / HTTP server connection definitions | Client connections and tool discovery need an execution adapter |
| workflows | Versioned graphs with branch, parallel/join and bounded loop nodes | Structural validation is provided; GraphEngine executes registered async node handlers |
| rag | Documents, sections, chunks, vectors, evidence relations and Chroma/Kuzu generations | Splitting, embedding/rerank/triple extraction, combined document/graph retrieval |
| memory | Project/Task memories, provenance, revisions and derived Task summaries | Automatic recall, bounded context, Tool previews, optional summary/extraction and CRUD Tools |

한국어 API와 그래프 조립 예시는 [컴포넌트 정의 안내](../../docs/component-definitions.md)를 참고한다.
기본 LargeLanguageModel은 tools/skills/mcp/rag/agents/workflows/memory를 등록하며
Project에서 선택한 종류만 생성한다. 재사용 업무 정의는 agents 하나로 관리한다.
장기 기억의 공개 API, 후보 승인 정책, 모델 Tool과 저장 형식은 [Memory 안내](../../docs/memory.md)를 참고한다.
범용 확장은 [공통 처리기 계약](../../docs/completion-processing.md), 장기 대화 최적화는 [Memory 처리 정책](../../docs/memory-processing.md)을 참고한다.

## Declare a component

```python
from llm.components import Component, ComponentRegistry

class NotesComponent(Component):
    name = "notes"
    directory = "knowledge"  # Explicit direct child of the Project root.

    def default_configuration(self):
        return {"format_version": 1}

components = ComponentRegistry((NotesComponent(),))
```

Registration rejects missing/unsafe directory declarations,
core-owned tasks/logs/state/cache directories and duplicate directory ownership.
Each component owns everything beneath its declared root. Paths remain out of
ProjectPaths. Base file operations reject symlinks and linked ancestors;
permanent removal checks every descendant. This assumes cooperative exclusive
workspace ownership, not hostile external filesystem mutation. Component code is
trusted Python and can override the contract; it is not sandboxed.

Default persistence:

```text
<project>/
  project.json                      # Component names and ProjectConfig.component_configurations
  knowledge/
    records/
      <id>.json                     # One open definition per file
  agents/
    records/reviewer.json           # Model/settings/prompt chosen by the app
  workflows/
    records/review.json             # Nodes/edges/reference keys chosen by the app
  tools/
    records/add.json                # Optional native function-tool definition
```

Only selected components are initialized. Records are plain dicts, not fixed
schema dataclasses; unknown keys survive load/save and clone. `serialize(dict)`
returns JSON text and `deserialize(text)` returns a detached dict. Values must be
JSON-safe, string-keyed and finite; runtime objects are rejected. Field names are
unrestricted, including names used in schema definitions. ProjectConfig uses the
same JSON compatibility checks without an application key-name blacklist. There is no automatic migration.
Workflow's validated graph format declares schema_version 1; other definitions remain open objects.
`validate_configuration` and `validate_record` are optional semantic hooks.

## Use through ProjectManager

```python
# Configure ProjectManager with the registry once.
project = projects.create("Example", components=("notes",))
notes = projects.component(project, "notes")
identifier = notes.create({"title": "Example", "tags": ["draft"]}, identifier="intro")
notes.update(identifier, {"new_option": True})
value = notes.load(identifier)
all_values = notes.list()                 # {record_id: dict, ...}
notes.save(identifier, {"replacement": True})
notes.delete(identifier)
notes.configure({"arbitrary_setting": {"enabled": True}})
configuration = notes.configuration()

projects.set_components(project, ("notes", "agents", "workflows"))  # Register first.
projects.remove_component(project, "notes")                 # Disable, retain data.
projects.remove_component(project, "notes", permanent=True) # Remove retained tree.
```

`create` rejects duplicate IDs; `save` replaces an existing record; `update` is a
shallow key update (nested values replace); `delete` removes one record. Configure
replaces the configuration dict. To remove a key, load/pop/save. Reads return
independent values. Mutable JSON uses atomic replacement and fsync; updates hold
the workspace lock over the complete read/modify/write operation.

The ComponentData handle reloads authoritative Project state and selection on
every call, so stale handles cannot modify a deleted or disabled Project/component.
Direct Component/ComponentRegistry APIs are lower-level and require the caller's
workspace ownership scope. In an async UI, use a-prefixed handle methods:
`await notes.acreate(...)`, `await notes.alist()`, `await notes.aconfigure(...)`.
When using the backend, first obtain a handle with
`await project.components.aget("notes")` to offload lookup too. All facade handle
operations share its storage lane and shutdown guard. Direct ProjectManager
handles use a per-handle StorageIO lane on their original event loop. Existing
synchronous APIs and explicit `StorageIO(projects.ownership).run` remain usable.
Runtime capabilities are resolved in RunManager's
existing ordered storage lane.

`set_components` initializes missing directories/configuration before publishing
the new selection, preserving previous data on re-enable. Initialization must be
idempotent; overridden initializers should call `super().initialize(project)`.
Failure may retain partial directories, but does not publish the changed selection.
Permanent removal requires all Project Tasks to be inactive/detached. It publishes
disabled selection before recursive deletion; failed/interrupted removal leaves
it disabled and can be retried, including when already disabled. Removal is not
a multi-file transaction. Deleting Project removes its entire component tree.

Project cloning copies configuration; components copy records into the new Project, retaining record
IDs so graph references stay valid. It does not copy unregistered artifacts,
indexes, connections or runtime handlers. Override clone for a component-specific
artifact policy. RAG rebuilds its indexes from saved document/vector/graph data.
Workflow records require schema_version 1. Configuration comes only from ProjectConfig; legacy component.json is rejected.
No old-format reader or implicit conversion is provided.

## Tool specialization and runtime capabilities

ToolComponent selects `ToolData`, a ComponentData subclass, through
`data_class = ToolData`. Both `projects.component(project, "tools")` and the
backend's `project.components.tools` return this handle. All common CRUD and
`configure(dict)` methods remain available.

```python
tools = projects.component(project, "tools")
tools.create({
    "type": "function",
    "function": {
        "name": "add", "description": "Add numbers",
        "parameters": {"type": "object", "properties": {}},
        "strict": True,
    },
})  # Uses the function name as its record ID; the handler is only needed for execution.
tools.enable("add")                  # Add without duplicates.
tools.enable("search", "read_file")
tools.disable("search")              # Already disabled names are harmless.
tools.set_enabled(["add"])           # Replace only the selection.
names = tools.enabled()              # Detached list, in selection order.

# Optional extensions still use the open configuration API (full replacement).
tools.configure({"enabled": ["add"], "custom_policy": {"label": "example"}})
```

Selection methods preserve every other configuration key and hold the workspace
lock across read/modify/write. enable/disable accept any number of nonempty string
names and are idempotent; set_enabled accepts a sequence and rejects duplicates.
An empty replacement disables all tools. Registering a definition does not enable
it, and selecting a name does not register a Python handler. Selection can be
edited before definitions/handlers exist; execution still requires valid binding.
Changes affect subsequent Run snapshots, not an already executing Run.

ToolData also provides `aenable`, `adisable`, `aset_enabled`, `aenabled` for async
callers. Custom handle convenience methods can expose an off-loop variant using
`async_name = async_method(sync_name)` from llm.services.infrastructure.storage, just like the
built-in handles. The synchronous method must still lock and reload lifecycle
state; async_method only delegates it to the owner's guarded storage runner.

Other components may opt into the same extension mechanism:

```python
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked

class NotesData(ComponentData):
    @workspace_locked
    def titles(self):
        project, component = self._current()  # Reload lifecycle and selection.
        return [record.get("title", "") for record in component.list(project).values()]

class NotesComponent(Component):
    name = "notes"
    directory = "knowledge"
    data_class = NotesData
```

Omit data_class (or set it to None) to keep generic ComponentData. Registration
rejects values that are not ComponentData subclasses. Custom handles inherit its
constructor; custom operations must lock and reload state as shown. Domain logic
belongs to the component, while the handle supplies safe Project-scoped access.
There is no automatic forwarding of arbitrary component methods. Attribute access
is dynamic; use `project.components["name"]` for names colliding with handle methods.

Tool definitions preserve provider extension keys. Function identity and argument
schema are validated independently of the application catalog. CRUD, configuration
and clone work with an empty catalog; handlers are required only for execution. Missing
record overrides use the registered catalog definition. Both are current Tool definition sources.
Disable a tool before deleting its override. Saving JSON never imports code or
creates a handler; startup must register handlers again.

ToolComponent declares capabilities = ("tools",) and resolves a fresh ToolRegistry
only when tools is requested. ComponentToolResolver in tools/resolver.py collects
and validates that result.
RunManager still accepts `capabilities=components` and wraps it with this adapter;
custom resolvers must implement resolve(project, names). For direct resolution use
`ComponentToolResolver(components).resolve_tools(project)` instead of the removed
ComponentRegistry.resolve_tools API. Other capabilities use arbitrary keys and
values through `components.resolve(project, "retriever")`. Declare a tuple of
capability names and implement resolve(project, capability); the registry skips
components that do not declare the requested name. Base capabilities is empty.

The EngineContext tool snapshot stays fixed for one Run. Definition/configuration
edits affect later Runs. Engine agent/graph execution and Steps remain inside
the owning Run; components own their data and retrieval operations. ToolExecutor records JSON call arguments
and results in the owning Step; the provider's tool-message transcript remains runtime-only.
Embedding/rerank implementations and public imports live in components/rag.
Triple extraction and graph indexing also live in components/rag. The single
RAGComponent exposes rag_search with documents, relations and versioned sources. See
[RAG 공개 API](../../docs/rag-components.md) for indexed document CRUD and retrieval.
