"""GraphEngine의 tool 노드를 공통 ToolExecutor에 연결한다."""

import json

from llm.compat import aclosing
from llm.services.runtime.tools import ToolExecutor


class ToolNode:
    """Workflow inputs 매핑, 정적 arguments 또는 state의 arguments_key를 실행 인자로 쓴다."""

    required_capabilities = ("tools",)

    def __init__(self, *, timeout_seconds=None, max_output_chars=None):
        self.executor = ToolExecutor(timeout_seconds=timeout_seconds, max_output_chars=max_output_chars)

    def validate(self, definition, context):
        context.tools.get(definition["tool"])
        for key in ("result_key", "arguments_key"):
            if key in definition and (not isinstance(definition[key], str) or not definition[key]):
                raise ValueError(f"{key} must be a nonempty string")
        if sum(key in definition for key in ("inputs", "arguments", "arguments_key")) > 1:
            raise ValueError("Choose inputs, arguments or arguments_key")
        if "arguments_key" not in definition and "inputs" not in definition:
            context.tools.prepare(definition["tool"], json.dumps(definition.get("arguments", {})))

    async def __call__(self, node):
        definition = node.definition
        if "inputs" in definition:
            arguments = node.inputs
        else:
            arguments = node.state[definition["arguments_key"]] if "arguments_key" in definition else definition.get("arguments", {})
        tool, values = node.context.tools.prepare(definition["tool"], json.dumps(arguments))
        result = {}
        async with aclosing(self.executor.execute(tool, values, result=result, context=node.context,
                                                 metadata={"node_id": node.node_id}, decision=node.decision)) as events:
            async for event in events:
                await node.emit(event)
        return {definition.get("result_key", node.node_id): result["value"]}
