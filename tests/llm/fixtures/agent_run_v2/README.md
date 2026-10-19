# Actual stage-C Agent Runs

These bytes were produced by unchanged production code at
`3076192f58f5b803136b2a1bcf14a5fc9946f6fd` with Python 3.12.14 before stage D.
The checked producer `generate.py` asserts the exact SHA and a clean `llm/` diff.
It uses FakeStreamingEngine only, without model authentication or external calls.

Command in the generating workspace:

```sh
TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION \
.venv-linux312/bin/python /workspace/refactor-bf-evidence/agent-run-v2/generate.py
```

The fixture contains an independent Agent envelope, a completed explicit-Agent
Run, an interrupted attempt, and a genuinely accepted queued request preserved by
shutdown. Sessions still use version 1 and the single current_run_id field; Runs
use version 2 and frozen Agent bindings. The manifest records all identities and
SHA-256 hashes of 21 files. Tests must extract isolated copies with the tar data
filter and verify the original archive remains unchanged. Regeneration requires
a separate baseline checkout; never reset the active refactoring branch.
