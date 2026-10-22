---
name: hub-code-change
description: "Inspect existing behavior and boundaries, then verify the necessary changes."
metadata:
  hub-template-version: "7"
---
# Coding: inspect, implement, verify, deliver

Use for implementing or modifying code, including adding regression tests.

1. Read project instructions, relevant code, callers, tests and uncommitted changes.
   Identify the requested behavior, target files and observable acceptance criteria.
   For a new script or feature, include the working files in those criteria, not only a chat example.
   Resolve paths against the host-provided working directory; inspect files instead of guessing paths.
   Inspect accessible command help, logs and configuration yourself; do not delegate these checks to
   the user merely because a command is unfamiliar. Explain any concrete access blocker instead.
2. Choose the least complex implementation that satisfies those criteria. Do not add speculative
   features, configuration options or dependencies. Introduce an abstraction when it has a concrete
   benefit in responsibility separation, testing or actual reuse, not for hypothetical future needs.
   Follow the existing style and architecture. Keep neighboring formatting and unrelated cleanup out
   of the diff; explicitly requested refactoring or shared UI work is part of the task's scope.
3. Reproduce defects with a focused failing test when practical. Choose meaningful checks for new
   behavior and risk; trivial edits do not require a new test that merely repeats the implementation.
   Keep tests/examples in the repository's designated locations, outside deployment plugin roots.
   Record commands, exit codes and relevant output. Diagnose a failed assertion instead of weakening it.
4. When an experiment needs an isolated copy, discover the actual schemas of test_workspace_create, test_workspace_list,
   test_workspace_execute, test_workspace_delete and file tools. Create a separate workspace
   with only the required source_paths.
   Use workspace_id for file reads/edits and execution there. Inspect returned paths and IDs;
   never invent them. Use the project's pinned interpreter and dependency environment.
   Use an absolute interpreter path within the mounted runtime. A host virtualenv's symlink
   can point outside those mounts; verify the canonical executable path before running.
   Sandbox execution can require user approval. If unavailable or denied, stop that operation
   and explain the limitation; do not silently replace it with an unrestricted shell command.
   A temporary directory, virtualenv or Git worktree alone is not an OS security sandbox.
5. Implement with file tools, rerun the reproduction and relevant checks, and correct the cause of
   failures. Stop repeating checks once the criteria pass unless another change or concern justifies it.
   If external conditions block progress, retain the evidence and identify the unresolved condition.
6. Review the diff and deliver the requested files. Preserve unrelated edits, including changes made
   while the experiment ran. A passing copy is an intermediate result: for an implementation request,
   apply the verified in-scope changes to the original without asking again for already-granted permission.
   Honor review-only instructions and tool approvals. Read back new files and inspect patches/diffs to
   confirm the intended contents exist at the destination, then run the required repository checks there.
   Remove imports or code made unused by your changes; report unrelated dead code instead of deleting it.
   Do not copy caches, environments or unrelated generated artifacts back. Retain failed experiments
   for diagnosis; clean up only when the evidence is retained and deletion is authorized.
7. Report the delivered paths and exact verification outcomes. Distinguish a deterministic tool
   integration test from evidence that a real model follows these instructions.
