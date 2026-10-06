"""승인 UI 상태, Graph 취소 경계, 기억 병합과 큰 문맥의 통합 회귀 검사."""
import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from llm.components.memory import MemoryConflictError
from llm.components.workflows import WorkflowGraph
from llm.services.runtime.tools import ToolPolicy, ToolApprovalRequired
from llm.core.interactions import approval_request
from tests.llm import test_interactions
from tests.llm import test_graph_checkpoints
from tests.llm import test_memory_processing
from tests.llm.test_memory_processing import answer
from tests.llm.test_loop import chunk, call


class ApprovalHardeningTests(unittest.IsolatedAsyncioTestCase):
    setup_app = test_interactions.InteractionRuntimeTests.setup_app
    paused = test_interactions.InteractionRuntimeTests.paused

    async def test_rolled_back_interaction_changes_do_not_notify_ui(self):
        from llm.services.infrastructure import transactions
        from llm.engines.base import EngineEventType
        for operation in ('respond', 'cancel', 'renew'):
            with self.subTest(operation=operation):
                options = ({'expires_at': (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}
                           if operation == 'renew' else {})
                app, _, run = await self.paused(**options)
                request, = await run.ainteractions()
                before = [view.to_dict() for view in await run.ainteraction_views()]
                observed = []
                def observe(current, event):
                    if event.type == EngineEventType.INTERACTION_CHANGED:
                        observed.append(event)
                app.events.subscribe(observe)
                original = transactions._write
                def fail_commit(path, data):
                    if path.name == 'COMMITTED':
                        raise OSError('interaction commit unavailable')
                    return original(path, data)
                with patch.object(transactions, '_write', side_effect=fail_commit):
                    with self.assertRaisesRegex(OSError, 'commit unavailable'):
                        if operation == 'respond':
                            await run.arespond(request.respond('approve'))
                        elif operation == 'cancel':
                            await run.acancel_interaction(request)
                        else:
                            await run.arenew_interaction(request)
                # 예약된 UI 알림이 있다면 처리한 뒤 검사한다. 시간 지연을 추측하지 않는다.
                await asyncio.sleep(0)
                await asyncio.gather(*tuple(app._observation_tasks))
                await app.events.flush()
                self.assertEqual([view.to_dict() for view in await run.ainteraction_views()], before)
                self.assertEqual(await run.ainteraction_responses(), [])
                self.assertEqual(observed, [])
                self.assertEqual(self.effects, [])

    async def test_interaction_notification_observes_committed_receipt_and_detached_view(self):
        from copy import deepcopy
        from llm.engines.base import EngineEventType
        from llm.services.infrastructure.transactions import current_transaction
        app, _, run = await self.paused()
        request, = await run.ainteractions()
        observed, notified = [], asyncio.Event()
        async def observe(current, event):
            if event.type == EngineEventType.INTERACTION_CHANGED:
                # 저장 스레드의 열린 transaction 문맥도 UI 콜백으로 유출되면 안 된다.
                transaction = current_transaction()
                receipts = await run.ainteraction_responses()
                views = [v.to_dict() for v in await run.ainteraction_views()]
                observed.append((transaction, receipts, views, deepcopy(event.metadata['interactions'])))
                event.metadata['interactions'].clear()
                notified.set()
        app.events.subscribe(observe)
        response = await run.arespond(request.respond('approve'))
        await asyncio.wait_for(notified.wait(), 5)
        transaction, receipts, views, payload = observed[0]
        self.assertIsNone(transaction)
        self.assertEqual(receipts, [response])
        self.assertEqual(views[0]['status'], 'answered')
        self.assertEqual([v.to_dict() for v in await run.ainteraction_views()], views)
        self.assertEqual(payload, views)
        self.assertEqual(run.data.status, 'paused')
        self.assertEqual(self.effects, [])

    async def test_dropped_interaction_notifications_reconcile_without_executing(self):
        from llm.engines.base import EngineEventType
        app, session, run = await self.paused()
        request, = await run.ainteractions()
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        async def slow_ui(current, event):
            if event.type == EngineEventType.INTERACTION_CHANGED:
                entered.set()
                await release.wait()
                seen.append(event.metadata['interactions'])
        subscription = app.events.subscribe(slow_ui, delivery='queued', buffer_size=1, overflow='drop_oldest')
        try:
            await run.acancel_interaction(request)
            await asyncio.wait_for(entered.wait(), 5)
            renewed = await run.arenew_interaction(request)
            await run.arespond(renewed.respond('approve'))
            await asyncio.sleep(0)
            await asyncio.gather(*tuple(app._observation_tasks))
            self.assertGreater(subscription.stats['dropped'], 0)
            view, = await run.ainteraction_views()
            self.assertEqual(view.status, 'answered')
            self.assertTrue(view.can_resume)
            self.assertEqual(view.request.id, renewed.id)
            self.assertEqual(len(await run.ainteraction_responses()), 1)
            self.assertEqual(self.effects, [])
            self.assertEqual(run.data.status, 'paused')
        finally:
            release.set()
        await app.events.flush()
        self.assertEqual([values[0]['status'] for values in seen], ['cancelled', 'answered'])
        resumed = await (await session.run.resume(run.id, engine='loop')).wait()
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        view, = await run.ainteraction_views()
        self.assertEqual((view.status, view.execution_status), ('submitted', 'completed'))
        self.assertEqual(view.resumed_run_id, resumed.id)
        self.assertEqual(self.effects, [{}])

    async def test_cancel_renew_preview_and_ui_execution_state(self):
        app, session, run = await self.paused()
        request, = await run.ainteractions()
        plan = await session.run.resume_plan(run.id, engine="loop")
        self.assertFalse(plan.can_resume)
        await run.acancel_interaction(request)
        view, = await run.ainteraction_views()
        self.assertEqual(view.status, "cancelled")
        with self.assertRaises(Exception):
            await session.run.resume(run.id, engine="loop")
        renewed = await run.arenew_interaction(request)
        self.assertNotEqual(renewed.id, request.id)
        self.assertEqual(renewed.action, request.action)
        with self.assertRaises(ValueError):
            await run.arespond(request.respond("approve"))
        await run.arespond(renewed.respond("approve"))
        before = len(await session.aconversation())
        plan = await session.run.resume_plan(run.id, engine="loop")
        self.assertTrue(plan.can_resume, plan)
        self.assertEqual(len(await session.aconversation()), before)
        resumed = await (await session.run.resume(run.id, engine="loop")).wait()
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        view, = await run.ainteraction_views()
        self.assertEqual((view.status, view.execution_status), ("submitted", "completed"))
        self.assertFalse(view.can_resume)
        self.assertEqual(await run.ainteractions(pending_only=True), [])

    async def test_expired_request_renewal_and_post_persistence_notification(self):
        app, session, run = await self.paused(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        old, = await run.ainteractions()
        renewed = await run.arenew_interaction(old)
        notified = asyncio.Event()
        def observe(current, event):
            if event.type.value == "interaction_changed":
                # 알림에서 조회해도 이미 영수증이 저장되어 있다.
                if run.interaction_responses():
                    notified.set()
        app.on_event = observe
        await run.arespond(renewed.respond("approve"))
        await asyncio.wait_for(notified.wait(), 3)
        resumed = await (await session.run.resume(run.id, engine="loop")).wait()
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)

    async def test_auto_approval_requires_host_ceiling_and_explicit_resume(self):
        for host_allows in (False, True):
            effects = []
            async def act(arguments):
                effects.append(1)
            async def authorize(call):
                raise ToolApprovalRequired(request=approval_request("read", category="file.read", risk="low"))
            app, session, _ = await self.setup_app(act, [
                [chunk(calls=[call('{}', name='act')], finish='tool_calls')], [chunk('done', finish='stop')]],
                ToolPolicy(authorize=authorize, auto_approve_categories=("file.read",) if host_allows else ()))
            await session.project.aconfigure_policies({"approval": {"enabled": True, "rules": [
                {"id": "read-only", "category": "file.read", "max_risk": "low"}]}})
            run = await (await session.run.submit("read", engine="loop")).wait()
            self.assertEqual(run.data.status, "paused", run.data.error)
            responses = await run.ainteraction_responses()
            self.assertEqual(len(responses), int(host_allows))
            self.assertEqual(effects, [])
            if host_allows:
                self.assertEqual((responses[0].actor, responses[0].policy_id), ("policy", "read-only"))
                resumed = await (await session.run.resume(run.id, engine="loop")).wait()
                self.assertEqual(resumed.data.status, "completed", resumed.data.error)
                self.assertEqual(effects, [1])

    async def test_policy_storage_failure_keeps_paused_session_reusable(self):
        with patch('llm.services.runtime.runs.RunRepository.apply_interaction_policy', side_effect=OSError("disk")):
            app, session, run = await self.paused()
        self.assertEqual(session.data.status, "idle")
        request, = await run.ainteractions()
        await run.arespond(request.respond("approve"))
        resumed = await (await session.run.resume(run.id, engine="loop")).wait()
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)


class GraphHardeningTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_graph_checkpoints.GraphCheckpointTests.asyncSetUp
    setup_graph = test_graph_checkpoints.GraphCheckpointTests.setup_graph
    run_graph = test_graph_checkpoints.GraphCheckpointTests.run_graph

    async def test_uncooperative_handler_blocks_next_session_work_until_exit(self):
        started, release = asyncio.Event(), asyncio.Event()
        visits, late = [], []
        async def work(node):
            visits.append(node.context.run.id)
            if len(visits) == 1:
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                    try:
                        node.context.tool_scope.require_active()
                    except Exception as error:
                        late.append(error.code)
            return {}
        graph = WorkflowGraph(entry="work").node("work", "work").node("end", "end").connect("work", "end").to_dict()
        await self.setup_graph(graph, work, cleanup_timeout=0.05)
        first = await self.session.run.submit("first", engine="graph", engine_options={"workflow": "flow"})
        await asyncio.wait_for(started.wait(), 5)
        second = await self.session.run.submit("second", engine="graph", engine_options={"workflow": "flow"})
        try:
            await asyncio.wait_for(self.session.run.interrupt(), 3)
            interrupted = await first.wait(timeout=3)
            self.assertEqual(interrupted.data.status, "interrupted")
            self.assertEqual((await self.session.run.astatus()).unfinished_work, 1)
            self.assertEqual(len(visits), 1)
        finally:
            release.set()
        completed = await second.wait(timeout=10)
        self.assertEqual(completed.data.status, "completed", completed.data.error)
        self.assertEqual(late, ["tool_scope_closed"])

    async def test_shutdown_retains_session_lease_until_handler_really_stops(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def work(node):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return {}
        graph = WorkflowGraph(entry="work").node("work", "work").node("end", "end").connect("work", "end").to_dict()
        await self.setup_graph(graph, work, cleanup_timeout=0.05)
        await self.session.run.submit("first", engine="graph", engine_options={"workflow": "flow"})
        await asyncio.wait_for(started.wait(), 5)
        from llm.llm import LargeLanguageModel
        other = LargeLanguageModel(self.root, engines={"graph": self.engine})
        self.addAsyncCleanup(other.shutdown)
        try:
            await asyncio.wait_for(self.app.shutdown(), 3)
            with self.assertRaises(Exception):
                await other.projects.aload(self.project.id)
        finally:
            release.set()
        await asyncio.sleep(0.05)
        session = (await other.projects.aload(self.project.id)).sessions.load(self.session.id)
        completed = await (await session.run.submit("after cleanup", engine="graph", engine_options={"workflow": "flow"})).wait()
        self.assertEqual(completed.data.status, "completed", completed.data.error)

    async def test_selected_config_binding_allows_ui_but_rejects_model_change(self):
        async def work(node):
            return {}
        graph = WorkflowGraph(entry="work").node("work", "work", pause_before=True).node("end", "end").connect("work", "end").to_dict()
        await self.setup_graph(graph, work, config_keys=("business",))
        run = await self.run_graph()
        request, = await run.ainteractions()
        await run.arespond(request.respond("approve"))
        await self.session.run.shutdown()
        config = self.project.data.config.to_dict()
        config.setdefault("data", {})["ui_color"] = "blue"
        await self.project.asave(config=config)
        self.assertTrue((await self.session.run.resume_plan(run.id, engine="graph")).can_resume)
        config["parameters"]["engines"] = {"loop": {"config": {"completion": {"model": "changed/model"}}}}
        await self.project.asave(config=config)
        plan = await self.session.run.resume_plan(run.id, engine="graph")
        self.assertFalse(plan.can_resume)
        self.assertIn("settings changed", str(plan.blockers))

    async def test_uncertain_node_retry_is_a_common_interaction(self):
        calls = []
        async def work(node):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("uncertain effect")
            return {}
        graph = WorkflowGraph(entry="work").node("work", "work").node("end", "end").connect("work", "end").to_dict()
        await self.setup_graph(graph, work)
        failed = await self.run_graph()
        request, = await failed.ainteractions()
        self.assertEqual(request.category, "execution.retry_uncertain")
        await failed.arespond(request.respond("approve"))
        resumed = await (await self.session.run.resume(failed.id, engine="graph")).wait()
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertEqual(len(calls), 2)


class MemoryHardeningTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_memory_processing.MemoryProcessingTests.asyncSetUp
    configure = test_memory_processing.MemoryProcessingTests.configure
    run_loop = test_memory_processing.MemoryProcessingTests.run_loop

    async def proposals(self):
        await self.memory.acreate({"content": "old specification"}, identifier="old")
        await self.memory.acreate({"content": "reviewed specification", "status": "candidate",
            "replaces": [{"id": "old", "revision": 1}]}, identifier="proposal")

    async def test_consolidation_applies_review_and_keeps_history(self):
        await self.proposals()
        receipt = await self.memory.aconsolidate("proposal", expected_revision=1)
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual((await self.memory.aload("proposal"))["status"], "confirmed")
        self.assertTrue((await self.memory.aload("old", include_deleted=True))["deleted"])
        self.assertEqual(len(await self.memory.ahistory("old")), 2)
        self.assertEqual(await self.memory.arecover_consolidations(), [])

    async def test_partial_consolidation_rolls_back_until_explicit_retry(self):
        await self.proposals()
        original = self.component._write
        writes = []
        def fail_second(project, identifier, data):
            writes.append(identifier)
            if len(writes) == 2:
                raise OSError("simulated full disk")
            return original(project, identifier, data)
        with patch.object(self.component, "_write", side_effect=fail_second):
            with self.assertRaises(OSError):
                await self.memory.aconsolidate("proposal", expected_revision=1)
        self.assertIn("old", await self.memory.alist())
        self.assertEqual((await self.memory.aload("proposal"))["revision"], 1)
        self.assertEqual(await self.memory.apending_consolidations(), [])
        self.assertEqual(await self.memory.arecover_consolidations(), [])
        await self.memory.aconsolidate("proposal", expected_revision=1)
        self.assertEqual(set(await self.memory.alist()), {"proposal"})

    async def test_stale_consolidation_does_not_modify_candidate(self):
        await self.proposals()
        await self.memory.aupdate("old", {"content": "newer requirement"}, expected_revision=1)
        with self.assertRaises(MemoryConflictError):
            await self.memory.aconsolidate("proposal", expected_revision=1)
        self.assertEqual((await self.memory.aload("proposal"))["revision"], 1)
        self.assertEqual(await self.memory.apending_consolidations(), [])

    async def test_search_adapter_only_receives_visible_records_and_cache_invalidates(self):
        await self.memory.aconfigure({'config': {'search_status': 'confirmed', 'cache_records': 256}})
        await self.memory.acreate({"content": "first"}, identifier="one")
        await self.memory.acreate({"content": "hidden", "status": "candidate"}, identifier="candidate")
        def rank(query, records):
            self.assertEqual(set(records), {"one"})
            return {"one": 0.9}
        self.component.search_fn = rank
        await self.memory.asearch("semantic synonym")
        with patch.object(self.component, "_read", wraps=self.component._read) as reader:
            await self.memory.asearch("semantic synonym")
            self.assertEqual(reader.call_count, 0)
        await self.memory.aupdate("one", {"content": "second"}, expected_revision=1)
        self.assertEqual((await self.memory.asearch("synonym"))[0]["memory"]["content"], "second")
        self.component.search_fn = lambda query, records: {"candidate": 1}
        with self.assertRaises(ValueError):
            await self.memory.asearch("hidden")

    async def test_large_tool_exchange_fragments_keep_pairs_and_progress(self):
        payloads = []
        def summarize(**request):
            payloads.append(json.loads(request["messages"][1]["content"]))
            yield from answer(json.dumps({"summary": "Checked source; preserve unfinished work."}))
        self.component.completion_fn = summarize
        await self.configure(summarize=True, summary_after_chars=1, active_keep_iterations=1,
            model_input_chars=1400, summary_chars=100, max_summary_calls=2)
        await self.memory.acreate({"content": "reference " * 140}, identifier="source")
        from tests.llm.test_loop import ScriptedCompletion
        model = ScriptedCompletion(*[
            [chunk(calls=[call('{"identifier":"source"}', name='memory_get', call_id=f'lookup_{i}')], finish='tool_calls')]
            for i in range(7)], answer())
        run, _ = await self.run_loop(model=model)
        self.assertEqual(run.data.status, "completed", run.data.error)
        self.assertTrue(any("fragment" in str(p) for p in payloads))
        self.assertTrue(all(len(json.dumps(p, ensure_ascii=False)) <= 1400 for p in payloads))
        self.assertIn("Completed work reference", str(model.requests[-1]["messages"]))
        for request in model.requests:
            messages = request["messages"]
            requested = {c["id"] for m in messages for c in m.get("tool_calls", [])}
            returned = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
            self.assertEqual(requested, returned)

    async def test_large_turn_partial_summary_keeps_original_until_covered(self):
        payloads = []
        def summarize(**request):
            payloads.append(json.loads(request["messages"][1]["content"]))
            yield from answer(json.dumps({"summary": "Preserve the original requirement; work remains."}))
        self.component.completion_fn = summarize
        await self.run_loop("LONG REQUIREMENT " * 250)
        await self.run_loop("recent")
        await self.configure(summarize=True, summary_after_chars=1, keep_turns=1,
            model_input_chars=1000, summary_chars=100, max_summary_calls=1)
        _, model = await self.run_loop()
        summary = await self.memory.asummary(self.session.id)
        self.assertEqual(summary["metadata"]["coverage_count"], 0)
        self.assertGreater(summary["metadata"]["partial"]["offset"], 0)
        self.assertTrue(any("LONG REQUIREMENT" in str(m) for m in model.requests[0]["messages"]))
        for _ in range(10):
            await self.run_loop()
            summary = await self.memory.asummary(self.session.id)
            if summary["metadata"]["coverage_count"] >= 2:
                break
        self.assertGreaterEqual(summary["metadata"]["coverage_count"], 2)
        self.assertTrue(all(len(json.dumps(p, ensure_ascii=False)) <= 1000 for p in payloads))
        self.assertEqual((await self.session.aconversation())[0].content, "LONG REQUIREMENT " * 250)
