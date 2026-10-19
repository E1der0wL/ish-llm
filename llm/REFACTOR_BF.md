# Agent backend refactor: stage evidence

The specification is `CODEX_ish_llm_backend_worker_verified.md`. A's implementation
and historical evidence remain in [providers/REFACTOR_A.md](providers/REFACTOR_A.md).
Changes use Codex's `work` checkout and push to `refactor/llm-backend`; `main`,
`hub/`, and `tests/hub/` are unchanged. `ish.platform/` is ignored external source.

This is a chronological evidence log. Earlier pending/failing entries describe
their execution time and remain preserved. The current consolidated state and
complete validation matrices are in [REFACTOR_FINAL.md](REFACTOR_FINAL.md).

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

The first frozen-source LLM/Hub gates below initially did not certify C
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

## D — recipient admission and one concurrent Session dispatcher

C commit: `3076192f58f5b803136b2a1bcf14a5fc9946f6fd`, pushed. D is implemented
and verified by the final completed gates recorded at the end of this section.

Session v2 adds sorted, distinct `agent_ids` and `current_run_ids`; the old singular
ID is a single-active compatibility projection. Explicit v1 reads retain original
bytes. `session.agents.attach/detach/list` (and async variants) mutate references
to the owning Project catalogue. Runtime detail writes preserve concurrent title,
metadata, and participant changes. Attached Agents and accepted/historical Run
references protect definition deletion. A detached Agent is unavailable to new
explicit submissions, without changing previously accepted recipients.

`submit(agent_id=...)`, `submit(agent_ids=(...))`, and `submit(recipients='all')`
normalize to a closed version-2 admission envelope in the original User Message.
Empty, duplicate, ambiguous, foreign, missing and unattached recipients are
rejected before ACK. `all` freezes membership at submission. An intervening
membership change causes an explicit admission conflict. The legacy `engine=`
adapter explicitly attaches its stable compatibility Agent at atomic execution
begin and uses an exclusive
FIFO lane, preserving old Session-wide sequencing through the same dispatcher.
No second Engine execution path or RequestGroup repository is introduced.

The User acceptance transaction records all bindings. Legacy exclusive requests
preserve the existing atomic group-plus-start rollback boundary. For new explicit
recipient requests, a subsequent single
transaction records the complete Run/Assistant group, `run_ids` and pending index;
partial creation rolls back. Each Run starts in its own atomic RUNNING/STREAMING/
Session-active-set transaction. A shared, hashed context snapshot is captured
before the first recipient starts, after earlier completed history becomes
available; recipient identity is frozen earlier at User ACK. This distinction
preserves queued legacy context. Streaming/queued outputs are excluded, and the
same snapshot is used by every sibling even if later recipients wait for a lane.

`RequestHandle.runs/aruns/wait_all` return separate Run handles. The old
`run/result/wait` remain single-recipient operations and reject ambiguous groups.
Only a one-recipient User retains its old `run_id` compatibility projection.
Bounded result polling validates the journal projection against Run records;
owned recovery and archive validation check complete group cardinality, bindings,
message ownership and missing references. They never invent missing siblings.

The Session dispatcher schedules different Agents concurrently; requests to one
Agent retain acceptance FIFO. `run.max_parallel_runs` optionally limits active
executions; policy changes are observed at scheduling boundaries. Completion
removes only its own active ID. Per-Run cancel interrupts that Run or cancels a
proven unstarted pending admission. Session interrupt selects current work;
shutdown preserves queued/unstarted work and waits for finalizers and quarantined
uncancellable effects before releasing ownership. Paused approval does not occupy
an execution lane. Existing Tool scopes, quota reservations, operation receipts,
checkpoint validation and nested Agent Steps remain in force.

Recovery automatically dispatches only new PENDING records with an exact group,
`execution_started=False`, matching queued Assistant, and no Step, completion or
checkpoint. Uncertain RUNNING/old PENDING records become interrupted and require
explicit continuation. The mutable pending index can be repaired from Run states;
identity/cardinality corruption cannot. Restart fingerprints exclude that mutable
index only. Explicit Resume keeps the original Agent and lane semantics, checks
current membership and binding, and does not repeat completed Tool effects.

History groups all sibling responses under their one original User. Clone remaps
conversation IDs, preserves participants within a Project (or a whole Project
clone), and strips executable admission. A cross-Project conversation-only copy
carries no executable membership. Run retention selects complete sibling groups,
charges the shared User once, and protects the entire group when any sibling or
original message is needed by a checkpoint/reference. Backup accepts only proven
unstarted pending Runs and validates every recipient/Run/Assistant reference.

The actual C-produced golden archive `tests/llm/fixtures/agent_run_v2/` records
producer SHA, script, versions, original IDs and 21 file hashes. Tests read without
rewriting, then perform explicit v2 participant writes and recover its real queued
request exactly once while preserving the interrupted Run. Original A/B fixtures
are unchanged.

Focused D evidence currently includes:

- Participant/transition/transaction checks: 34 pass; initial dispatcher/Run API/
  reliability checks: 61 pass; C contract adaptations and storage: 57 pass.
- History checks initially found an unnecessary full Run scan (69 pass, 1 fail);
  bounded lookup fixed it, and the same 70 tests passed.
- A 101-test state run found one failure and one error in manually inconsistent
  pending-state recovery. Run-state-authoritative index repair fixed the focused
  15-test rerun. Missing/duplicate group rejection remains strict.
- A 50-test policy/history run had four errors (three affected cases, one repeated
  during cleanup): policy dependency failure stopped the dispatcher, corruption
  fixture needed a valid new context envelope, and cross-Project conversation
  clone incorrectly required copied participants. These were corrected without
  deleting old assertions. A subsequent 56-test run had one diagnostic failure;
  recovery now reports invalid recipient group and invalid input separately.
- Nine schedule/quota/receipt/recovery tests passed. The mixed schedule test uses
  20 recorded orders, four Sessions, three Agents, explicit Engine Events, and
  checks Run/Message/Step ownership plus the active set after every terminal
  boundary. It reopens a fresh Backend and prints IDs/orders to the test log.
- Dedicated child processes reach User-write, COMMITTED acceptance, partial group,
  complete group, active-set start, execution binding, streaming, first completion,
  approval, approved, Tool claim, external effect and durable receipt boundaries.
  Fresh Backends prove zero automatic replay for uncertain effects and exactly
  once scheduling of proven unstarted recipients. Explicit retry still requires
  new Tool approval, and a started uncertain ledger blocks effects afterward;
  a completed receipt is reused after approval without a duplicate effect.
- Crash harness iterations accurately retain failures: `d-crash-2` had one error
  and one failure (missing explicit retry selection; wrong checkpoint filename);
  `d-crash-3` had three failed expectations because retry correctly paused for
  fresh approval. The corrected `d-crash-4` passed all three tests, including
  partial-JSON checkpoint refusal and restoration of the original valid bytes.
- The first frozen full gate used `d-gate-source.json`. Failures
  included the old runtime metrics field, the RUNNING callback Steering window,
  unnecessary final-group rereads during paging, and cloned Steering ordering.
  This gate **failed**; its results and the corrected gates are recorded below.

All D command logs and actual exit codes are in
`/workspace/refactor-bf-evidence/d-*`. Live authenticated model calls and kernel
short-write injection remain unverified; injected fsync/replace failures and
partial JSON are separate deterministic storage tests, not substitutes for them.


First frozen D gates finished with **1,706 LLM tests: 1,696 pass, 5 failures,
5 errors, 0 Skip, exit 1, 411.227 s**; **534 Hub tests: all pass, 0 Skip,
exit 0, 560.683 s**, including the nine X11 tests. All 629 frozen Python hashes
matched afterward. The five errors came from the obsolete singular runtime
metrics field. The five failures were the start-notification Steering window,
finished-group rereads during paginated queries, cloned Steering ordering,
pre-execution interruption, and the old ENOSPC begin boundary.

The corrected Dispatcher keeps per-group unfinished ID sets and queued-request
gauges, reaps finished groups without redundant repository reads, accepts Steering
at the original RUNNING callback boundary, and bridges the queue-to-first-start
interrupt window. Cloned Steering carries an explicit remapped turn origin.
Related existing and new regression: **105 pass, exit 0, 25.024 s**.
The next safety run had **102 tests, 101 pass, 1 failure, exit 1, 36.082 s**:
the ENOSPC probe showed legacy participant registration still wrote Session at
User ACK. Registration moved to atomic begin, so accepted User input survives
begin failure and no Run/effect leaks. The unmodified recovery probe now passes;
the combined recovery/mixed-schedule/C-contract rerun passed **33 tests**, exit 0, 18.533 s (`d-atomic-begin-fixed`).

Additional tests verify actual Agent timeout while a sibling Tool remains active,
simultaneous same-Agent submission FIFO, cancellation winning immediately before
the group transaction, and backup/restore dispatching only a proven unstarted
recipient while retaining its interrupted sibling. Provider audit remains
48 files / 410 matching lines / 0 unclassified (`d-provider-audit.json`).


The second frozen D run passed **1,710 LLM tests**, 0 failure/Skip, exit 0,
403.524 s; **534 Hub tests**, 0 failure/Skip, exit 0, 559.173 s; and the targeted
host modules passed **44 tests**, 0 failure/Skip, exit 0, 19.400 s. All 629 Python
source hashes matched (`d-verified-*`). These results preceded the final two
boundary protections below, so the final source is being gated again.

An additional isolated corruption audit reproduced a lifecycle risk: an empty
Session active index could permit permanent deletion while authoritative Run
records remained RUNNING. No real workspace data was involved. Lifecycle
inactivity now checks both the complete Session index and Run records, using the
shared repository's bounded result query for RUNNING records. Existing custom
repository overrides retain their original fallback contract.
Delete/clone/backup reject the inconsistency; owned recovery interrupts uncertain
Runs without invoking Engines, after which explicit deletion is allowed. The
same audit before/after records `blocked=false` / `blocked=true`. A dedicated
child-process streaming crash test validates the guard and unchanged Run bytes.

A selected cancellation is also retained across the durable RUNNING notification
before the consumer task exists. Its deterministic test holds that notification,
cancels only the selected Run, and proves zero Engine effects while the other
Agent stays active. These protections plus lifecycle/custom-repository/runtime/
transaction regressions passed **113 tests** (`d-last-boundaries-focused`).

The next full gate (`d-final-*`) was stopped with SIGINT: both LLM and Hub
exit 130, **incomplete**, not passing evidence. An unchanged checkpoint lifecycle
assertion caught an unnecessary second full Run list read. The exact one-test
diagnostic failed independently (exit 1). The safety guard now uses the existing
bounded `list_results(..., status=running, limit=1)` query; the same checkpoint
test plus custom repositories, process crashes and mixed schedules passed all
**60 tests**, exit 0, 19.882 s (`d-index-query-fixed`). This preserves both
authoritative active-Run protection and the existing read-count assertion.

The final release gate freezes the same 629 source/test paths again in
`d-release-source.json`. LLM completed **1,712 tests**, all passing, zero Skip,
exit 0, 403.372 s. The three host modules completed **44 tests**, all passing,
zero Skip, exit 0, 19.217 s. Provider audit remains 48 files / 410 lines /
zero unclassified. The corresponding Hub run executed **534 tests: 533 pass,
1 failure, 0 Skip**, exit 1, 557.221 s. The unchanged pure-Hub test
`tests.hub.test_render_pipeline.WorkerTests.test_idle_polling_slows_but_dirty_wakes`
observed two reads instead of one. The log records its coroutine stalled for
**0.521 s** at the 0.1-second wait, exceeding the worker's 0.5-second idle polling
interval. Its runtime is a SimpleNamespace/mock and never calls LLM code; both
Hub implementation and test are byte-identical to main. No assertion, test timing,
Skip condition or Hub code was altered.

The exact test passed independently (1 test, exit 0, 0.137 s); its complete module
passed (19 tests, exit 0, 1.520 s). A complete Hub rerun without a concurrent LLM
suite (`d-serial-hub`) passed **534 tests**, zero failures/Skip, exit 0,
549.571 s, including the unchanged polling assertion and all nine X11 checks.
The failed full run remains failed evidence and its timing sensitivity remains
recorded; this is not a claim that one successful rerun eliminates flakiness.

Final D gate: **1,712 LLM + 534 Hub + 44 targeted host tests passed**, all actual
exit codes 0, zero residual Skip. All 629 frozen Python source/test hashes matched
after completion. The old golden fixture bytes, pinned host checkout and forbidden
product/test paths are unchanged. **D is implemented and verified; E and F remain
pending implementation.** Authenticated models and dedicated kernel short-write
injection remain unverified.

## E — explicit same-Project Session exchange (verified)

D commit: `4ffbb5e69abdfe7579e0db83db9531a209b3ae3e`, pushed. D final gates and
its earlier Hub timing failure remain recorded above. E is not yet committed.

`services/runtime/transfers.py` adds `SessionExchange`/`TransferHandle` and a
source-Session transport receipt. It is an idempotency/correlation record, not a
second queue, Run, scheduler or RequestGroup. Existing RunManager `_submit`
atomically writes that receipt and the target User; target admission alone owns
execution. Same-key/same-payload lookup reuses the original Message, while target,
content, recipients, source Run or deadline changes conflict. Interrupted sends
drain accepted storage; F13 proves pre-COMMITTED rollback and post-COMMITTED
reopening through real child-process exits. Missing receipts never authorize a
replacement request.

Authority remains the trusted Backend host's same-Project ownership. An optional
synchronous literal-bool host authorizer narrows independent actions and is
checked again at dispatch. No model Tool is automatically installed. Source
Agent/Message IDs are derived from a real source Run; public callers cannot
supply them. Explicit recipients, a unique ancestor Session chain of at most 16,
and original deadline survive target continuations. Different Project IDs,
ambiguous/missing recipients, spoofed source fields and cycles fail before ACK.

Only portable file ConversationStore journals support this durable API. Memory
and nonportable factories are explicitly rejected before acceptance; existing
ordinary memory/file execution APIs are unchanged. Absolute deadlines reject new
expired acceptance, cap active timeout and block expired resume without resetting
approval, Tool scope, quotas or operation receipts. Query/idempotent reopen still
works after expiry or detach. Source Memory, internal context and approval records
are never copied to target; the target independently resolves its own Agent and
resources. Results are detached JSON projections over Run/Assistant history,
including continuation links and stable denial/expiry codes.

Clone strips executable transfer routing into inert provenance. Retained receipts
conservatively protect connected Session/turn history from deletion and retention;
whole Project deletion still removes the whole ownership domain. Archive and
recovery inspect both sides and continuation origins, including Run/User provenance
mismatch. Archive validation uses read-only Run readers rather than writing logs
inside the archive. Public API examples and limits are in `services/README.md`.

Focused E evidence (`/workspace/refactor-bf-evidence/e-*`):

- Initial exchange + existing Run API/custom repository/checkpoint regression:
  **67 pass**, exit 0, 9.929 s.
- F13 child death, cross-Session contracts, D process crashes, checkpoint, turn
  deletion, operational storage and policy regression: **81 pass**, exit 0,
  25.083 s.
- Expanded selected cancellation, expired approval, immutable origin and D/C
  regression: **102 pass**, exit 0, 22.884 s.
- The first complete new-module run passed **14 tests**, exit 0, 6.490 s, before
  the final explicit denial/expiry error-code assertions. A final focused run and
  frozen full gates follow below; they are not assumed successful in advance.
- F13 failpoints `before_user`, `target_written`, `receipt_written`, `committed`
  exit with code 93 in dedicated children. Fresh owners see zero/zero/zero/one
  accepted input; explicit safe send/reopen produces exactly one effect. A second
  new Backend/same-key send preserves the same User/Run and effect count. IDs,
  markers and effect rows are emitted as `E_F13` evidence.
- C11 uses a held source Run plus two actually entered target Engines, Event
  synchronization, immediate acceptance, independent active sets and a blocked
  return transfer. Sender wait cancellation/timeout leaves targets running.
  A 16-Session chain is accepted; its next hop is rejected without admission.
- Actual target Tool approval pauses with zero effects; target resume requires its
  own approval and executes once. Continuation cannot escape the source chain or
  original deadline through either public resume API.

Authenticated models and kernel short-write injection remain unverified. E full
regressions and commit/push are pending; F remains unimplemented.

Final E focused modules passed **14 tests**, zero failures/Skip, exit 0,
6.552 s (`e-release-focused`). The first frozen LLM gate then executed **1,726
 tests: 1,725 pass, 1 error, 0 Skip**, exit 1, 402.651 s (`e-gate-llm`). Hub was
**not started** because that gate failed. Targeted host modules passed **44 tests**,
exit 0, 19.231 s. These are separate actual outcomes, not a successful full gate.

The error was the unchanged external Component hook contract:
`test_execution_boundaries.ObservationTests.test_external_component_hooks_do_not_require_local_storage`.
Component-only reference inspection receives only a registry and must not require
local Sessions. Transfer references now join the actual Session/Run retention
planning paths, preserving the existing Component-only method. The exact test
failed independently before correction (1 error, exit 1, 0.004 s), and the corrected
execution/transport/retention/mixed-schedule regression passed **66 tests**, exit 0,
20.346 s (`e-retention-fixed`). Its assertion is unchanged. New frozen gates below
must pass before E commit or F implementation.

Corrected frozen E LLM gate (`e-verified-llm`) passed **1,726 tests**,
zero failures/Skip, exit 0, 401.583 s. Targeted host modules passed **44 tests**,
zero failures/Skip, exit 0, 19.048 s (`e-verified-host`). The sequential full Hub
gate is still running; it is not yet counted as passed. Python sources remain
frozen at `e-verified-source.json` (632 files).

Final E Hub gate (`e-verified-hub`) passed **534 tests**, zero failures/Skip,
exit 0, 544.154 s, including all nine X11 tests. All **632** frozen Python
source/test hashes match after both full suites. Final E results are therefore
**1,726 LLM + 534 Hub + 44 targeted host**, all exit 0 and zero residual Skip.
E is implemented and verified; its earlier failed gate is preserved above.
The pinned host is clean and ignored; forbidden product/test paths still match
baseline main `94229cb865374d99f20ec48c5e48269488041a4e`. F remains the next stage.
Authenticated models and dedicated kernel short-write injection remain unverified.

## F — final candidate review

E commit `d5cddc05d339940faa07e4dc8240bf706617967b` is pushed. Its final frozen
1,726 LLM / 534 Hub / 44 targeted host gates all passed with zero Skip.

`components/review.py` defines detached common observations and typed decisions;
`CompletionSession.review_final` is optional, with explicit stable processor
`review_revision`. Pipeline instances remain invocation-local. All reviewers
must accept; block precedes continue. Callback timeout, text feedback cap and
strict return types fail closed. No application success rule or automatic Tool
is added. Agent processor selection only narrows host/parent capabilities,
including Graph descendants and `agent_run`; Graph orchestration has no candidate.

`engines/loop/review.py` owns versioned JSON-only receipts. An iteration stores its
prepared common observation without Provider parameters/credentials. A completed
attempt reservation precedes the pure callback; the decision receipt precedes
continuation or final OUTPUT. Committed decisions are reused, incomplete decisions
allow bounded pure re-review, and candidate/reviewer/aggregate/attempt corruption
is refused. Existing Tool receipt and approval paths remain the only authority.

Reviewed model Step deltas/outputs are internal until final acceptance. A continue
adds explicit untrusted feedback without modifying the original User or Tool
pairs and remains inside the same Loop/Run budgets. Live Steering stays open until
acceptance, then its final boundary is checked. No-review streaming and old binding
bytes are retained. Generic usage admission counts validated resume ancestors'
model and Component receipts without double-counting Project totals; unknown
usage keeps its reservation. Review-enabled Run timeout stores the original
absolute deadline, conservatively including approval pauses. This opt-in deadline
never extends during Resume; the no-review timeout path is unchanged.

Initial and intermediate results are preserved under `/workspace/refactor-bf-evidence/`:

- `f-baseline`: 77 tests, one schema-baseline failure, exit 1, 16.797 s. The new
  policy field also appears in ParameterLayout's explicit config-key rejection
  schema. Both additions are now asserted before comparing the unchanged golden
  schema; no fixture was regenerated.
- `f-review-initial`: 9 tests, 7 failing subtests and 1 error, exit 1, 2.476 s.
  Review continuation initially followed the final Steering close. Decision now
  precedes that boundary; acceptance then checks final Steering. The test's
  RunHandle access was also corrected to `.data.status`.
- `f-review-corrected`: 45 tests passed, zero Skip, exit 0, 9.808 s, including all
  existing Loop Steering tests and the explicit new schema assertions.
- `f-review-expanded`: 14 tests, 2 errors, exit 1, 3.715 s. Graph's explicit field
  allowlist needed the new inherited processor selection; a new async-comprehension
  assertion also needed an awaited list. Corrected run: 14 pass, exit 0, 4.621 s.
- `f-crash-initial`: F14's six dedicated process-death subcases passed, exit 0,
  3.286 s. Child exit 94 and reached markers prove response/attempt/computed/
  continue/accept/publication boundaries. Fresh owners never automatically replay
  the interrupted Run. Explicit Resume preserves three total model calls and one
  actual Tool effect in every case. Only computed-but-uncommitted review adds a
  pure re-review (three callbacks instead of two). Second reopen preserves IDs,
  interrupted/completed statuses, final text and effect count. Full evidence uses
  `F_F14` JSON rows; publication is not an exactly-once notification claim.
- `f-boundaries`: 169 tests passed, zero Skip, exit 0, 70.625 s, covering new F,
  processing/common Provider, Agent/Graph, Memory/Goal, operation/usage and schema
  regression (before the last additional composed tests).
- `f-composed` (18 tests, 1 failure, 5.810 s) and `f-final-focused` (20 tests,
  1 failure, 10.550 s) exposed a mistaken new test expectation: saved-response
  Resume skips prepare, leaving Memory's existing lazy processing state unset,
  so its extraction is not repeated. The test now explicitly verifies that
  established boundary and preserved auxiliary receipts. A separate actual RAG
  call tests ComponentUsage ancestor quotas, instead of claiming a skipped
  auxiliary operation exercises quota admission.

C12 holds two Agent reviewers using Events before either can finish, then permits
one Agent to complete while the other continues. It checks separate Session
instances, candidate/feedback/Checkpoint scope, Run/Step/Assistant IDs and active
sets; `F_C12` rows record the exact schedule. The composed tests also cover nested
Graph decision reuse, Memory/Goal Tool-pair preservation, live Steering during
both accept/continue, failed/unknown-token quota across Resume, callback cancellation,
configuration revision changes, corrupt receipts and invocation credential omission.

F implementation and final gates are not yet declared complete. Authenticated
models and kernel short-write injection remain unverified.

The final expanded delegation gate (`f-release-focused`) executed 61 tests and
failed with 12 failure reports and 2 errors (including subtests), exit 1,
29.367 s. New processor narrowing incorrectly assumed every invocation node was
a dataclass. Existing Graph uses a frozen dataclass, while `agent_run` supplies
an invocation-local namespace. The selection adapter now preserves unchanged
contexts and copies the appropriate existing node representation only when
narrowing is requested. No approval assertion, retry rule, test Skip or Tool
implementation was changed. The failed gate is retained and must be followed by
a passing delegation/approval/cancellation/resume gate before full regression.

After the node fix, `f-release-fixed` ran 61 tests: one new test failed because it
assumed dictionary iteration order for nested receipts (exit 1, 22.538 s). The
assertion now checks both exact local keys and the receipt count, preserving
identity rather than assuming incidental ordering. `f-focused-verified` then
passed **61 tests**, zero failures/Skip, exit 0, 22.341 s. All unchanged delegation,
approval, cancellation, nested binding and project-policy tests passed.

The final source is frozen in `f-gate-source.json`: **636 Python files** across
llm/tests/llm/hub/tests/hub. LLM then Hub full suites and the three targeted host
modules are running. Provider inventory is **50 files / 417 matching lines /
0 unclassified**; the current report is `llm/PROVIDER_AUDIT_FINAL.json`. The old A
inventory, fixtures and evidence remain unchanged.

Frozen final F LLM regression (`f-gate-llm`) passed **1,747 tests**, zero
failures/Skip, exit 0, 413.616 s. Targeted real-host modules (`f-gate-host`) passed
**44 tests**, zero failures/Skip, exit 0, 18.860 s. The sequential full Hub suite
is still running on the same frozen source and is not yet declared successful.

Final F Hub regression (`f-gate-hub`) passed **534 tests**, zero failures/Skip,
exit 0, 549.426 s. This includes all **nine** actual Xvfb/xclip tests. After all
gates, the exact **636** Python paths and SHA-256 hashes matched the frozen
manifest. The final results are **1,747 LLM + 534 Hub + 44 targeted host**;
the targeted cases overlap the full suites, so the unique full total is 2,281.
`f-final-verification.json` records actual counts, exits and log hashes. The
pinned host remains clean/ignored, A evidence and all golden archives are
unchanged, and hub/tests/hub/examples still match baseline main.

F is implemented and verified in this Cloud environment. Historical failures
above remain part of the evidence. Authenticated model calls, kernel short-write
fault injection, separate CI and production validation remain unverified.
The Korean consolidated report, complete F01–F14/C01–C12 matrices and concrete
future Hub integration call sites are in `REFACTOR_FINAL.md`.
