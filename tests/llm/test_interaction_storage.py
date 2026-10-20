"""A missing stored expiry must not reactivate approval or admit a new Run."""

from copy import deepcopy
from dataclasses import replace
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from llm.components.tools import Tool, ToolRegistry
from llm.core.interactions import (
    InteractionOption, InteractionRequest, InteractionResponse, approval_request,
)
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel, BackendServices
from llm.services.runtime.tools import ToolRuntime, ToolApprovalRequired
from llm.services.runtime.interactions import InteractionRepository
from llm.services.infrastructure.locking import WorkspaceOwnership
from tests.llm.support.runtime_tools import RuntimeTools, TEST_CONTRACT
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from tests.llm import test_interactions
from tests.llm import test_graph_checkpoints
from tests.llm.test_graph_engine import straight


class InteractionReaderTests(unittest.TestCase):
    def test_all_serialized_fields_are_required_without_changing_the_source(self):
        request = approval_request("review")
        for instance in (request.options[0], request, request.respond("approve")):
            value = instance.to_dict()
            self.assertEqual(type(instance).from_dict(value), instance)
            for field in value:
                with self.subTest(model=type(instance).__name__, missing=field):
                    damaged = deepcopy(value)
                    del damaged[field]
                    before = deepcopy(damaged)
                    with self.assertRaises((ValueError, TypeError)):
                        type(instance).from_dict(damaged)
                    self.assertEqual(damaged, before)

    def test_nested_options_and_wrong_types_are_rejected(self):
        request = approval_request("review")
        mutations = {
            "created_at": None, "expires_at": False, "source": [],
            "action": None, "binding": False, "revision": True,
            "options": (), "input_schema": [],
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                damaged = {**request.to_dict(), field: value}
                with self.assertRaises((ValueError, TypeError)):
                    InteractionRequest.from_dict(damaged)
        damaged = request.to_dict()
        del damaged["options"][0]["effect"]
        with self.assertRaises((ValueError, TypeError)):
            InteractionRequest.from_dict(damaged)
        for instance in (request.options[0], request, request.respond("approve")):
            with self.subTest(model=type(instance).__name__):
                with self.assertRaises((ValueError, TypeError)):
                    type(instance).from_dict({**instance.to_dict(), "unknown": None})

    def test_explicit_nullable_and_falsey_values_roundtrip(self):
        for value in (None, False, 0, "", [], {}):
            request = InteractionRequest("choice", (
                InteractionOption("select", "select", value=value),), kind="choice",
                expires_at=None, risk=None, risk_scheme=None)
            restored = InteractionRequest.from_dict(request.to_dict())
            self.assertEqual(restored, request)
            response = request.respond("select")
            self.assertEqual(InteractionResponse.from_dict(response.to_dict()), response)
            self.assertEqual(restored.options[0].value, value)


class InteractionIntegrityTests(unittest.IsolatedAsyncioTestCase):
    setup_app = test_interactions.InteractionRuntimeTests.setup_app
    paused = test_interactions.InteractionRuntimeTests.paused

    async def test_missing_control_is_not_pending_and_cannot_admit_effects(self):
        for state in ("pending", "answered", "cancelled", "renewed"):
            with self.subTest(state=state):
                _, session, run = await self.paused()
                request, = await run.ainteractions()
                if state == "answered":
                    await run.arespond(request.respond("approve"))
                if state in ("cancelled", "renewed"):
                    await run.acancel_interaction(request)
                if state == "renewed":
                    request = await run.arenew_interaction(request)
                path = run.data.paths.state / "interactions" / (request.id + ".json")
                # Absence is the injected fault even when the old implementation
                # never persisted its initial pending state.
                path.unlink(missing_ok=True)
                with self.assertRaises((ValueError, TypeError, FileNotFoundError)):
                    await run.ainteraction_views()
                with self.assertRaises((ValueError, TypeError, FileNotFoundError)):
                    await run.arespond(request.respond("approve"))
                with self.assertRaises((ValueError, TypeError, FileNotFoundError)):
                    await run.acancel_interaction(request)
                with self.assertRaises((ValueError, TypeError, FileNotFoundError)):
                    await run.arenew_interaction(request)
                with self.assertRaises(ValueError):
                    await session.run.resume(run.id, engine="loop",
                                             decisions={request.binding["key"]: True})
                self.assertFalse(path.exists())
                self.assertEqual(self.effects, [])
                self.assertEqual(len(await session.run.alist()), 1)

    async def test_missing_control_fields_do_not_restore_state(self):
        for state, field in (("cancelled", "cancelled"), ("renewed", "replacement"),
                             ("answered", "response")):
            with self.subTest(state=state, field=field):
                _, session, run = await self.paused()
                request, = await run.ainteractions()
                if state == "answered":
                    await run.arespond(request.respond("approve"))
                else:
                    await run.acancel_interaction(request)
                    if state == "renewed":
                        await run.arenew_interaction(request)
                path = run.data.paths.state / "interactions" / (request.id + ".json")
                damaged = json.loads(path.read_text())
                del damaged[field]
                path.write_text(json.dumps(damaged))
                before = path.read_bytes()
                with self.assertRaises((ValueError, TypeError)):
                    await run.ainteraction_views()
                with self.assertRaises(ValueError):
                    await session.run.resume(run.id, engine="loop")
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(self.effects, [])

    async def test_corrupt_response_is_not_accepted_by_backup(self):
        _, session, run = await self.paused()
        request, = await run.ainteractions()
        await run.arespond(request.respond("approve"))
        await session.run.shutdown()
        path = run.data.paths.state / "interactions" / (request.id + ".json")
        damaged = json.loads(path.read_text())
        del damaged["response"]["actor"]
        path.write_text(json.dumps(damaged))
        before = path.read_bytes()
        with TemporaryDirectory() as directory:
            target = Path(directory) / "archive"
            with self.assertRaises((ValueError, TypeError)):
                await session.project.abackup(target)
            self.assertFalse(target.exists())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.effects, [])

    async def test_complete_control_schema_rejects_missing_and_invalid_fields(self):
        _, _, run = await self.paused()
        request, = await run.ainteractions()
        path = run.data.paths.state / "interactions" / (request.id + ".json")
        original = json.loads(path.read_text())
        invalid = [(name, {k: v for k, v in original.items() if k != name}) for name in original]
        invalid.extend((name, {**original, name: value}) for name, value in (
            ("storage_version", True), ("storage_version", 1.0), ("cancelled", None),
            ("cancelled", 0), ("response", {}), ("replacement", {}), ("request_revision", True),
            ("run_id", "wrong"), ("request_id", "wrong"), ("request_fingerprint", "wrong")))
        for name, value in invalid:
            with self.subTest(field=name, value=value):
                path.write_text(json.dumps(value))
                before = path.read_bytes()
                with self.assertRaises((ValueError, TypeError)):
                    await run.ainteraction_views()
                self.assertEqual(path.read_bytes(), before)
        path.write_text(json.dumps(original))
        self.assertTrue((await run.ainteraction_views())[0].can_respond)
        self.assertEqual(self.effects, [])

    async def test_concurrent_renewals_create_one_complete_chain_and_reject_old_answer(self):
        _, _, run = await self.paused()
        request, = await run.ainteractions()
        await run.acancel_interaction(request)
        results = await asyncio.gather(run.arenew_interaction(request),
                                       run.arenew_interaction(request), return_exceptions=True)
        successes = [r for r in results if isinstance(r, InteractionRequest)]
        failures = [r for r in results if isinstance(r, ValueError)]
        self.assertEqual((len(successes), len(failures)), (1, 1))
        with self.assertRaises(ValueError):
            await run.arespond(request.respond("approve"))
        view, = await run.ainteraction_views()
        self.assertEqual(view.request, successes[0])
        self.assertEqual(view.status, "pending")
        self.assertEqual(self.effects, [])

    async def test_pending_request_and_cancelled_renewal_survive_reopen(self):
        app, session, run = await self.paused()
        request, = await run.ainteractions()
        root = session.project.paths.root.parent.parent
        project_id, session_id, run_id = session.project.id, session.id, run.id
        engine, component = app.engines.get("loop"), app.project_manager.components.get("tools")
        runtime = app._manager(session._snapshot).tool_runtime
        await app.shutdown()
        reopened = LargeLanguageModel(root, engines={"loop": engine}, components=[component],
                                      services=BackendServices(tool_runtime=runtime))
        self.addAsyncCleanup(reopened.shutdown)
        session = (await reopened.projects.aload(project_id)).sessions.load(session_id)
        run = await session.run.aload(run_id)
        view, = await run.ainteraction_views()
        self.assertEqual(view.status, "pending")
        self.assertEqual(view.request, request)
        await run.acancel_interaction(request)
        replacement = await run.arenew_interaction(request)
        await run.arespond(replacement.respond("approve"))
        resumed = await (await session.run.resume(run.id, engine="loop")).wait()
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertEqual(self.effects, [{}])


class InteractionAtomicityTests(unittest.TestCase):
    def test_process_crash_during_renewal_keeps_old_or_complete_new_chain(self):
        with TemporaryDirectory() as directory:
            saw_old = saw_new = False
            for cut in range(1, 100):
                root = Path(directory) / str(cut)
                root.mkdir()
                request = approval_request("review").bind("loop", "key")
                (root / "request.json").write_text(json.dumps(request.to_dict()))
                run = SimpleNamespace(id="a" * 32, paths=SimpleNamespace(state=root / "state"))
                owner, repository = WorkspaceOwnership(root), InteractionRepository()
                with owner.scope():
                    repository.initialize(run, request)
                    repository.cancel(run, request)
                original = repository._path(run, request).read_bytes()
                code = """
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
from llm.core.interactions import InteractionRequest
from llm.services.runtime.interactions import InteractionRepository
from llm.services.infrastructure.locking import WorkspaceOwnership
root, cut = Path(sys.argv[1]), int(sys.argv[2])
request = InteractionRequest.from_dict(json.loads((root / "request.json").read_text()))
run = SimpleNamespace(id="a" * 32, paths=SimpleNamespace(state=root / "state"))
owner, repository = WorkspaceOwnership(root), InteractionRepository()
original, count = os.fsync, 0
def fail(fd):
    global count
    original(fd)
    count += 1
    if count == cut:
        os._exit(75)
os.fsync = fail
with owner.scope():
    repository.renew(run, request)
"""
                result = subprocess.run([sys.executable, "-c", code, str(root), str(cut)],
                                        capture_output=True, text=True)
                self.assertIn(result.returncode, (0, 75), result.stderr)
                with owner.scope():
                    control = repository.envelope(run, request)
                    self.assertTrue(control["cancelled"])
                    effective, = repository.effective(run, [request])
                    controls = list((root / "state/interactions").glob("*.json"))
                    if control["replacement"] is None:
                        saw_old = True
                        self.assertEqual(effective, request)
                        self.assertEqual(repository._path(run, request).read_bytes(), original)
                        self.assertEqual(len(controls), 1)
                    else:
                        saw_new = True
                        self.assertNotEqual(effective.id, request.id)
                        current = repository.envelope(run, effective)
                        self.assertIsNone(current["response"])
                        self.assertFalse(current["cancelled"])
                        self.assertEqual(len(controls), 2)
                if result.returncode == 0:
                    break
            else:
                self.fail("renewal crash injection did not reach the final fsync")
            self.assertTrue(saw_old)
            self.assertTrue(saw_new)


class InteractionAttemptTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_graph_checkpoints.GraphCheckpointTests.asyncSetUp
    setup_graph = test_graph_checkpoints.GraphCheckpointTests.setup_graph
    run_graph = test_graph_checkpoints.GraphCheckpointTests.run_graph

    async def test_each_uncertain_attempt_requires_its_own_approval_and_preserves_prior_receipts(self):
        effect_path = self.root / "effects.txt"
        async def work(node):
            with effect_path.open("a") as stream:
                stream.write("effect\n")
            if len(effect_path.read_text().splitlines()) < 3:
                raise RuntimeError("effect occurred before failure")
            return {}
        await self.setup_graph(straight(), work)
        first = await self.run_graph()
        original, = await first.ainteractions()
        saved = await first.arespond(original.respond("approve"))
        second = await (await self.session.run.resume(first.id, engine="graph")).wait()
        self.assertEqual(second.data.status, "failed")
        request, = await second.ainteractions()
        self.assertNotEqual(original.id, request.id)
        self.assertEqual(await first.ainteraction_responses(), [saved])
        self.assertEqual(await second.ainteraction_responses(), [])
        self.assertEqual(effect_path.read_text(), "effect\n" * 2)
        with self.assertRaises(ValueError):
            await self.session.run.resume(second.id, engine="graph")
        self.assertEqual(effect_path.read_text(), "effect\n" * 2)
        await second.arespond(request.respond("approve"))
        third = await (await self.session.run.resume(second.id, engine="graph")).wait()
        self.assertEqual(third.data.status, "completed", third.data.error)
        self.assertEqual(effect_path.read_text(), "effect\n" * 3)
        self.assertEqual(await first.ainteraction_responses(), [saved])


class InteractionStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_expiry_cannot_reactivate_stored_request(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            effect_path = root / "effect.txt"

            async def effect(arguments):
                with effect_path.open("a") as stream:
                    stream.write("effect\n")

            async def authorize(invocation):
                raise ToolApprovalRequired(request=replace(
                    approval_request("expired review"),
                    expires_at="2000-01-01T00:00:00+00:00"))

            model = ScriptedCompletion(
                [chunk(calls=[call("{}", name="effect")], finish="tool_calls")],
                [chunk("done", finish="stop")])
            component = RuntimeTools(ToolRegistry((Tool(
                "effect", "effect", {"type": "object"}, effect, contract=TEST_CONTRACT),)))
            async with LargeLanguageModel(
                    root / "workspace", components=[component],
                    engines={"loop": LoopEngine(completion_fn=model)},
                    services=BackendServices(tool_runtime=ToolRuntime(authorize=authorize))) as app:
                project = await app.projects.acreate(components=["tools"], config={
                    "parameters": {
                        "engines": {"loop": {"config": {"completion": {"model": "test"}}}},
                        "components": {"tools": {"config": {"enabled": ["effect"]}}}}})
                session = await project.sessions.acreate()
                paused = await (await session.run.submit("go", engine="loop")).wait()
                self.assertEqual(paused.data.status, "paused", paused.data.error)
                request, = await paused.ainteractions()
                self.assertTrue(request.expired)
                key = request.binding["key"]

                # The same explicit decision is rejected for an intact expired
                # request. A missing field cannot grant a fresh request lifetime.
                with self.assertRaises(ValueError):
                    await session.run.resume(paused.id, engine="loop", decisions={key: True})
                self.assertFalse(effect_path.exists())

                candidates = []
                for path in (paused.data.paths.state / "checkpoints/loop/records").glob("*.json"):
                    record = json.loads(path.read_text())
                    if "interaction" in record["value"]:
                        candidates.append((path, record))
                self.assertEqual(len(candidates), 1)
                path, record = candidates[0]
                del record["value"]["interaction"]["expires_at"]
                path.write_text(json.dumps(record))
                before = path.read_bytes()

                rejected = False
                try:
                    resumed = await session.run.resume(paused.id, engine="loop",
                                                       decisions={key: True})
                except (ValueError, TypeError):
                    rejected = True
                else:
                    # Observe the actual effect before asserting failure. An
                    # unawaited admitted Run could hide this regression.
                    await resumed.wait()

                self.assertEqual({
                    "rejected_before_admission": rejected,
                    "effects": effect_path.read_text() if effect_path.exists() else "",
                    "runs": len(await session.run.alist()),
                    "model_calls": len(model.requests),
                    "corrupt_source_unchanged": path.read_bytes() == before,
                }, {
                    "rejected_before_admission": True,
                    "effects": "",
                    "runs": 1,
                    "model_calls": 1,
                    "corrupt_source_unchanged": True,
                })
