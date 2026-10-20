import io
import unittest

from tests.llm.run_regression import Result, flatten, summarize


class RegressionRunnerTests(unittest.TestCase):
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
