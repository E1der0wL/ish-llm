"""Explicit object types and current storage/usage contracts; no compatibility readers."""

from llm.providers.litellm import LiteLLMCompletionAdapter

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from llm.core.configuration import resolve_config, resolve_engine_config
from llm.core.models import ProjectConfig
from llm.services.runtime.tools import ToolExecutionScope, ToolRuntime, ToolCall
from llm.services.runtime.checkpoints import CheckpointRepository
from llm.services.runtime.operations import OperationRepository, operation_token
from llm.services.infrastructure.backups import DirectoryBackups
from llm.services.infrastructure.transactions import Transaction, TransactionManager
from llm.components.tools.builtin.workspaces import TestWorkspaces
from llm.components.vision.component import VisionComponent
from hub.config.preferences import PreferencesStore
from hub.config.view_state import ViewStateStore, ReadingPosition
from tests.llm.support.runtime_tools import TEST_CONTRACT


class PresenceContracts(unittest.TestCase):
    def test_configuration_objects_reject_wrong_falsey_types(self):
        for value in (False, 0, "", [], ()):
            operations = {
                "project": lambda: ProjectConfig(value),
                "host": lambda: resolve_config([], host=value),
                "session": lambda: resolve_engine_config({}, "loop", session_config=value),
                "agent": lambda: resolve_engine_config({}, "loop", agent=value),
            }
            for name, operation in operations.items():
                with self.subTest(value=value, boundary=name), self.assertRaises((TypeError, ValueError)):
                    operation()

    def test_valid_falsey_leaf_values_and_optional_layers_survive(self):
        explicit = {"zero": 0, "false": False, "null": None, "empty": []}
        prior = {key: "old" for key in explicit}
        view = resolve_config([("project", prior), ("agent", explicit)])
        self.assertEqual(view["values"], explicit)
        self.assertEqual(view["sources"], {"/" + key: "agent" for key in explicit})
        self.assertEqual(resolve_engine_config({}, "loop")["values"], {})
        self.assertEqual(ProjectConfig(None).policies, {})
        self.assertEqual(ProjectConfig({"data": explicit}).data, explicit)

    def test_scope_objects_reject_wrong_falsey_types(self):
        for value in (False, 0, "", [], ()):
            for keyword in ("policy", "retry"):
                with self.subTest(value=value, keyword=keyword), self.assertRaises((TypeError, ValueError)):
                    ToolExecutionScope(ToolRuntime(), **{keyword: value})

    def test_scope_keeps_zero_retry_empty_allowlist_and_nullable_caps(self):
        scope = ToolExecutionScope(ToolRuntime(),
            policy={"allowed_tools": [], "max_calls": None},
            retry={"max_retries": 0, "delay_seconds": 0})
        self.assertEqual(scope.allowed_tools, ())
        self.assertIsNone(scope.max_calls)
        self.assertEqual((scope.max_retries, scope.retry_delay), (0, 0))


class VersionContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def rejects_invalid_versions_without_writing(self, path, key, load):
        original = path.read_bytes()
        record = json.loads(original)
        # Read the value from the owner-produced record; no version selection here.
        self.assertIs(type(record[key]), int)
        load()
        for candidate in (True, False, float(record[key]), str(record[key]), None,
                          record[key] - 1, record[key] + 1):
            mutated = {**record, key: candidate}
            path.write_text(json.dumps(mutated))
            before = path.read_bytes()
            with self.subTest(candidate=candidate), self.assertRaises((TypeError, ValueError)):
                load()
            self.assertEqual(path.read_bytes(), before)
        missing = dict(record)
        del missing[key]
        path.write_text(json.dumps(missing))
        before = path.read_bytes()
        with self.assertRaises((TypeError, ValueError)):
            load()
        self.assertEqual(path.read_bytes(), before)
        path.write_bytes(original)
        load()

    def test_shared_domain_version_guards_preserve_originals_and_reject_invalid_new_writes(self):
        from llm.core.models import DOMAIN_STORAGE_VERSION
        from llm.services.infrastructure.storage import atomic_domain_json, read_domain_record
        path = self.root / 'current.json'
        value = {'storage_version': DOMAIN_STORAGE_VERSION, 'payload': 'preserve'}
        atomic_domain_json(path, value)
        self.rejects_invalid_versions_without_writing(path, 'storage_version', lambda: read_domain_record(path))
        candidates = [{**value, 'storage_version': item} for item in (
            True, False, float(DOMAIN_STORAGE_VERSION), str(DOMAIN_STORAGE_VERSION), None,
            DOMAIN_STORAGE_VERSION - 1, DOMAIN_STORAGE_VERSION + 1)]
        candidates.append({'payload': 'missing version'})
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        for target in (path, self.root / 'new.json'):
            for candidate in candidates:
                with self.subTest(target=target.name, candidate=candidate), self.assertRaises(ValueError):
                    atomic_domain_json(target, candidate)
                self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)

    def test_checkpoint_current_writer_and_reader(self):
        run = SimpleNamespace(id="run", engine="loop", metadata={},
                              paths=SimpleNamespace(state=self.root / "state"))
        repository = CheckpointRepository()
        repository.record(run, SimpleNamespace(metadata={
            "name": "loop", "operation": "initialize", "header": {}, "records": {}}))
        self.rejects_invalid_versions_without_writing(
            self.root / "state/checkpoints/loop/checkpoint.json", "schema_version",
            lambda: repository.load(run, "loop"))

    def test_operation_current_writer_and_reader(self):
        session = SimpleNamespace(id="session", project_id="project",
                                  paths=SimpleNamespace(state=self.root / "state", root=self.root))
        call = ToolCall("effect", {}, "step", "project", "session", "run", "key",
                        operation_token("project", "session", "key"), contract=TEST_CONTRACT.to_dict())
        repository = OperationRepository()
        repository.initialize(session)
        self.assertEqual(repository.claim(session, call), {"reused": False})
        self.rejects_invalid_versions_without_writing(
            repository._path(session, "key"), "schema_version",
            lambda: repository.load(session, "key"))

    def test_workspace_current_writer_and_reader(self):
        source = self.root / "source"
        source.mkdir()
        spaces = TestWorkspaces(self.root / "spaces", source_root=source, allow_network=False)
        call = ToolCall("create", {}, "step", "project", "session", "run", contract=TEST_CONTRACT.to_dict())
        value = spaces.repository.create(call, []).to_dict()
        identifier = value["workspace_id"]
        directory = self.root / "spaces/project" / identifier
        self.rejects_invalid_versions_without_writing(
            directory / "record.json", "storage_version",
            lambda: spaces.repository.record(directory, "project", require_ready=True))

    def test_vision_current_writer_and_reader(self):
        project = SimpleNamespace(id="fixture", paths=SimpleNamespace(root=self.root), config=ProjectConfig(),
                                  initialized_components=())
        component = VisionComponent()
        component.initialize(project)
        project.initialized_components = (component.name,)
        raw = b"fixture-image"
        info = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                "mime_type": "image/png", "width": 1, "height": 1}
        component.import_bytes(project, raw, info, title="fixture", metadata={}, identifier="image")
        self.rejects_invalid_versions_without_writing(
            self.root / "vision/records/image.json", "format_version",
            lambda: component.load(project, "image"))

    def test_preferences_current_writer_and_reader(self):
        store = PreferencesStore(self.root)
        store.save("general", {})
        self.rejects_invalid_versions_without_writing(store.path, "version", store.load)

    def test_view_state_current_writer_and_reader(self):
        store = ViewStateStore(self.root)
        store.save("project", "session", {"session": ReadingPosition("message", 0, 0)})
        self.rejects_invalid_versions_without_writing(store.path, "version",
                                                     lambda: store.load("project"))

    def test_backup_current_writer_and_reader(self):
        source = self.root / "source"
        source.mkdir()
        (source / "payload").write_text("preserve")
        repository = DirectoryBackups()
        backup = repository.create(source, self.root / "backup", metadata={})
        self.rejects_invalid_versions_without_writing(
            backup / "manifest.json", "format_version", lambda: repository.verify(backup))
        self.assertEqual((source / "payload").read_text(), "preserve")

    def test_transaction_current_writer_and_reader(self):
        manager = TransactionManager(self.root)
        transaction = Transaction(manager)
        transaction._entry({"kind": "create", "path": "target"})
        self.rejects_invalid_versions_without_writing(
            transaction.path / "00000000.json", "format_version",
            lambda: manager._entries(transaction.path))


class UsageContracts(unittest.TestCase):
    def test_restore_rejects_incomplete_or_malformed_usage_atomically(self):
        scope = ToolExecutionScope(ToolRuntime(), policy={"max_calls": 1})
        valid = scope.checkpoint()
        invalid = [None, {}, [], False]
        invalid += [{k: v for k, v in valid.items() if k != missing} for missing in valid]
        invalid += [{**valid, key: value}
                    for key in ("calls", "completed", "revision")
                    for value in (True, 1.0, -1, None)]
        invalid += [{**valid, key: value}
                    for key in ("pending_approvals", "invocations")
                    for value in (None, "", [1], [""])]
        invalid += [{**valid, "unrecognized": True}, {**valid, "invocations": ["same", "same"]}]
        for value in invalid:
            with self.subTest(value=value):
                before = deepcopy(vars(scope))
                with self.assertRaises((TypeError, ValueError)):
                    scope.restore([valid, value])
                # Compare state fields, not the copied runtime object identity.
                for key in ("calls", "completed", "pending_approvals", "invocations", "revision"):
                    self.assertEqual(getattr(scope, key), before[key])
                self.assertEqual(scope.max_calls, 1)

    def test_restore_requires_record_list_and_empty_list_does_not_reset_usage(self):
        scope = ToolExecutionScope(ToolRuntime(), policy={"max_calls": 1})
        scope.calls = 1
        for value in (None, False, 0, "", {}, ()):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                scope.restore(value)
        scope.restore([])
        self.assertEqual(scope.calls, 1)
        self.assertEqual(scope.max_calls, 1)

    def test_current_usage_roundtrip_preserves_budget_and_reservations(self):
        original = ToolExecutionScope(ToolRuntime(), policy={"max_calls": 1})
        original.calls = 1
        original.pending_approvals = ["approval"]
        original.invocations = {"invocation"}
        saved = original.checkpoint()
        restored = ToolExecutionScope(ToolRuntime(), policy={"max_calls": 1})
        restored.restore([saved])
        for field in ("calls", "completed", "revision", "pending_approvals", "invocations"):
            self.assertEqual(getattr(restored, field), getattr(original, field))
        self.assertEqual(restored.max_calls, 1)
        self.assertEqual(saved["calls"], 1)


class ResumeUsageContracts(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def store_payload(app, run, value):
        # Keep a valid storage envelope so this tests the Engine's semantic usage
        # guard independently of the checkpoint owner's byte/revision guard.
        from llm.engines.base import EngineEvent, EngineEventType
        with app.project_manager.ownership.scope():
            app.run_repository.record_checkpoint(run, EngineEvent(EngineEventType.CHECKPOINT,
                metadata={"name": "loop", "operation": "record", **value}))

    async def test_approval_reservation_current_authority_and_corrupt_usage_are_distinct(self):
        from llm.components.tools import Tool, ToolRegistry
        from llm.engines.loop import LoopEngine
        from llm.llm import LargeLanguageModel, BackendServices
        from llm.services.runtime.tools import ToolApprovalRequired
        from tests.llm.support.runtime_tools import RuntimeTools
        from tests.llm.test_loop import ScriptedCompletion, chunk, call

        for outcome in ("approve", "host_deny", "corrupt"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                effects = root / "effects.txt"
                mode = ["ask"]
                async def authorize(invocation):
                    if mode[0] == "deny":
                        return False
                    raise ToolApprovalRequired("Review this effect")
                async def effect(arguments):
                    with effects.open("a") as stream:
                        stream.write("effect\n")
                    return None
                model = ScriptedCompletion(
                    [chunk(calls=[call("{}", name="effect")], finish="tool_calls")],
                    [chunk("done", finish="stop")])
                component = RuntimeTools(ToolRegistry((Tool("effect", "effect", {"type": "object"}, effect, contract=TEST_CONTRACT),)))
                engine = LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))
                services = BackendServices(tool_runtime=ToolRuntime(authorize=authorize))
                async with LargeLanguageModel(root / "workspace", components=[component],
                                               engines={"loop": engine}, services=services) as app:
                    project = await app.projects.acreate(components=["tools"], config={
                        "policies": {"tools": {"max_calls": 1}},
                        "parameters": {"engines": {"loop": {"config": {"completion": {"model": "test"}}}},
                                       "components": {"tools": {"config": {"enabled": ["effect"]}}}}})
                    await project.agents.acreate({"engine": "loop", "tools": ["effect"]}, identifier="loop")
                    session = await project.sessions.acreate(agent_ids=sorted(await project.agents.alist()))
                    paused = (await (await session.run.submit("go", recipients=["loop"])).wait())[0]
                    self.assertEqual(paused.data.status, "paused", paused.data.error)
                    self.assertFalse(effects.exists())
                    saved = await paused.acheckpoint()
                    waiting = {key: value for key, value in saved["records"].items() if value["status"] == "waiting"}
                    self.assertEqual(len(waiting), 1)
                    self.assertEqual(next(iter(waiting.values()))["runtime_tool_usage"]["calls"], 1)
                    records = {}
                    for path in (paused.data.paths.state / "checkpoints/loop/records").glob("*.json"):
                        if outcome == "corrupt":
                            value = json.loads(path.read_text())
                            del value["value"]["runtime_tool_usage"]
                            self.store_payload(app, paused.data, value)
                        records[path] = path.read_bytes()
                    project_id, session_id, run_id = project.id, session.id, paused.id
                mode[0] = "deny" if outcome == "host_deny" else "ask"
                async with LargeLanguageModel(root / "workspace", components=[component],
                                               engines={"loop": engine}, services=services) as app:
                    session = (await app.projects.aload(project_id)).sessions.load(session_id)
                    decisions = {key: True for key in waiting}
                    if outcome == "corrupt":
                        plan = await session.run.resume_plan(run_id, agent_id="loop")
                        self.assertEqual(plan.state, "corrupt")
                        self.assertFalse(plan.can_resume)
                        self.assertEqual([block.code for block in plan.blockers], ["tool_usage_corrupt"])
                        with self.assertRaisesRegex(ValueError, "Tool usage"):
                            await session.run.resume(run_id, agent_id="loop", decisions=decisions)
                        self.assertEqual([run.id for run in await session.run.alist()], [run_id])
                    else:
                        resumed = (await (await session.run.resume(run_id, agent_id="loop", decisions=decisions)).wait())[0]
                        if outcome == "approve":
                            self.assertEqual(resumed.data.status, "completed", resumed.data.error)
                            self.assertEqual(effects.read_text(), "effect\n")
                            self.assertEqual(len(model.requests), 2)
                        else:
                            self.assertEqual(resumed.data.status, "failed")
                            self.assertEqual(resumed.result.error_code, "tool_denied")
                    if outcome != "approve":
                        self.assertFalse(effects.exists())
                        self.assertEqual(len(model.requests), 1)
                    for path, before in records.items():
                        self.assertEqual(path.read_bytes(), before)

    async def test_exhausted_budget_allows_result_reuse_but_not_new_tool_effect(self):
        from llm.components.tools import Tool, ToolRegistry
        from llm.engines.loop import LoopEngine
        from llm.llm import LargeLanguageModel
        from tests.llm.support.runtime_tools import RuntimeTools
        from tests.llm.test_loop import ScriptedCompletion, chunk, call

        for request_another_tool in (False, True):
            with self.subTest(request_another_tool=request_another_tool), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                effects = root / "effects.txt"
                async def effect(arguments):
                    with effects.open("a") as stream:
                        stream.write(arguments["value"] + "\n")
                    return {"saved_result": arguments["value"]}
                continuation = ([chunk(calls=[call('{"value":"second"}', name="effect",
                    call_id="second")], finish="tool_calls")] if request_another_tool
                    else [chunk("answer from saved result", finish="stop")])
                model = ScriptedCompletion(
                    [chunk(calls=[call('{"value":"first"}', name="effect", call_id="first")], finish="tool_calls")],
                    [ConnectionError("stop after committed effect")], continuation)
                component = RuntimeTools(ToolRegistry((Tool("effect", "effect", {"type": "object"}, effect, contract=TEST_CONTRACT),)))
                engine = LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))
                async with LargeLanguageModel(root / "workspace", components=[component], engines={"loop": engine}) as app:
                    project = await app.projects.acreate(components=["tools"], config={
                        "policies": {"tools": {"max_calls": 1}},
                        "parameters": {"engines": {"loop": {"config": {"completion": {"model": "test"}}}},
                                       "components": {"tools": {"config": {"enabled": ["effect"]}}}}})
                    await project.agents.acreate({"engine": "loop", "tools": ["effect"]}, identifier="loop")
                    session = await project.sessions.acreate(agent_ids=sorted(await project.agents.alist()))
                    failed = (await (await session.run.submit("go", recipients=["loop"])).wait())[0]
                    self.assertEqual(failed.data.status, "failed")
                    self.assertEqual(effects.read_text(), "first\n")
                    project_id, session_id, run_id = project.id, session.id, failed.id
                async with LargeLanguageModel(root / "workspace", components=[component], engines={"loop": engine}) as app:
                    session = (await app.projects.aload(project_id)).sessions.load(session_id)
                    plan = await session.run.resume_plan(run_id, agent_id="loop")
                    self.assertTrue(plan.can_resume, plan.blockers)
                    self.assertEqual(plan.state, "valid")
                    resumed = (await (await session.run.resume(run_id, agent_id="loop")).wait())[0]
                    self.assertEqual(effects.read_text(), "first\n")
                    self.assertEqual(len(model.requests), 3)
                    if request_another_tool:
                        self.assertEqual(resumed.data.status, "failed")
                        self.assertEqual(resumed.result.error_code, "tool_budget_exceeded")
                    else:
                        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
                        self.assertEqual(resumed.response.content, "answer from saved result")

    async def test_missing_usage_counter_rejects_reopened_resume_before_new_effect(self):
        await self._assert_missing_usage_rejected(remove_envelope=False)

    async def test_missing_usage_envelope_rejects_reopened_resume_before_new_effect(self):
        await self._assert_missing_usage_rejected(remove_envelope=True)

    async def _assert_missing_usage_rejected(self, *, remove_envelope):
        from llm.components.tools import Tool, ToolRegistry
        from llm.engines.loop import LoopEngine
        from llm.llm import LargeLanguageModel
        from tests.llm.support.runtime_tools import RuntimeTools
        from tests.llm.test_loop import ScriptedCompletion, chunk, call

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effects = root / "effects.txt"

            async def effect(arguments):
                with effects.open("a") as stream:
                    stream.write(arguments["value"] + "\n")
                return arguments

            model = ScriptedCompletion(
                [chunk(calls=[call('{"value":"first"}', name="effect", call_id="first")], finish="tool_calls")],
                [ConnectionError("stop after persisted effect")],
                [chunk(calls=[call('{"value":"second"}', name="effect", call_id="second")], finish="tool_calls")],
                [chunk("done", finish="stop")])
            component = RuntimeTools(ToolRegistry((Tool("effect", "effect", {"type": "object"}, effect, contract=TEST_CONTRACT),)))
            engine = LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))
            async with LargeLanguageModel(root / "workspace", components=[component], engines={"loop": engine}) as app:
                project = await app.projects.acreate(components=["tools"], config={
                    "policies": {"tools": {"max_calls": 1}},
                    "parameters": {"engines": {"loop": {"config": {"completion": {"model": "test"}}}},
                                   "components": {"tools": {"config": {"enabled": ["effect"]}}}}})
                await project.agents.acreate({"engine": "loop", "tools": ["effect"]}, identifier="loop")
                session = await project.sessions.acreate(agent_ids=sorted(await project.agents.alist()))
                failed = (await (await session.run.submit("go", recipients=["loop"])).wait())[0]
                self.assertEqual(failed.data.status, "failed", failed.data.error)
                self.assertEqual(effects.read_text(), "first\n")
                self.assertEqual(len(model.requests), 2)
                changed = {}
                for path in (failed.data.paths.state / "checkpoints/loop/records").glob("*.json"):
                    value = json.loads(path.read_text())
                    if "runtime_tool_usage" in value["value"]:
                        if remove_envelope:
                            del value["value"]["runtime_tool_usage"]
                        else:
                            del value["value"]["runtime_tool_usage"]["calls"]
                        self.store_payload(app, failed.data, value)
                        changed[path] = path.read_bytes()
                self.assertTrue(changed, "Test must reach a persisted usage record")
                project_id, session_id, run_id = project.id, session.id, failed.id
            async with LargeLanguageModel(root / "workspace", components=[component], engines={"loop": engine}) as app:
                session = (await app.projects.aload(project_id)).sessions.load(session_id)
                plan = await session.run.resume_plan(run_id, agent_id="loop")
                self.assertFalse(plan.can_resume)
                self.assertEqual(plan.state, "corrupt")
                self.assertEqual([block.code for block in plan.blockers], ["tool_usage_corrupt"])
                if remove_envelope:
                    rejected = False
                    try:
                        await (await session.run.resume(run_id, agent_id="loop")).wait()
                    except ValueError as error:
                        self.assertRegex(str(error), "Tool usage")
                        rejected = True
                    # Reach the real effect assertion even if admission incorrectly succeeds.
                    self.assertEqual(effects.read_text(), "first\n",
                                     "missing usage envelope reset the resumed Tool budget")
                    self.assertTrue(rejected, "Malformed checkpoint must be rejected before admission")
                else:
                    with self.assertRaisesRegex(ValueError, "Tool usage requires"):
                        await session.run.resume(run_id, agent_id="loop")
                self.assertEqual(effects.read_text(), "first\n")
                self.assertEqual(len(model.requests), 2)
                self.assertEqual([run.id for run in await session.run.alist()], [run_id])
                for path, before in changed.items():
                    self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
