"""Real Loop/Tool execution, durable approvals, component persistence and recall."""

from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.providers.operations import LiteLLMOperations

import json
import tempfile
import unittest

from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.tools.resources import HubRAGComponent
from llm.components.rag import EmbeddingModel
from llm.engines.loop import LoopEngine
from tests.hub.test_components import embedding, Extractor
from tests.llm.test_loop import ScriptedCompletion, call, chunk


class ResourceToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.provider = ScriptedCompletion()
        self.runtime = HubRuntime(HubConfig(self.directory.name, model="test/model", auto_title=False,
            engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=self.provider))},
            component_factories=(lambda: HubRAGComponent(
                embedding=EmbeddingModel(model="test/embedding", adapter=LiteLLMOperations(call=embedding)), extractor=Extractor()),)))
        await self.runtime.start()
        self.addAsyncCleanup(self.runtime.close)
        self.session = await self.runtime.project.sessions.acreate("Resources")

    async def invoke(self, name, *, approve=True, **arguments):
        self.provider.responses.extend([
            [chunk(calls=[call(json.dumps(arguments), name=name)], finish="tool_calls")],
            [chunk("Verified", finish="stop")]])
        result = await (await self.session.run.submit(name, agent_id="assistant")).wait()
        if str(result.data.status) == "paused" and approve:
            request, = await result.ainteractions()
            await result.arespond(request.respond("approve"))
            result = await (await self.session.run.resume(result.id, agent_id="assistant")).wait()
        if str(result.data.status) == "paused":
            return result
        self.assertEqual(str(result.data.status), "completed", result.data.error)
        steps = [step for step in await result.steps.alist() if step.kind == "tool" and step.output is not None]
        return steps[-1].output.data

    async def test_skill_workflow_agent_creation_is_validated_and_approval_bound(self):
        definitions = {
            "skill": {"title": "Review", "instructions": "Inspect the change and verify tests."},
            "workflow": {"schema_version": 1, "entry": "finish", "nodes": {"finish": {"type": "end"}}, "edges": []},
            "agent": {"purpose": "Review supplied text", "engine": "loop", "tools": []},
        }
        for prefix, definition in definitions.items():
            schema = await self.invoke(prefix + "_schema")
            self.assertIn("properties", schema)
            result = await self.invoke(prefix + "_create", identifier="created-" + prefix, definition=definition)
            self.assertEqual(result["created"], "created-" + prefix)
            self.assertEqual(result["applies_to"], "subsequent_runs")
            read = await self.invoke(prefix + "_read", identifier="created-" + prefix)
            self.assertIn("hub_source", json.dumps(read))
        agents = self.runtime.project.agents
        before = await agents.aload("created-agent")
        # Invalid definitions and duplicate IDs are rejected by the public API.
        with self.assertRaises(FileExistsError):
            await agents.acreate(definitions["agent"], identifier="created-agent")
        self.assertEqual(await agents.aload("created-agent"), before)
        with self.assertRaises(ValueError):
            await agents.acreate({}, identifier="invalid")
        await agents.acreate({"engine": "loop"}, identifier="engine-only")
        self.assertEqual(await agents.aload("engine-only"), {"engine": "loop"})
        paused = await self.invoke("agent_create", approve=False, identifier="pending-agent", definition=definitions["agent"])
        self.assertEqual(str(paused.data.status), "paused")
        self.assertNotIn("pending-agent", await agents.alist())

    async def test_memory_recall_and_goal_progress_reach_completion(self):
        memory = await self.invoke("memory_create", content="The project color is violet.", kind="fact", scope="project")
        self.assertIn("violet", json.dumps(memory))
        self.provider.responses.append([chunk("Recalled", finish="stop")])
        result = await (await self.session.run.submit("What is the project color violet?", agent_id="assistant")).wait()
        self.assertEqual(str(result.data.status), "completed", result.data.error)
        steps = await result.steps.alist()
        recalled = [step for step in steps if "memory" in step.kind]
        self.assertTrue(recalled)
        self.assertIn("memories", json.dumps([step.output.data for step in recalled if step.output]))
        goal = await self.invoke("goal_create", identifier="verify", title="Verify resources", objective="Save and verify a guide",
                                 success_criteria=["Guide stored", "Readback matches"])
        updated = await self.invoke("goal_update_progress", identifier="verify", expected_version=goal["version"],
                                    changes={"progress": ["Guide stored"], "next_actions": ["Readback"]})
        self.provider.responses.append([chunk("Progress checked", finish="stop")])
        await (await self.session.run.submit("Continue the work", agent_id="assistant")).wait()
        self.assertIn("Active Goal Reference", json.dumps(self.provider.requests[-1]["messages"]))
        self.assertIn("Readback", json.dumps(self.provider.requests[-1]["messages"]))
        complete = await self.invoke("goal_complete", identifier="verify", expected_version=updated["version"])
        self.assertEqual(complete["data"]["status"], "completed")

    async def test_refinement_proposal_changes_target_only_after_approval(self):
        await self.invoke("skill_create", identifier="guide", definition={"instructions": "Before"})
        target = await self.invoke("refinement_target", target={"component": "skills", "identifier": "guide"})
        runs = await self.session.run.alist()
        evidence_id = runs[-1].id
        proposed = await self.invoke("refinement_propose", target={"component": "skills", "identifier": "guide"},
            operation="update", expected_version=target["version"], reason="Add a missing verification step",
            evidence=[{"session_id": self.session.id, "run_id": evidence_id}], patch={"instructions": "After: verify readback"})
        skills = await self.runtime.project.components.aget("skills")
        self.assertEqual((await skills.aload("guide"))["instructions"], "Before")
        identifier, = await self.runtime.project.components.refinement.alist()
        paused = await self.invoke("refinement_apply", approve=False, identifier=identifier, expected_version=proposed["version"])
        self.assertEqual((await skills.aload("guide"))["instructions"], "Before")
        request, = await paused.ainteractions()
        await paused.arespond(request.respond("approve"))
        resumed = await (await self.session.run.resume(paused.id, agent_id="assistant")).wait()
        self.assertEqual(str(resumed.data.status), "completed", resumed.data.error)
        self.assertEqual((await skills.aload("guide"))["instructions"], "After: verify readback")

    async def test_rag_authoring_prepares_index_and_can_be_retrieved(self):
        result = await self.invoke("rag_add_document", identifier="note", title="Hub note", content="Violet is the project color.")
        self.assertEqual(result["id"], "note")
        data = await self.runtime.project.components.aget("rag")
        document = await data.aget_document("note")
        self.assertIn("Violet", document["content"])
        self.assertIn("Violet", document["description"])
        found = await self.invoke("rag_search", query="Violet", method="bm25", rerank=False)
        self.assertIn("note", json.dumps(found))
        preview = [message for message in self.provider.requests[-1]["messages"]
                   if message.get("role") == "system" and "RAG document overview" in message.get("content", "")]
        self.assertEqual(len(preview), 1)
        self.assertIn('"id": "note"', preview[0]["content"])

    async def test_saved_agent_executes_in_parent_run(self):
        agents = self.runtime.project.agents
        await agents.acreate({"purpose": "Review supplied text", "engine": "loop", "tools": []}, identifier="reviewer")
        self.provider.responses.extend([
            [chunk(calls=[call(json.dumps({"agent_id": "reviewer", "input": {"task": "Review"}}), name="agent_run")], finish="tool_calls")],
            [chunk("Agent checked the supplied text", finish="stop")],
            [chunk("Review finished", finish="stop")]])
        result = await (await self.session.run.submit("Review this using reviewer", agent_id="assistant")).wait()
        if str(result.data.status) == "paused":
            request, = await result.ainteractions()
            await result.arespond(request.respond("approve"))
            result = await (await self.session.run.resume(result.id, agent_id="assistant")).wait()
        self.assertEqual(str(result.data.status), "completed", result.data.error)
        outputs = [step.output.data for step in await result.steps.alist() if step.kind == "tool" and step.output]
        self.assertIn("Agent checked", json.dumps(outputs))

    async def test_authored_workflow_uses_registered_handler_schema_and_executes(self):
        schema = await self.invoke("workflow_schema")
        handlers = schema["x-hub-action-handlers"]
        self.assertEqual(handlers["tool"]["required"], ["tool"])
        self.assertEqual(handlers["agent"]["required"], ["agent"])
        await self.invoke("workflow_create", identifier="inventory", definition={
            "schema_version": 1, "entry": "inventory", "nodes": {
                "inventory": {"type": "tool", "tool": "skill_list", "arguments": {}, "result_key": "skills"},
                "finish": {"type": "end"}}, "edges": [{"source": "inventory", "target": "finish"}]})
        await self.invoke("agent_create", identifier="inventory-agent",
                          definition={"engine": "graph", "engine_options": {"workflow": "inventory"}})
        result = await (await self.session.run.submit("List guides", agent_id="inventory-agent")).wait()
        self.assertEqual(str(result.data.status), "completed", result.data.error)
        outputs = [step.output.data for step in await result.steps.alist() if step.kind == "tool" and step.output]
        self.assertIn("hub-create-workflow", json.dumps(outputs))
