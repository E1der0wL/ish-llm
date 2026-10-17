# Hub operation recovery and guidance

Backend diagnostics remain authoritative. Hub translates structured operation codes
into localized next-action guidance; exception text is not used to infer retry safety.
Execution failures continue to use the execution error contract and system response.

## Resume admission

The input action and request-history retry share `ui/chat/resume.py`. Preparation
targets the selected logical turn, including older turns followed by completed
responses. It preserves the partial answer and does not append a temporary response.

`interaction_required` opens the durable interaction in the existing input form.
Cancelled or expired requests may be renewed by an explicit retry; denied requests
are not silently changed. Revision conflicts at resume admission are re-planned once.
Only a fresh backend plan can authorize resume or restart. Uncertain effects,
corruption and missing execution proof never cause an automatic fresh submission.

## Operation error presentation

Common request, settings, component and project operations show localized guidance.
RAG resume blockers retain diagnostic codes instead of being reduced to strings.
Explicit Hub preconditions (busy work, changed selection, non-resumable requests and
existing RAG imports) carry application diagnostic codes. Validation inside an editor
keeps its precise message so users can fix the entered value.

Project switching, restart failure and partial request deletion also use this path.
Unknown failures retain a generic notice rather than inventing a cause or retrying.
Renderer fallbacks and local JSON/form validation remain local display errors; they do
not admit execution or bypass backend recovery rules.

The log popup includes **Hub operation diagnostics** with the original contract and
traceback when available. This project-scoped, bounded buffer retains at most 32
entries in the current Hub process. It does not add a new persistent log directory;
backend execution logs retain their existing persistence and ownership.

## Regression coverage

- Current and older request retries keep their original display position.
- Stale snapshots cannot dismiss preparation based on a different response.
- Failed empty responses use the existing slot during restart preparation.
- Pending tool approval returns to the input form before any repeat execution.
- Completed tools are reused through backend checkpoints.
- Admission conflicts are bounded and cannot fall back to unapproved execution.
- RAG blockers and unknown failures have user guidance and separate detailed evidence.
