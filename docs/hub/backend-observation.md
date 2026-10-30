# Backend observation costs

Hub owns display observations; llm owns persistence, execution, authorization and
recovery. This optimization changes only the Hub side of that boundary. It does
not skip backend validation, read backend files directly, or reuse display data
to approve Tools or admit/resume Runs.

## Repeated work removed

Previously, each selected-session frame fetched Session data, runtime status,
latest Runs, paused Runs, mutable Steps, the entire conversation, Project data
and Agent definitions. Public Run queries perform history integrity checks, so
`limit=1` does not mean reading a single file. Repeating those queries every
0.1 seconds also competes with submissions and control operations for storage.

`SnapshotReader` now uses `ObservationReads` for short-lived public API results:

| Observation | Refresh trigger |
| --- | --- |
| Session/runtime/latest/paused | Run lifecycle, interaction/steering change, explicit Hub Session mutation |
| Run metadata | Non-text Engine event, Run lifecycle, explicit Session mutation |
| Mutable Steps | Step lifecycle/update/output, Run lifecycle |
| Conversation | User-visible output, Run lifecycle, interaction/steering change, explicit Session mutation |
| Project/Agents | Settings or record-management completion, project activation |

Selected-session, Run, Step, Project and Agent reads expire after two seconds.
Nonselected Session summaries reconcile at stable per-Session intervals between
7.5 and 10 seconds, avoiding synchronized expiry; lifecycle and
interaction events still invalidate them immediately. Missing events clear these
observations and force reconciliation through public services. This is a safety
net for changes outside Hub, not a delay imposed on delivered events. Existing
terminal history projections remain reusable because completed records are
immutable; history deletion and project activation still clear them.

Cache entries are bounded to 1,024, and only the selected conversation is retained.
Cancellation/errors never cache partial results. Invalidating during an awaited
read prevents that result from repopulating the cache. A snapshot already in
flight can still display an earlier observation once; the dirty wakeup fetches
the newer state. No display cache is durable.

If a completed/paused Assistant message is already visible but the cached runtime
still lists its Run as active, the reader reconciles that Session once before
publishing. This closes the final-output/lifecycle delivery race: the next Enter
must not offer steering merely because the runtime observation is older than the
finished answer. This bounded retry does not poll or replay execution.

The worker spaces polls from the **end** of the previous read, avoiding immediate
repeated scans when a read is slow. Input still preempts background observation.
Opening an already-running Hub no longer selects/starts the same Session again.
Selecting an opened Session reuses its worker; selection publishes through the
normal dirty refresh instead of a second queued foreground snapshot. A waiting
stage is reported only when initialization or the Hub command lock actually
blocks the operation.

## Verification and limits

`tests.hub.test_observation_reads` checks work counts, visible/internal deltas,
Step and lifecycle invalidation, interaction changes, event loss, expiry,
in-flight invalidation, cancelled reads, bounded entries, Session recovery reuse,
real waiting stages and slow-read pacing. Existing integration tests exercise
streaming, final-review drafts, approvals, interruption and multiple Sessions.

In 100 unchanged frames within one reconciliation interval, status reads fall
from 100 to 1; Run list calls from 200 to 2 (latest and paused); conversation,
mutable-Step, Project and Agent reads from 100 each to 1 each. These are service
call counts, not a claim of a 100-fold wall-clock improvement. A separate
before/after comparison advances a simulated clock at 10 Hz for ten seconds:
100 frames perform 5 status reads, 10 Run list calls, and 5 reads each of the
conversation, mutable Steps, Project and Agents (95% fewer calls). Periodic
reconciliation is included. The source and results are retained under
`tests/hub/reports/backend-observation/compare_work.py` and `work-counts.json`.

First loads, periodic reconciliation and explicit log/history dialogs still
perform backend validation. Backend disk speed, logging, large journals,
settings-form construction and provider latency are not eliminated by this
change. llm source is unchanged.

## Lazy forms and incremental display follow-up

Settings retain the full schema, draft text, unknown JSON keys, default suggestions
and host-lock metadata independently of PTK widgets. Opening a Project page builds
inputs only for the visible section/tab. Other fields create their inputs when
displayed; revisiting them reuses the widget and draft. Dirty checks, filtering,
validation and saving read draft text without creating hidden inputs. Unvisited
settings are therefore still validated and preserved by the existing SchemaForm
owner. This delays widget construction, not schema validation or persistence.

When the detached conversation observation is unchanged, ConversationProjection
indexes records by Run and rebuilds the rows for observed Runs instead of
reprojecting every historical record. Ordinary unchanged turns retain their display
objects. Edited/resumed histories still pass through the canonical TurnIndex and
continued_messages owners; this optimization does not introduce another resume
ordering algorithm. Source replacement, profile changes, deletion and lost events
rebuild the index. In-flight invalidation retains the complete observed history for
that frame, then forces reconciliation.
The source index becomes reusable only after the full projection succeeds. An
exception while reading a historical Run leaves it pending a full refresh, so
the next observation cannot silently omit unfinished historical rows.

MessageRenderCache skips unchanged immutable message objects before parsing block
dependencies and assembling trace rows. Width/theme/language/registry changes still
invalidate rendering. Renderers with per-block dependency revisions (including
images) continue to check those dependencies even when their global version is
unchanged. Search anchors and tag objects use the same rendering output.

Session summaries reuse the detached Session observation until it changes. Only
background expiry is staggered; lifecycle, approval/question, explicit mutation
and lost-event invalidation keep their existing immediate refresh path.

`tests.hub.test_incremental_ui` checks bounded changed-Run work, dropped events
during projection, source replacement/deletion, Session summary identity and
expiry phases, lazy field validation, dynamic renderer dependencies, resizing and
anchor/object equality. `tests.hub.test_settings_tabs` also verifies real Project
schema switching, unvisited values and retained drafts. Reference-list traversal,
first loads and full reconciliation still exist; this is not viewport virtualization.

The synthetic comparison under `tests/hub/reports/incremental-ui/` prepares 300
fields and displays ten. Initial PTK input construction falls from 300 to zero;
displaying the ten fields creates ten inputs. For 1,000 historical requests and
50 changed-Run frames, continuation processing falls from 50,050 source records
to 50, with all historical display objects retained. Local medians were about
218 ms to 1.1 ms for form preparation and 11.2 ms to 0.21 ms per projection.
These one-CPU mocked-service measurements exclude disk, network, provider calls,
terminal painting and full page layout; they are not an end-to-end speed guarantee.
The reproducible script and both source snapshots are recorded in `measure.py`,
`before.json` and `after.json` in that report directory.

### Incremental UI verification (2026-10-28)

The ten new incremental-UI tests pass on Linux Python 3.12.14. They cover lazy
fields, unchanged-row reuse, canonical display equivalence, event loss, failed
history reads, dynamic renderers and Session refresh phases. Architecture rules
S1–S7 and documentation checks pass. All 385 protected llm source files match
the pre-change snapshot; the llm runtime suite was not run.

The full Hub run and subsequent affected-module reruns cover 796 distinct tests:
778 pass, nine fail with timeouts, and nine X11 tests are skipped because the
required X11 test tools are unavailable. **The complete suite is not green.**
Timeouts remain in question readiness/interruption, settings-to-response flow,
slash submission, automatic approval/resume and concurrent Tool retry tests.
The latest affected-module run has 60 passes and nine timeouts. Original
deadlines and assertions remain unchanged. Two retry tests now close their
backend even on failure, avoiding cascading temporary-directory cleanup errors;
an observation test uses the real EngineContext contract instead of a partial
test double.

A separate diagnostic observed one question test reaching its three-second
deadline before any provider call, then becoming ready after about 4.6 seconds.
A settings response case passed separately. These observations do not prove
the cause of every timeout and are not counted as passing regression results.
No timeout was hidden by replacing the test result with the diagnostic outcome.

The actual per-test results, source snapshots, protected-source hash check and
remaining failures are recorded in
`tests/hub/reports/incremental-ui/validation-summary.json`. This is consolidated
evidence across runs, not a successful full run of the final snapshot. The
existing 68 unreviewed contract-inventory files remain explicitly unreviewed.

## Backend preparation follow-up

HubConfig.prepare_provider selects local SDK initialization during startup (Hub's
application default is true; false leaves lazy initialization in place). Hub uses
the selected native adapter's public ainitialize(), without sending a model probe,
loading credentials or adding inference defaults. A custom backend factory or Loop
factory retains its own initialization. The backend's explicit-only configuration
and Provider retry rules are unchanged. Cold import time moves to startup; it does
not disappear, and warm requests must be measured separately.

BindingCache retains its existing live signature/revision/constraint checks.
ToolRegistry.copy() shares only unexposed definitions; prepare() detaches a shared
entry before exposing its live Tool, and entries already exposed are copied.
Handler identity lookup uses get_handler(), without copying schemas. Registration
state and mutable subclass state remain isolated. No authorization result is cached.

Hub's Tool input diagnostics copy an already validated standard ToolRegistry
through Python's deepcopy protocol, substituting only that exact registry class
and retaining handler identities. They do not reconstruct the same Tools through
registration, read/write backend private fields, or bypass execution validation.
The copy owns separate registrations and isolates schema mutations on exposure; custom registry subclasses are
left unchanged. Native selection still validates its selected Tools once. Native
registry merging, registration, authorization bindings and prepare retain their
backend owner. The additional diagnostic copy performs no registrations.

Settings save uses one validator for the original draft and its prepared values
before the first await. It still validates both and preserves backend save/version
checks. The read-back after persistence obtains a fresh schema, as does every
later command. No long-lived schema or execution-policy cache is introduced.

Functional backend-transition tests use a separate bounded 60-second watchdog
and 50 ms polling. Existing short keyboard/render latency limits and asyncio debug
mode remain unchanged. Native events/futures remain the wait mechanism where
already available. The sequential slash-request test aborts at its first failure
instead of submitting another request into the preceding Run's active dialog.

`tests.hub.test_tool_input_diagnostics` checks zero duplicate registrations,
detached copies, preserved handlers/contracts, native merging/selection and
invalid input rejection. `tests.hub.test_settings` verifies both drafts are
validated before any write, one schema serves that synchronous pair, a fresh
schema is read after persistence, and stale save versions remain rejected.

## Low-resource follow-up

`ConversationProjection` owns only disposable display rows for the selected
conversation. Between events, the same detached conversation/Run/Step observations
and the same displayed elapsed second reuse the entire message tuple. Changed
frames reuse settled historical rows; active/paused/latest Runs still use live
evidence. Profile changes, history edits, selection changes and dropped events
invalidate the appropriate projection. In-flight invalidation prevents retaining
new settled rows from that older build. The source objects remain referenced so
recycled object identities cannot match an unrelated observation. Retained rows
are pruned to the selected history, never written to backend storage or used to
admit, authorize, retry or resume work.

The UI presentation cache also reuses an unchanged immutable tuple. The Run summary
clock is whole seconds, and message-clock refreshes occur once per displayed second.
Fractional message durations remain available when combining resumed attempts.
Run summary clocks also bypass completion-metadata deserialization entirely.
Subsecond clock changes alone do not trigger a full UI update. Streaming content and lifecycle events still use
the configured output interval. Changed conversations and periodic reconciliation
still require a history traversal: this is not viewport virtualization or an
independent transcript memory limit.

`SessionNamer` remembers checks by source Run and observed Agent configuration.
Repeated frames no longer start tasks that reload the same title history. This
does not cache settings for execution: a genuine attempt reads the public Agent
configuration again. Hub settings saves invalidate configuration immediately;
external API edits are discovered by the normal two-second reconciliation.
Cancellation permits a later attempt, and skipped checks do not create a dirty
refresh loop. The title-check registry is bounded.

Settings `catalog` contains navigation/schema/preferences only. `usage` is a
separate read command requested by the profile page, once per catalog load. It
reports progress through the existing progress owner without locking settings
navigation or the Hub command lane. Its response updates both text and chart;
an older catalog's response cannot replace newer usage. Public receipt errors
remain per-project observations. Statistics still use llm's public usage API.

The view reports show/hide to the backend worker. While minimized, ordinary
output observations are coalesced to at most one per second; idle reconciliation
uses the existing two-second ceiling. Run lifecycle and interaction changes,
plus reopening Hub, wake the observer at the configured output interval. Backend
execution, subscriptions and notification collection continue while hidden.

`tests.hub.test_low_resource_observation` covers unchanged-frame work counts,
historical row identity, pruning, event loss, external-session expiry, bounded
title scheduling, cancellation, hidden/urgent refresh and lazy statistics. These
are deterministic work-count/ordering checks, not a hardware-speed guarantee.

## Follow-up verification (2026-10-28)

On Linux Python 3.12.14, the full Hub run discovered 779 tests: 767 passed,
three test-double initialization errors were corrected, and nine X11 integration
tests were skipped because Xvfb/xclip were unavailable. Subsequent affected-module
runs cover 118 distinct tests, all passing after corrections, including the new
Tool-input diagnostics. Across that full run and the reruns, the current inventory
is 786 tests: 777 passed and nine remain unverified. This is consolidated evidence,
**not a claim that one full run of the final snapshot passed**. The original failed
reports are retained. No llm runtime regression suite was executed, and all 385
protected llm source files match the pre-change snapshot.

Evidence is under `tests/hub/reports/low-resource/validation-summary.json`; source
snapshots and individual reports are listed there. Architecture rules S1–S7 and
the documentation check pass. The contract inventory still explicitly lists its
existing 68 unreviewed files; this change does not claim to finish that larger audit.

The synthetic before/after benchmark pins one CPU and observes 1,000 historical
requests plus 90,000 reasoning characters. Across 100 unchanged frames, median
projection time changes from about 120 ms to 0.8 ms; projection builds fall from
100 to zero and completion-metadata parses from 400 to zero. Twenty changed frames
reuse all 1,000 historical display rows. Peak Python allocations measured by
tracemalloc fall from about 2.61 MB to 1.87 MB in this workload.

The benchmark fixes the reconciliation clock, mocks public services, and excludes
disk, network, provider latency and terminal painting. It measures the unchanged
observation path, not end-to-end UI speed or the first load. Reproduce it with
`tests/hub/reports/low-resource/measure.py`; inputs and measurements are retained
in the same report directory.

## Preparation follow-up verification (2026-10-28)

The final complete Hub inventory on Linux Python 3.12.14 contains 799 tests:
790 passed, zero failed/errored, and nine X11 interoperability tests were skipped
because Xvfb/xclip were unavailable. There are no missing, unexpected or duplicate
test IDs. The previous question/settings/approval/retry/slash timeout cases all
pass within this run. An earlier focused run also passed all 86 selected cases.
Short UI latency assertions and asyncio debug behavior remain enabled.

The first full attempt terminated with SIGSEGV while the optional 120-second
faulthandler timer printed a thread trace. Its incomplete report is retained,
not counted as passing evidence. The isolated coding module passed, and the full
inventory was then rerun with the standard regression runner and the same source,
without that optional native stack-dump timer. This establishes the test results,
not a definitive diagnosis of the earlier native crash.

Evidence is in `tests/hub/reports/preparation-cost/validation-summary.json` and
the `ish-provider-v9q7ebha-verified` regression report. Protected llm source hashes
and tested Hub/test source hashes are checked separately. No llm runtime suite
was run. Architecture rules S1–S7 and documentation checks pass; the existing
outstanding contract inventory remains explicitly recorded.

The same fake-provider preparation diagnostic observed 323 Tool registrations
before and 187 after, with the question-ready phase changing from 3.697 to 1.953
seconds. This measures complete Run preparation before the first question, not
just the question form: 68 distinct Tools are involved, and `ask_user` itself is
registered twice. Native component merging and Agent selection still re-register
definitions, so 187 is not claimed to be the minimum possible count. Timing is
one instrumented observation, not a provider/network or production latency
guarantee. Inputs, call counts and limitations are in the preparation-cost report.

## Run preparation and changed-output follow-up (2026-10-29)

`EnvironmentEngine` owns a single-Run `BindingCache` for derived shell Tool
bindings. Repeated preparation/execution binding can reuse a detached registry
only while the source registry identity, public definition/contract signature,
handler identities, shell snapshot, Project configuration and Project/Session/Run
identities match. Agent/context/instruction wrappers do not share this cache, and
execution cleanup clears it. Custom Tool registries bypass it. Native admission,
execution binding, Tool argument validation and approval checks remain active.
Native `ToolRegistry.extend/select` still validate their inputs; Hub does not
access private backend registry state to suppress those checks.

Standard Loop/Graph model labels no longer construct another selected Engine in
submission preflight or snapshot assembly. Custom factories retain their normal
resolution behavior, and backend submission still validates the selected Agent.
This reduces work before durable admission and before UI publication without
changing QUEUED/COMMITTED transitions. A genuinely queued request still waits for
the active Run; Hub never declares a request committed before backend persistence.

`StreamingParse` belongs to a message's worker-side render cache. Closed top-level
fenced code and standalone XML prefixes are reused; unfinished Markdown is parsed
normally. Rewrites, truncation and final publication reconcile through the full
canonical parser. Long unfinished blocks can still require full suffix parsing.
The output text, raw objects, offsets and tag-bar grouping are unchanged.

`ConversationProjection` reuses settled prefix rows and traverses only the changed
suffix on an incremental frame. Linked edits/resumes retain `TurnIndex` folding;
source changes and observation races force reconciliation. The returned immutable
tuple still copies row references, so this removes Python row traversal rather
than claiming constant-time assembly. `Joined` slices read only requested viewport
rows instead of materializing the full transcript.

Reproduction scripts, measurements and scoped contract review evidence are under
`tests/hub/reports/run-path-speed/`. Measurements use fake providers or synthetic
messages and do not predict provider/network or terminal latency.

Verification: the Linux Python 3.12.14 full Hub run contains 806 tests, with
797 passed, zero failures/errors and nine X11 skips (Xvfb/xclip unavailable).
Follow-up checks of the preserved unknown-engine diagnostic and shared parser
declarations pass all 16 and 39 selected cases. Together these cover 807 unique
Hub cases, including the added diagnostic regression. The full snapshot and
follow-up snapshots are retained separately; skipped X11 cases remain unverified.
No llm runtime suite was run. Architecture S1–S7 and documentation checks pass.

## Local execution and lifecycle latency (2026-10-29)

The isolated `tests/hub/reports/local-latency/` harness uses the real BackendWorker,
file storage and a deterministic in-process provider: a greeting, then a lookup
which calls `skill_list` and `rag_list_documents`. It records command admission,
Run/Engine observation delivery, published frames, event-loop lag, storage-call
entry/execution/exit time and real `fsync` durations. It neither calls a model
service nor opens the user's workspace. Source snapshots live on WSL's Linux
filesystem with Python 3.12.14. Earlier failed harness runs are retained separately.

Disk durability dominates this host's local test: one greeting caused 347 `fsync`
calls, and the two-tool lookup caused 959. The initial unprofiled measurement spent
about 17 and 47 seconds inside those calls respectively. These are instrumented
isolated workloads, not a retrospective attribution of the user's provider time.
Storage-call entry includes executor/ownership waits; exit includes transaction
commit and callback delivery. Those durations must not be labelled pure file-read
or mutex time. Hub does not disable flushing, defer acknowledgement before durable
QUEUED, discard checkpoints, or patch backend transactions to hide this cost.

The following Hub-owned work is removed or reordered:

* The default `HubLoopEngine` now uses the same model-label fast path as native
  Loop/Graph. The earlier exact-type check missed Hub's default subclass and
  rebuilt/validated an Agent Engine merely to paint the model label. Unknown
  custom implementations still use their explicit resolution contract.
* Persisted Run and Engine events populate the bounded Run observation cache.
  Public handles are reused separately, with the existing cache bound. A newer
  event wins over a slow read; late running Engine events cannot overwrite a
  terminal lifecycle observation. Event loss and normal expiry still reconcile
  through public services. Admission, approval and resume never use this cache.
* Session runtime/queue status is read after history queries, avoiding a queued
  count captured before a request began executing during those queries.
* An urgent lifecycle/interaction change cancels an obsolete frame read and
  requests a fresh frame. Native service cancellation still drains its accepted
  transaction. Ordinary text deltas do not repeatedly cancel reads; shutdown
  cleans up the read and its wakeup task. Only one lifecycle preemption is allowed
  before a successful frame, so concurrent-session events cannot starve display.
  This does not cancel a Run or Tool.

The deterministic regressions in `test_observation_reads`,
`test_low_resource_observation` and `test_run_path_speed` check those ownership,
ordering, work-count and cancellation rules without machine-speed thresholds.
The remaining durable-write latency belongs to backend storage and the host;
reducing the write/flush count requires a separate backend design review.

## Admission feedback without a full history read (2026-10-30)

Idle submission preflight reads fresh public Session data first. An empty
`active_run_ids` selects the normal input UI path without a full runtime/history
query; otherwise the existing public runtime-status query resolves recipients.
This is not a cached admission decision: native `submit` still validates the
request and commits QUEUED before acknowledging it. Paused/pending/recovered
execution and all actual scheduling remain backend-owned.

`RequestStarted` is a small immutable UI observation emitted only after a public
persisted Run-start event. `BackendWorker` dispatches it independently of full
snapshot assembly. `SubmittedMessages` owns a bounded, transient set of these
proofs, scoped by Project/Session/request ID. It updates only a queued user row
to committed; it does not synthesize Run execution state, queue counts, answers,
or permission. A proof arriving before the submit acknowledgement is retained;
a stale queued snapshot cannot undo it. Canonical non-queued observations clear
the proof, and terminal rows are never downgraded. Existing full snapshots still
reconcile event loss and control execution actions through public services.

Hub configures the public `LogConfig(log_reads=False)` switch to avoid writing
an operational log for every display read. Mutation/lifecycle/error logs,
provider diagnostics, conversation persistence and checkpoints remain enabled.
No backend implementation, durability barrier or queue ordering is changed.

For streaming deltas, `HubDefaults.OUTPUT_BUFFER` selects the public
`BackendServices.output_buffer` contract: at most 32 deltas, a 0.1-second batching
window, or 65,536 characters per batch. Explicit Project `policies.output`
values still override this host default. The native consumer flushes deltas
before every non-delta event and drains cancellation. Tool/approval/Step/checkpoint
barriers remain durable before execution continues; the UI receives only persisted
events. This reduces per-delta transactions, not the mandatory admission or
lifecycle transactions. No filesystem sync call is bypassed.

Request selection treats explicit explanation topics such as “작성하는 예시
보여줘” as lookup requests. A separate action clause still selects work mode;
custom Engine and Agent requirements retain the existing routing guards. This
is a toolbox selection hint, never an authorization decision.

`tests.hub.test_submission_observation` covers immediate feedback under a held
snapshot lock, acknowledgement ordering, stale reads, scope and terminal-state
preservation, and mixed explanation/action requests. `test_log_retention` checks
that display reads stop generating log writes while mutations remain recorded.

Read-only directory maps and compatible-server Tool-call serialization are
described in [Tool inspection](tool-inspection.md). Their caches and request-shape
observations do not alter backend execution or storage ownership.
