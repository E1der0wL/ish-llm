---
name: hub-create-workflow
description: "Define and validate graph execution workflows."
metadata:
  hub-template-version: "7"
---
# Create a Workflow

Use when the user asks for a reusable graph execution flow.

1. Define inputs, expected outputs, ordered work, conditional branches and success checks.
   Use `workflow_list` and `workflow_read` to inspect applicable existing definitions.
2. Read `workflow_schema`. Use schema_version 1 and its current node/edge contract. Titles
   and descriptions belong in metadata. Do not invent executable node types.
3. Read `x-hub-action-handlers` in the schema for the registered node types and their fields.
   Hub normally supplies `agent` and `tool`; custom engines may differ. A null handler schema
   means its contract is unavailable. Read saved agents and available tool schemas before
   referencing them. Use JSON-pointer inputs/outputs for state bindings and bounded loop
   nodes for repetition. Do not add arbitrary cyclic edges.
4. Call `workflow_create` with a new identifier and the definition. Validation errors are
   evidence to fix the definition; do not bypass validation by writing internal files.
5. Read it back with `workflow_read` and check all references and reachable end paths.
   A saved graph is not proof of successful execution. Run a safe representative scenario
   only when execution is requested and supported, otherwise state it has only been validated.

Smallest valid structural example (does no substantive work):

```json
{"schema_version":1,"entry":"finish","nodes":{"finish":{"type":"end"}},"edges":[]}
```

With Hub's default `tool` handler, a read-only skill inventory workflow is:

```json
{"schema_version":1,"entry":"inventory","nodes":{"inventory":{"type":"tool","tool":"skill_list","arguments":{},"result_key":"skills"},"finish":{"type":"end"}},"edges":[{"source":"inventory","target":"finish"}]}
```

Use only one of `inputs`, `arguments`, or `arguments_key` on a Tool node.

Creation never executes the workflow or expands permission. Saved execution definitions apply
to subsequent Runs. If a selected handler's contract is unavailable, ask for that information.
