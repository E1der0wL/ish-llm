"""저장 Agent만 선택하는 위임 Tool. 실행/체크포인트 처리는 공통 Engine runtime에 위임한다."""

from llm.core.tool_contracts import ToolContract
from llm.components.tools import Tool, ToolRegistry
from llm.engines.agents import AgentExecution, AgentDelegation
from llm.engines.agent_values import AgentDelegationArguments
from llm.core.tool_contracts import ToolEffect, ToolIsolation


class AgentDelegationTool:
    NAME = "agent_run"
    DESCRIPTION = "Execute a saved Agent inside this Run without adding authority."
    CONTRACT = ToolContract(revision='1', effect=ToolEffect.UNKNOWN,
        isolation=ToolIsolation.NONE, approval_required=False,
        operation_key_required=False, retry_fields=())


def agent_tools(engines):
    runtime = AgentExecution(engines=engines)
    handler = AgentDelegation(runtime)
    return ToolRegistry((Tool(AgentDelegationTool.NAME, AgentDelegationTool.DESCRIPTION,
        AgentDelegationArguments.describe_config(), handler, contract=AgentDelegationTool.CONTRACT),))
