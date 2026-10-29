# Hub application creation profile and Agent APIs

## Clarifying user decisions

The shared `asset/prompts/work-context.md` guidance asks agents to clarify material
choices with `ask_user`, consult prior answers and pending interactions, and avoid
repeating a question unless new evidence changes the decision. Tool-accessible
facts remain the agent's responsibility. This guidance reaches existing agents
through the request-owned environment processor; saved custom agent prompts and
approval policies are not overwritten. It adds no inference call or completion
gate. Prompt delivery is tested; model compliance is not a deterministic guarantee.

`hub/config/defaults.py::HubDefaults` is the application creation-profile entry
point. It composes global profile/appearance/general defaults from their validated
setting owners and declares the model-independent Component and Engine choices.
`backend/project/defaults.py` only applies this profile through public Project APIs
when creating a Project. Explicit values, including null/false/empty selections,
win. Opening an existing Project never reapplies defaults. Backend configuration
schemas, validators, execution policies and storage remain owned by llm.

No model name, credential or endpoint is supplied by this profile. Users still
choose their Loop Agent model and connection, RAG embedding/reranker/extraction
models, MCP servers and custom workflow definitions. Defaults do not install an
OCR executable or make a remote service available. New Projects require RAG
relation extraction, so document registration needs an extraction model or an
explicit choice to disable extraction. These choices can all be captured in a preset.

The Agent owns `completion`, while its Engine owns execution limits and policies.
A Graph Agent chooses a saved Workflow; model-using nodes reference separately
configured Loop Agents. The default `assistant` receives the selected built-in
tools, not an invented model or connection. When RAG is selected on the new Project,
the Hub-created `assistant` also stores `resources.rag: true`, exposing the backend's
`rag_search` tool. This does not force retrieval on every turn or configure embedding
and reranking models. Explicit Agent definitions and existing saved selections are
not rewritten. New Projects also receive a `planner`
Graph Agent and `hub-code-workflow`: inspect → implement → verify → report. Its
Agent nodes delegate to the initial behavioral Agent (`assistant` normally), so
the user configures that Agent's model/connection once. The Graph Agent contains
only execution selection and descriptions, with no duplicate completion/tool settings.
Creation skips these resources when Workflows or Graph Agent-node execution is
unavailable, or when the explicit primary Agent is workflow-based, named `planner`,
or uses an Engine outside the registered Agent-node handler's execution registry.
Existing Projects are never reseeded, and saved edits/deletions are retained.
Template version 9 shortens the default system/work-context instructions and optional
work-cycle guidance. The basic template stays below 2,400 characters; protocol additions
for suggestions and completion reports are shortened too. Risk/discovery guidance is
included only when the exposed tools support it. It preserves action delivery, approval, clarification, evidence,
source paging and component ownership rules; detailed procedures remain in on-demand
skills and tool schemas. Existing saved Agent system prompts are not overwritten.
To shorten one of those Agents, explicitly replace its system_prompt in `/agent`
with the current template (system.md plus work-context.md), preserving user additions.
Instructions live under `asset/prompts/coding-workflow/`; the public WorkflowGraph
builder in `asset/coding_workflow.py` produces editable schema-version-1 data.

| Capability | Ready from the Hub creation profile | User/host prerequisite |
|---|---|---|
| Files, shell, Skills, Goals | Tools, starter skills, active-goal context | Agent model/connection; normal approvals and working-root permissions |
| Large Tool output | Data-backed command originals, 2,000-character stream previews, explicit page policies | Selected Data and built-in Tools; disk space, normal approvals |
| Memory | Keyword recall, candidate writes | Model-based summarization/extraction additionally need their own completion settings |
| Refinement | Evidence/proposal tools and approved application | Actual evidence, target revisions, approval; evaluation when explicitly required |
| RAG | Hybrid search, chunk/index limits, required relation extraction | Embedding model; reranker or explicit rerank=false; extraction model or explicit failure_policy=disabled |
| MCP | Component and Agent resource integration | Reachable server definitions and their own authentication/dependencies |
| Graph | Engine, `planner` and staged coding Workflow | Initial behavioral Agent's model/connection, selected tools and project toolchains |
| Image OCR | Tesseract selection and timeout | Installed executable and required language data |

RAG uses the backend's automatic JSON mode, suggested relation types and one
structured-output repair attempt when extraction is enabled. Host extractors
that only implement `extract(chunks)` retain their own protocol; model-specific
options are defaulted only when the public extraction-configuration capability
is supported. Explicit user configuration still goes through backend validation.
RAG and Memory processing receive separate copies of the Hub provider retry
template (four attempts, 10-second initial and 60-second maximum delay); neither
inherits a Loop caller's runtime policy. These are new-Project defaults only.

New Projects automatically approve approval-targeted Tools only when Hub's model
risk classification is known and at most 20 (`low`). `HubDefaults.APPROVAL_MAX_RISK`
owns this threshold; the backend's ordinary `ApprovalPolicy` enforces it. Unknown
scores, scores above 20, different classification schemes and uncertain replay
still require approval. An explicitly supplied approval policy (including an empty
or disabled policy) is preserved. Reopening a Project never changes its policy.
For live pauses Hub's `AutomaticApprovals` continues only durable policy-approved
interactions that the public resume plan permits, retaining the checkpoint and
partial output. It never invents decisions or `retry_nodes`, never replays old
pauses on startup, and skips denied/cancelled/uncertain interactions. Restart and
shutdown cancel pending continuation scheduling.
Input-form observations read requests and validated response receipts under the
same backend ownership scope. Fully answered requests leave the form immediately,
including while their continuation is queued. Outstanding requests still use the
backend's pending-interaction query for expiry, cancellation and resume eligibility;
Hub never infers permission or suppresses lookup failures.

`ToolDataSetup` owns only creation choices and explicit-save preparation for the
default output package. `HubBuiltinTools` binds lifecycle handles through the public
runtime capability API; the backend owns captures, retained references and range
reads. The default read grant permits OS-readable external paths and symlink
targets, independently of write/cwd and sandbox permissions. Explicit grants and
constraint maps (including empty maps) are preserved. Existing projects are never
reseeded. See [large-data setup](large-data-tools.md) for the activation fields,
page-mode restrictions and storage limits.

## Named JSON presets

Use the profile dropdown's **Export current settings**, enter a name, and save.
The configured editor is optional for export and available for authoring. Files
live at `<launch workspace>/.hub/presets/<name>.json`; the filename stem is the
display name. Selection alone lives in `.hub/preset-selection.json`. The old
aggregate `presets.json` is not read or implicitly migrated.

Each JSON has `version`, `global`, and `project`. Globals contain profile,
appearance and general settings. Project contains config, selected components,
conversation storage and optional `agents` (a name-to-definition map). Export
includes public Agent definitions and their explicit connection overrides; the
internal title Agent/Engine is excluded. Model names, API keys and addresses are
preserved exactly if the user supplied them. Generated preset files have owner-only
permissions and atomic replacement. A per-directory lock prevents concurrent
duplicate exports. Names cannot escape the directory; symlink files are rejected.

Applying validates before saving, respects host-fixed values and active-work
guards, and reports partial failures. Agent updates use revision-checked public
APIs and preserve unrelated Agents. It does not copy sessions, history or Component
data (for example a referenced Workflow must also exist at the destination).
First creation in an empty root can use the selected preset; explicit host Agent
definitions still win. Existing roots are never silently overwritten on startup.

## Summary-model setup

Saving `parameters.components.memory.config.processing.completion.model` through
Hub settings (including preset application) fills missing summary prerequisites.
The summary model owns its API key/base; Hub never borrows the Agent connection.
Existing explicit values, including `summarize: false`, remain authoritative.
Simply opening an existing project does not materialize these defaults.

`HubDefaults.MEMORY_SUMMARY` declares the application choices: keep one recent
turn and one active Tool iteration, start older-history/active-exchange summarization
at 24,000 characters, summarize Tool
results above 8,000 characters with an 18,000-character per-call source budget,
4,000-character summary cap and at most 64 summary calls. A missing summary cap is
bounded to half an explicitly configured context character limit, leaving room
for provenance and recalled facts (the default recall context of 4,000 characters
therefore selects a 2,000-character summary). These are character
bounds, not token limits. Model calls incur additional usage. Existing processing
provider policies remain independent from the Agent/Loop caller.

Hub registers Data and, when this summary configuration is saved, enables that
dependency and creates `hub_tool_sources` through public ComponentData APIs.
Memory owns source references, chunk cursors and summary persistence. A custom
Tool-data destination is preserved and remains the user's responsibility. An
explicit `compress_tools` preview mode is not combined with summary mode.
The original result is preserved if summary processing fails or exhausts its
budget. With summary mode and the `memory_tool_result` Tool selected, Hub replaces
an oversized remaining model-visible Tool body with its backend-issued Run/Step
reference. The model can read bounded ranges instead of resending the full output
or repeating the command. This is explicitly an incomplete summary; it does not
claim that unread data was analyzed. New default Agents select this Run-bound reader;
existing restricted Agent selections are never expanded automatically. Without
the reader or a durable reference, the original is retained and backend context
guards remain authoritative. Giant user inputs or
Tool definitions can still exceed the configured token limit. Failed/partial
summary requests retain the backend's explicit-resume requirements.

Active compaction is separate from individual Tool-result summarization: many
results below `tool_result_chars` can still overflow a request when accumulated.
Hub now supplies missing `compact_active: true` / `active_keep_iterations: 1`
on explicit summary-model save. This uses Memory's public processing contract;
completed Tool receipts, original history and checkpoint ownership are unchanged.
Existing explicit `keep_turns`, `summary_chars`, false flags and token budgets
are never silently overwritten. Existing projects should save their Memory
settings again to fill missing fields. If they retain older explicit limits,
review `keep_turns`, `compact_active`, `active_keep_iterations`, `summary_chars`
and `context_chars`. A summary must fit with its provenance in `context_chars`.

An HTTP 400 context error is evidence of the provider's rejected total input/output
budget, not proof that Memory never ran. Check summary Step failures (connection,
format or budget), recent/current inputs, Tool definitions, and Agent output reserve.
The on-premises provider's exact token limit must be configured by its owner; Hub
does not invent a model context size or silently truncate current instructions.

Preflight order is important: Loop calls the Component processing pipeline before
the completion policy and provider. Hub first projects native character-based
`file_read` and retained process results into bounded model views. Small returned
bodies stay inline; complete small command results omit storage-only metadata.
Partial results contain visible text and a schema-checked `next_read` call using
`file_read` or `process_output`, rather than another Memory reference. The budget
includes the JSON envelope, escaping and continuation metadata, so a nominal
8,000-character source page may display fewer characters. Its next cursor starts
at the end of the **displayed** text; no omitted characters are skipped. Both
process streams keep independent cursors, remaining counts and capture-loss flags.
Existing preview limits remain authoritative: Hub does not fetch additional bytes
or execute Tools during projection. Large ordinary files follow the same bounded
page path; file hashing/indexing may still scan the source on the backend.

The pre-Memory processor is installed through Hub's Engine context binding,
before the selected processors, including explicit negative Memory priorities.
It modifies detached completion bodies only. Durable results, Step references,
Tool arguments, approvals and checkpoints remain backend-owned. Reader hints are
checked against selected definitions and Run constraints; no hidden Tool is enabled.
Unrecognized/line-based results retain their existing processing path. Large
paths or metadata that cannot fit with a usable page also retain that path.
Memory summarizes other oversized Tool results in
bounded fragments, and then compacts older completed Tool exchanges. The summary
payload budget includes the previous summary and JSON envelope; the model's system
instruction and output reserve are additional. A long result is not blindly sent
as independent Agent requests: summary fragments feed one derived reference while
the original Tool result remains stored. Incomplete summaries do not replace the
original. Current user instructions, Tool definitions, retained recent exchanges
and provider-side chat templates can still exceed a token budget. The configured
counter and server limit must agree; character thresholds cannot prove that an
arbitrary private model will accept the assembled request.

## Application tools and Session peers

The default Agent advertises `hub_help`, `hub_participants`,
`hub_agent_message`, `hub_session_rename`, `hub_preset_export`, and
`hub_task_result`, alongside the existing Component tools. Existing Agent tool
allowlists are not expanded on reopen: select the tools explicitly for those Agents.
`hub/asset/prompts/application.md` is shipped with the plugin and returned by the
help tool. It describes supported APIs, UI entry points, source responsibilities
and limits. No arbitrary Python evaluation or backend-file editing API is exposed.

Handlers resolve the calling Tool's Project, Session and Run, never the Project
currently highlighted by the user. Peer messages target another invited Agent in
the same Session and use `session.run.submit`, preserving durable queue admission
and the captured shell context. They are visible ordinary requests, not hidden
messages or synchronous waits. The sender is recorded in request context and in
the request text. Replies can be queued the same way; automatic chains stop after
four hops. Write actions still pass through the backend's Tool approval policy.
For synchronous bounded delegation, the existing `agent_run` retains the parent
Run's scope and checkpoint ownership.

## Completion conditions

New Projects using the standard Hub Loop activate `policy.final_review` with
three review attempts, two continuations, a five-second hook timeout and a
2,000-character feedback bound. Explicit settings override this profile. Custom
Engine registrations retain their own execution policy; enable final review
explicitly when appropriate. The title Engine is not reviewed.

The model first decides whether a request needs external work or a conversational
answer. Greetings, explanations and chat-only drafts do not require a completion
report or an inventory of Goals, Skills and Refinement. `hub_task_result` is optional
for work that benefits from explicit evidence. If used, a completed report requires
actual call IDs in the current Loop execution's uncompacted transcript. Fabricated
evidence causes bounded continuation without repeating effects. A missing report
alone does not reject an answer. An empty final answer (including reasoning-only or
suggestion-only output) requests an ordinary answer using already completed results.
If this budget is exhausted, the UI retains provider-visible thoughts and explains
the final-answer failure instead of displaying only a completed badge.

Every cited call must have an actual result, including citations in information
and blocked reports. Duplicate citations are rejected. A report must also follow
the latest tool result: additional work invalidates a stale report and requests a
fresh verification/report without repeating effects. The review revision is
`hub-task-evidence-v3`.

`asset/prompts/work-cycle.md` is transient completion context after actual Tool use,
when the report tool is available. It explains active-goal reuse, candidate Memory, evidence-based
Refinement, and checking saved files/diffs/test results before claiming completion.
This also reaches existing Hub Loop projects without overwriting their saved
prompts, skills or Agent tool selections. It does not silently enable final review
in an existing Project that has disabled or omitted that policy.

This is an evidence gate and model self-assessment, not proof that arbitrary code
is correct. Relevant tests and actual tool results remain necessary. It never
grants approval, retries uncertain effects, or resets backend budgets. The backend
owns candidate visibility, review receipts, cancellation and resume behavior.

## Quiet post-response records

The ish installer and standalone Hub application enable `HubConfig.postprocess`.
Embedded runtimes opt in explicitly with `postprocess=True`; `None` leaves this
application composition choice unset. No stored preference schema or old records
are migrated. Ordinary answers finish first. Standalone greetings/thanks and
failed, interrupted or paused requests do not start bookkeeping. Automatic title
generation also waits for a substantive exchange.

`BackgroundRecords` creates a hidden Session and a tool-free `_hub_postprocess`
Agent/Engine job through public backend APIs. It uses the source Agent's chosen
model/connection, with independent auxiliary request parameters and no inherited
large output reserve. This adds a bounded model call and normal backend usage
accounting after substantive answers. Graph Agents without their own model are
skipped. Inputs are bounded excerpts, not a second copy of the complete transcript.

The resulting plan can add, edit and delete internal Memory/Goal records, or
create an unapplied Refinement proposal for the captured Agent. New memories use
the configured tool-write status; existing candidate and confirmed records are
eligible for editing/deletion. New Goals record authorized work, never an invented
task. Deletion uses public component APIs (Memory retains recoverable history).
Component policies are checked again before applying. Existing records use
revision/generation or version checks; a conflict does not overwrite newer data.
The model cannot select another Project, execute Tools, change uncaptured records,
create arbitrary work or apply Refinement. Partial successful writes
remain valid records if a later independent write conflicts; errors are logged.

Only live completion events schedule work, once per observed Run. There is one
model job at a time, with a 60-second job deadline and bounded deduplication history.
A newer foreground Run cancels older work for that Session. Shutdown/restart
cancels jobs, and historical Runs are not replayed after restart. Internal job
events do not change the conversation's status, notifications or render tick.
Failures remain in Project operational logs as `hub.background.failed`; they do
not turn the user's successful answer into a failure. Internal Sessions are
soft-deleted after cleanup; backend storage retains its ownership and evidence.

Hub Goal writing Tools are approval-free internal record operations, while their
effect classification, Session scope and backend receipts remain intact. Native
Memory create/update/delete Tools already require no approval; Hub retains their
write-status policy and concurrency checks. Refinement application and external
file/shell effects keep the foreground approval-policy flow. Important work
needed to answer the current request still runs in the foreground.

## Setting and component guidance

`backend/project/settings_help.py` maps scoped, declared public schema paths to
localized display descriptions. Project settings and Agent forms share this
presentation layer. Known terse descriptions are expanded with purpose, units,
prerequisites and consequences; unknown extension descriptions remain unchanged.
Descriptions never change required fields, ranges, enums, defaults, missing/null
semantics or backend validation. Tests check every guidance path against current
public Project and Loop/Graph Agent schemas and compare all non-description data.

Each component manager has its own Korean/English Markdown help, including
import shape, setup, actual use and limitations. The short import-field hints
remain short; the extra explanation lives in the help reader.

## Shared UI surfaces

HeaderBar owns its restart/minimize HeaderButtons; their backgrounds match the
header in both pages. `appearance.comment2` defaults to `#3A3A3A` and colors the
progress rail; the handle retains its existing accent. This newly declared color
is optional in the current stored appearance schema; all pre-existing required
fields remain required and unknown fields remain invalid.

Dropdown menus reuse Prompt-Toolkit completion selectors for the background and
current row, with no frame or radio marks. Enter/Space, Escape, keyboard navigation
and click-to-commit retain the existing dropdown semantics.

Validation: `tests/hub/test_application_defaults.py`, `test_presets_widgets.py`,
`test_header_actions.py`, `test_input_contract.py`, and `test_creation_contracts.py`.

## Validation record — 2026-10-26

Linux Python 3.12.14 was used with a normalized, hash-checked source snapshot on
the WSL Linux filesystem. Tests and reports remain outside the deployment roots.

| Execution | Result |
|---|---|
| Full Hub discovery and execution | 616 executed; 605 passed initially, 11 failures/errors recorded |
| Hub affected-area recheck | 71/71 passed; covers all 11 initial failures |
| Final preset composition recheck | 28/28 passed after the optional-color export adjustment |
| Full backend discovery and execution | 2,510 executed; 2,509 passed initially, one appearance-schema expectation failed |
| Backend contract recheck | 26/26 passed after testing the declared optional color while retaining required-field/legacy-key rejection |
| Environment, documentation, architecture S1–S7 | Passed |
| Contract coverage | 647 product files, 15,424 candidates; no stale, unclassified, incomplete or outstanding entries |

Every discovered Hub and backend test has passing evidence, with no unresolved
failure, skip or missing execution. The initial full runs are retained as failed
runs; the rechecks are separate runs, not rewritten full-run results. The header,
instruction, color and scripted-provider expectations were updated for the new
contracts; provider-only streaming tests explicitly disable final review, while
the application test exercises review, approval, actual file creation and resume.
The factory type-check correction and final preset overlay are also rechecked.

Commands use `python -m tests.llm.run_regression --suite hub` / `--suite llm`,
explicit `--modules` for the rechecks, `check_environment`, `check_docs`,
`check_architecture --require S1 ... --require S7`, and
`check_contract_coverage --require-complete`. Exact commands, original results,
test IDs and source hashes are in the local ignored
`tests/hub/reports/application-defaults/` directory. Provider responses are
controlled fixtures/local HTTP; these results do not certify a remote model's
instruction following. X11 uses a private display. No product file in `llm/`
was modified.

## Defaults and guidance verification — 2026-10-27

The current update adds scoped friendly descriptions, independent Component retry
defaults, and the fresh-evidence work cycle described above. WSL Linux Python
3.12.14 runs use normalized source snapshots on the Linux filesystem. The initial
focused run exposed an unsupported default for a host-supplied extractor and a
wrong help-key suffix; both were corrected. Agent UI snapshots now wait for PTK's
next render after an editor change. Component-manager startup tests allow ten
seconds for backend connection (a measured normal startup was 3.56 seconds), while
ordinary UI waits retain their three-second bound. All 46 affected-area tests pass,
including the actual public Goal/Memory/Refinement/Tool execution paths with
deterministic model responses. Existing user configuration remains authoritative.

With explicit user permission, a supplied credential was passed to a temporary
WSL process for live synthetic tests. Credentials were not put in source, reports,
presets or persistent Hub workspaces. Temporary data was removed after each run.

| Live check | Result and scope |
|---|---|
| RAG Markdown ingestion and search | Passed: 163-character synthetic document, `gemini-embedding-001`, hybrid retrieval; registration finished and the expected document was returned (38.8 seconds total). Reranking and relation extraction were disabled for this smoke test. |
| Graph Agent with a saved Workflow | Passed: `gemini-3.8-flash` Loop Agent inside a Graph Agent Workflow, completed Agent/LLM/end-node Steps and the expected `LANTERN-42` response (115.37 seconds including preparation). |

The first Graph setup incorrectly supplied `tools` on a Graph Agent and was
rejected by the public schema; the test definition was corrected. A subsequent
attempt with `gemini-2.5-flash` failed with provider HTTP 404 saying the model was
no longer available to new users of that account, despite its presence in model
discovery. Step diagnostics exposed that precise cause. The successful test used
the replacement model explicitly; Hub does not silently switch user models or
persist that model as a default. These smoke tests verify the named paths, not
all providers, all Workflow structures or arbitrary model instruction following.

Local ignored evidence is under `tests/hub/reports/defaults-guidance/`: original
focused/full-run output, final affected-area checks, public schema inventory,
redacted live results, contract gates and source-integrity checks. Original failed
runs are retained separately from corrected rechecks.

The full Hub run executed 635 tests: 631 passed initially, and the four UI
timing/render synchronization failures passed after correction in the 46-test
affected-area run. The final unframed component-button change passed all 17
popup/widget tests. Architecture/documentation/contract tests passed (44 tests),
and static contract gates report no unresolved issues. The full backend run was
stopped at the user's request because this task did not edit `llm/`; it is not
reported as a completed backend-suite pass. Pre-existing backend changes are
included unchanged in the explicitly authorized publication.
