import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.llm.run_regression import Result, flatten, main, select_modules, summarize


class RegressionRunnerTests(unittest.TestCase):
    def test_progress_is_durable_and_preserves_failure_result(self):
        from functools import partial

        class Failing(unittest.TestCase):
            def runTest(self):
                self.fail('visible failure')

        with tempfile.TemporaryDirectory() as directory, \
                patch('tests.llm.run_regression.faulthandler.dump_traceback_later') as trace, \
                patch('tests.llm.run_regression.faulthandler.cancel_dump_traceback_later') as cancel:
            path = Path(directory) / 'progress.jsonl'
            test = Failing()
            result = unittest.TextTestRunner(stream=io.StringIO(),
                resultclass=partial(Result, progress_path=path)).run(unittest.TestSuite([test]))
            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([item['state'] for item in records], ['started', 'finished'])
            self.assertEqual([item['test'] for item in records], [test.id(), test.id()])
            self.assertGreaterEqual(records[-1]['seconds'], 0)
            self.assertGreaterEqual(records[-1]['threads'], 1)
            self.assertFalse(summarize([test.id()], result)['ok'])
            trace.assert_called_once_with(120, repeat=True)
            cancel.assert_called_once_with()

    def run_cases(self, cases, expected=None):
        suite = unittest.TestSuite(cases)
        ids = [test.id() for test in flatten(suite)]
        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=Result).run(suite)
        return summarize(ids if expected is None else expected, result)

    def test_all_discovered_tests_must_start_once(self):
        class Passing(unittest.TestCase):
            def runTest(self):
                self.assertTrue(True)
        self.assertTrue(self.run_cases([Passing()])["ok"])
        self.assertFalse(self.run_cases([Passing(), Passing()])["ok"])
        self.assertFalse(self.run_cases([Passing()], ["missing.test"])["ok"])
        self.assertFalse(self.run_cases([])["ok"])

    def test_skip_and_expected_failure_are_unverified(self):
        class Skipped(unittest.TestCase):
            def runTest(self):
                self.skipTest("missing host")
        class Expected(unittest.TestCase):
            @unittest.expectedFailure
            def runTest(self):
                self.fail("known bug")
        for case in (Skipped(), Expected()):
            with self.subTest(case=type(case).__name__):
                report = self.run_cases([case])
                self.assertFalse(report["ok"])
                self.assertEqual(report["passed"], [])

    def test_failed_and_skipped_subtests_do_not_count_as_passed(self):
        class Subtests(unittest.TestCase):
            def runTest(self):
                with self.subTest(part="failed"):
                    self.fail("regression")
        class SkippedSubtest(unittest.TestCase):
            def runTest(self):
                with self.subTest(part="skipped"):
                    self.skipTest("unverified")
        for case in (Subtests(), SkippedSubtest()):
            with self.subTest(case=type(case).__name__):
                report = self.run_cases([case])
                self.assertFalse(report["ok"])
                self.assertEqual(report["passed"], [])

    def test_selection_keeps_every_method_and_rejects_incomplete_module_names(self):
        class Case(unittest.TestCase):
            def __init__(self, identifier):
                super().__init__()
                self.identifier = identifier

            def id(self):
                return self.identifier

        ids = ["tests.llm.test_one.First.test_a", "tests.llm.test_one.Second.test_b",
               "tests.hub.test_two.First.test_a"]
        suite = unittest.TestSuite(Case(identifier) for identifier in ids)
        selected = select_modules(suite, ["tests.llm.test_one"])
        self.assertEqual([test.id() for test in flatten(selected)], ids[:2])
        for modules in ([], ["tests.llm"], ["tests.llm.test_one.First"],
                        ["tests.llm.test_one", "tests.llm.test_one"], ["tests.llm.missing"]):
            with self.subTest(modules=modules), self.assertRaises(ValueError):
                select_modules(suite, modules)
        duplicated = unittest.TestSuite([*flatten(suite), Case(ids[-1])])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            select_modules(duplicated, ["tests.llm.test_one"])

    def test_unexpected_success_is_not_verified(self):
        class Unexpected(unittest.TestCase):
            @unittest.expectedFailure
            def runTest(self):
                pass
        report = self.run_cases([Unexpected()])
        self.assertFalse(report["ok"])
        self.assertEqual(report["passed"], [])
        self.assertEqual(len(report["unexpected_successes"]), 1)

    def test_cli_selection_reports_full_discovery_without_claiming_full_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "selected.json"
            command = [sys.executable, "-m", "tests.llm.run_regression", "--suite", "llm",
                       "--modules", "tests.llm.test_checks_output", "--output", str(output)]
            process = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            report = json.loads(output.read_text())
            self.assertTrue(report["ok"])
            self.assertEqual(report["scope"], "selected")
            self.assertEqual(report["modules"], ["tests.llm.test_checks_output"])
            self.assertEqual(len(report["discovered"]), 2)
            self.assertEqual(report["discovered"], report["started"])
            self.assertGreater(len(report["available"]), len(report["started"]))
            self.assertEqual(report["missing"], [])

    def test_partial_execution_cannot_hide_a_discovery_error(self):
        with tempfile.TemporaryDirectory() as directory, patch("sys.argv", [
                "run_regression", "--suite", "llm", "--modules", "tests.llm.valid",
                "--output", str(Path(directory) / "report.json")]), \
                patch("tests.llm.run_regression.unittest.TestLoader") as loader, \
                patch("sys.stderr", new_callable=io.StringIO):
            loader.return_value.discover.return_value = unittest.TestSuite()
            loader.return_value.errors = ["another module failed to import"]
            with self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 2)

    def test_default_cli_path_runs_every_discovered_case(self):
        class Passing(unittest.TestCase):
            def runTest(self):
                pass
        with tempfile.TemporaryDirectory() as directory, patch("sys.argv", [
                "run_regression", "--suite", "llm", "--output", directory + "/report.json"]), \
                patch("tests.llm.run_regression.unittest.TestLoader") as loader, \
                patch("sys.stdout", new_callable=io.StringIO), \
                patch("sys.stderr", new_callable=io.StringIO):
            loader.return_value.discover.return_value = unittest.TestSuite([Passing()])
            loader.return_value.errors = []
            self.assertEqual(main(), 0)
            report = json.loads((Path(directory) / "report.json").read_text())
            self.assertEqual(report["scope"], "full")
            self.assertIsNone(report["modules"])
            self.assertEqual(report["available"], report["discovered"])
            self.assertEqual(report["discovered"], report["started"])
