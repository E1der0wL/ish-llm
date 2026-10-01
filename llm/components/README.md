> 설정은 [명시적 설정 계약](../CONFIGURATION.md)을 따른다. 미설정 정책을 생성하지 않으며, SDK 옵션은 생략한다.

# Project components

Components own Project-root directories and persistent definitions. They do not
execute Runs. `Component` in `base.py` provides directory initialization/removal,
open JSON serialization/deserialization, configuration, definition CRUD and clone.
`ComponentRegistry` registers identities/directories and collects generic optional
capability resolution; neither module imports Tools or requires `resolve_tools`.

| Package | Persisted data | Runtime responsibility |
| --- | --- | --- |
| tools | Python source packages and optional requirements; enabled names in ProjectConfig | Load decorated async main into the existing ToolRegistry/ToolExecutor |
| agents | Purpose, completion model/options and optional prompt/resource references | Purpose-specific model calls belong to Engines |
| skills | Instructions and optional resource descriptions | The consumer decides how to apply instructions |
| mcp | stdio / HTTP server connection definitions | Client connections and tool discovery need an execution adapter |
| workflows | Versioned graphs with branch, parallel/join and bounded loop nodes | Structural validation is provided; GraphEngine executes registered async node handlers |
| rag | Documents, sections, chunks, vectors, evidence relations and Chroma/Kuzu generations | Splitting, embedding/rerank/triple extraction, combined document/graph retrieval |
| memory | Project/Session memories, provenance, revisions and derived Session summaries | Automatic recall, bounded context, Tool previews, optional summary/extraction and CRUD Tools |

한국어 API와 그래프 조립 예시는 [컴포넌트 정의 안내](../../docs/component-definitions.md)를 참고한다.
기본 LargeLanguageModel은 tools/skills/mcp/rag/agents/workflows/memory/prompts를 등록하며
Project에서 선택한 종류만 생성한다. 재사용 업무 정의는 agents 하나로 관리한다.
프롬프트·few-shot CRUD와 RAG 연결은 [Prompt 안내](prompts/README.md)를 참고한다.
장기 기억의 공개 API, 후보 승인 정책, 모델 Tool과 저장 형식은 [Memory 안내](../../docs/memory.md)를 참고한다.
범용 확장은 [공통 처리기 계약](../../docs/completion-processing.md), 장기 대화 최적화는 [Memory 처리 정책](../../docs/memory-processing.md)을 참고한다.

## Declare a component

```python
from llm.components import Component, ComponentRegistry

class NotesComponent(Component):
    name = "notes"
    directory = "knowledge"  # Explicit direct child of the Project root.

    def configuration_schema(self):
        return {"type": "object", "properties": {"language": {"type": "string"}}}

components = ComponentRegistry((NotesComponent(),))
```

Registration rejects missing/unsafe directory declarations,
core-owned sessions/logs/state/cache directories and duplicate directory ownership.
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
Permanent removal requires all Project Sessions to be inactive/detached. It publishes
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

Project Tool은 `tools/<name>/<name>.py`와 선택적인 `requirements.txt`로 저장한다.
API 표현은 `{"source": "...", "requirements": "..."}`이며 `identifier`를 명시한다.
조회·편집·clone·backup은 Python을 import하지 않는다.

```python
tools = project.components.tools
await tools.acreate({"source": source_text, "requirements": "httpx>=0.28,<1\n"},
                    identifier="web_search")
await tools.aprepare("web_search")  # ish 공용 plugin/lib 준비 + 함수 계약 검증
await tools.aenable("web_search")
await tools.adisable("web_search")
await tools.adelete("web_search")   # enabled 상태이면 거부
```

`@tool`의 ToolContract, `async main`의 docstring/signature/type hints에서 실행 정의를 파생한다.
이름은 패키지 디렉토리에서 얻는다. 별도 JSON 정의나 애플리케이션 handler 등록이 필요 없다.
`enable`/`set_enabled`는 패키지의 정적 존재를 확인한다. ProjectConfig의 `enabled` 저장은
이름 배열과 중복만 검증하므로 clone 시 파일 복사 전에 설정을 저장할 수 있다.
변경은 다음 Run의 로딩에 반영되며 활성 Run의 Tool snapshot은 유지한다.
공용 의존성은 현재 ish `--home`의 `config.PLUGIN_LIB_DIR`만 사용한다.
상세 계약과 예제는 [Python Tool packages](tools/README.md)를 참고한다.

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
