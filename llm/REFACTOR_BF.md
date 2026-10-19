# Agent backend refactor: stage evidence

The specification is `CODEX_ish_llm_backend_worker_verified.md`. A's implementation
and historical evidence remain in [providers/REFACTOR_A.md](providers/REFACTOR_A.md).
Changes use Codex's `work` checkout and push to `refactor/llm-backend`; `main`,
`hub/`, and `tests/hub/` are unchanged. `ish.platform/` is ignored external source.

## Reproducible environment and host correction

Python is **3.12.14**, host `E1der0wL/ish` is pinned at
`d833790959a76ac457fdeea3f21a6bea1eecfe12`; imports use `ish.platform/src/ish`.
LiteLLM 1.103.1 / OpenAI 2.54.0 preserve A's verified dependency baseline.
Commands run from the repository with:

```sh
export TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION
export HUB_TEST_XVFB=/workspace/.setup/x11/root/usr/bin/Xvfb
export HUB_TEST_XCLIP=/workspace/.setup/x11/root/usr/bin/xclip
.venv-linux312/bin/python -m unittest -v tests.llm.test_plugin tests.llm.test_tool_packages tests.hub.test_ish_integration
.venv-linux312/bin/python -m unittest discover -v -s tests/llm -t .
.venv-linux312/bin/python -m unittest discover -v -s tests/hub -t .
```

Bubblewrap-dependent tests require permitted execution outside the cloud command
sandbox (the sandbox prevents UID mapping); tool isolation is never disabled.
Xvfb `2:21.1.16-1.3+deb13u4` and xclip `0.13-4` were downloaded from authenticated
Debian trixie/trixie-security repositories and extracted locally without system
modifications. The original test launches its private Xvfb using those supported
environment variables. No assertion or Skip condition was changed.

Compatibility commit: `044c5b82c90302ca8934c74bcdda1c91995f9a3d`, pushed.
`tests/llm/test_plugin.py` changed only `_library_available` to
`_is_library_available`. The host implementation and rename history were checked:
`import_parents=False` still delegates to `has_import`, without importing the
plugin library in the host process.

| Check | Before correction | After correction |
|---|---|---|
| Exact real-platform loader test | 1 failure, exit 1: subprocess AttributeError | Included in 44 passing targeted tests |
| Three targeted modules | Historical 43 pass, 1 fail, 0 Skip | 44 pass, 0 fail/Skip, exit 0, 18.455 s |
| Full LLM | Historical 1,657 pass, 1 fail, 0 Skip | 1,658 pass, 0 fail/Skip, exit 0, 338.932 s |
| Full Hub | Historical 525 pass, 9 X11 Skip | 534 pass, 0 fail/Skip, exit 0, 483.787 s |
| X11 module alone | 9 Skip | 9 pass, exit 0, 2.081 s |

Original host evidence is preserved in `/workspace/host-validation-evidence/`.
Correction logs, command exit codes, package hashes and push output are under
`/workspace/refactor-bf-evidence/compat/`. These are observations from actual
executions; historical failures have not been relabelled as successes.

## B — independent Project Agent domain

`core/agents.py` owns the JSON definition schema, IDs and content revision.
`services/lifecycle/agents.py` owns `AgentRepository` and lifecycle-checked
`ProjectAgents`; `ProjectHandle.agents` exposes sync/async CRUD, snapshot, prompt
editing and mandatory-CAS `revise`. Only `engine` is required. New domain writes
validate the registered Engine ID; Engine-specific runtime prerequisites remain
the selected Engine's responsibility. Legacy offline Component editors retain
their earlier ability to edit definitions for separate Graph Engine registries;
execution still validates the actual selected registry and factory.

There is one catalogue at the existing `agents/records/<id>.json` path. New writes
use `{storage_version: 1, id, definition, revision}`. Explicit legacy raw-definition
reads validate but do not rewrite files. Invalid versions, mismatched IDs or
revisions, linked paths and unsupported old `component.json` storage are rejected
before overwrite. Revision bytes use the original sorted JSON hashing convention,
preserving nested Agent checkpoint fingerprints. Agent execution state remains in
Run/Step records; Session/Message/Run schemas are unchanged in B.

The compatibility `AgentComponent` and `AgentData` delegate to that catalogue;
registration/selection is unnecessary for `project.agents`. Their existing
Component input schema continues to require `purpose` for legacy writes, as
required by the unchanged Hub public-API/approval tests. This is an explicit
legacy input adapter, not a second domain schema or catalogue. Both facades can
read new minimal definitions; engine-only creation/editing uses `project.agents`.
Legacy selection checks and domain changes share the same ownership transaction.
Component removal
cannot delete Project-owned definitions. Project clone copies the catalogue once,
including disabled definitions; backup validation always checks it. Deleting a
referenced Agent is refused for saved Workflows, attached Sessions and historical
Run/Step evidence. Agent resource references protect Skill files as well as entire
Skill/Prompt/Tool/MCP records, even without an active AgentComponent.

Graph AgentNode and `agent_run` still use the shared `AgentExecution` resolver,
parent Run/Step identity, scopes and checkpoint validation. That resolver obtains
the independent catalogue through the existing capability interface and validates
the pure domain schema. Refinement targets use the same domain API with the
existing approval, CAS and rollback requirements. Skill impact reports no longer
require an AgentComponent registration.

The real pre-B golden archive in `tests/llm/fixtures/agent_v1/` was generated by
unchanged production code at `7984c8e225ca07711c866607d110727dfbb3c391`; its producer,
IDs and every file hash are retained. It includes a raw Agent, completed engine-only
Run, and durable queued request. The earlier A Loop/Graph checkpoint and approval
fixtures are untouched.

Verification before full suites:

- Existing Agent/Graph/Skill/Refinement regression: 89 tests passed, exit 0.
- Expanded domain/Agent/Graph/Skill/Refinement/legacy/storage regression: 136 tests
  passed, exit 0 (before the final additional execution-binding test).
- Final domain module: 13 tests passed, exit 0, 1.927 s. It covers Component-free
  minimal CRUD, invalid Engine, CAS contention, references, clone/backup/restore,
  stale handles, envelope rejection, rollback, approval, frozen active bindings,
  changed paused bindings, and actual legacy queue execution.
- Agent write crash test reaches three distinct failpoints in dedicated child
  processes: before write, after file write before transaction commit, and after
  the durable COMMITTED marker. A new Backend recovers old/old/new definitions
  respectively and checks their revisions. Exit codes and marker files prove
  each boundary was reached. The transaction's COMMITTED marker is the commit
  point; atomic file replacement alone does not acknowledge a new definition.
- Initial new-test run had two test-harness errors from calling the response
  property as a function; the tests now use `aresponse()`. The failure log is
  retained. No product assertion was removed to fix those errors.
- Provider reference audit: 48 files / 408 matching lines / 0 unclassified. The
  moved `max_tool_calls` schema is classified as Agent policy, preserving A's
  historical audit report rather than rewriting its baseline.

The first full B LLM run executed 1,671 tests in 341.928 s: 1,670 passed, one error,
zero Skip, exit 1. The existing external-Component hook test supplies only
`paths.root`; the new catalogue initially assumed `paths.agents`. The repository
now derives its owned suffix from the original minimal root contract. The exact
unchanged test failed independently before correction and passed afterward.

The first full Hub run executed 534 tests in 484.480 s: 533 passed, one failure,
zero Skip, exit 1. Its existing assertion requires legacy Component creation to
reject a missing purpose. The compatibility input adapter above preserves that
contract while the independent domain accepts an engine-only definition. Hub
source/tests and that assertion are unchanged. This first Hub diagnostic run
overlapped later corrections and is not the final frozen-source gate.

During final test additions, a nested Workflow fixture omitted the required
`on_limit` field: 17 tests passed / 1 test error in the 18-test focused run. Adding
the field fixed the fixture, without weakening its reference assertions. The
repeated focused run passed all 18 tests. Two prematurely started full LLM reruns
were cancelled with SIGINT (exit 130) before completion while these corrections
were made; `b-full-llm-final` and `b-full-llm-verified` are **incomplete**, despite
their filenames, and do not count as passing verification.

Final B LLM regression passed **1,671 tests**, zero failures/Skip, exit 0,
340.830 s (`b-gate-llm`). Final Hub regression passed **534 tests**, zero
failures/Skip, exit 0, 483.622 s (`b-gate-hub`), including all nine X11 checks.
Both suites used the frozen source and exact host above; the source hashes were
checked again after execution. **B is implemented and verified.** Logs and exit
files use `/workspace/refactor-bf-evidence/b-*`.

After both compatibility corrections, the combined Agent/Graph/Skill/Refinement/
legacy/storage/execution-boundary regression passed **153 tests**, exit 0,
26.455 s. Unchanged `tests.hub.test_resource_tools` and
`tests.hub.test_component_managers` passed **12 tests**, exit 0, 35.981 s, including
the previously failing legacy creation assertion. Workflow deletion protection
walks real nodes and loop bodies; Agent-shaped literals in state/metadata are
not treated as resource references. The final gate freezes SHA-256 of 315 source
and test Python files in `b-gate-source.json` and runs `b-gate-{llm,hub}`.

## Remaining stages and gates

C adds immutable top-level Agent/Engine bindings and one dispatch path, including
legacy engine-only normalization. D changes User Message/Run cardinality and
Session participants before enabling per-Agent concurrent execution. E reuses
that durable request contract for same-Project cross-Session requests. F adds
typed final review with candidate buffering and bounded continuation. Each stage
requires focused tests, applicable fault/concurrency boundaries, full LLM/Hub
regressions, a commit and a push before advancing.

B changes Agent persistence (F05 binding and write/CAS recovery); fan-out F01–F04,
F12 and C01–C10 require fresh D validation. F13/C11 belong to E and F14/C12 to F.
Existing approval, operation receipt and checkpoint regressions remain gates in
every suite; their earlier success does not certify future concurrency changes.

Live authenticated model calls and dedicated kernel short-write injection remain
**unverified**. The nine X11 checks are now verified, not residual Skip entries.
Environment draft saving does not establish Publish or fresh-task restoration.
