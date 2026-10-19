# Provider independence: stage A

Baseline: `94229cb865374d99f20ec48c5e48269488041a4e`. Work started clean on
the Codex-managed `work` branch. Scope: `llm/` and `tests/llm/` only.

## Implementation sequence

1. Freeze real baseline persistence; add common request/message/content/Tool/
   response values and explicit LiteLLM/legacy codecs. Validate JSON, call/result
   matching, opaque state, and runtime client identity.
2. Connect streaming, Loop, Tool registry, processors, Memory/Goals and token
   accounting to common values. Preserve existing public compatibility at named
   boundaries, not with SDK conditionals in engines. Validate real Tool execution,
   approval, stream failures, usage, and processor authority.
3. Normalize nonstreaming RAG/Vision operations. Preserve existing public raw
   return APIs through adapter projections. Verify worker descriptors and Graph
   bindings against old data, including completed Tool receipt reuse.
4. Run the complete backend suite and relevant unchanged Hub tests, classify all
   remaining provider-wire references, document failures/skips and stage gates.

## Compatibility decisions

Persistent conversation Message records retain their existing role/ID/ownership.
The new model values describe invocations only. A versioned model-message encoding
is distinct from the old adapter-owned response record. Old records are decoded
at one compatibility boundary. Existing worker and Graph binding encodings stay
stable so adding a common representation cannot grant a new approval or invalidate
unchanged approved tools. SDK options and opaque state belong to adapters.

No Agent domain, dispatch, fan-out, cross-session API, or final-review work (B–F)
is included in this stage. Relevant stage-A fault boundaries are model stream
receipt (F06), Tool approval/effects (F07–F10), and checkpoint consistency (F11).
Later-stage fan-out and final-review matrix entries are not claimed as tested.

## Baseline evidence

Python 3.12.14, LiteLLM 1.103.1, OpenAI 2.54.0; `TERM=xterm-256color`.
The focused baseline command (adapter boundaries, Loop, completion processing,
RAG models, Vision, Tool workers) ran 99 tests, all passed.
`tests/llm/fixtures/provider_v1/generate.py` ran before production changes and
created real completed, failed-after-Tool, and approval-paused workspaces. The
manifest records the baseline SHA, identifiers, and original file hashes.
