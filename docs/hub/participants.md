# Named Agents and session participants

`/agent` uses the shared definition manager. Create an Agent with a stable
identifier, a display name, and its Engine/Tool/Prompt configuration. The editor
queries `project.agents.adescribe(engine=...)` and renders the selected schema
through `SchemaForm`: labels, descriptions, types and editable values. Engine
selection keeps a separate unsaved draft per Engine; Graph has no model fields.
Root schema intersections are projected into individual inputs for display only;
the complete original schema, including `allOf` and Engine selection constraints,
is validated before saving and again by the backend. Invalid edits remain drafts.
Reads use `asnapshot`, writes use `arevise(expected_revision=...)`. Display names
are application metadata (`metadata.title`); execution still uses the Agent ID.
The internal title-generation Agent is excluded from the picker and manager list.

Open **Participants** from the Home composer auxiliary buttons. It occupies the
same output sidebar as Tags; opening either closes the other. The sidebar captures
keyboard and mouse focus until closed. Up/Down highlights a participant;
Space/Enter opens actions: edit Agent, move group, change recipient or expel.
A group offers change recipient and delete group. With no selected recipient, the request goes
to everyone invited to the current session. The composer title shows the target.
Tab moves to **Add agent**. Left/Right selects the adjacent **Add group** button.
Add agent lists project Agents not already invited and opens their manager.
Escape closes the sidebar. Both sidebars use the same width and inset.

Groups live in Hub-owned `Session.metadata.hub_participant_groups`, written only
through the public Session API. A group has a name and invited Agent IDs; one
Agent belongs to at most one group. Moving to “Ungrouped” removes its grouping.
Deleting a group keeps its Agents invited; expelling removes session membership,
not the Project Agent definition. Group targeting expands current members into
explicit backend recipients. An empty group does not silently broadcast. External
membership changes cannot cause metadata to re-invite an Agent.

Model, endpoint and authentication belong to `Agent.completion`. Engine Project
settings describe execution only. The newly created `assistant` owns Hub's system
prompt and allowed Tools; credentials and model names remain unset unless supplied
explicitly. Existing stored definitions are never automatically rewritten.
Automatic titles use a private Agent per source session with explicitly copied
auxiliary model settings, so concurrent naming jobs cannot share mutable models.

New sessions start with the configured default Agent (or the first available
public Agent). Invitations are explicit and persist in `Session.agent_ids` using
the public Session API. Existing sessions retain their saved participants. The
backend requires the Session runtime to be detached before changing membership:
Hub rejects invitations while work is active or queued, and detaches/restarts an
idle Session around the save. It never interrupts work to add a participant.
The saved list is unique and sorted even when invitation order differs from Agent
ID order. Re-inviting an existing member leaves membership unchanged.

Broadcast admission creates one user request with explicit recipient IDs. The
backend schedules one Run per Agent and owns concurrency. Hub projects each active
Run's phase, reasoning, tool history and response independently, with the Agent's
display name from its Run-start binding. Changing an Agent later does not rename
historical responses. Recipient selection is remembered per session during the
current Hub lifetime; invitations persist across restarts.
The existing stop control interrupts active Runs in the session. Each affected
response receives immediate stop feedback, even if the newest Run already ended.
While multiple recipients are selected and work is running, the composer offers
a queued follow-up to all selected recipients. Select one participant to steer
its active Run; Hub never silently chooses one member of a broadcast for steering.

Graph responses include a Workflow diagram in the reasoning section. The diagram
uses the definition captured in Graph Step evidence, not a subsequently edited
Workflow. Node status markers update from recorded Steps; completed graphs are
cached. This is an observation-only view and does not execute or replay nodes.
Diagrams are laid out for the available output width by the render worker. Graph
state changes invalidate that trace item, preserving other Agents' cached bodies.

`WorkflowObservation` is Hub's sole display adapter for Graph Step evidence. Graph
owns the field declarations and path rules; `WorkflowGraph` validates the captured
definition. Missing required evidence is not reconstructed from a Step name or an
empty graph. No Graph root means there is no diagram to display. The adapter owns
only the immutable text/status projection, never execution or resume authority.

Settled Graph observations are reused, and completed Loop responses are not scanned
for Graph Steps. The registered engine's mode also recognizes Graph aliases.

Tests: `tests.hub.test_participants`, `tests.hub.test_multi_agent`, and the shared
component manager, output interaction and mouse widget suites. No provider calls
or paid model credentials are required for these deterministic tests.
`tests.hub.test_observation_contracts` covers reverse-order invitations, restart
persistence, broadcast recipients, strict Graph evidence and histories over 1,024 Runs.
