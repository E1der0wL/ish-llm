# llm

## Session 도메인 변경
영속 계층은 **Project → Session → Run → Step**이며 Engine과 Component는 확장 계약입니다.
`project.sessions`, `SessionManager`, `EngineContext.session`, `session_id`를 사용합니다.
설정은 `ProjectConfig.parameters`에서 대상별로 전달합니다. 세션은 프로세스를 종료한 뒤에도
다시 열 수 있는 작업·대화 공간입니다.

이 버전은 `storage_version=1`, `sessions/<id>/session.json` 저장 형식을 사용합니다.
기존 Task 형식의 workspace는 자동 변환하지 않고 거부합니다. 기존 자료는 그대로 보관하고
새 workspace 경로에서 테스트하세요. GitHub의 이전 버전 API·예제를 함께 섞어 사용하지 마세요.

사내 Markdown을 근거로 설정 변경안을 만들고 검증·사용자 승인 후 적용하는 테스트는
[설정 변경 Workflow 예제](../../examples/llm/configuration_workflow.md)를 참고하세요.

Loop 명시적 재개, 조건부 Tool 재시도·승인, 누적 사용량·보관 정책, 편집 충돌 검사와
RAG 영속 색인 작업은 [장시간 실행·복구 API](long-running.md)에 정리했습니다.

## 공통 출력 API

Loop·Graph·Agent·Tool 결과는 `EngineOutput`, 스트리밍 변경은 `EngineDelta`로 전달합니다.
최종 결과는 `(await run.aresult()).output`, Step 결과는 `step.output`, 부분 출력은
`await run.aoutputs()`로 조회합니다. UI 재연결은 `run.aoutput_events(after=cursor)`를 사용합니다.
[엔진 구현 및 UI 연동 안내](engine-output.md)를 참고하세요.

명시적 프로젝트 생성·재사용과 UI용 설정 통합 조회는
[프로젝트 설정 안내](project-settings.md)를 참고하세요.

서비스 확장과 사용 예시는 [한국어 서비스 확장 안내](service-extensions.md)를 참고하세요.
Run/Tool 실행 한도, 승인 어댑터, 문맥 토큰 예산, UI 알림·대기열 제어는
[실행 안정성과 UI 연결](runtime-reliability.md)에 정리했습니다.
OS별 Tool 실행 격리와 영속 작업 키는 [프로세스 격리·중복 방지 안내](process-isolation.md)를 참고하세요.
Skill/MCP/RAG/Agent/Workflow의 데이터 형식과 그래프 조립 방법은
[컴포넌트 정의 안내](component-definitions.md)에 정리했습니다.
코드 작성 → 정적 검사 → 테스트 → 재수정을 실행하는 예제와 노드 API는
[GraphEngine 안내](graph-engine.md)를 참고하세요.
GraphEngine은 LangGraph로 Workflow를 실행하고 AgentNode는 기존 LoopEngine을 통해
LiteLLM을 호출합니다. Workflow JSON과 Run/Step 기록 방식은 유지됩니다.
파일·명령·프로세스·검증·프롬프트 Tool과 외부 서비스 어댑터 연결은
[기본 Tool 안내](builtin-tools.md)에 정리했습니다.
실제 마크다운 등록·BM25/Chroma 검색·Kuzu 관계 검색을 검증한 독립 예제는
[RAG / GraphRAG 통합 테스트](markdown-rag-test.md)를 참고하세요.
UI에서 사용할 문서 등록·수정·삭제·색인·검색은 [RAG Component 공개 API](rag-components.md)에
정리했습니다. 실행 예제는 `examples/llm/rag_components.py`입니다.
대화는 `backend.projects.create(..., conversation_storage="file" 또는 "memory")`로
프로젝트별로 선택하며 재시작 시에도 선택을 유지합니다. 백엔드 설정은 기본값입니다.
메모리 수명과 사용법은 [대화 저장 방식](conversation-storage.md)을
참고하세요. Project/Session/Run/Step 파일은 유지됩니다.

AI execution library and plugin for the ish shell platform. Includes a
LiteLLM LoopEngine that streams text,
executes explicitly registered tools, and continues until a final answer.
The library targets Python 3.12.14 on Linux. The host supplies the shell and UI; this plugin
does not yet provide a conversation UI or a persistent background worker.

## Use as an ish plugin

Copy the **`llm/` package directory**, including its subdirectories, into
`~/ish/plugin/script/llm/` (or `<ish --home directory>/plugin/script/llm/`).
The repository checkout itself does not need to be renamed or copied there.

```text
plugin/script/llm/
    __init__.py
    llm.py              # PLUGIN_META and main(*argv)
    components/
    core/
    engines/
    providers/
    services/
```

The host reads the literal `PLUGIN_META` in `llm.py`, checks its Python
`dependencies`, and loads the entry point as `llm.llm`. `requirements` is reserved
for other ish plugins and is empty. `rank-bm25|rank_bm25` means install the
`rank-bm25` distribution and check the `rank_bm25` import. ChromaDB, Kuzu and
rank-bm25 support the RAG component's vector, graph and keyword indexes.
They are required by plugin loading because they appear
in its metadata, even though the current LoopEngine does not import them.

Add this to the host's `.ishrc.py`, then restart ish:

```python
prompt.set_tool("llm", plugin_name="llm", function_name="main")
```

At the ish prompt, with the provider's environment configured:

```sh
llm --help
llm --engine loop --model openai/gpt-4o-mini --prompt "Hello" --workspace /path/to/ai-workspace
```

The function accepts positional strings from ish rather than reading the host's
`sys.argv`. Each invocation creates a new Project/Session, streams output in the
tool worker, and shuts down RunManager before reporting its process exit code.
This command entry point does not resume a selected Session. Use LargeLanguageModel
below to keep selected Session managers alive and close them on exit. Same-workspace
workers cannot independently own the workspace at the same time.

The plugin targets Linux/WSL and Python `3.12.14`. The host's compiled
build uses a matching external Python to install dependencies when necessary.
See `ish.platform/README.md` for host requirements. Plugin metadata and development
dependencies use the same LiteLLM/jsonschema/LangGraph version ranges.
Package installation and full PTY execution
on Linux/WSL still need integration verification.

The Python package is now `llm`; its distribution name is `ish-llm`, avoiding the
host's `ish` package/distribution. Update old `from ish...` imports to `from llm...`.
Existing JSON/JSONL data and the `.ish.lock` filename remain compatible.

## Backend API for ish and other plugins

Retrieve the class in `.ishrc.py` after automatic plugin loading:

```python
llm = plugin.get("llm")
LargeLanguageModel = llm.LargeLanguageModel
backend = LargeLanguageModel("/path/to/ai-workspace")
```

Construction does not create directories or start an event loop. Keep this
instance in the process that owns execution and call its async methods on one
event loop. This backend API does not use `asyncio.run` or terminate the process.
Do not pass a live backend through `prompt.set_tool` to another process; that
worker must construct its own backend. The `main` command remains a separate
single-request worker adapter.

```python
project = backend.projects.create(
    "Research",
    config={"parameters": {"engines": {"loop": {'config': {'completion': {'model': 'openai/gpt-4o-mini'}}}}}, "custom_setting": 42},
    components=["tools"],
)
session = project.sessions.create("Conversation")

# Tool selection can be edited independently of runtime handler registration.
project.components.tools.enable("add")
project.components.tools.disable("add")
project.components.tools.set_enabled([])
print(project.components.tools.enabled())  # []

# In the host's async input handler:
async def on_user_input(text):
    request = await session.run.submit(text, engine="loop")
    return await request.wait()  # Only this request, not all future queued input.

async def inspect_finished_run(run):
    result = await run.aresult()
    print(result.engine, result.status, result.total_tokens)
    print((await run.aresponse()).content)
    print(await run.steps.alist())
    print(await session.aconversation())

# In the host's async shutdown path:
async def close_backend():
    await backend.shutdown()
```

Supply `on_event(run, event)` and `on_run_event(event)` callbacks (sync or async) to
the constructor for streaming and lifecycle updates. `wait_idle` means the queue
drained; inspect Run status or lifecycle events to distinguish success/failure.

Every request must specify `engine`, for example `await session.run.submit(text,
engine="loop")`. Registering an Engine only makes it available; there is no
automatic selection, even if exactly one Engine is registered. Omitting the
keyword raises TypeError before queue admission; None/blank/non-string values
raise RunRequestError with `engine_required`. Unknown names use
`engine_not_registered`. Model configuration remains in `completion["model"]`.

ProjectConfig preserves arbitrary workspace keys; every submitted request still
requires an explicit Engine. Session metadata has no default_engine field and does
not silently discard removed fields. Recovery uses the Engine stored on each
queued message. Missing or invalid selections fail with engine_required and the
queue continues without guessing an Engine.
The host must wire input and shutdown callbacks; merely importing the class does
not install UI hooks. Close the old backend before replacing it during rc reload.
`async with LargeLanguageModel(...) as backend:` is also
supported for a bounded session.

`backend.projects.load(project_id)` and `project.sessions.load(session_id)` require
explicit IDs. Loading a Session twice shares its existing RunManager. Each Session is
serial; different Sessions can run concurrently. `await session.run.shutdown()` detaches
only that Session; a subsequent submission creates a new manager and recovers its
durable queue. Shut down an attached Session before editing its execution configuration,
cloning or deleting it. Titles and metadata can be updated while it runs.
`backend.shutdown()` is terminal and closes all owned managers.

Project/Session handles expose `save(title=..., config=...)`, `clone(title=...)`,
`delete(permanent=False)` and `restore()`. Their `.data` properties return fresh
detached domain models. The handles themselves are service objects and are never
serialized into Project/Session JSON. Run and Step handles expose history; they do
not execute Engines or edit lifecycle status directly. Register strategies with
`backend.engines.register(name, engine)` and select one with `submit(..., engine=name)`.

Component definitions use the same chain (the spelling is `components`):

```python
identifier = project.components.tools.create({
    "type": "function",
    "function": {
        "name": "search",
        "description": "Search project documents",
        "parameters": {"type": "object", "properties": {}},
    },
})
definition = project.components.tools.load(identifier)
project.components.tools.update(identifier, {"custom_setting": True})
```

`create` persists a JSON definition and requires no runtime handler. It does
not enable execution automatically. Supply handlers via a `ToolRegistry` passed
to `ToolComponent(catalog)` and configure enabled tool names when ready. The same
CRUD works for custom components through `project.components["name"]`; bracket
access also handles names that collide with collection methods.

Default backend registration includes Tools and LoopEngine, but only explicitly
selected components create directories. To register a different component list:

```python
from llm.components.tools import ToolComponent
from llm.components.workflows import WorkflowComponent

backend = LargeLanguageModel("/path/to/workspace", components=[
    ToolComponent(), WorkflowComponent(),
])
project = backend.projects.create(components=["workflows"])
project.components.select(["workflows", "tools"])
```

Lower-level `ProjectManager(ProjectRepository(root), components=[...])` now
creates its own SessionManager and ComponentRegistry. Explicit SessionManager/registry
injection remains supported. Synchronous metadata APIs remain available;
UI callers should prefer the async facade methods below.

## Async facade and request results

Existing synchronous APIs remain supported. Each storage method also has an `a`
prefix variant (`acreate`, `aload`, `alist`, `asave`, `aclone`, `adelete`, `arestore`).
Use `aget_data()` instead of the disk-reading `.data` property on Project/Session/Run
handles, and `aconversation()` for messages. All async facade storage calls use
one bounded background transaction lane per backend; they hold workspace ownership
through the entire operation. Cancellation drains accepted writes, and backend
shutdown drains admitted facade I/O before releasing runtimes. Async operations
use the backend's original process/event loop. Synchronous methods still block.

```python
# Inside an async handler; backend already contains the "loop" Engine.
project = await backend.projects.acreate("Example", config=ProjectConfig(parameters={"engines": {"loop": {'config': {'completion': {'model': 'gemini/gemini-3.6-flash'}}}}}), components=["tools"])
session = await project.sessions.acreate("Conversation")
tools = await project.components.aget("tools")  # No synchronous attribute lookup.
await tools.aset_enabled([])  # Enable names after registering execution handlers.

request = await session.run.submit("Hello", engine="loop")
run = await request.wait(timeout=60)
result = await run.aresult()
response = await run.aresponse()
print(result.status, result.finish_reasons, result.total_tokens, response.content)

project_results = await project.results.alist()
session_results = await session.results.alist(include_running=True)
same_result = await session.results.aload(run.id)
steps = await run.steps.alist()
```

Facade submit now returns **RequestHandle**, not Message; its `.id` remains the
durable user Message ID. Read `.data` / `await aget_data()` for the Message. The
lower-level RunManager.submit still returns Message. RequestHandle.wait returns
RunHandle on completion, failure or interruption; inspect result.status/error_code
to distinguish them. `request.result` / `await request.aresult()` returns None
before a Run exists, then a current ExecutionResult snapshot. `request.run` /
`await request.aget_run()` exposes that RunHandle when available.

Multiple callers can wait independently. Timeout/cancelling a wait never cancels
execution; explicit interrupt remains Session-scoped. If the worker stops before a
queued request runs, wait raises RuntimeError and the request remains persisted.
Storage/worker failures propagate. A cancelled submit may still accept input, as
before; do not blindly retry. Reopen requests after restart with
`await session.run.arequest(message_id)` (or synchronous `request(message_id)`), then
call wait to recover/schedule pending input. Stale Runs are interrupted, never
replayed. Completed history can be waited on without attaching a runtime.

`project.results` / `session.results` offer list/load and alist/aload over Run-owned
observations. Lists omit pending/running Runs and deleted Sessions by default;
include_running/include_deleted opt in. `run.result` and `run.response` also have
aresult/aresponse methods. Response is the persisted Assistant Message, including
partial output after failure/interruption. No new result files are created.

Component handles expose aconfiguration/aconfigure and a-prefixed CRUD, while
ToolData adds aenable/adisable/aset_enabled/aenabled. Use components.aselect/aremove
for selection/removal. Raw dynamic attribute access such as components.tools and
disk-reading properties remain synchronous. Custom component convenience methods
can opt into the same lane using services.storage.async_method, like ToolData.
Result/history lists still scan their scope; pagination/indexing is not introduced.
Session configuration/lifecycle guards remain: await session.run.shutdown() before changing configuration,
cloning or deleting an attached Session.

LoopEngine reads `config.parameters.engines[registered_engine_name]`, including
Session overrides. EngineContext.settings() uses the owning Run's engine name.
For stages in a PipelineEngine, that is the pipeline's registered name. To use
another section explicitly, construct `LoopEngine(settings_name="loop")` (or any
other section name). Constructor limits/system_prompt still override JSON settings;
completion_kwargs still overrides Project/Session completion arguments. Reusing the
same LoopEngine under multiple names does not mutate its shared settings.

## Install and test with Python 3.12.14

The development and validation target is **Linux Python 3.12.14 only**, pinned in
`.python-version` and `pyproject.toml`. The plugin uses native standard-library
dataclasses, string enums, async timeout scopes and async iterator closing.

Using uv on a fresh Linux/WSL machine:

```sh
uv python install 3.12.14
uv venv --python 3.12.14 .venv-linux312
uv pip install --python .venv-linux312/bin/python -e ".[rag]"
```

Execution supports **Linux only** (including WSL2). Install Bubblewrap with your
distribution package manager for sandbox Tools and the complete test suite.
Use `.venv-linux312/` for Linux validation. Environment directories are excluded
from source control. Windows virtual environments cannot execute this plugin.

Base dependencies include LiteLLM and LangGraph. The complete suite, including
real local RAG index tests, also requires `".[rag]"`. Plugin loading checks every
library listed in `PLUGIN_META` independently of packaging extras. GraphEngine
uses LangGraph for scheduling and LoopEngine for LiteLLM calls; it does not use
`langchain-litellm`. Workflow node checkpoints and explicit `session.run.resume(...)`
are available. Use `pause_before: true` for a saved boundary; uncertain action
nodes require explicit retry approval. See [checkpoint and UI storage APIs](graph-checkpoints.md).

## Run the tests

From the repository root on Linux/WSL (the runner rejects any other interpreter
version and copies source to the Linux filesystem before running):

```sh
.venv-linux312/bin/python tests/llm/run_linux.py --full
```

See [handoff.md](handoff.md) for this workstation's exact WSL interpreter path.

After installing dependencies, the suite uses temporary workspaces and no live
model API or credentials. Its SDK test replaces HTTP with in-memory SSE and
token accounting with a local test vocabulary. It covers
streaming, serial queues, concurrent Sessions, cancellation, engine failures,
shutdown, recovery after an abruptly terminated subprocess, and lifecycle
operations.

Latest Linux test results are recorded in [handoff.md](handoff.md).
SDK tests use mock SSE; process and Bubblewrap tests use actual Linux subprocesses.
These tests do not establish interactive ish PTY/UI coverage.

Keep the checkout and environment on the Linux filesystem when testing under WSL: `/mnt/c` and
`/mnt/d` can add enough I/O latency to invalidate timing-sensitive tests.llm.

A separate live Gemini 3.8 Flash run fetched three official BLEACH webpages,
generated a Korean report, saved it with file_create, and verified it with
file_read. See [the live web example](builtin-tools.md#실제-웹-조회와-파일-저장-예제).

Deploy the `llm/` folder to ish; the plugin does not require a wheel install.
Generated packages from previous validation environments are not current releases.

## Run a real streaming request

Set your provider credential in the environment, then run the demo. The command
below assumes `OPENAI_API_KEY` is already set. Replace the model identifier with
one available to your provider account.

```sh
python -m llm.llm --engine loop --model openai/gpt-4o-mini --prompt "Use add to calculate 12 + 30, then explain the result." --with-tools
```

Each invocation creates a Project and Session under `workspace/projects/`, prints
the Project path, and prints text deltas as they are persisted. `--with-tools`
registers only an example `add` function. Without it, the engine streams a single
answer unless the provider returns an unsupported tool call. Ctrl+C interrupts
active work through RunManager shutdown. `--api-base` selects an optional
compatible endpoint; `--temperature` defaults to omission so model defaults apply.

To attach the engine to your own services:

```python
from llm.engines.loop import LoopEngine

engines.register("loop", LoopEngine(
    max_iterations=8,
    completion_kwargs={"top_p": 0.9, "max_tokens": 4096},
    system_prompt="Answer accurately and concisely.",
))
# Select the registered Engine explicitly on every request:
# await manager.submit(content, engine="loop").
```

ProjectConfig.parameters.engines[name].config.completion stores explicit JSON-compatible Loop/LiteLLM options, including
model, temperature, api_base and provider-specific options. Authentication uses
the provider SDK environment or runtime-only completion_kwargs. There is no application credential resolver or field-name blocking.

LoopEngine calls `litellm.completion` with `stream=True`. SDK timeout/retry kwargs
are forwarded only when explicitly configured; DEFAULT_MAX_RETRIES=0 remains a
documented LiteLLM compatibility invariant. It forwards content deltas immediately, assembles indexed
tool-call fragments, validates the complete batch against registered JSON
schemas, executes async tool handlers serially, and includes their results in
the next completion. Register tools through `llm.components.tools.ToolRegistry`;
handlers receive an argument dictionary and return text or JSON-compatible data.
Handlers must avoid blocking the event loop and propagate cancellation.

Each LLM iteration and tool execution emits Step events. A `stop` finish reason
ends the Run. Truncated streams, provider/tool errors, invalid arguments, and
exhausted iteration limits fail the Run without automatic retries. Tools from a
last iteration are not executed when no follow-up completion can be made.
Output and tool arguments use only explicitly configured limits; a bounded stream
bridge applies backpressure without dropping content. Missing Loop request/tool
timeouts add no deadlines. SDK completion.timeout and Loop request_timeout are independent.

`completion_kwargs` is a mapping of LiteLLM completion arguments or a synchronous
`factory(context) -> mapping`. Values override Project model/temperature/API base
and default provider options. New provider-specific parameters pass through
without a new dataclass field; unsupported values are reported by LiteLLM.
Runtime SDK clients and callbacks are supported and retained by reference. Plain
dict/list/tuple containers are copied at configuration/snapshot/request boundaries.
These runtime parameters are not serialized to Project/Run metadata or service
logs. SDK environment and completion arguments remain available for provider options;
there is no application key-name filter or masking.

Application limits are direct keyword arguments: `max_iterations=8`,
`request_timeout=60.0`, `tool_timeout=30.0`, `buffer_size=8`, `max_tool_calls=16`,
`max_argument_chars=65536`, and `max_output_chars=1_000_000`. `LoopOptions` and
`options=` were removed. For model options use `completion_kwargs`, for example
`completion_kwargs={"max_tokens": 100}`.
Provider `timeout` is separate from the total per-round `request_timeout` limit;
configure both when necessary. Explicit SDK num_retries affects completion calls
only, never automatic tool or stale-Run replay. The Loop requires streaming and
one choice: stream=False/n other than 1 are rejected. messages and tools are built
from the Session transcript and Project registry; overriding messages/tools or legacy
functions/function_call is rejected. tool_choice and parallel_tool_calls are
forwarded, but actual tool execution remains serial.

## Project settings, Session overrides and Run results

```python
config = ProjectConfig(parameters={"engines": {"loop": {'policy': {'max_iterations': 8, 'request_timeout': 60}, 'config': {'system_prompt': 'Answer clearly.', 'completion': {'model': 'openai/my-model', 'top_p': 0.9, 'max_tokens': 4096}}}}}, data={"documents": ["manual.md"]})
project = projects.create("Workspace", config=config)
session = sessions.create(project, "Research", config={
    "parameters": {"engines": {"loop": {'config': {'completion': {'max_tokens': 2048}, 'system_prompt': 'Explain with examples.'}}}}, "data": {"topic": "Python"},
})
```

Project settings live in project.json; explicit Session overrides live in session.json.config.
ProjectConfig is an open JSON dict with policies, parameters and metadata. Only top-level
attribute shortcuts are provided; nested values use dictionary access. Removed SDK/default
sections are rejected rather than migrated. Component settings live in parameters.components.
Session creation does not copy Project settings. At execution, nested dictionaries merge;
explicit null overrides where supported. Missing keys remain missing. Loop reads completion
inside its own parameters.engines target. Host constructor values override inherited settings.
EngineContext.settings(name) returns a detached view and the selected `engine` mapping.
Project changes affect future Runs, while active Run snapshots stay unchanged.
See [the configuration contract](../../llm/CONFIGURATION.md) for sources and UI metadata.
Persisted Projects require components/conversation_storage; Sessions require config.
No default Project/Session selection or automatic migration is performed.


```python
await manager.submit("Explain the design", engine="loop")
await manager.wait_idle()

result = projects.results.list(project)[-1]  # ExecutionResult
print(result.run_id, result.engine, result.status)
print(result.total_tokens, result.finish_reasons)
for completion in result.completions:         # CompletionResult
    print(completion.step_id, completion.model, completion.finish_reason,
          completion.usage, completion.duration_seconds)
```

Run is the owner of execution observations. Each completion snapshot is persisted
by stable call ID in run.json.metadata.completions. Finishing or recovering a Run
also finalizes interrupted observations in that same file. No second result file
is written under Project or Session.

RunResultQuery exposes load/list through projects.results, sessions.results and
manager.results. ExecutionResult is an in-memory view, computed from the Run:

```python
result = sessions.results.load(session, run_id)       # direct lookup within a Session
project_runs = projects.results.list(project) # terminal Runs from active Sessions
all_runs = projects.results.list(project, include_deleted=True, include_running=True)
```

load accepts either a Project or Session; a Project lookup searches its Sessions.
Lists exclude soft-deleted Sessions and pending/running Runs by default; direct load
can inspect a soft-deleted Session's history. Reads hold workspace ownership but
never write result metadata. RunManager's query uses its injected RunRepository;
for custom storage, RunResultQuery(sessions, custom_run_repository) is also available.
Async UI callers can offload these synchronous queries through StorageIO.

Soft deletion retains Run history; permanent Session deletion removes its Runs and
therefore its query results. Project/Session clones start without Runs. Old Project
state/executions/*.json files are ignored and left untouched, including orphaned
summaries from deleted Sessions; they are not a fallback source. Existing Run metadata
needs no data move. There is no cached aggregate to synchronize or rebuild.
Project-wide queries scan Session/Run metadata; a disposable index can be added later
if measurements justify it without becoming the source of truth.

CompletionResult contains call/Step IDs, model, response ID, finish reason, status,
timestamps, elapsed seconds, numeric usage and usage_complete. ExecutionResult
contains Run/Session/Project IDs, Engine name, Run timing/status, completion results
and summed prompt_tokens/completion_tokens/total_tokens. Summaries aggregate all
LLM calls in Loops and pipelines. No cost estimate is inferred.

Set stream_options.include_usage explicitly when the provider supports it; the helper
does not insert this option. It captures SDK or dict usage-only chunks,
including numeric cached/reasoning token details. Totals are None if any call
lacks a count or has an uncompleted stream; partial observed usage remains on the
individual result. Unknown usage is not zero. Provider-reported counters are not
an independently verified billing ledger. No raw responses, headers, prompts,
API keys or tool arguments/results enter the execution summaries.

stream_completion now yields strings plus COMPLETION EngineEvents by default.
BaseEngine.execute() and Loop forward/persist them automatically. Use
include_events=False only for standalone text consumers that do not need usage
persistence. Custom Engine-protocol implementations can emit COMPLETION events
with CompletionResult themselves; non-reporting engines still get Run summaries
with unknown LLM usage. Cancellation never yields from a closing generator;
RunManager finalizes the last durable observation.

## Reusable embedding and rerank inference

Engine decides the execution sequence of a Run. Embedding and reranking are model
operations that Engines, Tools and RAG can share. Their implementations now live
in llm/components/rag. Import clients from that package; the former llm.inference
compatibility path has been removed. They are not
Engine strategies or new children in Project -> Session -> Run -> Step.
The current reusable clients call LiteLLM's
[aembedding](https://docs.litellm.ai/docs/embedding/supported_embedding) and
[arerank](https://docs.litellm.ai/docs/rerank) APIs:

```python
from llm.components.rag import EmbeddingModel, RerankModel

embedding = EmbeddingModel(model="openai/text-embedding-3-small", dimensions=256)
reranker = RerankModel(model="cohere/rerank-english-v3.0", top_n=3)

vectors = await embedding.embed(["first document", "second document"])
ranked = await reranker.rerank("the query", ["first document", "second document"], top_n=1)
# Native SDK responses: vectors.data / vectors.usage, ranked.results / ranked.meta.
```

Constructor kwargs are runtime defaults; call kwargs override them. Provider-specific
options pass through without a dataclass allowlist. Builtin request containers are
copied per instance/call while SDK clients/callbacks retain identity. Model clients
can be shared across concurrent Sessions. Missing timeout/retry options are omitted;
callers may explicitly supply SDK options. Native responses, exceptions and cancellation
propagate to the caller. The injected embedding_fn/rerank_fn must be asynchronous.
SDK import is lazy and offloaded. Importing model clients from llm.components.rag loads no Engines,
services, core models or LiteLLM SDK.

Custom Engines may declare their own model settings under targeted parameters:

```python
config.parameters.setdefault("engines", {})["retrieval"] = {
    "embedding": {"model": "openai/text-embedding-3-small"},
    "rerank": {"model": "cohere/rerank-english-v3.0", "top_n": 3},
}
# Session.config["parameters"]["engines"]["retrieval"] may explicitly override them.

async def prepare(context):
    settings = context.settings("retrieval")["engine"]
    result = await EmbeddingModel(**settings["embedding"]).embed(["document text"])
    context.state["embeddings"] = result.data

# Wrap prepare with PreparationStep(..., kind="embedding") or self.step(...).
# A Tool handler can await the same model methods without creating another Run.
```

Inference clients create no Steps/Runs, register no tools and write no files.
Callers own preparation, input selection, vector/index storage and lifecycle events.
Vectors/document results should stay in runtime state or component-owned storage,
not ordinary execution logs. Native model usage is returned, but inference calls
do not automatically emit COMPLETION events or enter the completion token aggregate;
a calling Engine must explicitly report observations if needed. Rerank billing
units are not assumed to be tokens. RAG collection/index CRUD remains planned.

## Write your own Engine

Start with BaseEngine. Implement only `run(context)`: yield strings for response
text, or use an async function returning None for work with no visible output.
The base class creates Step IDs and emits start/text/completion/failure events.
RunManager and StepManager still own all persistence and interruption handling.

```python
from llm.engines import EngineContext, BaseEngine

class EchoEngine(BaseEngine):
    async def run(self, context: EngineContext):
        yield "Echo: "
        yield context.messages[-1].content

engines.register("echo", EchoEngine("Echo response", kind="text"))
await manager.submit("hello", engine="echo")
```

A complete offline example with service setup is `examples/llm/custom_engine.py`:

```sh
.venv312/bin/python examples/llm/custom_engine.py
```

It prints `Echo: hello`, uses a temporary workspace, and makes no model requests.
You can also pass `BaseEngine("Name", action=async_function_or_generator)` without
writing a subclass. A generator yields strings and may forward COMPLETION events;
a coroutine must return None.
Store private/intermediate results in `context.state`, not in yielded dictionaries
or Step metadata. Use `timeout_seconds=` for an optional whole-Step deadline.

BaseEngine instances can be shared across Runs; execution state is local to each
execute call. Keep your own per-Run state in context.state/local variables rather
than on the Engine instance. Names, kind, metadata, and error_message are public,
persisted labels: use safe developer constants. Metadata is checked for JSON
serialization at construction. Raw action/SDK exceptions are replaced with a safe
failure event. Cancellation propagates, and async iterators are closed even on
failure/early close; no terminal event is yielded while the consumer is closing.
The existing Run worker finalizes an interrupted active Step. Blocking work must
still be offloaded by your action, and cancellation must not be swallowed.

The inherited `execute()` wraps `run()` in one Step. To implement multiple Steps,
override `execute(context)` and forward events from `self.step(context, action,
name=..., kind=...)`, closing each iterator with `contextlib.aclosing`. LoopEngine
uses this pattern for every LLM round and tool operation. PreparationStep also
inherits BaseEngine. PipelineEngine composes engines inside the same Run.

BaseEngine also supplies `stream_completion(request, response=None)`. It calls
LiteLLM's synchronous `completion(..., stream=True)` through the existing bounded
thread bridge, yielding text from dictionary or SDK chunks as it arrives. This
matches the [LiteLLM streaming format](https://docs.litellm.ai/docs/completion/stream).
A minimal text-completion engine can return that async iterator directly:

```python
from llm.engines import BaseEngine

class AnswerEngine(BaseEngine):
    def run(self, context):
        return self.stream_completion({
            "model": context.settings()["engine"]["config"]["completion"]["model"],
            "messages": [{"role": "user", "content": context.messages[-1].content}],
            "max_tokens": 1024,
            "timeout": 30,
        })

engines.register("answer", AnswerEngine("Answer", kind="llm", timeout_seconds=35))
```

`run()` here is an ordinary function returning an async iterator; BaseEngine
consumes and closes it. This example sends the current input and uses the SDK's
environment authentication. Custom engines choose their own history, system prompt,
Project options; LoopEngine resolves Project/Session defaults automatically.
For preparation or text transformation use `async def run()` with
`async with aclosing(self.stream_completion(request)) as items` and forward each
item. Text arrives as str; completion observations arrive as EngineEvent. The
inherited execute() handles both. If transforming text, first check isinstance(item, str).

Pass a fresh `response={}` to receive the assembled assistant message after normal
stream completion. It contains `role`, `content`, and optional `tool_calls` in
completion message format. Fragmented tool calls are ordered by index and
validated before success; the helper does not execute them. The output dictionary
is unchanged on failure/cancellation and must remain local to that request.
Completion observations separately report finish reason, model, response ID,
usage and timing. Reasoning/multimodal content is not surfaced by this helper. It
requires one choice and normal `stop` or `tool_calls` termination.

Each call copies builtin request containers while retaining live SDK handles.
Assembly never lives on the Engine instance. The helper emits COMPLETION observations but has no Step
lifecycle/deadline: use it inside `run()` or `self.step(...)` for those guarantees.
BaseEngine provides a LiteLLM convenience, not a cross-provider abstraction;
non-LLM engines can use only its Step event support without invoking LiteLLM.

Migration: import `BaseEngine` from `llm.engines` or `llm.engines.base` instead of
`StepEngine`/`llm.engines.step`. The old module, `_completion.py`, `LoopOptions`,
`LoopEngineError`, `_Turn`, and `_ToolCall` are removed. LoopEngine is a single
BaseEngine subclass with direct constructor options. Validation raises standard
ValueError/TypeError; execution failures retain Step error details and
RuntimeError through the common lifecycle. Existing event/persistence formats and
RunManager ownership are unchanged.

## Prepare a Run before its Loop

PipelineEngine runs Engine stages in order inside the same Run. PreparationStep
wraps an async callback as an observable Step. All stages receive the same
EngineContext and its fresh, runtime-only state dictionary. The Loop evaluates
completion_kwargs/system_prompt factories after preparation, once per execution.
A system prompt is prepended to the provider transcript and is not appended as a
new durable conversation message each round.

```python
import asyncio
import os
from pathlib import Path
from llm.engines.loop import LoopEngine
from llm.engines.pipeline import PipelineEngine, PreparationStep

async def prepare(context):
    # Offload blocking reads. This example loads context, not a RAG implementation.
    path = context.project.paths.root / "instructions.txt"
    context.state["instructions"] = await asyncio.to_thread(
        path.read_text, encoding="utf-8"
    )
    context.state["shell_env"] = dict(os.environ)  # for subprocess env=, not logs

engines.register("prepared_loop", PipelineEngine(stages=[
    PreparationStep("Read instructions", prepare, kind="retrieval"),
    LoopEngine(
        max_iterations=8,
        completion_kwargs={"top_p": 0.9, "max_tokens": 4096},
        system_prompt=lambda context: context.state["instructions"],
    ),
]))

# The caller creates instructions.txt in the Project before submitting.
await manager.submit("Help with this workspace", engine="prepared_loop")
```

PreparationStep adds a deadline only for explicit timeout_seconds;
missing or None adds no deadline. Callbacks/factories are developer code on the event
loop: avoid blocking calls, propagate cancellation, keep per-Run values in
context.state, and do not mutate global os.environ. A copied environment reflects
this process's current environment; it cannot automatically read changes from an
unrelated shell. RAG/Shell work belongs to component services; this feature does
not implement RAG synchronization or Shell execution. Engine stages never write
Session/Run/Step/conversation persistence directly.

A failed/timed-out preparation prevents subsequent stages; interruption marks the
Run and active Step interrupted and preserves queued input. Pipeline also stops
on non-success Step terminal events or unfinished Steps, and closes each stage's
iterator. A crash uses the existing stale-Run recovery policy: no preparation or
Loop side effects are automatically replayed. Pipelines may be nested; this is
sequential composition, not GraphEngine or an arbitrary-code loader. Shared
clients and developer component state still need their own concurrency handling.

RunManager accepts `on_event(run, event)` for displaying persisted events. The
callback receives snapshots after each event is recorded; keep it synchronous,
quick, and nonblocking. RunEventPublisher catches display exceptions and logs
`observer.failed` without failing the Run or dropping queued requests. Engine
and persistence errors still fail execution. See `llm/llm.py`.

The synchronous provider iterator lives in a dedicated daemon thread. Cancel
stops delivery immediately; a blocked provider read cannot be forcibly stopped
and closes in its owning thread after it returns or times out. Repeated
cancellations can temporarily leave multiple cleanup threads. They cannot
execute tools or write persistence, and their late deltas are discarded.

Visible deltas update the Assistant message during execution. The final Loop
answer replaces its content through an append-only conversation event; intermediate
outputs remain in the Run output journal. Tool arguments remain Step metadata,
while results use Step.output.data. Provider transcript messages are transient.
A restart never automatically resumes the tool loop of a stale Run.

API references: [LiteLLM streaming](https://docs.litellm.ai/docs/completion/stream),
[tool calling](https://docs.litellm.ai/docs/completion/function_call), and
[completion parameters](https://docs.litellm.ai/docs/completion/input).

## Use the foundation

```python
import asyncio
from pathlib import Path

from llm.core.models import ProjectConfig
from llm.engines.registry import EngineRegistry
from llm.engines.loop import LoopEngine
from llm.services.history.conversation import ConversationStore
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.runtime.runs import RunManager
from llm.services.lifecycle.sessions import SessionManager


async def main() -> None:
    sessions = SessionManager()
    projects = ProjectManager(ProjectRepository(Path("./workspace/projects")), sessions)
    project = projects.create("Example", config=ProjectConfig(parameters={"engines": {"loop": {'config': {'completion': {'model': 'openai/gpt-4o-mini'}}}}}))
    session = sessions.create(project, "Conversation")
    engines = EngineRegistry()
    engines.register("loop", LoopEngine())
    manager = RunManager(sessions, engines, session=session)
    try:
        await manager.submit("Hello", engine="loop")
        await manager.wait_idle()
        for message in ConversationStore(session.paths.conversation).list():
            print(message.role, message.status, message.content)
    finally:
        await manager.shutdown()


asyncio.run(main())
```

On restart, load the Project and Session through their managers, then call
`await manager.start()`. This interrupts stale state and restores
only queued requests. Calling `start` again on an already attached Session is safe.

Project, Session, Run, and Step metadata each use a Repository/Manager pair in
`lifecycle/projects.py`, `lifecycle/sessions.py`, `runtime/runs.py`, and
`lifecycle/steps.py` under `llm.services`. Repositories handle storage;
managers handle lifecycle or execution. SessionManager and StepManager create a
default repository unless one is injected:

```python
from llm.services.lifecycle.sessions import SessionManager, SessionRepository
from llm.services.lifecycle.steps import StepManager, StepRepository
from llm.services.runtime.runs import RunManager, RunRepository

sessions = SessionManager(repository=SessionRepository())
projects = ProjectManager(ProjectRepository(Path("./workspace/projects")), sessions)
steps = StepManager(repository=StepRepository())
manager = RunManager(sessions, engines, session=session, repository=RunRepository(), steps=steps)
```

Service implementations are grouped into `lifecycle/`, `runtime/`, `history/`,
and `infrastructure/`. The root keeps the facade, configuration and query APIs.
Within service classes, initialization and private helpers precede public APIs.
Import service implementations from their responsibility-based packages. Legacy flat
import aliases and `_compat/` have been removed, including old patch targets and
pickle class paths. See [the service layout](../../llm/services/README.md).

RunManager is imported from `llm.services.runtime.runs`. Use `repository=` and
`.repository` for its Run repository. ComponentData uses `create/acreate` for
persisted definitions; Registry.register still registers runtime objects.
Message events still use ConversationStore.
Project metadata now includes a `components` list. Older metadata loads with an
empty selection; existing directories are never implicitly enabled. Each submitted
request must name its Engine explicitly; registering `loop` does not select it.
The deterministic fake engine now exists only in `tests/llm/support/fake_engine.py`.

Use one shared ProjectRepository/service container per workspace and one event
loop for its RunManager. Manager calls automatically acquire an OS lock at
`<projects-root>/.ish.lock`. Attached Sessions retain it until `await manager.shutdown()`
has drained execution/storage, including when idle. A competing repository
instance/process raises `llm.services.infrastructure.locking.WorkspaceBusyError` before recovery
or mutation. Process exit releases ownership. Never delete the lock file to bypass
ownership. Different workspace roots are independent.

Shutdown affected runtimes before cloning, deleting, or restoring Sessions/Projects.
RunManager performs ordered disk work in background threads and caches incremental
conversation replay. QUEUED and streaming deltas still fsync before scheduling or
notification. Cancellation waits for in-flight writes; a cancelled `submit` can
still accept and schedule a request, so do not blindly retry. Per-delta operational
logging is omitted; the delta remains in conversation JSONL.

Lower-level ProjectManager/SessionManager CRUD methods are synchronous. Prefer the
facade's a-prefixed methods in a UI. For direct manager access, use the
ownership-aware adapter:

```python
from llm.services.infrastructure.storage import StorageIO

storage = StorageIO(projects.ownership)
project = await storage.run(projects.create, "My project")
session = await storage.run(sessions.create, project, "My session")
await manager.submit("Hello", engine="loop")
```

Injected synchronous storage/context/capability adapters run on worker threads
and must not require a running event loop. Engines and UI callbacks remain on
the event loop. Direct lower-level repository/component or ConversationStore
writes need `with projects.ownership.scope():`; manager APIs already supply it.
Locks coordinate local service processes; network filesystems and arbitrary
external writers are outside this contract. See architecture/production docs.

Session clones copy configuration and conversation snapshots with fresh IDs.
Cloned queued inputs become cancelled, Run links are cleared, and execution
history and artifacts are not copied. Project clones copy configuration and
active Session snapshots through SessionManager. Soft deletion marks metadata in
place and retains the stored files. Public Project/Session `save` methods edit
configuration only: they reload current ownership/lifecycle state and cannot
resurrect deleted records or set Session execution status. Session configuration edits require a
detached runtime; facade title/metadata edits use update_details and can run while attached. RunManager's internal transitions remain separate. Save Project
configuration changes before submitting requests; caller-owned snapshots are
not used as the source of current configuration.

## Select Project components

ProjectManager coordinates selected components; each component owns its paths,
initialization, configuration, and clone policy. `ProjectPaths.tools` and
`ProjectPaths.workflows` have been removed. Use `ToolPaths.for_project(project)`
and `WorkflowPaths.for_project(project)` when the respective subsystem needs paths.

```python
from pathlib import Path
from llm.components.registry import ComponentRegistry
from llm.components.tools import Tool, ToolRegistry, ToolComponent
from llm.components.workflows import WorkflowComponent
from llm.engines.registry import EngineRegistry
from llm.engines.loop import LoopEngine
from llm.core.models import ProjectConfig
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.lifecycle.sessions import SessionManager
from llm.services.runtime.runs import RunManager

async def add(arguments):
    return arguments["a"] + arguments["b"]

catalog = ToolRegistry((Tool("add", "Add two numbers", {
    "type": "object",
    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
    "required": ["a", "b"], "additionalProperties": False,
}, add),))
components = ComponentRegistry((ToolComponent(catalog), WorkflowComponent()))
sessions = SessionManager()
projects = ProjectManager(ProjectRepository(Path("workspace/projects")), sessions,
                          components=components)
project = projects.create("Example", components=("tools", "workflows"),
                          config=ProjectConfig(parameters={"engines": {"loop": {'config': {'completion': {'model': 'openai/gpt-4o-mini'}}}}}))
projects.configure_component(project, "tools", {'config': {'enabled': ['add']}})
session = sessions.create(project, "Conversation")
engines = EngineRegistry()
engines.register("loop", LoopEngine())
manager = RunManager(sessions, engines, session=session, capabilities=components)
```

Call `await manager.submit(text, engine="loop")` from an async UI handler.
Pass the same configured component registry to ProjectManager and RunManager.
Registered components are available to select; they are not automatically enabled.
`create(..., components=())` creates no tool/workflow directories. Selecting tools
creates `<project>/tools/records/`; selecting workflows creates its `records/` directory.
Configuration is stored only in ProjectConfig.parameters.components. Tool handlers remain in the application
catalog and are never serialized into Project JSON.

`projects.set_components(project, ("tools",))` changes selection after creation.
Disabling a component preserves its data. Initializers must be idempotent;
re-enabling tools keeps their previous configuration. Unknown/duplicate component
identities are rejected before Project creation. Initialization/clone failures
leave the new Project soft-deleted; restore retries initialization. A failed
selection update leaves the old selection in place, though partial directories
may remain. This is not a transactional plugin installer.

LoopEngine no longer accepts `tools=`. A Run resolves a fresh tool registry from
the saved Project selection/configuration into `EngineContext.tools`. This
snapshot remains fixed through that Run; later Runs see saved changes. A Project
without enabled tools cannot invoke another Project's tools, even through the
same LoopEngine instance. Missing component implementations fail execution before
the provider is called. After restart, reconstruct the registered Python handlers
and components; the saved names do not automatically import executable code.

ProjectManager initializes Session storage and the selected components. Additional
Project storage belongs in a registered Component.initialize implementation.

Project cloning delegates to each component. Agent/Workflow definitions retain
record IDs for references; runtime handlers are never persisted. RAG rebuilds
its local indexes from the saved documents, vectors and relations.

### Component data and custom components

Use AgentComponent for reusable model/prompt definitions and WorkflowGraph for
version 1 graphs, as described in [component definitions](component-definitions.md).
Agent records require purpose/completion.model; Workflow records validate on every
write and read. Extra JSON fields remain extensible.

The base and registry are independent of Tools. Subclass `Component`, explicitly
declare `name` and `directory`, and register the instance. Configuration and records
are open JSON dicts; you can add keys without changing a dataclass.

```python
from llm.components import Component
from llm.components.agents import AgentComponent
from llm.components.workflows import WorkflowGraph

class NotesComponent(Component):
    name = "notes"
    directory = "knowledge"

components.register(NotesComponent())
components.register(AgentComponent())
projects.set_components(project, ("tools", "workflows", "notes", "agents"))

agents = projects.component(project, "agents")
agents.create({
    "purpose": "Review code",
    "completion": {"model": "openai/gpt-4o-mini", "temperature": 0.2},
    "system_prompt": "Review code carefully.",
    "custom_option": True,
}, identifier="reviewer")

graphs = projects.component(project, "workflows")
graphs.create(WorkflowGraph(entry="review")
    .node("review", "agent", agent="reviewer")
    .node("done", "end").connect("review", "done").to_dict(),
    identifier="code_review")
graphs.update("code_review", {"description": "Review workflow"})
graph = graphs.load("code_review")
encoded = Component.serialize(graph)
restored = Component.deserialize(encoded)
all_graphs = graphs.list()  # {"code_review": {...}}

# Replace a record with save(id, data); update applies a shallow key patch.
# Delete one record with graphs.delete("code_review").
projects.remove_component(project, "notes")  # Disable and keep knowledge/.
projects.remove_component(project, "notes", permanent=True)  # Delete knowledge/.
```

Every selected component owns `<project>/<directory>/records/<id>.json`.
Its settings are stored only in ProjectConfig.parameters.components. Creation and `set_components` create missing directories.
Base initialization is idempotent. Permanent removal requires detached Sessions and
publishes disabled selection first; an I/O failure can leave partial data for retry.

`projects.component` returns a locked handle that rechecks Project state/selection
on every call. In async UI code, use `StorageIO(projects.ownership).run` for its
synchronous CRUD. Direct component methods require a workspace ownership scope.
JSON must have string keys and JSON-compatible values; live SDK objects remain
runtime-only. There is no application key-name filter. Agent/Workflow components own
definitions; GraphEngine interprets the supported Workflow schema only when explicitly selected.

ToolComponent additionally stores native function-tool definitions in
`tools/records/<tool-name>.json`. Create one with the usual LiteLLM `type/function`
dict. Reading, editing and cloning definitions needs no registered handler;
execution binds the same name to an application handler. Extra provider keys such as
`function.strict` survive persistence and appear in the LoopEngine tools argument.
The enabled list still controls availability. Disable a tool before deleting its
override. Existing enabled-name-only configuration keeps working.

RunManager still accepts `capabilities=components`. Tool-specific resolution now
lives in `llm.components.tools.resolver.ComponentToolResolver`; for direct access,
use `ComponentToolResolver(components).resolve_tools(project)`. Generic components
declare `capabilities = ("retriever",)` and implement
`resolve(project, capability_name)`. The registry calls only components declaring
the requested capability, and each component builds only that requested value.
See [component API and persistence details](../../llm/components/README.md).

SessionManager is bound to authoritative ProjectAccess when constructed with
ProjectManager. A standalone SessionManager must instead receive `project_access=`.
ConversationStore creation is injectable through `conversations=`, and
ConversationContextBuilder centralizes Run context and clone ordering. RunManager
defaults to the same factory/builder as SessionManager. For UI-to-storage separation,
configure these services once in the application's composition code.

## Session-bound execution and Run notifications

Each RunManager requires one Session at construction. Share ProjectRepository,
SessionManager and registries across managers to retain workspace coordination;
create a separate RunManager for each Session. A manager cannot switch Sessions.

```python
from llm.services.runtime.runs import RunManager, RunRequestError

# session was created with sessions.create(project, "Conversation").
def on_run_event(event):
    print(event.type, event.run.id, event.run.status, event.run.error_code)

manager = RunManager(sessions, engines, session=session,
                     capabilities=components, on_run_event=on_run_event)
try:
    await manager.submit("Explain this workspace", engine="loop")
    await manager.wait_idle()
except RunRequestError as error:
    print(error.code, str(error))
finally:
    await manager.shutdown()
```

`start()`, `submit(text, engine="loop")`, `wait_idle()`, `interrupt()` and `shutdown()` operate on
that bound Session; the old Project/Session positional arguments are removed.
shutdown interrupts only that Session, preserves its queued messages, drains writes
and releases its attachment. Other managers continue. Create a new manager for
the same Session and call start() to recover/resume queued requests after shutdown.

Missing Engine or component registrations raise RunRequestError **before queue
admission**, without creating messages/Runs or attaching a worker. Accepted input
is still fsynced as QUEUED before runtime scheduling. Restored queued requests
whose Engine is no longer registered become failed Runs without an Engine call.

`on_event(run, engine_event)` remains the streaming/Step observer. The separate
`on_run_event(event)` observes STARTED, COMPLETED, FAILED and INTERRUPTED, after
the corresponding durable state writes. Callbacks run synchronously on the event
loop, receive detached snapshots, and cannot fail execution by raising. Recovery
also emits INTERRUPTED for newly recovered stale Runs. These are in-process
notifications, not a durable event-subscription log; query Runs after reconnect.

Run.error_code contains a stable machine-readable string: engine_not_registered,
component_not_registered (request rejection), capability_failed, engine_failed,
interrupted or process_restart. Run.error contains the exception detail for a
failed execution; there is no exception-message masking. Error/code are also in
ExecutionResult query views. wait_idle means the queue drained, not that every Run
succeeded: use the terminal notification or query the Run status. Storage failure
that prevents finalization propagates through wait_idle/shutdown; no terminal
notification is emitted before a successful terminal save.

## Delete and restore

After `await manager.shutdown()`, use the lifecycle APIs:

```python
sessions.delete(session)                         # reversible, history retained
sessions.restore(session)
projects.delete(project)                   # reversible, Sessions retained
projects.restore(project)

sessions.delete(session, permanent=True)         # removes Session + history/Runs/Steps/files
projects.delete(project, permanent=True)   # removes Project + all its Sessions/files
```

`soft_delete` was renamed to `delete`. `permanent` is keyword-only and defaults
to `False`. Permanent deletion cannot be restored through `restore`. Active
persisted Runs and runtimes attached through the shared SessionManager block
deletion, including idle workers and queued input. Paths and ownership are
checked before removal; linked paths/descendants are rejected. This assumes
exclusive filesystem ownership; it is not protection against concurrent writers.

## Package boundaries and logs

`llm/engines` contains execution strategies and the Engine contract. LoopEngine
and GraphEngine are implemented; SingleEngine remains planned. Reusable Tool and
ToolRegistry and Project tool selection live in `llm/components/tools`.
Skill/MCP/RAG/Agent/Workflow components manage validated JSON definitions;
The unified RAG component owns document
CRUD, embedding, triple extraction, vector/graph indexes and combined retrieval.
MCP connections and Skill execution adapters remain separate future work. Provider
stream transport lives in `llm/providers/litellm.py`. SessionRuntime lives in
`llm/services/lifecycle/sessions.py` and is owned and scheduled by RunManager.

Services write structured operational JSON lines through Python `logging` and
`RotatingFileHandler`. Each Project, Session, Run, and Step owns
`logs/service.log`, with up to three 1 MiB backups. Conversation operations and
runtime scheduling log to Session; Run/Step lifecycle operations log to their
respective domains. Model observations use the existing Run persistence path and
Run logs. SDK authentication and diagnostics are managed by the provider library.

Only event names, IDs, statuses, counts, and deletion flags are recorded;
prompts, answers, titles, arbitrary metadata and references are
excluded. Log files are separate from durable conversation JSONL. Handlers are
closed after writes, and log I/O failures emit an operational warning. Operational
logs are best effort, not a transactional audit log. Permanent Session deletion
leaves its final deletion record in Project logs; permanent Project deletion
leaves it in the projects root's `logs/service.log`.

See [architecture](architecture.md) for boundaries and
[handoff](handoff.md) for implemented scope and next steps. See the
[production readiness assessment](production-readiness.md) before deployment.


실행 자원 제한, 출력 묶음 저장/인덱스, Project 백업/복원과 저장 버전 정책은
[실행 자원과 저장 정책](operational-storage.md)을 참고하세요.
