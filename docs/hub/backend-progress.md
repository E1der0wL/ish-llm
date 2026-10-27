# Backend startup and foreground progress

Hub keeps one shared one-row progress area in Home and Settings. Startup starts its
task as soon as the backend worker is launched; it finishes only when the initial
snapshot and saved reading position reach the UI, or when startup fails.

Component, definition and Agent editor dialogs register their status surface with
`TaskProgress`. While one is the active dialog, only that surface renders the
shared task/notice state; the underlying page reserves its usual row without
duplicating the display. The weak surface registry does not retain closed dialogs.
Closing the popup restores page rendering without recreating or losing tasks.

Startup reports engine/component preparation, storage connection, project selection,
new-project schema/Agent/starter-skill setup when needed, session enumeration,
selected-session recovery and conversation loading. Opening an existing project
does not eagerly resolve a second built-in toolkit. Capabilities are still resolved
and validated by the backend when an execution needs them. Session titles read for
selection are reused for notification registration instead of read twice.
New-project component names come from the registered catalog, without generating a
complete settings schema just to enumerate names.

`backend/progress.py` carries immutable observation records through a task-local
scope. Worker admission/lock waits and runtime stages use this scope without adding
arguments to llm APIs or changing scheduling, persistence or cancellation policies.
Detached background tasks cannot report into a completed foreground scope.

`CommandCallbacks` owns the optional stage callback alongside acknowledgement and
domain progress. The worker consumes stages; runtime/llm calls never receive this
UI-only argument. Stage selection uses explicit `ManagementAction`, `RAGAction`,
`SettingsOperation` and runtime method declarations. Unrecognized actions show a
neutral work stage instead of guessing read/write behavior from a name prefix.

`UIRequests` dispatches stages on the UI loop independently of acknowledgements and
domain progress. `ProgressCompletion` connects existing Settings/component progress
tasks to their request's stage receiver. Session operations, history/log loading and
explicit snapshot reads use the same progress area. Periodic snapshots, diagnostic
polls and ordinary assistant streaming do not create extra loading tasks. RAG's own
chunk/stage observations and bulk-deletion counts remain intact.

Counts represent actual items in the current stage, not estimated overall completion.
Unknown work sizes stay indeterminate. Stage changes invalidate the rendered line
immediately while repaint requests remain coalesced at the configured progress tick;
completed task IDs ignore late observations. Loading never takes focus
or adds another layout row. All stage labels are in the Korean/English language packs.

Restart also reports cancellation/draining followed by closing the old backend; the
replacement instance then reports its normal startup stages.

General dialogs own a bottom status row, after the actions. A dialog with an
existing row passes it to `Dialog.status_bar`; Agent's embedded Settings page
exports its row instead of rendering it twice. Popup loading keeps the underlying
page's row empty. Memory history/active-exchange/Tool-result summary Step events
project `summarizing` into the Assistant header; provider completions inside the
summary do not relabel it as ordinary thinking. Input-recovery compaction also
displays "Summarizing" while it is summarizing; actual analysis/retry phases retain
their own labels and the original diagnostics remain unchanged.

The thought timeline shows `컨텍스트 압축` / `Context compaction` while a
Memory summary or input-recovery summary Step is active, even before the helper
produces text. Auxiliary summary/diagnosis reasoning is not Agent reasoning and
is excluded by the owning Step ID from both live events and reconciled Run
receipts. Existing Agent thoughts/Tool history and partial answers remain visible;
raw helper receipts and diagnostics remain available in backend records. The
summary row disappears when that Step ends. No additional backend reads or
summary calls are introduced by this projection.
