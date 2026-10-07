"""Project policy와 Host mechanism, scheme-scoped 분류의 경계."""
import asyncio
import unittest
from llm.core.interactions import approval_request
from llm.core.models import ProjectConfig
from llm.engines.loop import LoopEngine
from llm.components.tools import ToolClassification
from llm.services.runtime.tools import ToolPolicy, ToolApprovalRequired
from tests.llm import test_long_running as long_running
from tests.llm.test_loop import call, chunk


class OwnershipTests(unittest.TestCase):
    def test_project_policy_cannot_be_replaced_by_host_constructor(self):
        for kwargs in ({"max_iterations": 4}, {"system_prompt": None}, {"request_timeout": 1}):
            with self.assertRaises(TypeError):
                LoopEngine(**kwargs)
        for kwargs in ({"max_calls": 1}, {"allowed_tools": ()}, {"max_retries": 1}, {"auto_approve_categories": ()}):
            with self.assertRaises(TypeError):
                ToolPolicy(**kwargs)

    def test_session_narrowing_and_prompt_null(self):
        engine = LoopEngine()
        project = {"parameters": {"engines": {"loop": {
            "config": {"system_prompt": "project"}, "policy": {"request_timeout": 300}}}}}
        session = {"parameters": {"engines": {"loop": {
            "config": {"system_prompt": None}, "policy": {"request_timeout": 60}}}}}
        view = engine.configuration(project, "loop", session_config=session)
        self.assertEqual(view["sources"]["/policy/request_timeout"], "session")
        self.assertIsNone(view["values"]["config"]["system_prompt"])
        for value in (301, None):
            session["parameters"]["engines"]["loop"]["policy"]["request_timeout"] = value
            with self.assertRaisesRegex(ValueError, "widens"):
                engine.configuration(project, "loop", session_config=session)

    def test_numeric_risk_is_exact_nonnegative_integer(self):
        for risk in (0, 42, 999999):
            self.assertEqual(approval_request("review", risk_scheme="app", risk=risk).risk, risk)
        self.assertIsNone(approval_request("review").risk)
        for risk in (True, -1, 1.5, "low", "42"):
            with self.assertRaises((ValueError, TypeError)):
                approval_request("review", risk_scheme="app", risk=risk)
        with self.assertRaises(ValueError):
            approval_request("review", risk=1)


class ApprovalAuthorityTests(unittest.IsolatedAsyncioTestCase):
    setup_app = long_running.LongRunningTests.setup_app

    async def test_async_classifier_and_cancellation_before_effects(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                started, stopped, effects = asyncio.Event(), asyncio.Event(), []
                async def act(args):
                    effects.append(args)
                async def classify(call):
                    started.set()
                    try:
                        if interrupted:
                            await asyncio.Event().wait()
                        return ToolClassification("read", "app", 10)
                    finally:
                        stopped.set()
                async def ask(call):
                    raise ToolApprovalRequired("review")
                _, session, _ = await self.setup_app(act, [
                    [chunk(calls=[call("{}", name="act")], finish="tool_calls")]],
                    ToolPolicy(authorize=ask, classify=classify))
                handle = await session.run.submit("go", engine="loop")
                await asyncio.wait_for(started.wait(), 10)
                if interrupted:
                    await session.run.interrupt()
                run = await handle.wait(timeout=10)
                self.assertEqual(run.data.status, "interrupted" if interrupted else "paused")
                self.assertTrue(stopped.is_set())
                self.assertEqual(effects, [])
                if not interrupted:
                    request, = await run.ainteractions(pending_only=True)
                    self.assertEqual((request.risk_scheme, request.risk), ("app", 10))

    async def test_same_backend_uses_each_projects_approval_rules(self):
        effects = []
        async def act(args):
            effects.append(args)
        async def ask(call):
            raise ToolApprovalRequired("review")
        app, first, model = await self.setup_app(act, [
            [chunk(calls=[call("{}", name="act")], finish="tool_calls")],
            [chunk(calls=[call("{}", name="act")], finish="tool_calls")]],
            ToolPolicy(authorize=ask, classify=lambda call: ToolClassification("read", "app", 10)),
            policies={"approval": {"enabled": True, "risk_scheme": "app", "rules": [
                {"id": "read", "category": "read", "max_risk": 10}]}})
        project = await app.projects.acreate(components=["tools"], config={"parameters": {
            "engines": {"loop": {"config": {"completion": {"model": "test/model"}}}}}})
        await project.components.tools.aenable("act")
        second = await project.sessions.acreate()
        a = await (await first.run.submit("go", engine="loop")).wait()
        b = await (await second.run.submit("go", engine="loop")).wait()
        self.assertEqual((a.data.status, b.data.status), ("paused", "paused"))
        self.assertEqual([r.actor for r in await a.ainteraction_responses()], ["policy"])
        self.assertEqual(await b.ainteraction_responses(), [])
        self.assertEqual(effects, [])

    async def test_saved_approval_cannot_override_changed_classification_or_technical_denial(self):
        for change in ("classification", "deny"):
            with self.subTest(change=change):
                state, effects = {"risk": 20, "deny": False}, []
                async def act(args):
                    effects.append(args)
                async def authorize(call):
                    if state["deny"]:
                        return False
                    raise ToolApprovalRequired("review")
                app, session, _ = await self.setup_app(act, [
                    [chunk(calls=[call("{}", name="act")], finish="tool_calls")],
                    [chunk("done", finish="stop")]], ToolPolicy(authorize=authorize,
                        classify=lambda call: ToolClassification("execute", "app", state["risk"])))
                run = await (await session.run.submit("go", engine="loop")).wait()
                request, = await run.ainteractions(pending_only=True)
                await run.arespond(request.respond("approve"))
                state["risk" if change == "classification" else "deny"] = 50 if change == "classification" else True
                resumed = await (await session.run.resume(run.id, engine="loop")).wait()
                self.assertEqual(resumed.data.status, "failed")
                self.assertEqual(effects, [])
                if change == "deny":
                    self.assertEqual(resumed.data.error_code, "tool_denied")
                else:
                    self.assertIn("classification changed", resumed.data.error)

    async def test_scheme_risk_and_final_arguments_control_project_autoapproval(self):
        for scheme, risk, expected in (("app", 20, 1), ("app", 40, 1), ("app", 41, 0), ("other", 1, 0), ("app", None, 0)):
            with self.subTest(scheme=scheme, risk=risk):
                effects, seen = [], []
                async def act(args):
                    effects.append(args)
                async def ask(call):
                    raise ToolApprovalRequired("review")
                def classify(call):
                    seen.append(call.arguments)
                    return ToolClassification("file.read", scheme, risk)
                app, session, _ = await self.setup_app(act, [
                    [chunk(calls=[call('{"risk":0}', name="act")], finish="tool_calls")],
                    [chunk("done", finish="stop")]], ToolPolicy(authorize=ask, classify=classify), policies={
                        "approval": {"enabled": True, "risk_scheme": "app", "rules": [
                            {"id": "read", "category": "file.read", "max_risk": 40}]},
                        "tools": {"argument_constraints": {"act": {"command": {"mode": "fixed", "value": "x"}}}}},
                    tool_schema={"type": "object", "properties": {"risk": {"type": "integer"}, "command": {"type": "string"}}})
                run = await (await session.run.submit("go", engine="loop")).wait()
                self.assertEqual(run.data.status, "paused", run.data.error)
                self.assertEqual(effects, [])
                self.assertEqual(seen, [{"risk": 0, "command": "x"}])
                responses = await run.ainteraction_responses()
                self.assertEqual(len(responses), expected)
                if expected:
                    self.assertEqual(responses[0].actor, "policy")
                    resumed = await (await session.run.resume(run.id, engine="loop")).wait()
                    self.assertEqual(resumed.data.status, "completed", resumed.data.error)
                    self.assertEqual(len(effects), 1)
