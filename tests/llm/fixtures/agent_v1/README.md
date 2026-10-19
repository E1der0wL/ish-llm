# Pre-domain Agent records

`workspace.tar.gz` was generated with the actual unchanged `llm/` implementation
at `7984c8e225ca07711c866607d110727dfbb3c391`, before B modified production code.
The producer verifies that SHA and that all `llm/` files match it. It used Python
3.12.14 and the repository's FakeStreamingEngine; no external model calls occur.

The exact producer is `generate.py`. In the original checkout layout the command
was `TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION
.venv-linux312/bin/python /workspace/refactor-bf-evidence/generate_legacy_agents.py`.
It deliberately refuses a different source HEAD; regenerate in a separate
baseline checkout, never by resetting an active refactoring branch. The script's
ROOT/output constants describe that original cloud layout.

The archive contains a raw legacy AgentComponent definition, a completed
engine-only Run and its User/Assistant records, and a separate Session's durable
engine-only queued Message. The queue was written through the old conversation
store's public create method under workspace ownership. Both Session records
retain the old single current_run_id schema. `manifest.json` records identities,
the original definition revision and SHA-256 of every generated file.

Tests extract independent copies with the safe tar data filter, validate every
file hash, read without rewriting the old Agent file, and execute the persisted
queue. The earlier `provider_v1` fixtures separately preserve the original
94229cb Loop/Graph checkpoints, pending approval, and operation receipts; they
remain unchanged.
