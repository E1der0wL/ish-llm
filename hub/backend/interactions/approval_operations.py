"""Explicit Hub approval-management inputs; native services retain all authority."""

from dataclasses import dataclass, replace

from llm.core.contracts import JsonValue
from llm.core.interactions import InteractionRequest


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalQuery(JsonValue):
    include_interactions: bool = False

    def observing(self, include_interactions):
        return replace(self, include_interactions=include_interactions)


@dataclass(frozen=True, slots=True)
class ApprovalRuleTarget(ApprovalQuery):
    id: str
    revision: int


@dataclass(frozen=True, slots=True)
class ApprovalEdit(ApprovalRuleTarget):
    arguments: dict | None = None
    prefixes: str | None = None
    max_risk: int | None = None


@dataclass(frozen=True, slots=True)
class ApprovalTarget(ApprovalQuery):
    session_id: str
    run_id: str
    request: InteractionRequest
    risk: int | None = None
    risk_scheme: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalAssessment(ApprovalTarget):
    REQUIRED = 'Risk and risk scheme are required for reclassification'

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if self.risk is None or not self.risk_scheme:
            raise ValueError(self.REQUIRED)


@dataclass(frozen=True, slots=True)
class ApprovalApplication(ApprovalQuery):
    session_id: str
    run_id: str
    request: InteractionRequest | None = None
    risk: int | None = None
    risk_scheme: str | None = None
