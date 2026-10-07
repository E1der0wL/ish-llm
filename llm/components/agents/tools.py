"""저장 Agent만 선택하는 위임 Tool. 실행/체크포인트 처리는 공통 Engine runtime에 위임한다."""

from copy import deepcopy

from llm.components.tools import Tool, ToolContract, ToolRegistry
from llm.core.schema import object_schema, field, open_schema
from llm.engines.agents import AgentExecution, AgentDelegation


def agent_tools(engines, records):
    runtime = AgentExecution(engines=engines)
    handler = AgentDelegation(runtime, deepcopy(records))
    return ToolRegistry((Tool("agent_run", "Execute a saved Agent inside this Run without adding authority.",
        object_schema({"agent_id": field("string", minLength=1),
                       "input": open_schema("selected saved Agent input_schema", category="implementation")},
                      required=["agent_id", "input"]),
        handler, contract=ToolContract()),))
