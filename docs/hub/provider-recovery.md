# Hub provider error analysis and input recovery

Hub selects bounded input summaries through llm's `CompletionRecovery` / `CompletionRepair` callback.
The backend owns eligibility, attempt/deadline budgets, Run/Step persistence, approval, Tool effects and
usage admission. `llm/` is unchanged by this integration. This is not a Run restart mechanism.

Error transport, classification and recovery authorization now follow the shared
[Hub error contract](error-handling.md). A model recommendation cannot authorize compaction unless
the captured error evidence also identifies a context limit. Tool argument repair shares the bounded
auxiliary model caller while retaining its backend-owned safety/approval checks.

## Execution and limits

`bootstrap.py` supplies `recover_completion` to `HubLoopEngine` (and explicitly opted-in
`CompletionRecoveryMixin` Loop engines) without an existing callback. The mixin also observes terminal
failures for optional explanation. Plain custom engines retain their own behavior. The shared recovery event relay
emits auxiliary model Steps through the owning Run's normal event consumer and awaits persistence.
Every auxiliary call is tool-free, uses the current completion implementation/connection, has no nested
SDK or Provider retries, and is counted by existing backend usage observation. No remote call is made
on successful requests. User-provided callbacks are retained; Graph/nested Agent execution does not borrow
the outer Loop's recovery state.

Provider transport retries keep their existing settings. After a request is rejected before response
data arrives, known context-limit evidence selects compaction directly, without
an extra error-analysis call. Other eligible rejections may use a short analysis
call to recommend `compact` or `stop`. Only rejected input can
be reduced; the model cannot change credentials, endpoints, settings, Tool arguments, approval or
retry unsafe effects. Unknown provider/response failures may be explained but not automatically replayed.
Authentication, rate limits and service outages use deterministic guidance and existing transport retry.

Corrected calls and transient retries share the selected Loop's `policy.provider.max_attempts` and original
Provider wall deadline. There is no second Hub retry loop or attempt budget. With no attempts remaining,
the backend does not call the repair selector. `recovery_timeout_seconds` additionally bounds Hub's
analysis and summarization work (default 60 seconds); Provider and enclosing Step/Run deadlines still apply.
SDK work that ignores cancellation can finish in its worker, but cannot admit a corrected call after cancellation.

Only reference bodies listed in the backend's `CompletionRecovery.editable` may be summarized. Hub returns
index-to-content replacements, not a rewritten request. The backend validates those replacements and preserves
system instructions, current user/steering input, Tool calls/arguments/IDs, message count and ordering.
Hub uses `llm.components.summarization.summarize_fragments`; the original conversation, checkpoints
and Tool outputs remain owned by llm. This live repair does not create a Memory compaction request or change
the user's Memory settings. Completed summaries are reused within this Run; there is no automatic restart
replay or historical record migration. These records are not a replacement for Memory's durable summary API.

Defaults are 12000 input characters per fragment (including previous summary and payload), 2000 output
summary characters and 16 summary calls per recovery. These are character budgets, not model-token limits.
Auxiliary requests do not inherit the primary Agent's large output-token reserve;
their own bounded summary request and backend usage policies apply.
Reduction must finish and actually shrink the request. The backend completion pipeline validates the
proposal and applies its configured policies. A partial/invalid summary is never
sent as a substitute for the full input. Oversized current requests, Tool definitions, incomplete Tool
exchanges or material exceeding recovery budgets may still need user changes.

## Presentation and diagnostics

Provider error details are always enabled in Hub; the former opt-out setting is removed.
Request structure summaries default to enabled. Explicit saved `false` values
remain respected. Server error fields can contain task content; structure summaries omit message bodies and
Tool arguments. The assistant failure message includes the captured server detail and validated model
explanation, with model inference labeled. Diagnosis reads the same failed Step first, then the owning
Run result's diagnostic if no Step is available, never old log files.

The common retry header displays neutral response-checking, context compression,
corrected-request and rejected-proposal phases. The UI withholds a terminal failure
verdict while recovery is still possible; known context limits start with context
compression rather than error analysis. Diagnostic evidence and eligibility are
not relabeled or weakened by these presentation choices.
Countdowns use the actual `provider_retry.delay_seconds`; recovery phases invent no waiting time. Recovery
outcomes and explanations are retained in the final failure diagnostic and provider activity log; auxiliary
analysis/summary output is internal Step data, not assistant response text. A successful recovery completes
the original Run and original user/assistant pair. Model analysis failure leaves the original error visible.

Settings live under General → Error analysis and recovery and apply after restarting Hub. Tests cover real
Loop execution, a single Tool invocation across correction, preserved source/input, counts, cancellation,
invalid analysis, incomplete summary, deadlines and terminal/transient error exclusions using fake models.

## Validation (2026-10-17)

Linux Python 3.12.14, using hash-recorded source copies on the WSL Linux filesystem:

- Recovery and existing Tool retry tests: 25 passed.
- Hub discovery: 466 tests, 457 passed and 9 skipped because Xvfb/xclip were unavailable.
  Reports: `tests/llm/reports/ish-provider-yp_v5jsy/{focused,hub}.txt`.
- llm discovery: all 1565 passed. Report: `tests/llm/reports/ish-provider-3p3blmd5/suite.txt`.
  The duplicate Hub run from that older snapshot was stopped in favor of the final Hub run above.
- All 274 Hub source/test Python files match the final snapshot; all 175 llm Python files match the
  backend validation snapshot. No llm source changes were made.

Provider failures in the new tests are simulated; no corporate model endpoint was contacted.
