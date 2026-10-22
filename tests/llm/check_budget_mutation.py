"""Prove the R3 regression observes a second real effect if the limit is lost."""

import io
import unittest
from unittest.mock import patch

from llm.services.runtime.tools import ToolExecutionScope


TEST = ("tests.llm.test_nested_graph.NestedGraphTests."
        "test_resume_preserves_one_call_agent_budget_and_blocks_new_effect")
ROOT_TEST = ("tests.llm.test_agent_execution.AgentExecutionTests."
             "test_resume_preserves_root_budget_and_missing_binding_is_not_repaired")


def main():
    original = ToolExecutionScope.child
    def drop_limit(scope, **kwargs):
        kwargs.pop("max_calls", None)
        return original(scope, **kwargs)
    output = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromNames((TEST, ROOT_TEST))
    with patch.object(ToolExecutionScope, "child", drop_limit):
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    text = output.getvalue()
    print(text, end="")
    failures = {test.id(): trace for test, trace in result.failures}
    detected = (result.testsRun == 2 and len(result.failures) == 2 and not result.errors
                and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses
                and "new Tool effect escaped the resumed Agent budget" in failures.get(TEST, "")
                and "new Tool effect escaped the resumed root Agent budget" in failures.get(ROOT_TEST, ""))
    print("R3 dropped-limit mutation detected by real effect assertion:", detected)
    return int(not detected)


if __name__ == "__main__":
    raise SystemExit(main())
