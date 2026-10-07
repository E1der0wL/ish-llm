"""제품 선택과 backend 계약, Skill 진화의 권한·버전 경계를 검증한다."""

import json
import unittest
from copy import deepcopy

from tests.llm import test_refinement as refinement_tests
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from tests.llm import test_agent_workflow as agent_tests
from tests.llm.test_agent_workflow import agent_graph, answer
from llm.components.prompts import PromptComponent
from llm.components.skills import SkillComponent
from llm.engines.loop import LoopEngine


class EvolutionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = refinement_tests.RefinementTests.asyncSetUp
    proposal = refinement_tests.RefinementTests.proposal
    approve = refinement_tests.RefinementTests.approve

    async def new_skill(self, operation="create", identifier="child"):
        value = {"operation": operation, "target": {"component": "skills", "identifier": identifier},
                 "expected_version": None, "patch": {"instructions": "Check before publishing"},
                 "reason": "Observed failure", "evidence": self.evidence}
        if operation == "fork":
            parent = await self.project.components.skills.asnapshot("guide")
            value["parent"] = {"identifier": "guide", "version": parent["version"]}
        return await self.refine.acreate(value), value

    async def evaluate(self, identifier):
        view = await self.refine.asnapshot(identifier)
        validation = await self.refine.avalidate(identifier)
        return await self.refine.arecord_evaluation(identifier, {
            "evaluator": "test/validator", "baseline_version": validation["baseline_version"],
            "candidate_version": validation["candidate_version"], "results": {"passed": True},
            "regressions": [], "improvements": ["checks evidence"], "evidence": self.evidence,
        }, expected_version=view["version"])

    async def test_lineage_and_agent_references_block_delete_and_fork_rollback(self):
        skills = self.project.components.skills
        identifier, _ = await self.new_skill("fork", "child")
        approved = await self.approve(identifier)
        applied = await self.refine.aapply(identifier, expected_version=approved["version"])
        child = await skills.asnapshot("child")
        await skills.acreate({"instructions": "Grandchild", "lineage": {
            "parent": "child", "parent_revision": child["version"]}}, identifier="grandchild")
        refs = await skills.adependencies("child")
        self.assertEqual(refs["agents"], [])
        self.assertEqual([r["skill_id"] for r in refs["children"]], ["grandchild"])
        with self.assertRaisesRegex(ValueError, "referenced"):
            await skills.adelete("child", expected_version=child["version"])
        with self.assertRaisesRegex(ValueError, "referenced"):
            await self.refine.arollback(identifier, expected_version=applied["version"])
        self.assertEqual(await self.refine.asnapshot(identifier), applied)
        await skills.adelete("grandchild")
        agent = await self.project.components.agents.asnapshot("guide")
        await self.project.components.agents.arevise("guide", {**agent["definition"],
            "resources": {"skills": ["child"]}}, expected_revision=agent["revision"])
        self.assertEqual([r["agent_id"] for r in (await skills.adependencies("child"))["agents"]], ["guide"])
        with self.assertRaisesRegex(ValueError, "referenced"):
            await skills.adelete("child")
        with self.assertRaisesRegex(ValueError, "referenced"):
            await self.refine.arollback(identifier, expected_version=applied["version"])
        updated = await self.project.components.agents.asnapshot("guide")
        await self.project.components.agents.arevise("guide", agent["definition"], expected_revision=updated["revision"])
        await self.refine.arollback(identifier, expected_version=applied["version"])
        self.assertEqual((await skills.adependencies("guide"))["children"], [])

    async def apply_tool(self, identifier, engine):
        view = await self.refine.asnapshot(identifier)
        model = ScriptedCompletion([chunk(calls=[call(json.dumps({"identifier": identifier,
            "expected_version": view["version"]}), name="refinement_apply")], finish="tool_calls")],
            [chunk("applied", finish="stop")])
        self.app.engines.register(engine, LoopEngine(completion_fn=model).for_agent({"engine": 'loop', "engine_options": LoopEngine.parameter_layout.pack({'completion': {"model": "test/refiner"}})}))
        paused = await (await self.session.run.submit("apply proposal", engine=engine)).wait()
        self.assertEqual(str(paused.data.status), "paused", paused.data.error)
        self.assertEqual((await self.refine.aload(identifier))["status"], "proposed")
        request, = await paused.ainteractions(pending_only=True)
        await paused.arespond(request.respond("approve"))
        resumed = await (await self.session.run.resume(paused.id, engine=engine)).wait()
        self.assertEqual(str(resumed.data.status), "completed", resumed.data.error)
        return await self.refine.asnapshot(identifier)

    async def test_create_collision_and_rollback(self):
        identifier, data = await self.new_skill()
        with self.assertRaises(FileNotFoundError):
            await self.project.components.skills.aload("child")
        approved = await self.approve(identifier)
        applied = await self.refine.aapply(identifier, expected_version=approved["version"])
        self.assertNotIn("lineage", await self.project.components.skills.aload("child"))
        with self.assertRaises(FileExistsError):
            await self.refine.acreate(data)
        await self.refine.arollback(identifier, expected_version=applied["version"])
        with self.assertRaises(FileNotFoundError):
            await self.project.components.skills.aload("child")

    async def test_creation_receipt_failure_rolls_back_new_skill(self):
        from unittest.mock import patch
        identifier, _ = await self.new_skill("fork")
        approved = await self.approve(identifier)
        with patch.object(self.parts[0], "save", side_effect=OSError("receipt failed")):
            with self.assertRaises(OSError):
                await self.refine.aapply(identifier, expected_version=approved["version"])
        with self.assertRaises(FileNotFoundError):
            await self.project.components.skills.aload("child")
        self.assertEqual((await self.refine.aload(identifier))["status"], "approved")

    async def test_fork_rejects_changed_parent_and_does_not_sync(self):
        identifier, data = await self.new_skill("fork")
        approved = await self.approve(identifier)
        await self.project.components.skills.aupdate("guide", {"instructions": "Changed"}, expected_version=data["parent"]["version"])
        with self.assertRaisesRegex(ValueError, "conflict"):
            await self.refine.aapply(identifier, expected_version=approved["version"])
        identifier, data = await self.new_skill("fork")
        approved = await self.approve(identifier)
        await self.refine.aapply(identifier, expected_version=approved["version"])
        child = await self.project.components.skills.aload("child")
        self.assertEqual(child["lineage"], {"parent": "guide", "parent_revision": data["parent"]["version"]})
        await self.project.components.skills.aupdate("guide", {"instructions": "Changed again"}, expected_version=data["parent"]["version"])
        self.assertEqual(await self.project.components.skills.aload("child"), child)
        self.assertEqual(await self.project.components.skills.aimpact("child"), [])

    async def test_evaluation_versions_and_authority(self):
        await self.refine.aconfigure({"policy": {"require_evaluation": True}})
        identifier, _ = await self.new_skill()
        with self.assertRaisesRegex(ValueError, "evaluation"):
            await self.approve(identifier)
        view = await self.refine.asnapshot(identifier)
        with self.assertRaisesRegex(ValueError, "versions"):
            await self.refine.arecord_evaluation(identifier, {"evaluator": "validator", "baseline_version": None,
                "candidate_version": "0" * 64, "results": {}, "regressions": [], "improvements": []},
                expected_version=view["version"])
        evaluated = await self.evaluate(identifier)
        self.assertEqual(evaluated["data"]["status"], "proposed")
        with self.assertRaises(FileNotFoundError):
            await self.project.components.skills.aload("child")
        await self.approve(identifier)
        for component, patch in (("agents", {"system_prompt": "mutate"}), ("agents", {"tools": ["shell"]}),
                                 ("skills", {"policy": {"approval": "auto"}}), ("skills", {"engine": "loop"})):
            with self.subTest(component=component, patch=patch), self.assertRaises(ValueError):
                await self.proposal(component, patch)

    async def test_tool_operation_selection_and_approval_contract(self):
        from llm.components.refinement.tools import refinement_tools
        self.assertNotIn("refinement_propose", refinement_tools(self.refine).names())
        registry = refinement_tools(self.refine, operations=["update"], apply=True)
        identifier, data, _ = await self.proposal()
        data["operation"] = "fork"
        with self.assertRaises(ValueError):
            registry.prepare("refinement_propose", json.dumps(data))
        self.assertTrue(registry.get("refinement_apply").contract.approval_required)
        view = await self.refine.asnapshot(identifier)
        with self.assertRaises(ValueError):
            registry.prepare("refinement_apply", json.dumps({"identifier": identifier,
                "expected_version": view["version"], "approval_required": False}))

    async def test_binding_preserves_authority_and_rejects_stale_skill(self):
        original = await self.project.components.agents.asnapshot("guide")
        data = {"operation": "bind_skills", "target": {"component": "agents", "identifier": "guide"},
                "expected_version": original["revision"], "patch": {"skills": ["guide"]},
                "reason": "Use guide", "evidence": self.evidence}
        identifier = await self.refine.acreate(data)
        approved = await self.approve(identifier)
        skill = await self.project.components.skills.asnapshot("guide")
        await self.project.components.skills.aupdate("guide", {"instructions": "Updated"}, expected_version=skill["version"])
        with self.assertRaisesRegex(ValueError, "Skill changed"):
            await self.refine.aapply(identifier, expected_version=approved["version"])
        self.assertEqual(await self.project.components.agents.asnapshot("guide"), original)
        data["patch"]["engine"] = "other"
        with self.assertRaises(ValueError):
            await self.refine.acreate(data)

    async def test_fork_evaluate_approve_bind_and_run_keeps_original_provenance(self):
        from llm.components.workflows import WorkflowComponent
        from llm.engines.graph import GraphEngine
        from llm.engines.graph.agent import AgentNode
        # Application이 Agent·Prompt·Skill·Tool 선택을 명시한다.
        self.app.project_manager.components.register(WorkflowComponent())
        await self.project.asave(components=[*self.project.data.components, "workflows"])
        profile = {"engine": "loop", "purpose": "Check work", "system_prompt": "Inline instruction",
                   "completion": {"model": "test/worker"},
                   "resources": {"prompt": "guide", "skills": ["guide"]}, "tools": []}
        view = await self.project.components.agents.asnapshot("guide")
        await self.project.components.agents.arevise("guide", profile, expected_revision=view["revision"])
        model = ScriptedCompletion([chunk(calls=[call('{}', name="not_allowed")], finish="tool_calls")], answer("success"))
        self.app.engines.register("graph", GraphEngine(handlers={"agent": AgentNode(engines={
            "loop": LoopEngine(completion_fn=model).for_agent({"engine": 'loop', "engine_options": LoopEngine.parameter_layout.pack({'completion': {"model": "test/worker"}})})})}))
        workflow = agent_graph()
        workflow["nodes"]["agent"]["agent"] = "guide"
        await self.project.components.workflows.acreate(workflow, identifier="flow")
        first = await (await self.session.run.submit("work", engine="graph", engine_options={"workflow": "flow"})).wait()
        self.assertEqual(str(first.data.status), "failed")
        self.evidence = [{"session_id": self.session.id, "run_id": first.id}]
        old_steps = deepcopy(await first.steps.alist())
        await self.refine.aconfigure({"policy": {"apply_tools": True, "require_evaluation": True,
                                               "proposal_operations": ["fork", "bind_skills"]}})
        parent = await self.project.components.skills.asnapshot("guide")
        proposal = {"operation": "fork", "target": {"component": "skills", "identifier": "child"},
            "expected_version": None, "parent": {"identifier": "guide", "version": parent["version"]},
            "patch": {"instructions": "Check allowed Tools before working"}, "reason": "Observed failure", "evidence": self.evidence}
        refiner = ScriptedCompletion([chunk(calls=[call(json.dumps(proposal), name="refinement_propose")], finish="tool_calls")], answer("Review proposal"))
        self.app.engines.register("refiner", LoopEngine(completion_fn=refiner).for_agent({"engine": 'loop', "engine_options": LoopEngine.parameter_layout.pack({'completion': {"model": "test/refiner"}})}))
        analysis = await (await self.session.run.submit("Analyze failure and propose a fork", engine="refiner")).wait()
        self.assertEqual(str(analysis.data.status), "completed", analysis.data.error)
        identifier, saved = next(iter((await self.refine.alist()).items()))
        self.assertEqual(saved["source"]["run_id"], analysis.id)
        await self.evaluate(identifier)
        await self.apply_tool(identifier, "apply_fork")
        self.assertEqual((await self.project.components.agents.aload("guide"))["resources"]["skills"], ["guide"])
        agent = await self.project.components.agents.asnapshot("guide")
        binding = await self.refine.acreate({"operation": "bind_skills", "target": {"component": "agents", "identifier": "guide"},
            "expected_version": agent["revision"], "patch": {"skills": ["child"]}, "reason": "Use evaluated guide", "evidence": self.evidence})
        await self.evaluate(binding)
        await self.apply_tool(binding, "apply_binding")
        second = await (await self.session.run.submit("work again", engine="graph", engine_options={"workflow": "flow"})).wait()
        self.assertEqual(str(second.data.status), "completed", second.data.error)
        self.assertEqual(await first.steps.alist(), old_steps)
        old = next(s for s in old_steps if s.kind == "agent")
        new = next(s for s in await second.steps.alist() if s.kind == "agent")
        self.assertEqual(set(old.metadata["resources"]["skill_revisions"]), {"guide"})
        self.assertEqual(set(new.metadata["resources"]["skill_revisions"]), {"child"})
        for key in ("engine", "tools", "system_prompt"):
            self.assertEqual(new.metadata["agent"][key], profile[key])
        self.assertEqual([i["agent_id"] for i in await self.project.components.skills.aimpact("child")], ["guide"])


class AgentCompositionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = agent_tests.AgentWorkflowTests.asyncSetUp
    setup_graph = agent_tests.AgentWorkflowTests.setup_graph
    run_graph = agent_tests.AgentWorkflowTests.run_graph

    async def test_prompt_revision_is_frozen_until_next_run(self):
        from dataclasses import replace
        from llm.components.tools import ToolRegistry
        self.profile["resources"] = {"prompt": "base"}
        async def edit_prompt(args):
            snapshot = await self.project.components.prompts.asnapshot("base")
            await self.project.components.prompts.aupdate("base", {"messages": [{"role": "system", "content": "New prompt"}]}, expected_version=snapshot["version"])
            return "edited"
        self.catalog = ToolRegistry([replace(self.catalog.get(name), handler=edit_prompt) for name in self.catalog.names()])
        model = ScriptedCompletion([chunk(calls=[call('{"value":"change"}', name="echo")], finish="tool_calls")],
                                   answer("first"), answer("second"))
        await self.setup_graph(agent_graph(), model, extra_components=[PromptComponent()])
        await self.project.components.prompts.acreate({"messages": [{"role": "system", "content": "Old prompt"}]}, identifier="base")
        first = await self.run_graph()
        self.assertEqual(str(first.data.status), "completed", first.data.error)
        self.assertIn("Old prompt", model.requests[1]["messages"][0]["content"])
        second = await self.run_graph()
        self.assertEqual(str(second.data.status), "completed", second.data.error)
        self.assertIn("New prompt", model.requests[2]["messages"][0]["content"])
        old = next(s for s in await first.steps.alist() if s.kind == "agent")
        new = next(s for s in await second.steps.alist() if s.kind == "agent")
        self.assertNotEqual(old.metadata["resources"]["prompt_revision"], new.metadata["resources"]["prompt_revision"])

    async def test_prompt_skill_order_and_no_authority_from_resource_data(self):
        self.profile["resources"] = {"prompt": "base", "skills": ["guide"]}
        model = ScriptedCompletion(answer("done"))
        await self.setup_graph(agent_graph(), model, extra_components=[PromptComponent(), SkillComponent()])
        await self.project.components.prompts.acreate({"messages": [{"role": "system", "content": "Reference prompt"}]}, identifier="base")
        await self.project.components.skills.acreate({"instructions": "Skill instruction", "metadata": {"tools": ["forbidden"], "engine": "other"}}, identifier="guide")
        run = await self.run_graph()
        self.assertEqual(str(run.data.status), "completed", run.data.error)
        prompt = model.requests[0]["messages"][0]["content"]
        self.assertLess(prompt.index("Reference prompt"), prompt.index(self.profile["system_prompt"]))
        self.assertLess(prompt.index(self.profile["system_prompt"]), prompt.index("Skill instruction"))
        self.assertEqual([t["function"]["name"] for t in model.requests[0]["tools"]], ["echo"])


class MemoryBoundaryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = refinement_tests.RefinementTests.asyncSetUp

    async def test_extraction_without_search_uses_stable_record_order(self):
        from llm.components.memory import MemoryComponent
        from unittest.mock import patch
        captured = []
        def extract(**request):
            captured.append(json.loads(request["messages"][-1]["content"]))
            return iter(answer(json.dumps({"memories": []})))
        memory = self.project.components.memory
        self.memory_component.completion_fn = extract
        await memory.acreate({"content": "Last"}, identifier="z")
        await memory.acreate({"content": "First"}, identifier="a")
        # No retrieval tuning/recall limit: extraction owns only its explicit input budget.
        await memory.aconfigure(MemoryComponent.parameter_layout.pack({"processing": {
            "priority": 1, "extract": True, "extract_prompt_id": "guide", "extract_scope": "session",
            "completion": {"model": "test/extract"}, "model_input_chars": 24000,
            "max_candidates": 2, "summary_chars": 2000}}))
        with patch.object(type(memory), "asearch", side_effect=AssertionError("retrieval must be independent")):
            for _ in range(2):
                run = await (await self.session.run.submit("remember", engine="loop")).wait()
                self.assertEqual(str(run.data.status), "completed", run.data.error)
        self.assertEqual([[r["id"] for r in p["existing"]] for p in captured], [["a", "guide", "z"]] * 2)

    async def test_memory_extraction_uses_selected_prompt_and_records_its_revision(self):
        from tests.llm.configuration_fixtures import memory_settings, memory_processing
        captured = []
        def extract(**request):
            captured.append(request)
            return iter(answer(json.dumps({"memories": [{"content": "Explicit remembered constraint", "kind": "constraint", "tags": [], "replaces": []}]})))
        self.memory_component.completion_fn = extract
        options = memory_processing({"recall": False, "extract": True, "completion": {"model": "test/extract"}})
        await self.project.components.memory.aconfigure(memory_settings({"processing": options}))
        failed = await (await self.session.run.submit("remember", engine="loop")).wait()
        self.assertEqual(str(failed.data.status), "failed")
        self.assertEqual(captured, [])
        options["extract_prompt_id"] = "guide"
        await self.project.components.memory.aconfigure(memory_settings({"processing": options}))
        run = await (await self.session.run.submit("remember", engine="loop")).wait()
        self.assertEqual(str(run.data.status), "completed", run.data.error)
        self.assertIn("Before", captured[0]["messages"][0]["content"])
        records = await self.project.components.memory.alist(session_id=self.session.id)
        candidate = next(value for value in records.values() if value["status"] == "candidate")
        prompt = await self.project.components.prompts.asnapshot("guide")
        self.assertEqual(candidate["metadata"]["extract_prompt"], {"source": "guide", "version": prompt["version"]})

    async def test_missing_strategy_custom_zero_score_and_explicit_cutoff(self):
        memory = self.project.components.memory
        self.assertEqual(await memory.aget_config(), {})
        with self.assertRaisesRegex(ValueError, "search_strategy"):
            await memory.asearch("Before")
        await memory.aconfigure({"config": {"search_strategy": "keyword"}})
        self.assertEqual(len(await memory.asearch("Before")), 1)
        self.assertEqual(await memory.asearch("missing"), [])
        self.memory_component.search_fn = lambda query, records: {key: 0 for key in records}
        await memory.aconfigure({})
        self.assertEqual((await memory.asearch("anything"))[0]["score"], 0)
        self.memory_component.search_fn = lambda query, records: {key: -1 for key in records}
        self.assertEqual((await memory.asearch("anything"))[0]["score"], -1)
        await memory.aconfigure({"config": {"min_score": .1}})
        self.assertEqual(await memory.asearch("anything"), [])

    async def test_extract_prompt_is_required_and_snapshot_is_not_live(self):
        memory = self.project.components.memory
        with self.assertRaisesRegex(ValueError, "extract_prompt"):
            await memory._async_call(memory._extraction_prompt, None)
        first = await memory._async_call(memory._extraction_prompt, "guide")
        prompt = await self.project.components.prompts.asnapshot("guide")
        await self.project.components.prompts.aupdate("guide", {"messages": [{"role": "system", "content": "Remember only task constraints"}]}, expected_version=prompt["version"])
        second = await memory._async_call(memory._extraction_prompt, "guide")
        self.assertIn("Before", first["text"])
        self.assertNotEqual(first["version"], second["version"])
        with self.assertRaises(TypeError):
            type(self.memory_component)(extract_prompt="Host policy")
        self.assertEqual(second["source"], "guide")


class GraphRankingTests(unittest.TestCase):
    def test_tuning_values_have_no_product_ceiling(self):
        from jsonschema import Draft202012Validator
        from llm.components.rag import RAGComponent
        from llm.components.rag.splitting import split_markdown
        from llm.providers.requests import resolve_provider_options
        schema = RAGComponent().describe_config()
        settings = {"config": {"chunk_size": 1, "search": {"limit": 101, "max_hops": 6, "relation_limit": 1001}},
                    "policy": {"embedding_concurrency": 33}}
        Draft202012Validator(schema).validate(settings)
        self.assertEqual(resolve_provider_options({"max_attempts": 11}), {"max_attempts": 11})
        self.assertEqual([c["text"] for c in split_markdown("abc", "doc", chunk_size=1)["chunks"]], ["a", "b", "c"])

    def test_same_corpus_explicit_ranking_changes_only_order(self):
        import tempfile
        from pathlib import Path
        from llm.components.rag.graph_indexing import build_graph, connection, rows
        from llm.components.rag.graph_search import graph_search
        options = {"buffer_pool_size": 64 * 1024 * 1024, "max_num_threads": 2}
        entities = [{"id": k, "name": k} for k in ("root", "a", "b")]
        def edge(target, source):
            return {"source": "root", "target": target, "type": "LINKS", "source_id": source,
                    "evidence": "root links " + target, "metadata": {}, "extracted_at": "2026-01-01T00:00:00+00:00"}
        documents = {"a": {"id": "a", "graph": {"entities": entities, "relations": [edge("a", "c1")]}},
                     "b": {"id": "b", "graph": {"entities": entities, "relations": [edge("b", "c2"), edge("b", "c3")]}}}
        original = deepcopy(documents)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "graph.kuzu"
            build_graph(path, documents, options=options)
            def stored_facts():
                # DB 자체의 checkpoint/header bytes는 열고 닫을 때 바뀔 수 있다.
                # 정렬 선택이 영속 노드/관계 값을 수정하지 않는지 검사한다.
                with connection(path, options=options) as conn:
                    return (rows(conn, "MATCH (e:Entity) RETURN e.id,e.name ORDER BY e.id"),
                            rows(conn, "MATCH (a:Entity)-[r:Link]->(b:Entity) RETURN a.id,b.id,r.kind,r.source_id,r.weight,r.evidence ORDER BY r.source_id"))
            before = stored_facts()
            stable = graph_search(path, "root", max_hops=1, limit=1, options=options)
            ranked = graph_search(path, "root", max_hops=1, limit=1, options=options, ranking="support_count")
            self.assertEqual(stable["relations"][0]["target"], "a")
            self.assertEqual(ranked["relations"][0]["target"], "b")
            self.assertEqual(ranked["relations"][0]["support_count"], 2)
            self.assertEqual(before, stored_facts())
            self.assertEqual(documents, original)
            with self.assertRaises(ValueError):
                graph_search(path, "root", max_hops=1, limit=1, options=options, ranking="recommended")
