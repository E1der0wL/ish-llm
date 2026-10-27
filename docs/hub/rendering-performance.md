# Hub rendering pipeline

The execution model and `llm/` contracts are unchanged. Rendering remains a
Prompt Toolkit consumer of Hub snapshots and OutputBlock/OutputObject data.

## Thread ownership

- The PTK loop owns input, focus, scroll positions, layout and terminal paint.
  `ConversationControl.create_content` requests preparation and consumes a ready
  immutable frame. It does not wait for a Future or render Markdown.
- One lazy `hub-render` thread per Hub owns transcript/preview Markdown, Rich,
  Pygments, structured JSON rendering, reader popups and search indexing.
- The existing backend thread owns execution and authoritative snapshots.
  Approval review JSON is formatted there before publication.
- The existing view-state writer also handles first-read position restoration;
  project switching no longer reads the position file in a PTK callback.
- Image conversion retains its existing two workers, four pending conversions
  and 32 cached/pending image keys. No new CPU executor pool was introduced.

The canonical `render_messages` function and unattached controls remain
synchronous for standalone rendering and tests. All running Hub surfaces share
the worker. The owner Application is remembered so a navigation callback outside
PTK's context variable cannot accidentally enter the synchronous path.

## Bounded work and delivery

Completed reasoning belongs to the immutable history prefix along with its
completed answer. Only reasoning paired with a committed/streaming Assistant
message enters the mutable tail. This avoids reassembling every historical
thought/tool trace on each current-response delta. A work-count regression checks
prefix identity across updates and compares all rows with the canonical renderer.

`RenderWorker` has one running job and one pending batch with five fixed slots:
transcript, preview, popup, search and question. A slot holds only its latest request.
The composer request card uses the same worker and cannot replace a full-details popup job.
The bound is independent of update count; separate slots ensure a draft preview
cannot erase a pending final transcript. Slots share one CPU thread. Generations
are checked both when work finishes and when the UI callback runs. Closing a
reader or Hub discards its pending results. Render failures use plain text on
the worker; no traceback is printed into the terminal.

Resize requests debounce for 75ms. Old prepared rows remain usable while the
new width is prepared. Reading anchors are resolved against the position at
delivery time, including navigation performed during rendering. Navigation
before the first frame is retained as a bounded position intent.

Backend snapshot publication also keeps one latest pending snapshot/callback.
Command completions are not dropped. Snapshot notifications retain the backend's
existing bounded sequence history.

`ui/snapshot_inbox.py` owns the mailbox lock, latest value and scheduled flag.
It neither holds a Controller reference nor starts/schedules a thread or event loop.
LiveController still decides when UI delivery is possible and applies each consumed
snapshot. `backend/observability/notifications.py` owns the existing 64-entry
notification buffer, 128 dedup keys and event/snapshot reconciliation identity.
These extractions preserve the existing bounds and do not change the rendering pipeline.

## Cache boundaries

`SnapshotReader` retains settled Run metadata, authors and Graph/Tool observations
for the conversation currently displayed. Deleting turns prunes those entries;
switching sessions clears them. This bound follows retained history rather than a
fixed FIFO of 1,024 items, which previously evicted the next item needed during
every full history traversal. The submission timestamp cache remains separately
bounded. Active Runs still consume fresh Step evidence, while settled Loop history
does not issue Step reads merely to discover whether a graph exists. The latest
selected terminal Run also reuses its Step snapshot. Pending, running and paused
Runs bypass that cache so interactions continue to reflect new evidence. This removes
repeated storage reads; it does not make traversal of a long conversation constant
time or bound transcript memory independently of retained history.

`MessageRenderCache` separates header metadata from body content. Elapsed time,
time labels and status labels do not reparse an unchanged body. The streaming
versus final distinction remains a parsing input, so unfinished XML is handled
correctly when a message completes.

Reasoning is a separate ChatMessage linked by the assistant's ID. Its `trace`
tuple interleaves immutable provider thought and Tool items using persisted start
times. Snapshot reconciliation rebuilds this ordering without a second transcript.
Resume updates the original logical Tool item; reused receipts do not add executions.
Header, answer body and each individual trace item have independent cache keys.
Changing only a Tool status does not rerender the thought Markdown or answer;
changing only thought text does not reparse/render the answer's code or tables.
The active reasoning and answer form one tail, including any queued requests
that follow them, so thought growth also reuses the historical prefix.
Height changes still require positioning the active
tail and PTK painting its changed cells; this is not a promise of zero layout work.

Each message retains its latest parsed blocks and rendered blocks. The parser
instance is reused. Renderer registration changes invalidate the layout context;
renderer completion revisions are checked per block. An image completion cannot
invalidate unrelated Markdown bodies. Markdown renderers reuse their Console
until width or theme changes.

Transcript rows use immutable indexed concatenations. A stable prefix of past
messages, its anchors, object offsets and widths is reused while the final
message changes. Appending the current tail does not copy all historical rows,
anchors or objects. Anchor lookup is binary search; named restoration uses a
prefix/tail mapping. Message presentation caches avoid repeated datetime
formatting and display transformations for unchanged historical messages.

## Observation and progress clocks

Active/queued work and dirty events use `output_refresh_interval` (default 0.1s).
Idle safety reconciliation uses `max(0.5, min(2, interval * 10))` seconds (default
1s). A dirty event wakes an idle observer, subject only to the active interval.
Snapshot reads remain preemptible by submission/control commands. Dropped-event
reconciliation and persisted state remain authoritative.

Progress model updates are immediate. Repaint requests coalesce through the
existing `progress_refresh_interval` timer; start/finish invalidate immediately.
Only the small single-row progress frame is rendered on the UI thread.

## Validation and instrumentation

2026-10-12: immediate composer restoration and durable decision acknowledgement
are separate from resume admission. Input-linked, nonfocusable RadioList choices
and content-height request cards replace the plain choice list. Transcript mouse
wheel handlers/routing and host mouse-mode overrides were removed. Tool rows use
the configured icon set; the assistant reasoning title has no leading icon.

Linux Python 3.12.14 validation used `ish-provider-in1uz9qc`: **29 focused tests**
passed; the complete **264-test Hub suite** had two 3-second timing timeouts under
concurrent backend test load. Both passed unchanged in `ui-recheck.txt` once that
load finished. `ish-provider-67j929ay` passed **199 focused backend tests**; its
**1,383-test backend suite** had one failure in the 0.1-second Run deadline test.
The complete affected BatchTests class passed unchanged in `timeout-recheck.txt`
without concurrent test load. No timeout thresholds or llm source were changed.
The final 384 Hub, Hub-test and llm files match the UI snapshot hashes. Recheck
logs are retained alongside the original results, rather than replacing them.

2026-10-12: the chronological thought/Tool trace, request cards, composer status
removal and wheel-routing update passed **262 Hub tests** and **21 focused tests**
in `tests/llm/reports/ish-provider-_a8gv9em/`. The complete backend suite passed
**1,383 tests** in `tests/llm/reports/ish-provider-8nz_pzua/suite.txt`, using Linux
Python 3.12.14. Final Hub, Hub tests and llm source hashes match the UI test
snapshot; `llm/` is unchanged. Coverage includes terminal wheel escape sequences,
ordinary arrow-key history, approval continuation, stale-card replacement,
chronological reconstruction and per-item trace cache reuse.

2026-10-12: the Tool-history/advisory-risk update passed all 250 Hub tests
(`tests/llm/reports/ish-provider-lrsfvop7/hub.txt`) and 34 focused tests in that
snapshot. Backend regression passed 1,383 tests
(`tests/llm/reports/ish-provider-7m8f803o/suite.txt`); `llm/` source is unchanged.
The new work-count regression changes thought text 50 times while a queued
request follows the active answer: the answer frame and historical prefix retain
identity, its parser byte count does not increase, and only the thought body is
rerendered. A Tool-status change updates only Tool rows. Tests also verify real
composer questions and advisory risk remaining subject to approval without an
extra completion call.

`tests.hub.test_render_pipeline` checks elapsed-only reuse, 199 historical
messages with 100 tail updates, canonical final rows/anchors/objects, image-local
invalidation, bounded hundreds-of-updates coalescing, stale delivery, one shared
worker, fallback, resize anchoring, idle wakeup and input/focus/scroll while both
rendering and a snapshot read are held unfinished. Existing PTK tests wait for
the prepared frame before examining rendered cells rather than assuming the
first paint already contains Markdown.

Counters are available on `RenderWorker.stats`, `MessageRenderCache`,
`ConversationControl.content_*` and `BackendWorker.snapshot_count`. They emit no
per-frame logs. Tests assert work counts and synchronization, not machine-speed
thresholds.

Run `tests/hub/manual/benchmark_rendering.py` with Linux Python 3.12.14 and a
hash-verified Linux source snapshot path. Its scenario is 200 historical messages,
a 100KB active response, 100 updates and an 80x24 terminal (the sidebar reduces
the actual content width). A gated burst makes coalescing deterministic. It
reports retained historical bodies, actual render/parser work, pending slots,
snapshot count and UI callback durations. This measures UI observation with a
synthetic snapshot source, not provider/network/storage throughput.

## Remaining costs

The currently changing message is still parsed in full when its text changes.
Unchanged parsed blocks reuse rendering, but there is no speculative incremental
Markdown parser: reference definitions and unfinished constructs must preserve
canonical final semantics. Very large active messages can therefore delay the
next prepared frame; input does not await it. Python threads still share the GIL.

Metadata scans over messages remain linear on the worker, and snapshot-to-view
selection still walks message/session metadata on the UI thread. History rows
are neither reparsed nor copied on each tail update. Width/theme changes require
a full layout in the worker. Search indexes the current prepared transcript on
that same bounded lane. Large settings-form construction remains outside this
output-path optimization; existing full-suite diagnostics can still show its
UI callback cost.

## Validation record (2026-10-11)

- Linux Python 3.12.14: final focused suite **38 passed**, complete Hub suite
  **165 passed**, including 13 new pipeline tests. Reports:
  `tests/llm/reports/ish-provider-d14rl7d0/`.
- Complete `llm` suite: **1,357 passed** in
  `tests/llm/reports/ish-provider-oxycef36/suite.txt`. Its 234 protected `llm/`
  and reference-host source files remained hash-identical. All Python files
  remained identical to the final Hub snapshot after validation.
- Final gated benchmark: all **200** historical bodies reused; **1** additional
  active body render for **100** updates; **1** prefix build; **209,901** total
  parser input bytes (initial plus final); pending surface peak **1** of a
  fixed maximum **4**. Measured UI content callback maximum **1.825ms** on this
  workstation. See `tests/hub/reports/rendering-benchmark-d14rl7d0.json`.
- Status: **OPTIMIZATION ALIGNED**. The remaining costs above are deliberately
  documented; timings do not promise a particular provider response latency.
