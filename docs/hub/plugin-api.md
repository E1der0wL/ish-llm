# Other ish plugins using Hub Agents

`hub.hub.client(prompt, plugin_id)` is the official async entry point. Hub must
already be installed. Constructing a client does not start the backend or open
the UI; the first operation starts the same worker used by Home. No second LLM
instance or workspace lock is acquired.

## Registration and configuration

```python
from hub import hub as hub_plugin
from hub.api import PluginDefinition, PluginContext
from hub.config.environment import ShellEnvironment

agents = hub_plugin.client(prompt, "shell-helper")

async def initialize():
    await agents.register(PluginDefinition(
        name="Shell helper",
        agents={"suggest": {
            "engine": "loop",
            "description": "Suggest a command for the current shell input.",
            "system_prompt": "Return one suggested command. Do not execute it.",
            "tools": [],
        }},
    ))
    return await agents.open_session("suggestions", agents=["suggest"])
```

Run async operations on the host application loop, not in a synchronous key
handler. Plugin IDs are stable names, not display labels. One plugin owns one
dedicated Project. `open_session` keys identify serial execution lanes; different
lanes may execute concurrently. Agents and session membership are created only
when absent. Re-registering adds missing template agents, but never overwrites
saved Agent definitions, Project policies, component selection or titles.
Deleting/uninstalling a caller does not delete its stored records.

The new Project uses file conversation storage and only the components/config
explicitly supplied by the template. It does not receive Home's assistant,
planner, title generation, background jobs or mandatory completion review.
Component activation and Tool exposure are separate backend contracts: a plugin
using `builtin_tools` must also supply its `config.enabled` list under
`config.parameters.components.builtin_tools`. Agent `tools` narrows that list.
Templates must not prefill credentials or choose a model on the user's behalf.

Users configure saved Agents at **/agent → Edit**. Plugin-owned Agents appear in
the ordinary Agent list with their plugin name, alongside the current Project's
Agents. New/imported Agents still belong to the current Project. Editing or
deleting an existing entry targets its original owning Project.
Backend reference protection still prevents deleting Agents used by Sessions or
other resources. Newly registered Agents, the default planner and manually created
Agents use Backend-owned record times. Lists read `project.agents.aget_times()`;
Hub does not insert dates into Agent definitions or interpret user metadata as dates.
Unsupported records without native times are not repaired or backfilled.
The ordinary schema-based Agent editor and revision checks are reused. Its
Completion review selector preserves inheritance, explicit null (disabled), and
enabled budgets. A Project-required review cannot be disabled by an Agent.
Detailed values remain available in the JSON editor.

## Requests and engine-independent outputs

```python
lane = await initialize()
request = await agents.submit(
    lane["session_id"], "Suggest the next command", recipients=["suggest"],
    context=PluginContext(
        environment=ShellEnvironment.from_prompt(prompt),
        text=selected_scrollback, source="ish scrollback", max_chars=16000,
    ),
    request_key=stable_input_id,
)
async for batch in request.events():
    for item in batch["outputs"]:
        render_output(item)  # Caller owns rendering, not Hub's Home widgets.
    render_status(batch["runs"])  # Each Run has interactions and durable questions.
```

Admission returns only after the backend durably accepts QUEUED. Caller-supplied
`request_key` enables retrying the *same* admission; retain it before submitting.
Use `client.receipt(session_id, request_key)` after an uncertain response.
The caller owns debounce/cache for automatic suggestions. Cancelling an awaiting
Python task is not proof that admission was cancelled.

Output identity is `(run_id, output_id)`. Each output event retains its backend
sequence, step ID, visibility, append/replace operation (delta), final flag and
metadata (output). An `output` event replaces the corresponding projection; a
`delta` applies its declared operation. A final output is not proof of Run success.
There are no Loop/Graph dispatch branches in this transport. Final-only engines
produce final-only output. Review-held internal candidates never become public
answers through this API. `include_internal=True` is an explicit opt-in for
observations; callers must keep them separate from user-visible final answers.

Backend events wake reads; durable output pages provide the actual delivery.
Batches include per-Run cursors. Page limits affect batch size, never output
contents. The low-frequency fallback catches dropped signals. Slow consumers do
not block execution or lose output. Closing an iterator only stops observation.
`terminal` means all recipient Runs stopped (including paused/failed/interrupted),
or an undispatched request was cancelled. The USER message itself can remain
committed after all Runs complete.

## Questions, approvals and control

- `request.cancel()` cancels only an undispatched request.
- `request.interrupt(run_id)` stops only that request's specified Run.
- `request.steer(run_id, text, request_key=...)` submits an additional instruction;
  it does not promise that an active external command changes immediately.
- Each Run's `interactions` uses the backend's durable InteractionView contract.
  Construct a response from the displayed `InteractionRequest.respond(...)` and
  pass `response.to_dict()` to `request.respond(...)`. Only an explicit user
  decision is accepted. This stores the decision and **does not execute**.
- Query `request.resume_plan(run_id)`, then explicitly call
  `request.resume(run_id, expected_revision=..., retry_nodes=..., decisions=...)`.
  A new request handle is returned. Protected/uncertain effects are not replayed
  automatically, and failed plans never fall back to restart.
- `request.cancel_interaction(run_id, request_dict)` cancels an individual
  interaction, not a remembered permission rule or the whole request.
- `ask_user` is a durable blocking question: use the interaction response/explicit
  resume sequence above. `ask_user_async` posts a nonblocking question. Read these
  from each Run's `questions` field or `request.questions(run_id)` after completion.
  `request.answer_question(run_id, response_dict)` saves only. Explicitly use
  `request.deliver_answer(source_run_id, question_id, mode="steer",
  target_run_id=..., targets=[...])` or `mode="submit", recipients=[...]` afterwards.
  `request.question_targets(run_id)` returns native targets; never infer Graph nodes.
  `request.get_delivery(run_id, question_id)` confirms intake, not application.
  `request.cancel_question(run_id, question_dict)` cancels unanswered questions.
  Native revision/idempotency and Session ownership checks remain authoritative.
  The old `answer_live`/`live_questions` Future-specific interface is removed.

Hidden sessions do not bypass approval. The calling plugin must present pending
questions/approvals and supply explicit user actions, or leave them pending.
Home's automatic resume controller does not take ownership of plugin requests.

## Lifetime, visibility and ownership

Request references contain Project/Session/request IDs and a backend generation.
After restart, old request handles fail explicitly. Use
`client.reconnect(session_id, request_id)` and saved output cursors to query saved
state without replay. Clients resolve the current installation, so they can be
reused after reinstall/restart. Closing Hub wakes output observers and releases
their waits. Backend shutdown still owns draining writes and execution cleanup.

Project identity is stored in `ProjectConfig.data.hub_plugin`. Internal session
metadata uses the shared `hub_internal` marker plus `hub_plugin` and a stable
`hub_plugin_session` key. All persistence uses public backend CRUD. Hub does not
inspect or edit backend-owned files. Ordinary Home catalogs and session histories
exclude plugin Projects/Sessions. Configuration remains available through the
ordinary Agent manager. Global usage totals can include plugin usage.

This is UI visibility, not an OS access-control boundary: records remain on disk
and explicit backend administration can read them. The caller provides bounded
terminal context and environment snapshots. Hub never captures terminal screens,
changes process cwd/environment, or stores context in Agent definitions.

Plugin identity is an application namespace, not authentication between trusted
local Python plugins. Graph Agents require their referenced Workflow resources
to exist in the owning Project; this API does not silently copy resources from
the selected Home Project or specialize Graph output handling.
