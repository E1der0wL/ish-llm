"""Discover every LLM/Hub test and fail on missing, duplicate or skipped tests.

The JSON artifact records discovered and actually started IDs, and each outcome.
Use the same strict result checks locally and in CI. By default a suite assigns
an entire tree; explicit modules select whole discovered modules for local gates.
"""

import argparse
from collections import Counter
import faulthandler
import json
from pathlib import Path
import sys
import threading
import time
import unittest

from tests.llm.checks_output import emit_annotation


SUITES = {"llm": "tests/llm", "hub": "tests/hub"}


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


class Result(unittest.TextTestResult):
    def __init__(self, *args, progress_path=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.started_ids = []
        self.failed_ids = set()
        self.progress_path = progress_path

    def startTest(self, test):
        self.started_ids.append(test.id())
        super().startTest(test)
        if self.progress_path:
            self.started_at = time.monotonic()
            self.record_progress(test, 'started')
            faulthandler.dump_traceback_later(120, repeat=True)

    def stopTest(self, test):
        if self.progress_path:
            faulthandler.cancel_dump_traceback_later()
            self.record_progress(test, 'finished', seconds=round(time.monotonic() - self.started_at, 3))
        super().stopTest(test)

    def record_progress(self, test, state, **values):
        with self.progress_path.open('a', encoding='utf-8') as output:
            output.write(json.dumps({'test': test.id(), 'state': state,
                                     'threads': threading.active_count(), **values}) + '\n')

    def addFailure(self, test, error):
        self.failed_ids.add(test.id())
        super().addFailure(test, error)

    def addError(self, test, error):
        self.failed_ids.add(test.id())
        super().addError(test, error)

    def addSubTest(self, test, subtest, error):
        if error is not None:
            self.failed_ids.add(test.id())
        super().addSubTest(test, subtest, error)


def summarize(expected, result):
    started = Counter(result.started_ids)
    duplicate = sorted(key for key, count in Counter(expected).items() if count != 1)
    duplicate += sorted(key for key, count in started.items() if count != 1)
    skipped = [{"id": test.id(), "reason": reason} for test, reason in result.skipped]
    excluded = (result.failed_ids | {item["id"] for item in skipped}
                | {test.id() for test, _ in result.expectedFailures}
                | {test.id() for test in result.unexpectedSuccesses})
    # A skipped subtest belongs to the started parent even though unittest gives
    # it a separate display ID. Never count its parent as fully verified.
    excluded.update(test.test_case.id() for test, _ in result.skipped
                    if hasattr(test, "test_case"))
    report = {
        "discovered": expected, "started": result.started_ids,
        "passed": sorted(set(started) - excluded), "failed": sorted(result.failed_ids),
        "failures": len(result.failures), "errors": len(result.errors), "skipped": skipped,
        "expected_failures": [test.id() for test, _ in result.expectedFailures],
        "unexpected_successes": [test.id() for test in result.unexpectedSuccesses],
        "missing": sorted(set(expected) - set(started)),
        "unexpected": sorted(set(started) - set(expected)), "duplicates": duplicate,
    }
    report["ok"] = (bool(expected) and result.wasSuccessful()
                    and not any(report[key] for key in (
                        "skipped", "expected_failures", "unexpected_successes",
                        "missing", "unexpected", "duplicates")))
    return report


def publish_annotations(report, result):
    """Keep actual counts and failure excerpts readable via the Checks API."""
    counts = {key: len(value) if isinstance(value, list) else value
              for key, value in report.items()}
    emit_annotation("Regression summary", counts, level="notice" if report["ok"] else "error")
    for test, trace in [*result.failures, *result.errors][:5]:
        emit_annotation("Regression failure detail",
                        {"id": test.id(), "trace": trace}, level="error")
    for skipped in report["skipped"][:5]:
        emit_annotation("Unverified skipped test", skipped, level="error")


def select_modules(suite, modules):
    """Select whole discovered modules; reject typos, overlap and hidden duplicates."""
    tests = list(flatten(suite))
    available = [test.id() for test in tests]
    if not modules or len(set(modules)) != len(modules):
        raise ValueError("Module selection must be nonempty and unique")
    if len(set(available)) != len(available):
        raise ValueError("Discovery contains duplicate test IDs")
    known = {identifier.rsplit(".", 2)[0] for identifier in available}
    unknown = sorted(set(modules) - known)
    if unknown:
        raise ValueError(f"Modules absent from discovery: {unknown}")
    selected = [test for test in tests if test.id().rsplit(".", 2)[0] in modules]
    return unittest.TestSuite(selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=(*SUITES, "all"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--progress", type=Path, help="Per-test JSONL and periodic stall traces")
    parser.add_argument("--modules", nargs="+", help="Exact discovered module names for a local partial gate")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    names = SUITES if args.suite == "all" else (args.suite,)
    suite = unittest.TestSuite()
    discovery_errors = []
    for name in names:
        loader = unittest.TestLoader()
        suite.addTests(loader.discover(str(root / SUITES[name]), top_level_dir=str(root)))
        if loader.errors:
            discovery_errors.extend(loader.errors)
            print("\n".join(loader.errors), file=sys.stderr)
    available = [test.id() for test in flatten(suite)]
    if args.modules is not None:
        if discovery_errors:
            parser.error("Discovery failed; refusing partial execution")
        try:
            suite = select_modules(suite, args.modules)
        except ValueError as error:
            parser.error(str(error))
    ids = [test.id() for test in flatten(suite)]
    start = time.monotonic()
    from functools import partial
    if args.progress:
        args.progress.parent.mkdir(parents=True, exist_ok=True)
        args.progress.write_text('', encoding='utf-8')
    result = unittest.TextTestRunner(verbosity=2, resultclass=partial(Result, progress_path=args.progress)).run(suite)
    report = summarize(ids, result)
    report.update(suite=args.suite, python=sys.version, seconds=time.monotonic() - start,
                  scope="selected" if args.modules is not None else "full",
                  modules=args.modules, available=available)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: (len(value) if isinstance(value, list) else value)
                      for key, value in report.items()}, ensure_ascii=False))
    publish_annotations(report, result)
    return int(not report["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
