"""Backend handoff: diagnostics, protected recovery inputs and owner resume plans."""

from llm.core.model_protocol import ModelMessage, ModelRequest, StopReason, ToolCall, ToolResult

from llm.providers.litellm import LiteLLMCompletionAdapter

import tempfile
import unittest
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from llm.errors import CodedError
from llm.providers.requests import ProviderError, error_code
from llm.providers.runtime import diagnostic_failure, diagnostic_scope, logging_scope, ProviderDiagnostics
from llm.components.processing import CompletionPipeline, CompletionRepair, CompletionMessage, CompletionRequest
from llm.engines.loop import LoopEngine
from llm.engines.base import BaseEngine
from llm.components.tools import Tool, ToolRegistry
from llm.llm import LargeLanguageModel, Diagnostic, ErrorClassification, classify_diagnostic
from llm.core.models import RunStatus
from llm.core.checkpoints import CheckpointError
from llm.core.plans import ResumePlan, JobResumePlan
from llm.services.runtime.runs import RunManager
from llm.services.runtime.tools import ToolExecutionError
from llm.providers.mcp import MCPToolError
from llm.components.rag.jobs import RAGJobs
from llm.components.workflows import WorkflowComponent
from tests.llm import test_conversation_turns as turns, test_rag_components as rag_tests
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from tests.llm.test_error_boundaries import UntrustedError
from tests.llm.support.runtime_tools import TEST_CONTRACT


class DiagnosticContracts(unittest.TestCase):
    def test_stored_and_computed_diagnostics_share_the_contract(self):
        class CustomError(CodedError, RuntimeError):
            code = "custom_failure"

            @property
            def diagnostic(self):
                return supplied

        supplied = Diagnostic("custom_failure", "owner message", severity="warning",
                              details={"owner": {"fact": [1, 2]}})
        stored = RuntimeError("stored")
        stored.diagnostic = supplied
        for error in (stored, CustomError("computed")):
            with self.subTest(error=str(error)):
                wrapper = RuntimeError("wrapper")
                wrapper.__cause__ = error
                value = Diagnostic.from_exception(wrapper)
                self.assertEqual(value, supplied)
                self.assertEqual(BaseEngine.step_failed_event("step", wrapper).diagnostic.details, supplied.details)
                value.details["owner"]["fact"].clear()
                self.assertEqual(supplied.details["owner"]["fact"], [1, 2])

    def test_recursive_or_broken_properties_do_not_replace_original_failure(self):
        class Recursive(CodedError, RuntimeError):
            code = "custom_failure"
            reads = 0

            @property
            def diagnostic(self):
                self.reads += 1
                return Diagnostic.from_exception(self)

        class Broken(Recursive):
            @property
            def diagnostic(self):
                raise ValueError("observer failed")

        class Invalid(Recursive):
            @property
            def diagnostic(self):
                return {"code": "not a Diagnostic"}

        for error in (Recursive("original"), Broken("original"), Invalid("original")):
            for _ in range(2):
                value = Diagnostic.from_exception(error)
                self.assertEqual((value.code, value.message), ("custom_failure", "original"))
                self.assertIn(type(error).__qualname__, value.details["exceptions"][0]["type"])
            if type(error) is Recursive:
                self.assertEqual(error.reads, 2)  # Guard is bounded and resets after each conversion.
            event = BaseEngine.step_failed_event("step", error)
            self.assertEqual(event.diagnostic.message, "original")
            self.assertEqual(event.failure_code, "custom_failure")

    def test_computed_inner_diagnostic_cannot_override_trusted_outer_code(self):
        class Denied(CodedError, RuntimeError):
            code = "tool_denied"
        outer = Denied("denied")
        outer.__cause__ = ToolExecutionError("inner", effect="none", retryable=True, recoverable=True)
        value = Diagnostic.from_exception(outer)
        self.assertEqual(value.code, "tool_denied")
        self.assertNotIn("effect", value.details)
        self.assertEqual(BaseEngine.step_failed_event("step", outer).diagnostic.code, "tool_denied")

    def test_recursive_property_chain_does_not_multiply_evaluation(self):
        class Recursive(CodedError, RuntimeError):
            reads = 0

            @property
            def diagnostic(self):
                self.reads += 1
                return Diagnostic.from_exception(self)

        errors = [Recursive("original") for _ in range(10)]
        for index, error in enumerate(errors):
            error.code = "owner_" + str(index)
            if index + 1 < len(errors):
                error.__cause__ = errors[index + 1]
        value = Diagnostic.from_exception(errors[0])
        self.assertEqual(value.code, "owner_0")
        self.assertEqual(len(value.details["exceptions"]), len(errors))
        self.assertEqual([error.reads for error in errors], [1] + [0] * 9)

    def test_projection_roundtrip_and_unknown_code(self):
        value = Diagnostic("custom_failure", "original", details={"exceptions": [{"status_code": 503}]})
        self.assertEqual(value.classification.category, "unknown")
        copy = Diagnostic.from_dict(value.to_dict())
        copy.details["exceptions"].clear()
        self.assertEqual(len(value.details["exceptions"]), 1)
        self.assertEqual(Diagnostic("notice", "fine", severity="info").classification.category, "unknown")
        limited = classify_diagnostic(Diagnostic("completion_incomplete", "limit", details={"raw_finish_reason": "length", "termination": StopReason.OUTPUT_LIMIT}))
        self.assertEqual((limited.category, limited.reason), ("resource", "output"))
        self.assertEqual(ErrorClassification.from_dict(limited.to_dict()), limited)

    def test_provider_cause_local_hints_and_opt_in_details(self):
        class HTTPError(Exception):
            status_code = 400
        with tempfile.TemporaryDirectory() as root, logging_scope(root):
            error = HTTPError("maximum context length exceeded")
            value = diagnostic_failure(error, operation="completion", stage="invoke")
            self.assertEqual(value.code, "provider_invalid_request")
            self.assertEqual(value.classification.category, "resource")
            self.assertEqual(value.classification.confidence, "inferred")
            self.assertNotIn("provider_error", value.details["exceptions"][0])
            parameter = diagnostic_failure(HTTPError("unsupported parameter: context window"),
                                           operation="completion", stage="invoke")
            self.assertEqual(parameter.classification.reason, "parameter")
            self.assertEqual(diagnostic_failure(HTTPError("context length must be an integer"),
                operation="completion", stage="invoke").classification.reason, "request")
            # Outer HTTP cause must not borrow text from another exception.
            outer = HTTPError("")
            outer.__cause__ = ValueError("maximum context length exceeded")
            value = diagnostic_failure(outer, operation="completion", stage="invoke")
            self.assertEqual(value.classification.reason, "request")
            outer.__cause__ = HTTPError("maximum context length exceeded")
            value = diagnostic_failure(outer, operation="completion", stage="invoke")
            self.assertEqual(value.classification.reason, "request")
            self.assertNotIn("request_failure", value.details)
            outer.__cause__ = outer
            self.assertEqual(len(diagnostic_failure(outer, operation="completion", stage="invoke").details["exceptions"]), 1)
        with tempfile.TemporaryDirectory() as root, logging_scope(root, diagnostics=ProviderDiagnostics(error_details=True)):
            value = diagnostic_failure(error, operation="completion", stage="invoke")
            self.assertEqual(value.details["exceptions"][0]["provider_error"]["message"], str(error))
            # A Tool failure does not acquire Provider diagnostics/options.
            tool = Diagnostic.from_exception(ValueError("tool"), boundary="tool")
            self.assertNotIn("provider_error", tool.details["exceptions"][0])

    def test_explicit_outer_code_wins_and_supplied_diagnostic_survives(self):
        class Denied(CodedError, ValueError):
            code = "tool_denied"
        outer = Denied("denied")
        outer.__cause__ = ProviderError("provider_unavailable")
        self.assertEqual(error_code(outer), "tool_denied")
        self.assertEqual(Diagnostic.from_exception(outer).classification.category, "access")
        source = Diagnostic("provider_rate_limit", "rate", details={"call_id": "call", "attempt": 2})
        inner = ProviderError(source.code, diagnostic=source)
        wrapper = RuntimeError("wrapper")
        wrapper.__cause__ = inner
        self.assertEqual(Diagnostic.from_exception(wrapper), source)

    def test_observers_cannot_rewrite_recovery_evidence_or_other_observers(self):
        observed = []
        def mutate(event):
            event.details["request_failure"] = {"reason": "context"}
            raise RuntimeError("observer failed")
        error = RuntimeError("invalid parameter")
        error.status_code = 400
        with tempfile.TemporaryDirectory() as root, logging_scope(root), \
                diagnostic_scope(observed.append), diagnostic_scope(mutate):
            value = diagnostic_failure(error, operation="completion", stage="invoke")
        self.assertEqual(value.classification.reason, "request")
        self.assertEqual(observed[-1].classification.reason, "request")

    def test_status_and_synthetic_500(self):
        for status, code in ((401, "provider_authentication"), (403, "provider_authentication"),
                             (429, "provider_rate_limit"), (503, "provider_unavailable"),
                             (500, "provider_unavailable")):
            error = RuntimeError("server")
            error.status_code = status
            self.assertEqual(error_code(error), code)
        error = RuntimeError("Empty or invalid response from LLM endpoint")
        error.status_code = 500
        self.assertEqual(error_code(error), "provider_invalid_response")


class CompletionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_protected_instructions_parameters_and_reference_bodies(self):
        request = ModelRequest([ModelMessage("system", "instructions"),
            ModelMessage("user", "old" * 100), ModelMessage("assistant", "old answer"),
            ModelMessage("user", "fix A; keep API"),
            ModelMessage("assistant", calls=(ToolCall("a", "read", {}),)),
            ModelMessage("tool", result=ToolResult("a", "log" * 100)),
            ModelMessage("user", "also keep tests")], {"model": "test"})
        original = deepcopy(request)
        diagnostic = Diagnostic("provider_invalid_request", "context")
        result = await CompletionPipeline.recover(request, diagnostic, 1, current_index=3,
            decide=lambda failure: CompletionRepair({1: "old summary", 5: "log summary"}))
        self.assertEqual(result.messages[3:5], request.messages[3:5])
        self.assertEqual(result.messages[6], request.messages[6])
        self.assertEqual(request, original)
        for index in (0, 3, 4, 6, -1):
            with self.subTest(index=index), self.assertRaises(ValueError):
                await CompletionPipeline.recover(request, diagnostic, 1, current_index=3,
                    decide=lambda failure: CompletionRepair({index: "replace"}))
        async def invalid_budget(candidate):
            candidate.messages = candidate.messages[:1]
            return candidate
        with self.assertRaisesRegex(ValueError, "current input"):
            await CompletionPipeline.recover(request, diagnostic, 1, current_index=3,
                decide=lambda failure: CompletionRepair({1: "short"}), prepare=invalid_budget)
        original = [CompletionMessage(ModelMessage("user", "request"), "id")]
        with self.assertRaisesRegex(ValueError, "instruction body"):
            CompletionPipeline._validate(CompletionRequest({}, [CompletionMessage(
                ModelMessage("user", ""), "id")], 1), original, ModelRequest(), "id")

    async def execute(self, responses, decide, *, provider=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        effects = []
        async def read(arguments):
            effects.append("read")
            return "original log " * 100
        tools = RuntimeTools(ToolRegistry([Tool("read", "Read", {"type": "object", "properties": {},
            "additionalProperties": False}, read, contract=TEST_CONTRACT)]))
        model = ScriptedCompletion(*responses)
        backend = LargeLanguageModel(temporary.name, engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model),
            recover_completion=decide)}, components=[tools])
        self.addAsyncCleanup(backend.shutdown)
        config = {"parameters": {"engines": {"loop": {"policy": {"provider": provider or {}}}},
            "components": {"tools": {"config": {"enabled": ["read"]}}}}}
        project = await backend.projects.acreate(config=config, components=["tools"])
        await project.agents.acreate({"completion": {"model": "test"}, "engine": "loop", "system_prompt": "keep instructions", "tools": ["read"]}, identifier="loop")
        session = await project.sessions.acreate(agent_ids=sorted(await project.agents.alist()))
        handle = (await (await session.run.submit("fix A; keep API", recipients=["loop"])).wait(timeout=10))[0]
        return await handle.aresult(), await handle.steps.alist(), model, effects, session

    @staticmethod
    def context_error():
        error = RuntimeError("maximum context length exceeded")
        error.status_code = 400
        return error

    async def test_current_completion_only_tool_not_replayed(self):
        seen = []
        def decide(failure):
            seen.append(failure)
            return CompletionRepair({failure.editable[-1]: "summary of log"})
        result, steps, model, effects, session = await self.execute([
            [chunk(calls=[call("{}", name="read")], finish="tool_calls")],
            [self.context_error()], [chunk("done", finish="stop")]], decide,
            provider={"max_attempts": 2})
        self.assertEqual(result.status, RunStatus.COMPLETED, result.error)
        self.assertEqual(effects, ["read"])
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(model.requests), 3)
        self.assertEqual(model.requests[-1]["messages"][0], model.requests[-2]["messages"][0])
        self.assertEqual(model.requests[-1]["messages"][1]["content"], "fix A; keep API")
        self.assertEqual(model.requests[-1]["messages"][-1]["content"], "summary of log")
        self.assertTrue(any("original log" in str(step.output.data) for step in steps if step.kind == "tool"))
        self.assertEqual((await session.aconversation())[0].content, "fix A; keep API")

    async def test_no_policy_no_repair_and_repair_does_not_renew_attempts(self):
        for provider, expected in ((None, 2), ({"max_attempts": 2}, 3)):
            calls = []
            def decide(failure):
                calls.append(failure.attempt)
                return CompletionRepair({failure.editable[-1]: "short"})
            result, _, model, effects, _ = await self.execute([
                [chunk(calls=[call("{}", name="read")], finish="tool_calls")],
                [self.context_error()], [self.context_error()]], decide, provider=provider)
            self.assertEqual(result.error_code, "provider_invalid_request")
            self.assertEqual(result.diagnostic.classification.reason, "context")
            self.assertEqual(len(model.requests), expected)
            self.assertEqual(len(calls), expected - 2)
            self.assertEqual(effects, ["read"])

    async def test_partial_response_does_not_repair(self):
        calls = []
        result, _, model, _, _ = await self.execute([[chunk("partial"), self.context_error()]],
            lambda failure: calls.append(failure), provider={"max_attempts": 3})
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(calls, [])
        self.assertEqual(len(model.requests), 1)

    async def test_callback_cannot_renew_provider_deadline(self):
        entered = []
        async def decide(failure):
            entered.append(True)
            await asyncio.Event().wait()
        result, _, model, effects, _ = await self.execute([
            [chunk(calls=[call("{}", name="read")], finish="tool_calls")], [self.context_error()]],
            decide, provider={"max_attempts": 3, "wall_timeout": 1.0})
        self.assertEqual(entered, [True])
        self.assertEqual(result.error_code, "provider_invalid_request")
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(effects, ["read"])

    async def test_invalid_or_failed_recovery_keeps_original_failure(self):
        def failed(failure):
            raise ValueError("summary failed")
        for decide in (failed, lambda failure: CompletionRepair({1: "replace current instruction"})):
            result, _, model, effects, _ = await self.execute([
                [chunk(calls=[call("{}", name="read")], finish="tool_calls")], [self.context_error()]],
                decide, provider={"max_attempts": 2})
            self.assertEqual(result.error_code, "provider_invalid_request")
            self.assertEqual(result.diagnostic.classification.reason, "context")
            self.assertEqual(len(model.requests), 2)
            self.assertEqual(effects, ["read"])

    async def test_recovery_cancellation_stops_without_replay(self):
        async def decide(failure):
            raise asyncio.CancelledError()
        result, _, model, effects, _ = await self.execute([
            [chunk(calls=[call("{}", name="read")], finish="tool_calls")], [self.context_error()]],
            decide, provider={"max_attempts": 2})
        self.assertEqual(result.status, RunStatus.INTERRUPTED)
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(effects, ["read"])


class DiagnosticRuntimeContracts(unittest.IsolatedAsyncioTestCase):
    async def test_tool_step_run_and_reopened_result_preserve_owner_diagnostic(self):
        for failure in (ToolExecutionError("missing", effect="none", retryable=True, recoverable=True),
                        MCPToolError("remote failure", effect="uncertain")):
            with self.subTest(code=failure.code), tempfile.TemporaryDirectory() as root:
                calls = []
                async def invoke(arguments):
                    calls.append(arguments)
                    raise failure
                tools = RuntimeTools(ToolRegistry([Tool("read", "Read", {"type": "object",
                    "properties": {}, "additionalProperties": False}, invoke, contract=TEST_CONTRACT)]))
                model = ScriptedCompletion([chunk(calls=[call("{}", name="read")], finish="tool_calls")])
                backend = LargeLanguageModel(root, engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))}, components=[tools])
                try:
                    project = await backend.projects.acreate(components=["tools"], config={"parameters": {
                        "engines": {"loop": {"config": {}}},
                        "components": {"tools": {"config": {"enabled": ["read"]}}}}})
                    await project.agents.acreate({"completion": {"model": "test"}, "engine": "loop", "system_prompt": "keep instructions", "tools": ["read"]}, identifier="loop")
                    session = await project.sessions.acreate(agent_ids=sorted(await project.agents.alist()))
                    run = (await (await session.run.submit("read", recipients=["loop"])).wait(timeout=10))[0]
                    result = await run.aresult()
                    step, = [item for item in await run.steps.alist() if item.kind == "tool"]
                    self.assertEqual(result.status, RunStatus.FAILED)
                    self.assertEqual(result.error_code, failure.code)
                    self.assertEqual(result.diagnostic.details, failure.diagnostic.details)
                    self.assertEqual(step.diagnostic.details, result.diagnostic.details)
                    self.assertEqual(calls, [{}])  # Diagnostic is not permission to retry.
                finally:
                    await backend.shutdown()
                reopened = LargeLanguageModel(root, engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))}, components=[tools])
                try:
                    loaded = await (await reopened.projects.aload(project.id)).sessions.aload(session.id)
                    stored = await (await loaded.run.aload(run.id)).aresult()
                    self.assertEqual(stored.diagnostic, result.diagnostic)
                    self.assertEqual(calls, [{}])
                finally:
                    await reopened.shutdown()

    async def test_custom_computed_diagnostic_does_not_grant_error_code_authority(self):
        class CustomError(RuntimeError):
            @property
            def diagnostic(self):
                return Diagnostic("custom_failure", "owner message", details={"component": {"fact": True}})

        async def fail(context):
            raise CustomError("original")
            yield

        with tempfile.TemporaryDirectory() as root:
            backend = LargeLanguageModel(root, engines={"custom": BaseEngine(action=fail)})
            try:
                project = await backend.projects.acreate(components=[])
                await project.agents.acreate({"engine": "custom"}, identifier="custom")
                session = await project.sessions.acreate(agent_ids=sorted(await project.agents.alist()))
                run = (await (await session.run.submit("run", recipients=["custom"])).wait(timeout=10))[0]
                result = await run.aresult()
                self.assertEqual(result.error_code, "engine_failed")
                self.assertEqual(result.diagnostic.details["observed_code"], "custom_failure")
                self.assertEqual(result.diagnostic.details["component"], {"fact": True})
            finally:
                await backend.shutdown()


class ResumeContracts(unittest.IsolatedAsyncioTestCase):
    setup_graph = turns.TurnResumeTests.setup_graph

    async def test_plan_observes_approval_and_cas_without_granting_it(self):
        run = await self.setup_graph()
        plan = await self.session.run.resume_plan(run.id, agent_id="graph")
        self.assertFalse(plan.can_resume)
        self.assertFalse(plan.can_restart)
        self.assertEqual(plan.state, "valid")
        self.assertEqual(ResumePlan.from_dict(plan.to_dict()), plan)
        request, = await run.ainteractions()
        await run.arespond(request.respond(request.options[0].id))
        with self.assertRaises(CheckpointError) as rejected:
            await self.session.run.resume(run.id, agent_id="graph", expected_revision=plan.revision)
        self.assertEqual(rejected.exception.code, "checkpoint_conflict")
        with patch.object(self.app.run_repository, "list", wraps=self.app.run_repository.list) as listed, \
                patch.object(self.app.run_repository, "checkpoint", wraps=self.app.run_repository.checkpoint) as checkpoints:
            plan = await self.session.run.resume_plan(run.id, agent_id="graph")
            self.assertEqual(listed.call_count, 1)
            self.assertEqual(checkpoints.call_count, 1)
        self.assertTrue(plan.can_resume, plan.blockers)
        resumed = (await (await self.session.run.resume(run.id, agent_id="graph",
            expected_revision=plan.revision)).wait(timeout=10))[0]
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual(self.effects, ["first", "second"])

    async def test_corrupt_missing_and_deleted_are_not_fresh_start(self):
        run = await self.setup_graph()
        path = run.data.paths.state / "checkpoints" / "graph" / "checkpoint.json"
        content = path.read_bytes()
        path.write_text("{broken", encoding="utf-8")
        plan = await self.session.run.resume_plan(run.id, agent_id="graph")
        self.assertEqual(plan.state, "corrupt")
        self.assertFalse(plan.can_restart)

        path.unlink()
        plan = await self.session.run.resume_plan(run.id, agent_id="graph")
        self.assertEqual(plan.state, "missing")
        self.assertFalse(plan.can_resume)
        path.write_bytes(content)
        await self.session.run.shutdown()
        await run.aabandon_resume(expected_revision=(await run.acheckpoint_plan()).revision)
        await run.adelete_checkpoints(expected_revision=(await run.acheckpoint_plan()).revision)
        # A separate backend reads the same persisted proof; no source replay.
        app = LargeLanguageModel(self.root, components=[WorkflowComponent()], engines={"graph": self.engine})
        self.addAsyncCleanup(app.shutdown)
        session = (await app.projects.aload(self.project.id)).sessions.load(self.session.id)
        plan = await session.run.resume_plan(run.id, agent_id="graph")
        self.assertEqual(plan.state, "deleted")
        self.assertFalse(plan.can_restart)
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        with self.assertRaises(CheckpointError) as corrupt:
            await session.run.resume_plan(run.id, agent_id="graph")
        self.assertEqual(corrupt.exception.code, "checkpoint_corrupt")

    async def test_custom_engine_without_steps_never_proves_no_effects(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        class CustomEngine(BaseEngine):
            async def execute(self, context):
                raise UntrustedError("original")
                yield
        app = LargeLanguageModel(temporary.name, engines={"custom": CustomEngine()})
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate(components=[])
        await project.agents.acreate({"engine": "custom"}, identifier="custom")
        session = await project.sessions.acreate(agent_ids=sorted(await project.agents.alist()))
        run = (await (await session.run.submit("work", recipients=["custom"])).wait(timeout=10))[0]
        self.assertEqual(await run.steps.alist(), [])
        self.assertTrue(run.data.metadata["execution_started"])
        plan = await session.run.resume_plan(run.id, agent_id="custom")
        self.assertEqual(plan.state, "unsupported")
        self.assertFalse(plan.can_restart)
        result = await run.aresult()
        self.assertEqual(result.error_code, "engine_failed")
        self.assertEqual(result.diagnostic.code, result.error_code)
        self.assertEqual(result.diagnostic.details["observed_code"], "provider_rate_limit")
        stored = run.data
        del stored.metadata["execution_started"]
        app.run_repository.save(stored)
        self.assertFalse((await session.run.resume_plan(run.id, agent_id="custom")).can_restart)
        self.assertNotIn("execution_started", run.data.metadata)

    async def test_only_predispatch_record_allows_explicit_restart(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        model = ScriptedCompletion([chunk("done", finish="stop")])
        app = LargeLanguageModel(temporary.name, engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate(components=[], config={"parameters": {"engines": {
            "loop": {"config": {}}}}})
        await project.agents.acreate({"completion": {"model": "test"}, "engine": "loop"}, identifier="loop")
        session = await project.sessions.acreate(agent_ids=sorted(await project.agents.alist()))
        with patch.object(RunManager, "_consume_limited", AsyncMock(side_effect=asyncio.CancelledError())):
            run = (await (await session.run.submit("original", recipients=["loop"])).wait(timeout=10))[0]
        plan = await session.run.resume_plan(run.id, agent_id="loop")
        self.assertEqual(plan.state, "not_created")
        self.assertTrue(plan.can_restart, plan.restart_blockers)
        self.assertEqual(model.requests, [])
        resumed = (await (await session.run.restart(run.id, agent_id="loop",
            expected_revision=plan.revision)).wait(timeout=10))[0]
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
        self.assertEqual(resumed.data.metadata["restart"]["run_id"], run.id)
        self.assertEqual(len(model.requests), 1)
        self.assertFalse((await session.run.resume_plan(run.id, agent_id="loop")).can_restart)
        with self.assertRaises(CheckpointError):
            await session.run.restart(run.id, agent_id="loop", expected_revision=plan.revision)


class JobResumeContracts(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = rag_tests.RAGTests.asyncSetUp

    async def test_failed_job_plan_cas_diagnostic_and_reuse(self):
        job = await self.rag.aenqueue_document(identifier="manual", title="Manual", content="Alice owns Atlas")
        with patch.object(self.rag_component, "_build", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.rag.arun_job(job["id"])
        diagnostic = await self.rag.ajob_diagnostic(job["id"])
        self.assertEqual(diagnostic.code, "rag_failed")
        self.assertEqual(diagnostic.details["boundary"], "component")
        plan = await self.rag.aresume_plan(job["id"])
        self.assertTrue(plan.can_resume, plan.blockers)
        self.assertEqual(JobResumePlan.from_dict(plan.to_dict()), plan)
        jobs = RAGJobs(self.rag)
        jobs._telemetry(job["id"], {"embedding_seconds": 1.0})
        with self.assertRaises(CheckpointError) as rejected:
            await self.rag.arun_job(job["id"], retry=True, expected_revision=plan.revision)
        self.assertEqual(rejected.exception.code, "checkpoint_conflict")
        self.assertEqual(await self.rag.ajob_diagnostic(job["id"]), diagnostic)
        self.assertEqual((await self.rag.ajob(job["id"]))["failures"], [])
        plan = await self.rag.aresume_plan(job["id"])
        self.assertIn("batches/embedding_0.json", plan.checkpoints)
        original_commit = RAGJobs._commit
        observations = []
        def observe_commit(jobs, identifier, document):
            observations.append(jobs.get(identifier))
            self.assertIsNone(jobs.data.job_diagnostic(identifier))
            self.assertEqual(jobs.progress(identifier).message, "")
            return original_commit(jobs, identifier, document)
        with patch.object(self.embed, "embed", AsyncMock(side_effect=AssertionError("must reuse vectors"))), \
                patch.object(RAGJobs, "_commit", observe_commit):
            await self.rag.arun_job(job["id"], retry=True, expected_revision=plan.revision)
        completed = await self.rag.ajob(job["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(observations[0]["status"], "running")
        self.assertNotIn("ended_at", observations[0])
        for record in (observations[0], completed):
            for key in ("error", "diagnostic_code", "diagnostic", "failed_phase"):
                self.assertNotIn(key, record)
            self.assertEqual(len(record["failures"]), 1)
            self.assertEqual(record["failures"][0]["diagnostic"], diagnostic.to_dict())
        self.assertIsNone(await self.rag.ajob_diagnostic(job["id"]))
        self.assertEqual((await self.rag.ajob_progress(job["id"])).message, "")
        self.assertFalse((await self.rag.aresume_plan(job["id"])).can_resume)

    async def test_multiple_failures_keep_history_and_only_latest_current_error(self):
        job = await self.rag.aenqueue_document(identifier="manual", title="Manual", content="Alice owns Atlas")
        failures = []
        for index, error in enumerate((OSError("first"), ValueError("second"))):
            with patch.object(self.rag_component, "_build", side_effect=error), self.assertRaises(type(error)):
                await self.rag.arun_job(job["id"], retry=bool(index))
            current = await self.rag.ajob_diagnostic(job["id"])
            self.assertEqual(current.message, str(error))
            failures.append(current.to_dict())
            stored = await self.rag.ajob(job["id"])
            self.assertEqual([item["diagnostic"] for item in stored.get("failures", [])], failures[:-1])
        await self.rag.arun_job(job["id"], retry=True)
        stored = await self.rag.ajob(job["id"])
        self.assertIsNone(await self.rag.ajob_diagnostic(job["id"]))
        self.assertEqual([item["diagnostic"] for item in stored["failures"]], failures)

    async def test_cancel_retry_keeps_history_and_current_cancellation(self):
        job = await self.rag.aenqueue_document(identifier="manual", title="Manual", content="Alice owns Atlas")
        with patch.object(self.embed, "embed", AsyncMock(side_effect=ConnectionError("offline"))):
            with self.assertRaises(ConnectionError):
                await self.rag.arun_job(job["id"])
        failure = await self.rag.ajob_diagnostic(job["id"])
        entered = asyncio.Event()
        async def pending(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(self.embed, "embed", pending):
            task = asyncio.create_task(self.rag.arun_job(job["id"], retry=True))
            try:
                await asyncio.wait_for(entered.wait(), 10)
                self.assertIsNone(await self.rag.ajob_diagnostic(job["id"]))
                self.assertEqual((await self.rag.ajob_progress(job["id"])).message, "")
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        stored = await self.rag.ajob(job["id"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(stored["failures"][0]["diagnostic"], failure.to_dict())
        self.assertEqual((await self.rag.ajob_diagnostic(job["id"])).code, "rag_cancelled")
        self.assertFalse((await self.rag.aresume_plan(job["id"])).can_resume)

    async def test_missing_input_and_abandonment_never_regain_permission(self):
        job = await self.rag.aenqueue_document(identifier="manual", title="Manual", content="Alice owns Atlas")
        with patch.object(self.embed, "embed", AsyncMock(side_effect=ConnectionError("offline"))):
            with self.assertRaises(ConnectionError):
                await self.rag.arun_job(job["id"])
        root = RAGJobs(self.rag)._path(job["id"])
        (root / "input.json").unlink()
        plan = await self.rag.aresume_plan(job["id"])
        self.assertEqual(plan.state, "missing")
        self.assertFalse(plan.can_resume)
        await self.rag.aabandon_job(job["id"], expected_revision=(await self.rag.acheckpoint_plan(job["id"])).revision)
        plan = await self.rag.aresume_plan(job["id"])
        self.assertFalse(plan.can_resume)
        self.assertIn("not_resumable", [item.code for item in plan.blockers])

    async def test_changed_configuration_and_cancelled_job_are_not_resumable(self):
        job = await self.rag.aenqueue_document(identifier="manual", title="Manual", content="Alice owns Atlas")
        with patch.object(self.embed, "embed", AsyncMock(side_effect=ConnectionError("offline"))):
            with self.assertRaises(ConnectionError):
                await self.rag.arun_job(job["id"])
        await self.rag.aconfigure({"config": {"chunk_size": 128}})
        plan = await self.rag.aresume_plan(job["id"])
        self.assertEqual(plan.state, "incompatible")
        self.assertFalse(plan.can_resume)
        with self.assertRaises(CheckpointError):
            await self.rag.arun_job(job["id"], retry=True, expected_revision=plan.revision)
        await self.rag.acancel_job(job["id"])
        plan = await self.rag.aresume_plan(job["id"])
        self.assertFalse(plan.can_resume)
