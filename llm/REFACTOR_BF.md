# Agent backend refactor: stage evidence

The specification is `CODEX_ish_llm_backend_worker_verified.md`. A's implementation
and historical evidence remain in [providers/REFACTOR_A.md](providers/REFACTOR_A.md).
Changes use Codex's `work` checkout and push to `refactor/llm-backend`; `main`,
`hub/`, and `tests/hub/` are unchanged. `ish.platform/` is ignored external source.

## Reproducible environment and host correction

Python is **3.12.14**, host `E1der0wL/ish` is pinned at
`d833790959a76ac457fdeea3f21a6bea1eecfe12`; imports use `ish.platform/src/ish`.
LiteLLM 1.103.1 / OpenAI 2.54.0 preserve A's verified dependency baseline.
Commands run from the repository with:

```sh
export TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION
export HUB_TEST_XVFB=/workspace/.setup/x11/root/usr/bin/Xvfb
export HUB_TEST_XCLIP=/workspace/.setup/x11/root/usr/bin/xclip
.venv-linux312/bin/python -m unittest -v tests.llm.test_plugin tests.llm.test_tool_packages tests.hub.test_ish_integration
.venv-linux312/bin/python -m unittest discover -v -s tests/llm -t .
.venv-linux312/bin/python -m unittest discover -v -s tests/hub -t .
```

Bubblewrap-dependent tests require permitted execution outside the cloud command
sandbox (the sandbox prevents UID mapping); tool isolation is never disabled.
Xvfb `2:21.1.16-1.3+deb13u4` and xclip `0.13-4` were downloaded from authenticated
Debian trixie/trixie-security repositories and extracted locally without system
modifications. The original test launches its private Xvfb using those supported
environment variables. No assertion or Skip condition was changed.

Compatibility commit: `044c5b82c90302ca8934c74bcdda1c91995f9a3d`, pushed.
`tests/llm/test_plugin.py` changed only `_library_available` to
`_is_library_available`. The host implementation and rename history were checked:
`import_parents=False` still delegates to `has_import`, without importing the
plugin library in the host process.

| Check | Before correction | After correction |
|---|---|---|
| Exact real-platform loader test | 1 failure, exit 1: subprocess AttributeError | Included in 44 passing targeted tests |
| Three targeted modules | Historical 43 pass, 1 fail, 0 Skip | 44 pass, 0 fail/Skip, exit 0, 18.455 s |
| Full LLM | Historical 1,657 pass, 1 fail, 0 Skip | 1,658 pass, 0 fail/Skip, exit 0, 338.932 s |
| Full Hub | Historical 525 pass, 9 X11 Skip | 534 pass, 0 fail/Skip, exit 0, 483.787 s |
| X11 module alone | 9 Skip | 9 pass, exit 0, 2.081 s |

Original host evidence is preserved in `/workspace/host-validation-evidence/`.
Correction logs, command exit codes, package hashes and push output are under
`/workspace/refactor-bf-evidence/compat/`. These are observations from actual
executions; historical failures have not been relabelled as successes.

## B — independent Project Agent domain

`core/agents.py` owns the JSON definition schema, IDs and content revision.
`services/lifecycle/agents.py` owns `AgentRepository` and lifecycle-checked
`ProjectAgents`; `ProjectHandle.agents` exposes sync/async CRUD, snapshot, prompt
editing and mandatory-CAS `revise`. Only `engine` is required. New domain writes
validate the registered Engine ID; Engine-specific runtime prerequisites remain
the selected Engine's responsibility. Legacy offline Component editors retain
their earlier ability to edit definitions for separate Graph Engine registries;
execution still validates the actual selected registry and factory.

There is one catalogue at the existing `agents/records/<id>.json` path. New writes
use `{storage_version: 1, id, definition, revision}`. Explicit legacy raw-definition
reads validate but do not rewrite files. Invalid versions, mismatched IDs or
revisions, linked paths and unsupported old `component.json` storage are rejected
before overwrite. Revision bytes use the original sorted JSON hashing convention,
preserving nested Agent checkpoint fingerprints. Agent execution state remains in
Run/Step records; Session/Message/Run schemas are unchanged in B.

The compatibility `AgentComponent` and `AgentData` delegate to that catalogue;
registration/selection is unnecessary for `project.agents`. Their existing
Component input schema continues to require `purpose` for legacy writes, as
required by the unchanged Hub public-API/approval tests. This is an explicit
legacy input adapter, not a second domain schema or catalogue. Both facades can
read new minimal definitions; engine-only creation/editing uses `project.agents`.
Legacy selection checks and domain changes share the same ownership transaction.
Component removal
cannot delete Project-owned definitions. Project clone copies the catalogue once,
including disabled definitions; backup validation always checks it. Deleting a
referenced Agent is refused for saved Workflows, attached Sessions and historical
Run/Step evidence. Agent resource references protect Skill files as well as entire
Skill/Prompt/Tool/MCP records, even without an active AgentComponent.

Graph AgentNode and `agent_run` still use the shared `AgentExecution` resolver,
parent Run/Step identity, scopes and checkpoint validation. That resolver obtains
the independent catalogue through the existing capability interface and validates
the pure domain schema. Refinement targets use the same domain API with the
existing approval, CAS and rollback requirements. Skill impact reports no longer
require an AgentComponent registration.

The real pre-B golden archive in `tests/llm/fixtures/agent_v1/` was generated by
unchanged production code at `7984c8e225ca07711c866607d110727dfbb3c391`; its producer,
IDs and every file hash are retained. It includes a raw Agent, completed engine-only
Run, and durable queued request. The earlier A Loop/Graph checkpoint and approval
fixtures are untouched.

Verification before full suites:

- Existing Agent/Graph/Skill/Refinement regression: 89 tests passed, exit 0.
- Expanded domain/Agent/Graph/Skill/Refinement/legacy/storage regression: 136 tests
  passed, exit 0 (before the final additional execution-binding test).
- Final domain module: 13 tests passed, exit 0, 1.927 s. It covers Component-free
  minimal CRUD, invalid Engine, CAS contention, references, clone/backup/restore,
  stale handles, envelope rejection, rollback, approval, frozen active bindings,
  changed paused bindings, and actual legacy queue execution.
- Agent write crash test reaches three distinct failpoints in dedicated child
  processes: before write, after file write before transaction commit, and after
  the durable COMMITTED marker. A new Backend recovers old/old/new definitions
  respectively and checks their revisions. Exit codes and marker files prove
  each boundary was reached. The transaction's COMMITTED marker is the commit
  point; atomic file replacement alone does not acknowledge a new definition.
- Initial new-test run had two test-harness errors from calling the response
  property as a function; the tests now use `aresponse()`. The failure log is
  retained. No product assertion was removed to fix those errors.
- Provider reference audit: 48 files / 408 matching lines / 0 unclassified. The
  moved `max_tool_calls` schema is classified as Agent policy, preserving A's
  historical audit report rather than rewriting its baseline.

The first full B LLM run executed 1,671 tests in 341.928 s: 1,670 passed, one error,
zero Skip, exit 1. The existing external-Component hook test supplies only
`paths.root`; the new catalogue initially assumed `paths.agents`. The repository
now derives its owned suffix from the original minimal root contract. The exact
unchanged test failed independently before correction and passed afterward.

The first full Hub run executed 534 tests in 484.480 s: 533 passed, one failure,
zero Skip, exit 1. Its existing assertion requires legacy Component creation to
reject a missing purpose. The compatibility input adapter above preserves that
contract while the independent domain accepts an engine-only definition. Hub
source/tests and that assertion are unchanged. This first Hub diagnostic run
overlapped later corrections and is not the final frozen-source gate.

During final test additions, a nested Workflow fixture omitted the required
`on_limit` field: 17 tests passed / 1 test error in the 18-test focused run. Adding
the field fixed the fixture, without weakening its reference assertions. The
repeated focused run passed all 18 tests. Two prematurely started full LLM reruns
were cancelled with SIGINT (exit 130) before completion while these corrections
were made; `b-full-llm-final` and `b-full-llm-verified` are **incomplete**, despite
their filenames, and do not count as passing verification.

Final B LLM regression passed **1,671 tests**, zero failures/Skip, exit 0,
340.830 s (`b-gate-llm`). Final Hub regression passed **534 tests**, zero
failures/Skip, exit 0, 483.622 s (`b-gate-hub`), including all nine X11 checks.
Both suites used the frozen source and exact host above; the source hashes were
checked again after execution. **B is implemented and verified.** Logs and exit
files use `/workspace/refactor-bf-evidence/b-*`.

After both compatibility corrections, the combined Agent/Graph/Skill/Refinement/
legacy/storage/execution-boundary regression passed **153 tests**, exit 0,
26.455 s. Unchanged `tests.hub.test_resource_tools` and
`tests.hub.test_component_managers` passed **12 tests**, exit 0, 35.981 s, including
the previously failing legacy creation assertion. Workflow deletion protection
walks real nodes and loop bodies; Agent-shaped literals in state/metadata are
not treated as resource references. The final gate freezes SHA-256 of 315 source
and test Python files in `b-gate-source.json` and runs `b-gate-{llm,hub}`.

## Remaining stages and gates

C is complete as recorded below. D changes User Message/Run cardinality and
Session participants before enabling per-Agent concurrent execution. E reuses
that durable request contract for same-Project cross-Session requests. F adds
typed final review with candidate buffering and bounded continuation. Each stage
requires focused tests, applicable fault/concurrency boundaries, full LLM/Hub
regressions, a commit and a push before advancing.

B changes Agent persistence (F05 binding and write/CAS recovery); fan-out F01–F04,
F12 and C01–C10 require fresh D validation. F13/C11 belong to E and F14/C12 to F.
Existing approval, operation receipt and checkpoint regressions remain gates in
every suite; their earlier success does not certify future concurrency changes.

Live authenticated model calls and dedicated kernel short-write injection remain
**unverified**. The nine X11 checks are now verified, not residual Skip entries.
Environment draft saving does not establish Publish or fresh-task restoration.

## C — one Agent-bound Run pipeline

B commit: `9d37f939bb93206271e4d5cd8a66a937ad93caf0`, pushed.

`RunAgentResolver` normalizes explicit Project Agent selection and the legacy
`engine=` input into one immutable binding. RunManager retains the only durable
queue, worker, Engine event consumption and finalization path. Top-level
`AgentInvocation` reuses AgentExecution resource selection, Prompt/Skill
composition and MCP connection helpers; it does not create another Run/Step
scheduler. Nested Graph AgentNodes and `agent_run` remain parent-owned Steps.
Both paths select Engines through `EngineRegistry.resolve_agent`.

The compatibility Agent ID is derived from Engine/options content and stored in
the same Project catalogue. Its reserved `engine-request-v1` adapter preserves
old request-factory fingerprints: constructing an empty Loop `for_agent` profile
would change its checkpoint options, so the adapter explicitly retains the old
factory contract. New Agent requests use `for_agent`, or a minimal custom Engine's
public request factory when it lacks that hook. No default Engine is guessed.
Ambiguous selectors and unknown Agents fail before queue admission.

Canonical RunManager writes version-2 Runs with Agent ID, selected definition,
revision and Engine identity; the original definition and actual configuration,
resource, Tool and policy bindings are persisted before Engine invocation. Active
Runs keep their frozen binding. Queued/resumed/restarted requests validate current
revisions before effects. Prompt/Skill/Tool changes cannot silently alter an
approved continuation. MCP remote definitions are checked after connection and
before resumed Engine execution; preflight can only validate local configuration
and resource references, not remote Tool definitions unavailable before connecting.

Explicit version-1 Run decoding preserves engine-only history and source hashes
without rewriting files or fabricating identity on a plain read. The low-level
legacy Run constructor still defaults to v1; canonical new admission explicitly
uses v2. Unsupported versions, invalid binding revisions and Agent/Engine identity
mismatches are rejected. Legacy queued requests normalize during their existing
transaction; malformed requests become observable failed Runs without killing the
worker. Existing A/B golden fixtures remain byte-identical.

Agent deletion now protects queued bindings; backup and recovery also validate
new Run references against the independent catalogue. Clone retains its existing
conversation/configuration-only contract and never replays old Runs/queues.
File/memory ConversationStore tests cover the new identity path. Root Tool scope,
Agent limits, input/output schema checks and resource composition use existing
approval/budget enforcement. Resume reuses durable receipts; duplicate continuation
is refused rather than replaying a Tool effect.

Focused evidence (`/workspace/refactor-bf-evidence/c-*`):

- Broad Agent, Run API, Engine, Graph, legacy data, storage/transaction and approval
  regression: 198 pass, zero fail/Skip, exit 0, 34.468 s.
- Actual host's three requested modules plus unchanged Hub resource/component
  contracts: 56 pass, zero fail/Skip, exit 0, 59.371 s.
- Final custom factory/Engine contract regression: 25 pass, exit 0, 5.236 s.
- Final new Agent Run module: 15 pass, exit 0, 3.622 s. It proves a Tool effect is
  absent before approval and occurs once after resume; Graph resume does not
  replay its completed effect; MCP opens/closes each attempt; changed queued
  configuration blocks effects; custom Engines honor domain schemas; missing
  Agent references block archive validation and appear in recovery diagnostics.
- Dedicated admission crash processes reach pre-write, post-write/pre-commit,
  and post-COMMITTED/pre-in-memory-queue boundaries with distinct exit codes
  81/82/83 and durable marker IDs. A fresh Backend sees zero/zero/one accepted
  request respectively. The accepted request retains its original Message ID,
  creates one Run, executes once, and a second fresh Backend does not replay it.
- Initial added-test runs exposed harness mistakes (conversation list called as
  a store, approval-plan readiness expected before approval, nonexistent
  `aconfigure`, Agent ID accessed on RunHandle instead of its data). Those runs
  are failed evidence, retained in `c-agent-runs-{1,3}` and `c-admission-crash`.
  The corrected tests keep the intended assertions and pass; no unrelated tests,
  assertion strength, Skip rules or Hub files changed.

Full frozen-source LLM/Hub gates are running; this section does not yet claim C
completion or D–F verification. Authenticated models and kernel short-write
injection remain unverified.

First C full-gate result (frozen 623 Python files): **1,686 LLM tests, 1,679 pass,
7 failures, zero Skip, exit 1, 368.197 s**. The corresponding Hub gate passed all
**534 tests**, zero failures/Skip, exit 0, 511.460 s. Source hashes matched after
both completed. The failed LLM gate is retained as `c-gate-llm`; it is not a
successful C gate.

The seven failures were corrected before the next full gate:

- Missing selectors now fail at the facade before constructing a manager.
- The new binding error preserves the existing `configuration changed` wording.
- Two unchanged Loop tests detected runtime test credentials copied into the
  new configuration snapshot. Configuration and public execution bindings now
  persist SHA-256 revisions, not raw views. The new explicit-Agent test also
  proves custom Engine credentials are absent from all JSON history while
  credential changes still change the stored identity. No real credentials or
  authenticated Provider calls are used by these tests.
- Naming tests explicitly verify the added v2 `agent_id`, retaining all original
  fields/order, v1 constructor defaults, and the unchanged historical fixture.
  The production local variable no longer reintroduces a retired API symbol.
- Agent invocation closes custom Engine iterators only when they expose
  `aclose`, preserving the existing optional-close iterator contract.

Before correction, the four behavioral regression tests failed again in an
isolated run (exit 1, 0.997 s). After correction, all **27** targeted tests passed
(exit 0, 4.788 s), including every failed contract and the 16-test Agent Run module.
The next complete frozen-source gates use `c-verified-{llm,hub}`; their filenames
alone do not establish success. No further Python edits occur during those gates.

Final corrected C gate: **1,687 LLM tests passed**, zero fail/Skip, exit 0,
363.398 s; **534 Hub tests passed**, zero fail/Skip, exit 0, 512.051 s, including
all nine X11 tests. The three explicitly requested host modules passed **44 tests**,
zero fail/Skip, exit 0, 18.852 s. Provider audit remains 48 files / 409 matching
lines / zero unclassified. All 623 frozen Python hashes matched afterward.
**C is implemented and verified against these gates.** D–F and their new fan-out,
cross-Session and review fault/concurrency gates remain pending; this is not a
claim of complete A–F safety or deployment readiness.
