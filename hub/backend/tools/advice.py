"""Convert model assessments to Hub risk classifications, never permission."""

from copy import deepcopy
from dataclasses import replace
from typing import Any

from llm.components.tools import Tool, ToolClassification, ToolRegistry
from llm.components.tools.builtin import BuiltinTools

FIELD = "_hub_risk"
MODEL_RISK_SCHEME = "hub-model-risk-v1"
RISK_SCORES = {"low": 20, "medium": 60, "high": 100}
INSTRUCTION = (
    "For every tool exposing _hub_risk, supply a short risk assessment in that argument. "
    "Inspect the actual paths, commands and code, not just the tool name. Consider data loss, "
    "overwrites, command side effects, secrets/network exposure, scope and reversibility. "
    "Use the user's language and explain the concrete reason in one short sentence. "
    "If effects cannot be established, choose unknown and state what is missing; do not invent certainty. "
    "Hub records this assessment as the action's risk classification for user review. "
    "It is never permission or an approval decision."
)


def execution_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in arguments.items() if key != FIELD}


def model_assessment(request: dict) -> tuple[str, str] | None:
    action = request.get("action")
    arguments = action.get("arguments") if isinstance(action, dict) else None
    value = arguments.get(FIELD) if isinstance(arguments, dict) else None
    if not isinstance(value, dict) or not isinstance(value.get("level"), str) or value["level"] not in (*RISK_SCORES, "unknown"):
        return None
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return None
    return value["level"], " ".join(reason.split())[:240]


def classify_model_risk(call) -> ToolClassification:
    """Default host classifier; explicit HubConfig.tool_classifier takes precedence.

    Missing and unknown assessments have no score, so threshold rules cannot
    approve them. The scheme is distinct from Hub's trusted-classifier presets.
    """
    assessment = model_assessment({"action": {"arguments": call.arguments}})
    return ToolClassification(risk_scheme=MODEL_RISK_SCHEME,
        risk=RISK_SCORES.get(assessment[0]) if assessment else None)


def bind_advice(toolkit: BuiltinTools) -> None:
    def handler(tool: Tool):
        async def invoke(arguments: dict[str, Any]) -> Any:
            return await tool.handler(execution_arguments(arguments))
        return invoke

    tools = []
    for name in toolkit.registry.names():
        tool = toolkit.registry.get(name)
        if tool.contract is not None and tool.contract.approval_required:
            parameters = deepcopy(tool.parameters)
            parameters.setdefault("properties", {})[FIELD] = {
                "type": "object", "description": "Risk assessment recorded for this exact action; never authorization.",
                "properties": {"level": {"type": "string", "enum": ["low", "medium", "high", "unknown"]},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 240}},
                "required": ["level", "reason"], "additionalProperties": False}
            # Keep the sandbox handler's trusted type and execution binding.
            # HubTestWorkspaces removes the classification metadata itself.
            tool = replace(tool, parameters=parameters,
                definition=replace(tool.definition, parameters=parameters),
                handler=tool.handler if name == "test_workspace_execute" else handler(tool),
                contract=replace(tool.contract, revision="hub-risk-advice-v1:" + tool.contract.revision))
        tools.append(tool)
    toolkit.registry = ToolRegistry(tuple(tools))
