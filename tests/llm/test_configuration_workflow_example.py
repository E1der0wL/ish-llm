from tests.llm.configuration_fixtures import rag_settings
"""사내 문법을 흉내 내지 않고 검색·수정·검증·승인 경계를 실제 서비스로 검사한다."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from llm.components.rag import RAGComponent, EmbeddingModel, TripleExtractor
from examples.llm import configuration_workflow as example
from tests.llm.test_loop import chunk, call
from tests.llm.test_rag_components import fake_embedding


QUOTE = "The current mode replaces the legacy mode."


async def extract(**request):
    return {"choices": [{"message": {"content": json.dumps({"entities": [], "relations": []})}}]}


def rag():
    return RAGComponent(embedding=EmbeddingModel(embedding_fn=fake_embedding),
                        extractor=TripleExtractor(completion_fn=extract))


class ProposalModel:
    def __init__(self, *, bad_first=False, compare=False, invalid_quote=False):
        self.bad_first, self.compare, self.invalid_quote = bad_first, compare, invalid_quote
        self.answers = 0

    def __call__(self, **request):
        if request["messages"][-1]["role"] != "tool":
            yield chunk(calls=[call('{"query":"legacy current", "method":"hybrid"}', name="rag_search")])
            yield chunk(finish="tool_calls")
            return
        result = json.loads(request["messages"][-1]["content"])
        identifier = result["documents"][0]["document_id"]
        self.answers += 1
        option = "invalid" if self.bad_first and self.answers == 1 else "current"
        proposal = {"summary": "Update configuration mode", "report_markdown": "| Before | After |\n|---|---|\n|legacy|current|",
            "changes": [] if self.compare else [{"path": "settings.conf", "content": f"include extra.conf\nmode={option}\n",
                                                  "reason": "Documented replacement"}],
            "citations": [{"document_id": identifier, "quote": "invented" if self.invalid_quote else QUOTE}],
            "needs_clarification": []}
        yield chunk(json.dumps(proposal))
        yield chunk(finish="stop")


class ConfigurationWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "settings.conf").write_text("include extra.conf\nmode=legacy\n")
        (self.source / "settings.conf").chmod(0o640)
        (self.source / "extra.conf").write_text("sample fixture\n")
        self.manual = self.root / "manual.md"
        self.manual.write_text("# Configuration\n\n" + QUOTE)
        self.config = {"source_root": str(self.source), "files": ["settings.conf", "*.conf"],
            "documents": [str(self.manual)], "max_attempts": 2,
            "validators": [{"name": "fixture-check", "argv": [sys.executable, "-c",
                "from pathlib import Path; import sys; p=Path(sys.argv[1]); "
                "assert (p/'extra.conf').read_text() == 'sample fixture\\n'; "
                "assert (p/'settings.conf').read_text() == 'include extra.conf\\nmode=current\\n'", "{candidate}"]}],
            "project_config": {"parameters": {"engines": {"loop": {**{"max_iterations": 3, "request_timeout": 20}, "completion": {"model": "test/chat"}}}, "components": {"rag": {**rag_settings(), "embedding_params": {"model": "test/embed"},
                    "extraction_params": {"model": "test/extract"},
                    "graph": {"buffer_pool_size": 67108864, "max_num_threads": 2}}}}}}

    async def plan(self, model=None, **kwargs):
        return await example.plan(self.root / "workspace", self.config, "Replace legacy with current",
            completion_fn=model or ProposalModel(), rag_component=rag(), **kwargs)

    def package_hash(self, report):
        return example.digest(report["interactions"][0]["action"]["arguments"]["package"])

    async def decide(self, report, decision="approve", **kwargs):
        return await example.decide(Path(report["bundle"]) / "report.json", self.config, decision,
            expected_package=kwargs.get("expected_package", self.package_hash(report)),
            completion_fn=ProposalModel(), rag_component=rag())

    async def test_rag_repair_pause_reopen_approval_and_apply(self):
        model = ProposalModel(bad_first=True)
        report = await self.plan(model)
        self.assertEqual(report["status"], "paused", report)
        self.assertEqual(model.answers, 2)
        self.assertIn("legacy", (self.source / "settings.conf").read_text())
        self.assertFalse((Path(report["bundle"]) / "before-apply.json").exists())
        self.assertEqual(len([s for s in report["steps"] if s["name"] == "rag_search"]), 2)
        review = (Path(report["bundle"]) / "review.md").read_text()
        self.assertIn("-mode=legacy", review)
        self.assertIn("+mode=current", review)
        completed = await self.decide(report)
        self.assertEqual(completed["status"], "completed", completed)
        self.assertNotEqual(completed["run_id"], report["run_id"])
        self.assertEqual(completed["output"]["application"]["status"], "applied")
        self.assertIn("current", (self.source / "settings.conf").read_text())
        self.assertEqual((self.source / "settings.conf").stat().st_mode & 0o777, 0o640)
        self.assertEqual(example.load_json(Path(report["bundle"]) / "before-apply.json")["files"]["settings.conf"]["content"],
                         "include extra.conf\nmode=legacy\n")
        with self.assertRaises(Exception):
            await self.decide(report)

    async def test_denial_never_writes_source(self):
        report = await self.plan()
        self.assertEqual(report["status"], "paused", report)
        denied = await self.decide(report, "deny")
        self.assertEqual(denied["status"], "failed")
        self.assertIn("rejected by user", denied["error"])
        self.assertIn("legacy", (self.source / "settings.conf").read_text())

    async def test_changed_include_after_review_blocks_all_writes(self):
        report = await self.plan()
        self.assertEqual(report["status"], "paused", report)
        (self.source / "extra.conf").write_text("changed by another editor\n")
        result = await self.decide(report)
        self.assertEqual(result["status"], "failed")
        self.assertIn("changed since review", result["error"])
        self.assertIn("legacy", (self.source / "settings.conf").read_text())
        self.assertEqual((self.source / "extra.conf").read_text(), "changed by another editor\n")

    async def test_wrong_approval_digest_keeps_request_pending(self):
        report = await self.plan()
        self.assertEqual(report["status"], "paused", report)
        with self.assertRaisesRegex(ValueError, "digest"):
            await self.decide(report, expected_package="0" * 64)
        self.assertIn("legacy", (self.source / "settings.conf").read_text())
        self.assertEqual((await self.decide(report))["status"], "completed")

    async def test_report_only_has_no_apply_or_validator(self):
        self.config["validators"] = []
        report = await self.plan(ProposalModel(compare=True), report_only=True)
        self.assertEqual(report["status"], "completed", report)
        self.assertEqual(report["output"]["proposal"]["changes"], [])
        self.assertEqual(report["interactions"], [])
        self.assertFalse(any(s["name"] == "apply_configuration" for s in report["steps"]))
        self.assertIn("legacy", (self.source / "settings.conf").read_text())

    async def test_missing_validator_fails_without_approval(self):
        self.config["validators"] = []
        report = await self.plan()
        self.assertEqual(report["status"], "failed", report)
        errors = example.load_json(Path(report["bundle"]) / "validation.json")["errors"]
        self.assertIn("none configured", str(errors))
        self.assertEqual(report["interactions"], [])
        self.assertIn("legacy", (self.source / "settings.conf").read_text())

    async def test_report_only_rejects_model_edit(self):
        report = await self.plan(report_only=True)
        self.assertEqual(report["status"], "failed", report)
        self.assertIn("cannot change", str(example.load_json(Path(report["bundle"]) / "validation.json")["errors"]))
        self.assertIn("legacy", (self.source / "settings.conf").read_text())

    async def test_invented_citation_blocks_approval(self):
        report = await self.plan(ProposalModel(invalid_quote=True))
        self.assertEqual(report["status"], "failed", report)
        self.assertEqual(report["interactions"], [])

    async def test_validator_failure_exhausts_repair_without_approval(self):
        self.config["validators"][0]["argv"] = [sys.executable, "-c", "raise SystemExit(3)"]
        report = await self.plan()
        self.assertEqual(report["status"], "failed", report)
        validation = example.load_json(Path(report["bundle"]) / "validation.json")
        self.assertEqual(validation["attempt"], 2)
        self.assertEqual(validation["checks"][0]["returncode"], 3)
        self.assertEqual(report["interactions"], [])
        self.assertIn("legacy", (self.source / "settings.conf").read_text())

    async def test_validator_cannot_mutate_the_reviewed_candidate(self):
        self.config["validators"][0]["argv"] = [sys.executable, "-c",
            "from pathlib import Path; Path('settings.conf').write_text('different content')"]
        report = await self.plan()
        self.assertEqual(report["status"], "failed", report)
        validation = example.load_json(Path(report["bundle"]) / "validation.json")
        self.assertIn("modified candidate", str(validation["errors"]))
        self.assertEqual(report["interactions"], [])
        self.assertIn("legacy", (self.source / "settings.conf").read_text())

    async def test_multiple_file_apply_rolls_back_on_later_write_failure(self):
        config = example.settings(self.config)
        source = example.read_snapshot(config)
        documents = example.read_documents(config)
        bundle = self.root / "review"
        bundle.mkdir()
        review = example.ConfigurationReview(config, bundle, source, documents)
        proposal = {"summary": "test", "report_markdown": "test", "needs_clarification": [],
            "citations": [{"document_id": next(iter(documents)), "quote": QUOTE}],
            "changes": [{"path": "settings.conf", "content": "new\n", "reason": "test"},
                        {"path": "extra.conf", "content": "new\n", "reason": "test"}]}
        package = {"binding": review.binding, "proposal": proposal,
                   "validation": [{"status": "completed", "returncode": 0, "truncated": False}]}
        write = example.write_text
        def fail_second(path, *args):
            if path == self.source / "extra.conf":
                raise OSError("disk failure")
            return write(path, *args)
        with patch.object(example, "write_text", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "disk failure"):
                await review.apply({"package": package})
        self.assertEqual(example.read_snapshot(config), source)

    async def test_selection_limits_and_symlinks_fail_before_model(self):
        self.config["max_source_bytes"] = 2
        with self.assertRaisesRegex(ValueError, "max_source_bytes"):
            await self.plan()
        self.config["max_source_bytes"] = 300_000
        (self.source / "linked.conf").symlink_to(self.manual)
        with self.assertRaisesRegex(ValueError, "linked"):
            await self.plan()


class CLIContractTests(unittest.TestCase):
    def test_user_request_sources_reach_plan_without_a_baked_in_question(self):
        with tempfile.TemporaryDirectory() as directory:
            request_file = Path(directory) / "request.txt"
            request_file.write_text("User-authored multiline\nrequest", encoding="utf-8")
            cases = [(["--request", "User-authored text"], False, "", "User-authored text"),
                     (["--request-file", str(request_file)], False, "", "User-authored multiline\nrequest"),
                     (["--request-file", "-"], False, "Piped user input", "Piped user input"),
                     ([], True, "", "Interactive user input")]
            for options, interactive, stdin, expected in cases:
                with self.subTest(options=options), patch.object(example, "load_json", return_value={}), \
                        patch.object(example, "configure_logging"), \
                        patch.object(example, "plan", new_callable=AsyncMock,
                                     return_value={"status": "completed", "bundle": "test"}) as run, \
                        patch.object(sys, "stdin", io.StringIO(stdin)), \
                        patch.object(sys.stdin, "isatty", return_value=interactive), \
                        patch("builtins.input", return_value="Interactive user input"), \
                        redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exited:
                    example.main("--config", "config.json", "plan", *options)
                self.assertEqual(exited.exception.code, 0)
                self.assertEqual(run.await_args.args[2], expected)

    def test_missing_or_empty_input_never_starts_a_run(self):
        for options in ([], ["--request", "  "], ["--request-file", "-"]):
            with self.subTest(options=options), patch.object(sys, "stdin", io.StringIO()), \
                    patch.object(example, "plan", new_callable=AsyncMock) as run, \
                    patch.object(example, "configure_logging") as logging, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exited:
                example.main("--config", "config.json", "plan", *options)
            self.assertEqual(exited.exception.code, 2)
            run.assert_not_called()
            logging.assert_not_called()

    def test_module_help_in_fresh_worker(self):
        result = subprocess.run([sys.executable, "-m", "examples.llm.configuration_workflow", "--help"],
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("approve", result.stdout)
        self.assertIn("deny", result.stdout)
