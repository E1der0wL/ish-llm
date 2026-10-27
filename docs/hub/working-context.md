# Tool context admission

Hub uses the existing Memory completion processor and the calling Loop's
CompletionPolicy. `backend/execution/working_context.py` owns the application
fallback; it is called by the existing Hub completion session after Memory.
The Hub processor follows an explicitly configured Memory priority too, without
rewriting that priority or any persisted project setting.
It does not create another conversation store, retry loop or token counter.

## Before each main model call

1. Memory applies its selected recall, Tool result storage/summary and active
   exchange compaction policies. Its auxiliary model input budget and failure
   policy remain independent of the main model.
2. Hub replaces oversized surviving Tool bodies with verified Step references
   when Memory and `memory_tool_result` are available to the selected Agent.
   This also covers partial/failed summaries and external Tool results. The
   per-result threshold follows Memory's `tool_result_chars`, or the Hub page
   default when unset. Existing reader pages are not immediately replaced again.
3. If Tool bodies accumulate beyond `HubDefaults.TOOL_CONTEXT_CHARS` (24,000
   characters), or the native CompletionPolicy cannot fit the required active
   request, older Tool bodies become references. The latest result/page remains
   available for the next decision.
4. When this still does not fit, complete prefix exchanges can become a compact
   reference index appended after the original user instruction. The native
   `compacted_tool_calls` contract declares removed exchange IDs. Hub retains the
   newest complete exchange (or the explicit Memory `active_keep_iterations`)
   and stops at steering, a non-Tool message, an
   incomplete pair or a result without a verified Step reference. A reference
   index is explicitly **not a semantic summary or proof of successful work**.
   Explicit `compact_active: false` disables this prefix removal too; body
   references and local budget rejection remain available.
5. Hub checks the transformed request again. Unreducible Tool bodies or protected
   instructions fail locally with `context_budget_exceeded`; oversized data is
   not blindly sent to the provider. Loop still performs its normal final budget
   selection and validation after all completion processors.

Only detached model input changes. Original messages, Tool arguments and results,
approval decisions, checkpoints, side-effect receipts and their owners remain
unchanged. No command is rerun to recreate a result. `memory_tool_result` reads
the completed Step through backend APIs, using the supplied Run/Step identity;
Hub neither guesses an ID nor reads backend files directly.

## Summary failures and cancellation

Hub's existing Memory default `failure_mode: continue` permits a failed summary
to reach this reference fallback. The configured Memory call limits and provider
retry policy remain authoritative. Explicit `failure_mode: raise` is respected.
The fallback itself calls no model and adds no retry. Missing references/read
capability cause a local budget failure instead of an invented source.
Cancellation and token-counter failures propagate without becoming summaries.

Normal context reduction emits an internal `hub.context` memory Step and uses
the existing **컨텍스트 압축** presentation, not error-recovery presentation.
The Step contains only a reference count, not copies of the Tool data.

## Configuration and limits

- A Memory summary model enables the existing setup defaults on explicit save.
  Its default `model_input_chars` remains 18,000 characters. No model name,
  credentials or endpoint is chosen by this feature.
- Set the selected Loop's `policy.completion.max_tokens`, `reserve_tokens` and
  registered `counter` for the actual server/model. Hub observes the exact
  resolved CompletionPolicy that Loop passes to this execution, including
  nested Loop Agents, and does not inject a global model capacity.
- The 24,000-character Tool-body ceiling is an application guard, **not a token
  estimate**. If CompletionPolicy is unset, there is no claimed guarantee for
  the entire model request. Many Tool definitions, a huge current user request
  or server-specific tokenization still need the correct native policy.
- Prior conversation selection and semantic summaries stay with Memory and
  CompletionPolicy. Large raw output should still use Data storage and paged
  Tools; this fallback is not a replacement for streaming/capture at the source.

Regression coverage is in `tests/hub/test_working_context.py` and
`tests/hub/test_memory_setup.py`: summary failure, accumulated small results,
native budget admission, large Tool arguments, steering boundaries, composed
Memory compaction IDs, missing sources, cancellation and one execution per Tool.
