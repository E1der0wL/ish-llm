"""Run existing offline SDK integration assertions with safe error diagnostics.

No authenticated model request is made. These tests use an in-memory HTTP
transport and a loopback HTTP server. Never turn a failure into a dependency
downgrade or a successful prerequisite.
"""

import argparse
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import unittest

from llm.providers.runtime import diagnostic_scope
from tests.llm.checks_output import emit_annotation


CASES = (
    "tests.llm.test_loop.LoopTests.test_real_litellm_sdk_with_mock_http_sse_and_tool_loop",
    "tests.hub.test_http_backend.HTTPBackendTests.test_production_backend_streams_from_configured_endpoint",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    versions = {}
    for name in ("litellm", "openai", "mcp", "httpx", "pydantic", "tiktoken"):
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = None
    errors = []

    def observe(event):
        if event.severity != "error":
            return
        # The provider boundary already excludes raw messages by default.
        # Narrow further to exception identities and source locations, without
        # request data, endpoint, credentials, or exception message text.
        details = event.details
        errors.append({
            "code": event.code, "stage": details.get("stage"),
            "exceptions": [{key: value for key, value in entry.items()
                            if key in ("type", "module", "attribute", "errno", "frames")}
                           for entry in details.get("exceptions", [])],
        })

    with diagnostic_scope(observe):
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromNames(CASES))
    report = {"versions": versions, "errors": errors, "started": result.testsRun,
              "failures": len(result.failures), "test_errors": len(result.errors),
              "skipped": len(result.skipped),
              "ok": result.wasSuccessful() and result.testsRun == len(CASES) and not result.skipped}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    emit_annotation("Offline SDK integration", {key: value for key, value in report.items() if key != "errors"},
                    level="notice" if report["ok"] else "error")
    for error in errors[:5]:
        emit_annotation("Offline SDK exception", error, level="error")
    return int(not report["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
