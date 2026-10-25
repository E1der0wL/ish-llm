"""Convert model assessments to Hub risk classifications, never permission."""

from llm.components.tools.builtin.workspace_values import WorkspaceExecuteArguments

from copy import deepcopy
from dataclasses import replace
from typing import Any

from llm.core.tool_contracts import ToolClassification
from llm.core.interactions import InteractionRequestObservation
from llm.services.runtime.tool_approval_records import ToolApprovalActionView
from llm.core.schema import SchemaKeyword, SchemaType, SchemaBoolean
from llm.components.tools import Tool, ToolRegistry
from llm.components.tools.builtin import BuiltinTools


class RiskAssessment:
    """Hub model advice only; no assessment is an authorization decision."""

    FIELD = "_hub_risk"
    SCHEME = "hub-model-risk-v1"
    LEVEL = "level"
    REASON = "reason"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"
    SCORES = {LOW: 20, MEDIUM: 60, HIGH: 100}
    ABSENT = None
    REASON_SEPARATOR = " "
    REASON_LIMIT = 240
    LEVEL_POSITION = 0
    REASON_POSITION = 1
    REVISION_PREFIX = "hub-risk-advice-v1:"
    SCHEMA = {
        SchemaKeyword.TYPE: SchemaType.OBJECT,
        SchemaKeyword.DESCRIPTION: "Risk assessment recorded for this exact action; never authorization.",
        SchemaKeyword.PROPERTIES: {
            LEVEL: {
                SchemaKeyword.TYPE: SchemaType.STRING,
                SchemaKeyword.ENUM: [LOW, MEDIUM, HIGH, UNKNOWN],
            },
            REASON: {
                SchemaKeyword.TYPE: SchemaType.STRING,
                SchemaKeyword.MIN_LENGTH: 1,
                SchemaKeyword.MAX_LENGTH: REASON_LIMIT,
            },
        },
        SchemaKeyword.REQUIRED: [LEVEL, REASON],
        SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT,
    }

    INSTRUCTION = (
        "For every tool exposing _hub_risk, supply a short risk assessment in that argument. "
        "Inspect the actual paths, commands and code, not just the tool name. Consider data loss, "
        "overwrites, command side effects, secrets/network exposure, scope and reversibility. "
        "Use the user's language and explain the concrete reason in one short sentence. "
        "If effects cannot be established, choose unknown and state what is missing; do not invent certainty. "
        "Hub records this assessment as the action's risk classification for user review. "
        "It is never permission or an approval decision."
    )

    @classmethod
    def from_arguments(cls, arguments):
        value = arguments.get(cls.FIELD) if isinstance(arguments, dict) else cls.ABSENT
        if (
            not isinstance(value, dict)
            or not isinstance(value.get(cls.LEVEL), str)
            or value[cls.LEVEL] not in (*cls.SCORES, cls.UNKNOWN)
        ):
            return cls.ABSENT
        reason = value.get(cls.REASON)
        if not isinstance(reason, str) or not reason.strip():
            return cls.ABSENT
        return (
            value[cls.LEVEL],
            cls.REASON_SEPARATOR.join(reason.split())[: cls.REASON_LIMIT],
        )

    @classmethod
    def level(cls, assessment):
        return assessment[cls.LEVEL_POSITION]

    @classmethod
    def reason(cls, assessment):
        return assessment[cls.REASON_POSITION]

    @classmethod
    def install_schema(cls, parameters):
        parameters.setdefault(SchemaKeyword.PROPERTIES, {})[cls.FIELD] = deepcopy(
            cls.SCHEMA
        )


FIELD = RiskAssessment.FIELD
MODEL_RISK_SCHEME = RiskAssessment.SCHEME
RISK_SCORES = RiskAssessment.SCORES
INSTRUCTION = RiskAssessment.INSTRUCTION


def execution_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in arguments.items() if key != FIELD}


def model_assessment(request: dict) -> tuple[str, str] | None:
    action = InteractionRequestObservation(request).action
    arguments = (
        ToolApprovalActionView(action).arguments
        if isinstance(action, dict)
        else RiskAssessment.ABSENT
    )
    return RiskAssessment.from_arguments(arguments)


def classify_model_risk(call) -> ToolClassification:
    """Default host classifier; explicit HubConfig.tool_classifier takes precedence.

    Missing and unknown assessments have no score, so threshold rules cannot
    approve them. The scheme is distinct from Hub's trusted-classifier presets.
    """
    assessment = RiskAssessment.from_arguments(call.arguments)
    return ToolClassification(
        risk_scheme=MODEL_RISK_SCHEME,
        risk=(
            RISK_SCORES.get(RiskAssessment.level(assessment))
            if assessment
            else RiskAssessment.ABSENT
        ),
    )


def bind_advice(toolkit: BuiltinTools) -> None:
    def handler(tool: Tool):
        async def invoke(arguments: dict[str, Any]) -> Any:
            return await tool.handler(execution_arguments(arguments))

        return invoke

    tools = []
    for name in toolkit.registry.names():
        tool = toolkit.registry.get(name)
        if tool.contract.approval_required:
            parameters = deepcopy(tool.parameters)
            RiskAssessment.install_schema(parameters)
            # Keep the sandbox handler's trusted type and execution binding.
            # HubTestWorkspaces removes the classification metadata itself.
            tool = replace(
                tool,
                parameters=parameters,
                handler=(
                    tool.handler
                    if name == WorkspaceExecuteArguments.NAME
                    else handler(tool)
                ),
                contract=replace(
                    tool.contract,
                    revision=RiskAssessment.REVISION_PREFIX + tool.contract.revision,
                ),
            )
        tools.append(tool)
    toolkit.registry = ToolRegistry(tuple(tools))
