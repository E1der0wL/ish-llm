# Hub rendering pipeline

The execution model and `llm/` contracts are unchanged. Rendering remains a
Prompt Toolkit consumer of Hub snapshots and OutputBlock/OutputObject data.

## Thread ownership

- The PTK loop owns input, focus, scroll positions, layout and terminal paint.
  `ConversationControl.create_content` requests preparation and consumes a ready
  immutable frame. It does not wait for a Future or render Markdown.
- One lazy `hub-render` thread per Hub owns transcript Markdown, Rich,
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

The request-preparation clock sleeps until the next displayed whole second or
retry-countdown change, bounded by the configured output interval. It does not
rebuild the same card between visible clock changes. All active Agent headers
advance from local observation timestamps, without reading history for a clock.
Submission and execution-state events still request an immediate paint. This is
presentation deduplication, not delayed execution or a new throttling setting.

Completed reasoning belongs to the immutable history prefix along with its
completed answer. Only reasoning paired with a committed/streaming Assistant
message enters the mutable tail. This avoids reassembling every historical
thought/tool trace on each current-response delta. A work-count regression checks
prefix identity across updates and compares all rows with the canonical renderer.

`RenderWorker` has one running job and one pending batch with four fixed slots:
transcript, popup, search and question. A slot holds only its latest request.
The composer request card uses the same worker and cannot replace a full-details popup job.
The bound is independent of update count; separate slots ensure a popup
cannot erase a pending final transcript. Slots share one CPU thread. Generations
are checked both when work finishes and when the UI callback runs. Closing a
reader or Hub discards its pending results. Render failures use plain text on
the worker; no traceback is printed into the terminal.

Strict search/popup jobs may return `RenderWork`. Row preparation, search-index
assembly and table-cell preparation yield at bounded row batches; after an 8ms
cooperative slice the same job goes to the end of its existing slot queue. There
is no extra executor, suspended-job queue or parallel Rich renderer. New requests,
close and generation changes discard obsolete work. Progressive transcript frames
retain their original completed-frame delivery. Canonical Markdown parsing, JSON
decoding and one indivisible Rich element can exceed a slice; this is cooperative
scheduling, not preemption of arbitrary Python calls.

Resize requests debounce for 75ms. Old prepared rows remain usable while the
new width is prepared. Reading anchors are resolved against the position at
delivery time, including navigation performed during rendering. Navigation
before the first frame is retained as a bounded position intent.

Follow-tail remains active across frame delivery and viewport-height measurement.
Large output bursts and a later, shorter paint area cannot consume that intent early.
Explicit scrolling, saved-position restoration, search navigation and disabling
auto-scroll retain their existing control over the reading position.

Preparation feedback hands its display anchor to the first observed response
for its accepted request. `PreparingResponse` owns that association;
`EngineOutput.handoff_message` is only a presentation hint. `LoopOutput` forwards
it to the transcript, without rewriting backend message IDs or storing another
conversation. Other output implementations may render complete frames without
using the hint.

While a Markdown frame is pending, `ConversationControl` replaces the local
header at its existing row instead of appending the observed header below it.
The handoff survives progressive older-frame delivery, remaps a reading anchor
when the observed frame arrives, and is released on current-frame delivery or
session replacement. A completed response or its first reasoning header uses
the same association; author text is never used to guess message identity.

`Frame` supplies immutable header heights from the render cache. Urgent updates
replace that whole existing header slot, clearing stale wrapped rows while
preserving the body geometry. New trailing headers reserve the canonical
message gap; headers inserted before already visible queued input wait for the
proper frame rather than flashing at the bottom. These changes reuse the
existing renderer thread, cache, update signals and rate ceiling.

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

`ActionBar` owns a four-entry layout cache keyed by allocated width. Its button
identity/order, effective widths and equal-width mode form the invalidation
signature. Dynamic captions still render live; a caption that changes a shared
button width rebuilds the layout. Focus traversal, measurement and painting reuse
the same container objects instead of rebuilding spacers and nested splits on each
visit. Resize measurements and actual paint widths can coexist within the bound.
Absent popup overlays and unloaded settings content also retain their own empty
widgets. These are presentation-only caches; they do not cache backend results,
defer button actions or alter popup disposal and focus restoration.

`SnapshotReader` retains settled Run metadata, authors and Graph/Tool observations
for at most four visited conversations. Deleting turns prunes those entries;
project switching clears them. This bound follows visited history rather than a
fixed FIFO of 1,024 items, which previously evicted the next item needed during
every full history traversal. The submission timestamp cache remains separately
bounded. Active Runs still consume fresh Step evidence, while settled Loop history
does not issue Step reads merely to discover whether a graph exists. The latest
selected terminal Run also reuses its Step snapshot. Pending, running and paused
Runs bypass that cache so interactions continue to reflect new evidence. This removes
repeated storage reads; it does not make traversal of a long conversation constant
time or bound transcript memory independently of retained history.

The selected history initially loads Run details only around the restored reading
anchor (24 conversation messages on either side), plus current/active Runs.
Scrolling coalesces a new observation window through the existing reading-position
callback. Previously loaded details are reused; no other conversation is prefetched.
Session changes cancel an obsolete snapshot read before acquiring the same runtime
lock. They never cancel execution, durable writes, or another foreground command.
Run/Engine events invalidate the affected visited projection even while it is hidden;
event loss clears observation caches and preserves the reading anchor.

The public `Session.aconversation()` contract still returns the conversation
projection as a whole. Hub does not invent a second store or read backend files to
page it. The savings are historical Run/Step reads and expensive presentation work,
not a guarantee that only visible raw conversation bytes are loaded.

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
invalidate unrelated Markdown bodies. Each render owner retains at most four
width/theme Consoles and 1,024 immutable Rich-style conversions. Header, body and
thought rendering share the transcript owner's resources. Completed trace parts
and widths are reused; changing one trace suffix does not revisit all prior rows.

Presentation, urgent headers and the transcript cache share an unchanged-prefix
comparison over immutable messages. The cache processes only the changed suffix
when the viewport, theme, renderer revision and dynamic resources allow it.
Deletion, reordering, resize and resource revisions still invalidate the affected
region. Prefix comparisons/copies remain proportional to metadata size; this is
not a claim that arbitrarily long histories use constant time or memory.

For histories longer than 24 displayed messages, Rich/resource rendering covers
the viewport and nearby messages. Follow-tail initially prepares the last 24;
saved anchors and tag navigation prepare their neighborhood. Offscreen messages
keep raw blocks, tags and lightweight height estimates, without allocating blank
rows or starting image conversion. Exact geometry replaces estimates on visit and
restores the current reading anchor. At most four visited render contexts are cached.
The viewport index is reused between paints and searched by binary search.

Explicit whole-session text search prepares the full transcript to retain existing
rendered-text search semantics. A single Markdown message remains the unit of
rendering, preserving tables, code and cross-block Markdown semantics. Very large
individual responses are not split into independently rendered arbitrary lines.

Transcript rows use immutable indexed concatenations. A stable prefix of past
messages, its anchors, object offsets and widths is reused while the final
message changes. Appending the current tail does not copy all historical rows,
anchors or objects. Anchor lookup is binary search; named restoration uses a
prefix/tail mapping. Message presentation caches avoid repeated datetime
formatting and display transformations for unchanged historical messages.

## Observation and progress clocks

Dirty events request observations; `output_refresh_interval` (default 0.1s) is
the minimum interval between read starts, not an active-Run polling timer. A
quiet active Run does not repeatedly read history at 10 Hz. The first pending
change after the interval elapsed is immediate. Bursts share one pending read,
and a change during that read remains pending for the next frame. Sustained
signals never postpone the deadline as a debounce would. Startup's published
snapshot is reused instead of requesting an identical initial frame again.

A separate two-second safety reconciliation remains for external public-API
edits and lost subscription events; unchanged observations are not published.
The existing hidden-view ceiling and urgent lifecycle wakeups remain. A lifecycle
change lets an already-running read finish and retain reusable public observations,
then rejects its obsolete frame. It does not cancel/restart storage reads per event.
Snapshot reads are preemptible by submission/control commands; durable writes
are not. Dropped-event reconciliation and persisted state remain authoritative.
Provider retry changes signal the observer directly, even before another Engine
event. Checkpoint-only receipts do not invalidate visible history: native
persistence and fresh resume-plan queries retain ownership of that evidence.

Progress model updates are immediate. Repaint requests coalesce through the
existing `progress_refresh_interval` timer; start/finish invalidate immediately.
Only the small single-row progress frame is rendered on the UI thread.

## Validation and instrumentation

2026-11-09: preparation-to-response header handoff passed **84 focused tests**
and the complete **1,205-test Hub suite** with Linux Python 3.12.14 on a
hash-verified WSL Linux filesystem snapshot. The full run took 2,469.99 seconds;
there were no failures, skips, missing tests or duplicate executions. Reports:
`tests/hub/reports/header-handoff-20261109/` (`focus04`, `full01`, `validation.json`).

Intermediate PTK frames are checked before and after worker delivery, including
progressive stale delivery, fast completion, reasoning-first responses, multiple
agents, wrapped headers, queued input and restored reading anchors. Header count,
row position, viewport height, follow-tail position and manual scroll intent stay
consistent across the preparation handoff. All 989 Hub, Hub-test and backend
source files match the tested snapshot; all 412 `llm/` files remain byte-identical.
Popup geometry, shared audit tests, strict architecture, complete contract coverage
and documentation checks passed. The five changed Hub source owners have current
review evidence and source hashes; execution and persistence ownership is unchanged.

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
are neither reparsed nor copied on each tail update. Width/theme changes prepare
the visible neighborhood again on the worker. Search indexes the transcript on
that same bounded lane. Large settings-form construction remains outside this
output-path optimization; existing full-suite diagnostics can still show its
UI callback cost.

`tests.hub.test_viewport_loading` asserts visible-neighborhood render counts,
visited-cache reuse/eviction, offscreen edits, search, event-loss reconciliation,
inactive-session invalidation and preemption of an obsolete display read.

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

## 버튼·출력 정렬과 지침 검증 (2026-11-09)

확정된 화면은 `Button`의 동적 텍스트 너비, `IconButton`의 지정 너비,
`AuxiliaryButton`의 기존 전역 색상과 상호작용을 구분한다. 출력 제목·본문·생각 과정은
상위 레이아웃의 여백을 다시 차감하지 않으며, 스크롤바 앞 한 칸은 Window margin이
소유한다. 사용자 말풍선·코드 블록 자체의 내부 여백은 유지한다.

Linux Python 3.12.14의 Linux 파일시스템 사본으로 검증했다. 자료는
`tests/hub/reports/button-output-alignment-20261109/`에 보관한다.

- 최종 집중 검사 `focus05`: **103개 통과**. 실제 PTK 셀 너비, 좁은 화면과 사이드바,
  Unicode/Nerd 아이콘, 언어 변경, 출력 캐시·준비 헤더 교체와 질문 UI를 포함한다.
- 최초 전체 검사는 추천 지침이 기존 입력 길이 상한을 넘는 것을 발견해 중단했다.
  지침을 짧게 고쳤으며 기존 850자 상한이나 검사 조건을 늘리지 않았다.
- `full02`: 전체 **1,209개 실행, 1,207개 통과, 2개 실패**(약 32분 27초).
  실패는 `test_conversation`의 이전 내부 여백 2칸 기대값이었다. 부모가 여백을
  제거한 실제 내용 너비 전체를 비교하도록 해당 기대값만 갱신했다.
- `focus06`: 그 모듈과 실제 화면·캐시·헤더 검사 **54개 모두 통과**.
  전체 실행 이후 제품 코드는 변경하지 않았다. 원래 전체 결과는 그대로 보존하며,
  `validation.json`이 전체 발견 ID와 재검증 ID를 대조한다. 최종 미해결 실패,
  누락·중복·skip은 모두 **0개**다. 이를 단일 실행 1,209개 전부 통과와 구분한다.
- 구조·감사 테스트 **39개**, strict 구조·계약 완전성·문서·팝업 geometry 검사 통과.
  감사 대상 **802개**의 미검토·해시 불일치·미분류·위반은 모두 0개다.
- 구현·Hub 테스트 **989개**의 최종 소스를 대조했다. `llm/` **412개 파일**은
  작업 이전 소스와 같으며, 실제 시스템 클립보드와 사용자 프로젝트를 시험 자료로 쓰지 않았다.

언어 지침과 추천 요청은 기존 transient completion 경로를 사용한다. 원본 사용자
메시지의 ID·내용은 유지하고 추가 번역/모델 호출은 만들지 않는다. 모의 Provider와
로컬 렌더링으로 검사했으며, 실제 사내 모델의 공개 생각 요약 언어·말투 준수는 보장하지 않는다.
