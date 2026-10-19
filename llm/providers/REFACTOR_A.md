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

## Implemented contracts and compatibility

- `core/model_protocol.py` is the stdlib-only model invocation contract. It is
  separate from persisted Message/Run records: typed content, completed Tool
  arguments/results/definitions, requests, responses, normalized termination,
  streaming observations, embedding vectors and ranking rows. Missing usage is
  `None`; existing CompletionResult's empty usage remains explicitly incomplete.
- A common streaming adapter advertises `model_protocol = 1`. Its decoder owns
  incomplete argument fragments, wire termination, opaque signatures and SDK
  metadata. No Tool is prepared until successful final decoding and whole-batch
  validation. Receiving any chunk still prevents automatic replay.
- Request copies isolate mutable options and JSON state while retaining live
  client/callback identity. Mark token counters `model_protocol = 1` to receive
  common requests. Unmarked host counters retain their old kwargs input through
  the codec. Services register defaults through `providers/tokens.py`.
- Processor code uses `CompletionMessage.message`, `with_text()`,
  `CompletionRequest.to_model()`, and observation `invocation`/`answer`/`messages`.
  The old mutable `.value`, `.to_kwargs()`, observation properties and recovery
  selector kwargs are explicit compatibility projections. Nested view edits,
  aliases and deep copies retain old mutation behavior; edits preserve opaque
  state. Tool authority and active exchanges remain protected.
- New Loop response records have a versioned common encoding. Old response
  records are read by `restore_response` without bulk migration. Persisted
  conversation records, approval/operation keys, Graph bindings and worker
  descriptor fingerprints retain their v1 encodings. Memory summary bodies and
  digest inputs retain their explicit v1 projections; original responses and
  source records remain available in checkpoints/Steps.
- `OperationAdapter` owns nonstream transport, configuration, retries, admission,
  observations and output formatting. RAG consumes normalized vectors/ranks/text;
  Vision supplies common image parts. Existing EmbeddingModel.embed and
  RerankModel.rerank native-return APIs remain available through provider
  projections; embed_result/rank expose normalized results. Custom adapters own
  their configuration schema, including settings incompatible with LiteLLM.
- BaseEngine's old response-dictionary API still projects only the answer;
  reasoning remains a separate observation. Common responses preserve reasoning
  in opaque adapter state for subsequent requests. Old finish-reason diagnostics
  are classified through a provider compatibility reader without rewriting them.

This is not an Agent domain, dispatcher, multi-Agent Run, cross-session messaging
or review_final implementation. B–F have not started.

## Reproducible Provider ownership audit

Run `.venv-linux312/bin/python -m tests.llm.support.provider_audit` from the repo.
The script combines the specification's Provider search with AST literal checks
(including role/content/messages/function/results). The committed result is
[PROVIDER_AUDIT.json](PROVIDER_AUDIT.json): **48 matching files, 408 matching lines,
0 unclassified files**. Categories are manually assigned after caller review;
the inventory is not proof of correctness based on names alone.

The ten Provider files own wire formats, SDK options, lazy SDK loading, metadata
and legacy projections. Four common-path files consume semantic values and use
named compatibility boundaries. Seven API/compatibility files retain public
return shapes or versioned Memory encodings. The other 27 files own prompt,
conversation, Tool provenance, document/vector, journal or other domain data.
In particular, Prompt role/content, RAG ingestion/cache embedding keys, persisted
journal delta, refinement Step call IDs and CLI choices are not SDK parsing.
Default adapter composition in BaseEngine remains explicit and replaceable.

## Required path-by-path review (§4-1 and §4-2)

Paths below are relative to `llm/`. “Kept” means inspected and intentionally
unchanged, not excluded from regression tests.

| Path | Result and ownership decision |
|---|---|
| `core/model_protocol.py` | Added common, validated model values; no SDK or persistent domain imports. |
| `providers/streaming.py` | Typed adapter protocol; CompletionUpdate is re-exported from core for compatibility. |
| `providers/litellm.py` | Encodes common requests, finalizes typed responses, normalizes usage/termination and preserves opaque metadata. |
| `providers/requests.py` | Kept: SDK response validation, retries, deadlines, observation and admission remain inside Provider. |
| `providers/embeddings.py` | Kept: SDK vector extraction and finite/dimension checks already belong to Provider. |
| `engines/base.py` | Common requests/results; explicit old-adapter/output projections; receipt retry boundary unchanged. |
| `engines/loop/engine.py` | Typed transcript and Tools; versioned response checkpoints; old response loader and binding encoding retained. |
| `components/processing.py` | Semantic processing with protected authority, old mutable API projections and detached observations. |
| `components/tools/registry.py` | Semantic definitions/argument objects; frozen old public definitions and JSON-string argument compatibility. |
| `components/tools/function.py` | Builds ToolDefinition including strict semantics, then uses the compatibility projection. |
| `components/tools/process.py` | Descriptor validation delegated to the definition codec; IPC/fingerprint remain v1. |
| `components/tools/_worker.py` | Kept: v1 descriptor/execute IPC and descriptor hash are intentionally stable; baseline golden comparison passes. |
| `components/tools/decorator.py` | Kept: strict is decorator metadata consumed as a common ToolDefinition property. |
| `policies/completion.py` | Counts/selects common messages; old kwargs counters are translated only by the codec. |
| `components/memory/processing.py` | Common auxiliary completion, threshold counting, Tool previews and source-preserving compaction. |
| `components/memory/compaction.py` | Common message bodies; explicit old summary encoding and provider-owned option reservations. |
| `components/memory/tool_results.py` | Uses ToolCall/ToolResult values and retains original Step resource references. |
| `components/goals/processing.py` | Uses common text replacement instead of modifying SDK dictionaries. |
| `components/rag/_client.py` | Injectable operation adapter and its configuration; native invocation compatibility retained. |
| `components/rag/embedding.py` | Common vector result path and provider-owned native return/cache enforcement. |
| `components/rag/rerank.py` | Normalized rank API beside the original native return API. |
| `components/rag/extraction.py` | Common prompt messages and text result; adapter translates JSON output intent. |
| `components/rag/data.py` | Consumes validated normalized rank rows; never reads SDK results/relevance_score. |
| `components/vision/backends.py` | Common multimodal request and normalized response; old analysis/image helper API preserved. |
| `components/vision/component.py` | Selected adapter owns completion validation, schema and enforced settings. |
| `services/runtime/policies.py` | Default counter catalog comes from Provider registration, not a LiteLLM function import. |
| `services/lifecycle/projects.py` | Same provider-owned counter catalog; host overrides remain supported. |
| `services/history/retention.py` | Builds a common request and delegates counter compatibility. |
| `core/diagnostics.py` | Uses normalized termination; old persisted detail decoding belongs to Provider. |
| `engines/graph/engine.py` | Kept: existing definitions() projection freezes v1 binding; old Graph checkpoint resumed with receipt reuse. |
| `providers/schema.py` | Kept: LiteLLM configuration and retry fields are adapter-owned. |
| `providers/retry.py` | Kept: SDK/client retry settings remain at the transport boundary. |
| `providers/parameters.py` | Copies common values and containers while retaining live client/callback identity. |
| `providers/runtime.py` | Kept: SDK request summaries and sanitized diagnostics belong to Provider. |
| `providers/observations.py` | Kept: ModelCall callbacks already own usage/count interpretation; no wire parsing in service quotas. |
| `components/tools/component.py` | Kept: public Tool prepare returns the frozen definition projection. |
| `components/tools/data.py` | Kept: prepare facade and persisted source/requirements contracts remain stable. |
| `components/tools/builtin/component.py` | Kept: registry accepts copied old definitions and normalizes them at its boundary. |
| `components/tools/builtin/catalog.py` | Kept: domain Tool argument schemas are provider independent. |
| `components/tools/constraints.py` | Kept: JSON Schema constraints act on semantic parameter/argument objects. |
| `components/memory/component.py` | Injects the auxiliary adapter and its schema/validation into Memory processing. |
| `components/memory/data.py` | Kept: stored Prompt rendering and summary references are domain data. |
| `components/memory/work_state.py` | Kept: provenance tool_calls are source call IDs, not model wire calls. |
| `components/rag/component.py` | Selected model operation schema/validation now comes from its adapter; cache/storage contracts retained. |
| `components/rag/prompts.py` | Kept: persistent Prompt examples and extraction contract; converted once into common messages. |
| `components/rag/ingestion.py` | Kept: validated vectors, cache identity and chunk receipts consume the model abstraction. |
| `components/rag/jobs.py` | Kept: document ingestion jobs/checkpoint/cache resume operate on normalized domain results. |
| `components/prompts/component.py` | Kept: stored role/content/messages belong to Prompt schema and rendering. |
| `components/vision/data.py` | Kept: text analysis output and explicit legacy image helper remain compatible. |
| `engines/agents.py` | Kept: Agent Prompt rendering, Tool scope and call-count metadata are domain contracts. |
| `engines/graph/tool.py` | Kept: old JSON argument serialization is accepted by ToolRegistry.prepare; no provider parsing. |
| `core/policies.py` | Kept: retention rejects caller-supplied message injection into counter options. |
| `core/results.py` | Adds normalized termination while retaining raw finish_reason and unknown-usage projections. |
| `core/models.py` | Kept: persisted Message role/ID/content and storage versions are intentionally separate. |
| `llm.py` | Kept: facade, lazy dependency declaration and CLI domain choices; no new persistent API required. |
| `services/composition.py` | Kept: host counter injection and service ownership are unchanged. |
| `services/runtime/runs.py` | Kept: JSON-only completion/checkpoint projections and lifecycle ownership; common values serialized by engines. |
| `services/runtime/usage.py` | Kept: reservation and accounting use ModelCall callbacks; unknown usage retains its reservation. |
| `services/runtime/checkpoints.py` | Kept: JSON, revision, digest and explicit-resume checks remain authoritative. |
| `services/runtime/tools.py` | Kept: execution result data, approvals and operation receipts are domain authority, not model messages. |
| `services/schema.py` | Kept: counter resource names still come from the registered catalog. |
| `services/api.py` | Kept: retention counter selection/query API is unchanged. |
| `services/lifecycle/components.py` | Kept: component ModelCall observation and counter scope remain in the service layer. |
| `services/history/context.py` | Kept: persisted context selection is separate from model invocation conversion. |
| `services/history/conversation.py` | Kept: conversation records and delta journal keep their original schema. |
| `components/refinement/component.py` | Kept: evidence call IDs refer to Steps; no SDK response parsing. |
| `components/refinement/data.py` | Kept: Step/source binding and approval evidence identity are unchanged. |

## Fault boundaries and concurrency scope

All named tests below are included in the full `tests/llm/` command. New fake
stream cancellation uses an asyncio Event handshake, not an arbitrary delay.
Existing process-crash fixtures use deterministic exit points and check durable
receipts/files after reopening. No random seed is used by these scenarios.

| ID | Stage-A status | Evidence and scope |
|---|---|---|
| F01 | N/A | New Agent request admission belongs to C/D; existing single-request transaction tests still run. |
| F02 | N/A | Dispatcher queue reconstruction is not changed in A. Existing queued-resume tests remain in regression. |
| F03 | N/A | Multi-recipient fan-out is not implemented in A. |
| F04 | N/A | `current_run_ids` is a later-stage change; existing Run begin rollback tests remain in regression. |
| F05 | N/A | New Project Agent revisions are B/C. Existing Graph/Loop binding rejection and baseline descriptor identity are verified. |
| F06 | Covered | `test_common_provider` before/after receipt failures and Event-controlled cancellation; `test_loop` cancellation during creation/blocked read; `test_long_running` partial arguments/output never replay. |
| F07 | Covered | `test_domain_hardening` failed interaction commit, committed response notifications and zero pre-approval effects; `test_common_provider`/`test_provider_legacy` approval/reopen/execute once. |
| F08 | Covered | `test_isolated_tools.OperationTests.test_claim_write_failure_has_no_effect`; `test_graph_checkpoints` start/checkpoint failure prevents effects; started/uncertain states require explicit reconciliation. |
| F09 | Covered | `test_isolated_tools` real child exit after external effect, completion-receipt failure, and zero automatic replay on restart. |
| F10 | Covered | `test_isolated_tools` completed operation reuse after backend restart; `test_execution_boundaries` atomic Step/Tool receipt failure; baseline/current common-provider checkpoints reuse completed effects. |
| F11 | Covered, with limitation below | `test_graph_checkpoints`, `test_backend_contracts`, `test_checkpoint_lifecycle`, `test_transactions`: bad/missing/digest/revision snapshots, checkpoint publication failure, crash before/after fsync/commit, original records preserved. |
| F12 | Covered for existing single-Run lifecycle | `test_transactions` final Step/Run/Session/Assistant rollback and committed deltas; `test_long_running` output reconciliation. Multi-Agent active-ID behavior is N/A until D. |
| F13 | N/A | Cross-session transfer is E. |
| F14 | N/A | review_final is F. |

The storage regression includes partial JSON/tails, replacement/truncation,
fsync failures and process death. A dedicated kernel short-write return injection
was **not** executed. This report does not claim exhaustive physical storage
fault verification or completion of the specification's entire safety matrix.

| ID | Status and reason |
|---|---|
| C01 | N/A: two Agent Run completion order requires B–D. |
| C02 | N/A: per-Agent failure isolation requires B–D. |
| C03 | N/A: same-Session multi-Agent cancellation/execution requires D. |
| C04 | N/A: same-Session approval wait alongside another Agent requires D. |
| C05 | N/A: the new Agent request queue/serialization contract requires C/D. |
| C06 | N/A: multi-Agent shared-resource race requires D; existing operation-key/receipt races remain tested. |
| C07 | N/A: multi-Agent quota admission requires D; existing shared quota tests and new unknown-usage counter test run. |
| C08 | N/A: new Agent detach/revise/admission binding requires B–D. |
| C09 | N/A: multiple active Run IDs are not introduced in A. |
| C10 | N/A: fan-out versus Message deletion/retention/clone requires D. |
| C11 | N/A: cross-session interaction requires E. |
| C12 | N/A: final review iteration isolation requires F. |

## Golden fixture evidence

`tests/llm/fixtures/provider_v1/workspace.tar.gz` was generated before production
changes on baseline `94229cb865374d99f20ec48c5e48269488041a4e` using
`python -m tests.llm.fixtures.provider_v1.generate`. It contains completed,
failed-after-Tool and approval-paused real file-backed workspaces. IDs and hashes
are in `manifest.json`. Model calls are deterministic synthetic test functions.

The supplemental `graph.tar.gz` and `graph_manifest.json` were generated from a
separate `git archive` of that same baseline. `generate_graph.py` verifies every
imported baseline `llm/` source against `git show BASE:path` before running. Its
manifest includes source hashes, storage version, workspace hashes and the real
baseline function Tool descriptor/fingerprint. Generation command:

```bash
git archive 94229cb865374d99f20ec48c5e48269488041a4e | tar -x -C /workspace/refactor-evidence/baseline-src
PYTHONPATH=/workspace/refactor-evidence/baseline-src .venv-linux312/bin/python \
  tests/llm/fixtures/provider_v1/generate_graph.py tests/llm/fixtures/provider_v1
```

Fresh backends load copied archive bytes, verify hashes, preserve prior Run
status/header, resume with exactly one authorized effect, reuse completed graph
nodes and compare worker descriptor hashes. No fixture contacts an external API
or contains actual user data. `definitions.json` additionally freezes baseline registry outputs where
Tool description is omitted or strict is explicitly null/false. Its generator
verifies the baseline registry source hash. These distinctions must survive the
codec because binding/fingerprint bytes, not just their semantic meaning, grant
resume compatibility. New Agent-domain/fan-out fixtures are intentionally
not claimed: those storage contracts are outside A.

## Verification commands and results

Run commands from the repository root with this prefix:

```bash
export TERM=xterm-256color
export LITELLM_LOCAL_MODEL_COST_MAP=True
export LITELLM_MODE=PRODUCTION
PY=.venv-linux312/bin/python
```

Python 3.12.14, LiteLLM 1.103.1 and OpenAI 2.54.0 were used. The complete suites
ran outside the nested execution sandbox because bubblewrap's UID namespace
mapping cannot run inside that sandbox. This is an execution environment
constraint, not a skipped or weakened isolation assertion.

| Command after `$PY -m unittest` | Actual result |
|---|---|
| `tests.llm.test_model_protocol tests.llm.test_common_provider tests.llm.test_provider_legacy tests.llm.test_backend_contracts tests.llm.test_adapter_boundaries tests.llm.test_memory_processing tests.llm.test_work_state tests.llm.test_goals tests.llm.test_loop tests.llm.test_long_running tests.llm.test_tool_workers` | 167 tests, OK (runtime stage). |
| `tests.llm.test_common_operations tests.llm.test_rag_models tests.llm.test_rag_components tests.llm.test_rag_resilience tests.llm.test_provider_runtime tests.llm.test_vision tests.llm.test_observability.BoundaryTests` | 105 tests, OK (operation stage). |
| `tests.llm.test_model_protocol tests.llm.test_completion_processing tests.llm.test_memory_processing tests.llm.test_common_provider` | 72 tests, OK (legacy mutation/alias fix). |
| `-v tests.llm.test_policy_ownership tests.llm.test_backend_contracts tests.llm.test_common_provider tests.hub.test_provider_recovery tests.hub.test_provider_stabilization` | 73 tests, OK (legacy override/kwargs fix). |
| `tests.llm.test_common_provider tests.llm.test_model_protocol` | 19 tests, OK (common fake through legacy override, opaque state and runtime identity). |
| `tests.llm.test_provider_legacy tests.llm.test_model_protocol tests.llm.test_tool_workers tests.llm.test_graph_checkpoints` | 52 tests, OK (omitted/null v1 Tool fields and binding bytes). |
| `discover -v -s tests/llm -t .` | 1,658 tests in 330.739s: 1,642 passed, 16 environment skips, 0 failures/errors. |
| `discover -v -s tests/hub -t .` | 534 tests in 445.651s: 470 passed, 64 environment skips, 0 failures/errors. |

Intermediate failures were retained and resolved, not counted as successful
validation. The first full backend run had 1,648 tests, 10 failures, 3 errors and
16 skips. Causes: misplaced adapter protocol marker, old kwargs presence/shape,
legacy diagnostics/reasoning projection, outdated incomplete Tool test fixtures,
and an operation-schema lookup on a fallback class. The second full run had
1,655 tests and one failure (an unwanted empty messages argument); the third had
1,656 tests and the same failure before its fix. Hub's first run had 534 tests,
15 failures and 64 skips; the second had two failures exposing an old
stream_completion override contract. The fourth backend run passed 1,657 tests
with 16 skips; the third Hub run passed 534 tests with 64 skips. A subsequent
review found the omitted/null Tool definition distinction and added a baseline
golden fixture plus the 52-test check before the final full runs. Those production issues were corrected in
`llm/`; no Hub assertion or product code was edited. The initial focused Memory
run (59 tests) had one invalid fixture error; initial RAG/Vision run (74 tests)
had two obsolete import-boundary assertions. Updated fixtures remain complete
Tool calls, and the import checks still prohibit Engine/Service/SDK loading.

Full logs are retained in the cloud workspace under
`/workspace/refactor-evidence/`: `full-llm-1.log`, `full-llm-2.log`,
`full-llm-final.log` (third, failed intermediate run), `full-llm-4.log`, `full-llm-5.log`,
`full-hub-1.log`, `full-hub-2.log`, `full-hub-3.log`, `full-hub-4.log`, `runtime-stage.log`,
`operations-stage.log`, `processor-compatibility-final.log`,
`legacy-overrides.log`, `override-common.log`, and `definition-compatibility.log`.

## Remaining limits, gates and next stage

- 16 backend tests require the external `ish.platform` host checkout (one plugin
  loader test and 15 dependency/installer tests). They are environment-blocked,
  not passes. The changed worker descriptor/IPC is covered by real local child
  processes and baseline hashes, but host loader/installer integration remains
  unverified here.
- 64 Hub tests are environment-blocked: 55 require the reference ish.platform
  host and nine require Xvfb/xclip. No native host/clipboard compatibility claim
  is made. No live authenticated external model service was exercised; the real
  LiteLLM SDK is covered with local mock HTTP/SSE.
- Implementation gate: A implemented and its focused tests passed. Safety gate:
  the listed applicable fault regressions passed; exhaustive storage short-write
  injection remains unverified. Full backend and external-app gates are limited
  by the explicit environment skips. Deployment-readiness is not claimed.
- New common-aware Engine stream overrides should set `model_protocol = 1` on
  their `stream_completion` method. Old unmarked overrides receive a kwargs
  snapshot. Unchanged forwarding/parameter-only edits preserve common opaque
  state; changing foreign opaque transcripts through the old API fails clearly
  and requires the common API. This prevents silent signature loss.
- There is no automatic storage migration or destructive cleanup. Rolling code
  back does not make old code understand newly written common response records.
  Preserve a pre-change workspace backup for downgrade, or finish/resume those
  new Runs with this version. Original old fixture bytes and old completed
  records remain readable and unchanged.
- Next stage B would review `core/models.py`, `core/paths.py`, `core/contracts.py`,
  `components/agents/{component,data}.py`, `services/lifecycle/{projects,sessions}.py`,
  `services/api.py`, `services/schema.py`, `engines/agents.py`, and `llm.py`.
  Confirm actual domain ownership/storage paths before implementing. Main risks
  are Agent identity/revisions, old AgentComponent definitions, immutable Run
  bindings, approval scope, snapshot/CAS and downgrade compatibility. Run dispatch,
  fan-out, cross-session requests and final review remain C–F, not implicit work
  bundled into B. Stop here for user review before starting B.

## Branch, commits and publication

Work used the Codex-managed `work` branch and was pushed to
[`refactor/llm-backend`](https://github.com/E1der0wL/ish-llm/tree/refactor/llm-backend).
`main` was not edited or merged. Production/test code revision used for the final
complete suites: `49629ad3c6216e1a423c69362801a30127dd5dd9`; the following evidence
commit changes only this report, the audit result and its inventory script.

| Commit | Review unit |
|---|---|
| `ba229f5` | Common contract and baseline fixture capture. |
| `ebf1a5b` | Streaming, Loop, Tool, Processor, Memory/Goals and counters; baseline Graph/worker checks. |
| `fd393da` | RAG/Vision operation adapters and normalized results. |
| `305de04` | Legacy Processor view aliases/copies and opaque multimodal state. |
| `66d6057` | Old Engine override kwargs and standalone request-presence compatibility. |
| `49629ad` | Exact omitted/null v1 Tool definition binding bytes, verified with baseline output. |

The branch push succeeded and its remote SHA was read back. Draft PR creation
was attempted with `gh pr create --draft --repo E1der0wL/ish-llm --base main
--head refactor/llm-backend --body-file /workspace/refactor-evidence/pr-body.md`
(and an explicit stage-A title). GitHub returned
`Post "https://api.github.com/graphql": Forbidden`; repository API reads also
returned Forbidden, including outside the nested execution sandbox. **No PR was
created.** A ready-to-use PR body remains at the path above; the
[GitHub PR creation page](https://github.com/E1der0wL/ish-llm/pull/new/refactor/llm-backend)
is a creation link, not an existing PR.
