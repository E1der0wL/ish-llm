---
name: hub-create-agent
description: "Define agents with clear responsibilities and tool access."
metadata:
  hub-template-version: "7"
---
# Create an Agent

Use when a recurring, bounded task needs a reusable agent definition.

1. Inspect existing definitions using `agent_list` and `agent_read`. Define a specific purpose,
   input/output contract, expected checks and limits. Avoid unnecessary delegation.
2. Read `agent_schema` for the selected engine. Loop Agents own their model and provider
   parameters in `completion`; Engine settings control execution. Ask for required choices
   or explicitly reuse user-authorized Agent settings. Missing model values are not inherited
   from the project or another Agent. Graph Agents select a workflow and have no model fields.
3. Declare only the tools/resources the task needs. Verify referenced skill and agent IDs.
   Agent permissions cannot exceed the owning project's policies or the parent Run's authority.
4. Call `agent_create` with a new identifier and the definition, then verify with `agent_read`.
   Creation does not execute the agent. It becomes available to `agent_run` in subsequent Runs.
5. If execution is authorized, validate a representative input through `agent_run` in a later
   Run and inspect its actual output. Clearly distinguish schema validation from behavior testing.

Minimal definition:

```json
{"purpose":"Review the supplied text for factual inconsistencies","engine":"loop","tools":[]}
```

Use system_prompt for task guidance, tools for an explicit allowlist, and resources.skills for
verified guide IDs. Do not place API keys in definitions or silently change project policies.
