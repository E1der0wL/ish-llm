# Request preparation observation review

Date: 2026-11-01. Scope: Hub request preparation toward one second, without changing
`llm/`, durability, execution validation or UI behavior. This is a Hub integration
review, not completion of the backend-wide Level 3 gate.

## Changes and ownership

- `SnapshotReader` retains latest/paused Run-list observations across durable queued
  admission. A Run lifecycle event invalidates the latest list; a running start
  retains the unchanged paused list. Interaction changes, non-running lifecycle,
  explicit mutations, event loss and the existing two-second expiry reconcile
  through public services. Pending interactions and admission still query live APIs.
- `AnswerDrafts` establishes an empty output cursor when the durable Run-start
  event arrives. This removes an unnecessary output snapshot query before the
  Engine emits output. It preserves deltas already received through the separate
  Engine stream and preserves loss/gap reconciliation. Mid-Run attachment without
  that event still reads the public Run view.
- `RAGCatalogSession` reads the catalog only when the actual provider request
  exposes `rag_search`. A later tool-exposure change can load it; component
  registration by itself no longer triggers retrieval preparation for greetings.
- `ToolViewSession` avoids thread dispatch and copying when no native Tool result
  exists to project. Real file/process output retains the previous bounded views,
  source reference and backend context-budget behavior.

Existing environment/Tool prewarming, selection-time cache invalidation, provider
preparation, change-driven redraw and rendering-rate limits are retained. Backend
execution binding, queued persistence, approval, review and resume are not skipped.
No output is labelled as thinking before a real execution event.

## Evidence and reproduction

Local evidence is beneath `tests/hub/reports/request-preparation-20261031/` (excluded
from deployment/discovery). `before-source.json` records the initial HEAD, dirty
state and normalized source hashes. `iteration1.json` and `iteration2.json` identify
the successive native Linux source copies. The baseline already contains backend
and environment-prewarming changes made before this task; these are preserved.

`phase_probe.py` uses the reference ish `Prompt`, PTK Enter handling, Hub's real
worker and file-backed backend on the Linux filesystem. A local HTTP SSE fixture
replaces the provider. It checks exact request content, one HTTP completion per
greeting and two for the file-tool flow, and completed conversation state. Clock
boundaries include Enter, receipt, Run/Step events, first HTTP request, final paint,
public API calls and real fsync calls. Shared/inclusive timings are not added as
independent costs. Native Python is 3.12.14.

The fixture disables auto-title, postprocessing and final review on both sides to
isolate preparation, and supplies no real API key. Production defaults are unchanged.
It is not evidence of real provider latency, corporate SSH performance, or an entire
new-workspace startup within one second. Read-only file requests include the second
provider call, so final paint is not their preparation-time metric.

Focused first-iteration tests passed 49/49. The second integration run passed 90/91;
its only error was an outdated SimpleNamespace test double missing `drafts.started`.
The fixture was corrected to assert that notification; the original failed report
is retained. An earlier command with nonexistent `test_steering` was rejected before
discovery and is not counted as a successful test run.

Final source: `/home/user/.cache/ish-hub-preparation-verified-g23k66vo`;
`verified-source.json` pins it. HEAD remained
`e9fc844c78c219ef51742411d95d6aaa12bd66ba`. `source-guard.json` records the exact
17 changed source/test/document/evidence files relative to task start. All 431
backend/host files have unchanged normalized hashes and original bytes. The tested
Hub product/tests match the working tree. Existing `tool_context_chars` declarations,
validation, persisted preferences and consumer propagation were not changed; their
tests are included in the full Hub run.

## Final comparison

Individual WSL invocations varied substantially: even the final Hub code ranged
from 1.60 to 5.09 seconds before first HTTP receipt. Process inspection between runs
showed WSL booting again; separate-invocation measurements cannot isolate code from
environment drift. Those original results remain in `timing-before`, `timing2`,
`timing-final` and `timing-repeat`; they are not discarded or claimed as reliable
speedups. This does not prove that WSL startup caused all variation.

`paired.py` then alternated before/after twice in **one WSL lifetime**, serially,
without simultaneous tests or profiling. Each side has 12 requests (two samples of
each scenario). `paired-summary.json` records boundaries and counts.

| Enter to first provider HTTP receipt | Before, seconds | After, seconds |
| --- | --- | --- |
| First greeting after Hub ready | 1.288–1.527 | 1.194–1.207 |
| Repeated greeting | 1.165–1.253 | 1.082–1.174 |
| First file-tool request | 1.608–1.788 | 1.666–1.791 |
| Repeated file-tool request | 1.713–2.013 | 1.713–1.979 |
| Approximately 28 KB input | 1.957–2.096 | 1.969–2.040 |
| Greeting after that history | 1.325–1.658 | 1.347–1.421 |

The mixed-scenario median changed from 1.633 to 1.543 seconds (about 5.5%). This
small sample does not establish a statistically significant improvement for every
case; file-tool and large-input times are effectively similar. Greetings are close
to the one-second target in this local fixture, not guaranteed below one second.

- First Engine Step observation: median 1.280 → 1.225 seconds; final range
  1.055–1.460 seconds. This is separate from actual HTTP receipt.
- Durable receipt: median 0.197 → 0.214 seconds; final range 0.163–0.396 seconds.
  Input-buffer consumption remains immediate: final median 0.027 seconds.
- Before first HTTP, Run-list queries fall from 44 to 12 across the 12 requests
  (about 73% fewer). The focused tests also check exact invalidation/expiry counts.
- Real pre-HTTP fsync calls remain 66 on each side. Total HTTP calls remain 16
  on each side (file-tool requests require a second completion). No persistence
  operation or model call is suppressed to meet the timing target.
- Native `RunManager._context` median: 0.223 → 0.209 seconds in this paired run.
  Earlier slower samples reached 1.108 seconds. Preparation still includes backend
  source/binding validation and storage contention; these costs are not attributed
  exclusively to disk and overlapping timings are not summed.

## Final validation

- Full Hub regression: **923 passed**, zero failures/errors/skips/missing/duplicates,
  Python 3.12.14, 1,926.081 seconds. Report: `full/hub.json`.
- Architecture S1–S7: zero new/stale findings, review errors and required outstanding
  findings. Direct architecture/coverage evidence tests: **16 passed**, zero failures
  or skips. Shared evidence was updated only for changed Hub owners/consumers.
- Document check: 200 documents, zero errors. `git diff --check` succeeds using the
  checkout's actual line-ending configuration.
- The whole ownership inventory remains incomplete: 67 previously unreviewed files
  and 4,742 unclassified candidates remain (including the additional observation
  flag in the already-unreviewed runtime file). No blanket review was added. The
  new flag only retains unchanged display lists after durable admission; it does
  not select execution, admission, authorization or persistence policy. Coverage
  has zero stale records, violations or structural issues; this is not a claim of
  complete repository-wide ownership review.
- No full backend execution suite was rerun: this follows the Hub handoff's scope.
  The final full Hub suite covers its real backend consumers, including approval,
  resume, Graph, RAG, settings, Tool constraints, output and X11 clipboard behavior.

The earlier test-double error is fixed in the final source and its test passes in
the full run. No source was changed after that run except this result documentation.

## Remaining boundary

Local profiling still shows native execution-context assembly, source/binding
validation and durable state transitions between receipt and the first model call.
Those backend guarantees are not bypassed or copied into Hub. The native provider
can introduce additional latency after preparation. A one-second guarantee across
machines, cold starts and provider endpoints is not established by this change.

For an actual user check, restart ish with this checkout's Hub and unchanged backend,
wait for connection initialization, then compare the first and repeated greeting in
a test session, followed by a permitted file read and interrupt/resume. No production
workspace or corporate endpoint is changed by the automated benchmark.
