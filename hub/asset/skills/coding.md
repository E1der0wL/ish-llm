# Coding: inspect, reproduce, repair, verify

Use for implementing or modifying code, including adding regression tests.

1. Read applicable project instructions, relevant code, callers and existing tests.
   Inspect uncommitted changes. Preserve unrelated edits and established architecture.
   Define observable acceptance criteria before editing; ask only for missing decisions
   that affect the result. Use only registered tools and respect approval decisions.
2. Reproduce defects with a small executable test. Keep tests/examples outside runtime
   plugin roots. Record the command, exit code and relevant output; a failure is evidence,
   not a reason to remove an assertion or claim success.
3. For experiments, discover the actual schemas of test_workspace_create, test_workspace_list,
   test_workspace_execute, test_workspace_delete and file tools. Create a separate workspace
   with only the required source_paths.
   Use workspace_id for file reads/edits and execution there. Inspect returned paths and IDs;
   never invent them. Use the project's pinned interpreter and dependency environment.
   Use an absolute interpreter path within the mounted runtime. A host virtualenv's symlink
   can point outside those mounts; verify the canonical executable path before running.
   Sandbox execution can require user approval. If unavailable or denied, stop that operation
   and explain the limitation; do not silently replace it with an unrestricted shell command.
   A temporary directory, virtualenv or Git worktree alone is not an OS security sandbox.
4. Make the smallest complete fix, add meaningful edge/failure cases, and rerun the original
   reproduction. Read failures, correct their cause and retest until acceptance criteria pass
   or a concrete external blocker remains. Preserve test output as evidence.
5. Review the final diff. A successful test of a copy does not mean the original was changed.
   Apply only authorized, reviewed changes to the original, check for intervening user edits,
   then run the required repository checks there. Do not copy caches, environments or unrelated
   generated artifacts back. Keep the workspace on failure for diagnosis; delete it only after
   necessary evidence is retained and deletion is authorized.
6. Report the resulting behavior, files changed, exact checks and outcomes, and remaining
   limitations. Distinguish a deterministic tool integration test from a real model-quality test.
   Never claim to have edited, executed, isolated or verified something without tool evidence.
