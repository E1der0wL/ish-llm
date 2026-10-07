"""Workflow의 Agent action. 실제 실행은 Tool 위임과 같은 runtime을 재사용한다."""

from llm.engines.agents import AgentExecution


class AgentNode(AgentExecution):
    """GraphNodeContext를 공통 Agent 실행 경계에 전달하는 Workflow handler."""
