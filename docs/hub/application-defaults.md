# Hub application creation profile and Agent APIs

`hub/config/defaults.py::HubDefaults` is the application creation-profile entry
point. It composes global profile/appearance/general defaults from their validated
setting owners and declares the model-independent Component and Engine choices.
`backend/project/defaults.py` only applies this profile through public Project APIs
when creating a Project. Explicit values, including null/false/empty selections,
win. Opening an existing Project never reapplies defaults. Backend configuration
schemas, validators, execution policies and storage remain owned by llm.

No model name, credential or endpoint is supplied by this profile. Users still
choose their Loop model and connection, RAG embedding/reranker models, optional
extraction model, MCP servers and workflow definitions. Defaults do not install an
OCR executable or make a remote service available. RAG extraction remains disabled
until explicitly configured, so an unconfigured extractor cannot block document
registration. These choices can all be captured in a preset.

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

Before a final answer, the model reports `completed`, `information`, or `blocked`
through `hub_task_result`. Completed work requires actual call IDs in the current
Loop execution's uncompacted tool/result transcript. Missing/fabricated evidence
causes bounded continuation with corrective feedback. Explanation-only requests
may report information; concrete blockers must be disclosed. Explicit Agent
selections that exclude the report tool opt out of this application review.

This is an evidence gate and model self-assessment, not proof that arbitrary code
is correct. Relevant tests and actual tool results remain necessary. It never
grants approval, retries uncertain effects, or resets backend budgets. The backend
owns candidate visibility, review receipts, cancellation and resume behavior.

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
