"""GraphEngine의 tool 노드를 공통 ToolExecutor에 연결한다."""

import json

from contextlib import aclosing
from llm.services.runtime.tools import ToolExecutor
from llm.core.schema import object_schema, open_schema, field


class ToolNode:
    """Workflow inputs 매핑, 정적 arguments 또는 state의 arguments_key를 실행 인자로 쓴다."""

    required_capabilities = ("tools",)

    def __init__(self, *, timeout_seconds=None, max_output_chars=None):
        self.executor = ToolExecutor(timeout_seconds=timeout_seconds, max_output_chars=max_output_chars)

    @staticmethod
    def configuration_schema():
        return object_schema({"tool": field("string", minLength=1),
            "arguments": open_schema("selected Tool parameter schema", category="implementation"),
            "arguments_key": field("string", minLength=1), "result_key": field("string", minLength=1)}, required=["tool"])

    def validate(self, definition, context):
        context.tools.get(definition["tool"])
        for key in ("result_key", "arguments_key"):
            if key in definition and (not isinstance(definition[key], str) or not definition[key]):
                raise ValueError(f"{key} must be a nonempty string")
        if sum(key in definition for key in ("inputs", "arguments", "arguments_key")) > 1:
            raise ValueError("Choose inputs, arguments or arguments_key")
        if "arguments_key" not in definition and "inputs" not in definition:
            context.tools.prepare(definition["tool"], json.dumps(definition.get("arguments", {})),
                                  constraints=context.tool_scope.policy.argument_constraints if context.tool_scope else None)

    async def __call__(self, node):
        definition = node.definition
        if "inputs" in definition:
            arguments = node.inputs
        else:
            arguments = node.state[definition["arguments_key"]] if "arguments_key" in definition else definition.get("arguments", {})
        tool, values = node.context.tools.prepare(definition["tool"], json.dumps(arguments),
            constraints=node.context.tool_scope.policy.argument_constraints if node.context.tool_scope else None)
        result = {}
        # node.decision은 pause_before 확인일 수도 있다. Tool 승인만 공통 helper가
        # 원본 checkpoint의 interaction/action과 연결해 해석하도록 위임한다.
        async with aclosing(node.context.execute_tool(tool, values, result=result, executor=self.executor,
                                                 metadata={"node_id": node.node_id},
                                                 checkpoint_key=node.checkpoint_key)) as events:
            async for event in events:
                await node.emit(event)
        return {definition.get("result_key", node.node_id): result["value"]}
