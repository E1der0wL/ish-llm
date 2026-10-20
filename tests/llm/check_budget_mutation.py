"""Prove the R3 regression observes a second real effect if the limit is lost."""

import io
import unittest
from unittest.mock import patch

from llm.services.runtime.tools import ToolExecutionScope


TEST = ("tests.llm.test_nested_graph.NestedGraphTests."
        "test_resume_preserves_one_call_agent_budget_and_blocks_new_effect")


def main():
    original = ToolExecutionScope.child
    def drop_limit(scope, **kwargs):
        kwargs.pop("max_calls", None)
        return original(scope, **kwargs)
    output = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromName(TEST)
    with patch.object(ToolExecutionScope, "child", drop_limit):
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    text = output.getvalue()
    print(text, end="")
    detected = (result.testsRun == 1 and len(result.failures) == 1 and not result.errors
                and not result.skipped
                and "new Tool effect escaped the resumed Agent budget" in result.failures[0][1])
    print("R3 dropped-limit mutation detected by real effect assertion:", detected)
    return int(not detected)


if __name__ == "__main__":
    raise SystemExit(main())
