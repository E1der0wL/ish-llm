"""Pure Workflow branch/loop/continuation decisions; no effects or persisted approvals."""

import operator
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional
from llm.components.workflows.graph import ConditionOperator, LoopLimit
from llm.components.workflows.contracts import WorkflowCondition, WorkflowCase, WorkflowNode
from llm.components.workflows.bindings import read_pointer
from llm.core.checkpoints import CheckpointStatus
from .errors import GraphExecutionError
from .node_records import GraphNodeRecord, GraphDecision


class GraphDecisionContract:
    MATCH = True
    NO_MATCH = False
    ABSENT = None
    ORDERED_TYPES = "Ordered comparison requires two numbers or two strings"
    LOOP_LIMIT = "Loop iteration limit reached"
    CONTAINER_INPUT = "Checkpoint container input changed; start a new request"
    NODE_INPUT = "Checkpoint node input does not match restored state"
    REJECTED = "Workflow continuation rejected by user"


class GraphNodeAction(StrEnum):
    REUSE = "reuse"
    PAUSE = "pause"
    EXECUTE = "execute"


@dataclass(frozen=True, slots=True)
class _NodePlan:
    """Transient decision and detached continuation value, never stored as authority."""

    action: GraphNodeAction
    decision: dict = field(default_factory=dict)


def matches(condition: dict, state: dict) -> bool:
    condition = WorkflowCondition(condition)
    try:
        value = read_pointer(state, condition.path)
    except KeyError:
        return GraphDecisionContract.NO_MATCH
    op = condition.operator
    if op == ConditionOperator.EXISTS:
        return GraphDecisionContract.MATCH
    other = condition.value

    def equal(left, right):
        if isinstance(left, bool) != isinstance(right, bool):
            return GraphDecisionContract.NO_MATCH
        if isinstance(left, dict) and isinstance(right, dict):
            return left.keys() == right.keys() and all(equal(left[key], right[key]) for key in left)
        if isinstance(left, list) and isinstance(right, list):
            return len(left) == len(right) and all(equal(a, b) for a, b in zip(left, right))
        return left == right

    if op in (ConditionOperator.EQ, ConditionOperator.NE):
        result = equal(value, other)
        return result if op == ConditionOperator.EQ else not result
    if op == ConditionOperator.IN:
        return any(equal(value, item) for item in other)
    if not (
        (type(value) in (int, float) and type(other) in (int, float))
        or (isinstance(value, str) and isinstance(other, str))
    ):
        raise GraphExecutionError(GraphDecisionContract.ORDERED_TYPES)
    return {
        ConditionOperator.LT: operator.lt,
        ConditionOperator.LE: operator.le,
        ConditionOperator.GT: operator.gt,
        ConditionOperator.GE: operator.ge,
    }[op](value, other)


def _branch_port(definition: dict, state: dict) -> str:
    node = WorkflowNode(definition)
    return next(
        (case.port for case in map(WorkflowCase, node.cases) if matches(case.condition, state)),
        node.default_port,
    )


def _loop_continues(definition: dict, state: dict, count: int) -> bool:
    node = WorkflowNode(definition)
    condition = node.loop_condition
    proceed = condition is GraphDecisionContract.ABSENT or matches(condition, state)
    if proceed and count < node.max_iterations:
        return GraphDecisionContract.MATCH
    if proceed and condition is not GraphDecisionContract.ABSENT and node.on_limit == LoopLimit.FAIL:
        raise GraphExecutionError(GraphDecisionContract.LOOP_LIMIT)
    return GraphDecisionContract.NO_MATCH


def _plan_node(
    definition: dict, state: dict, *, saved: Optional[dict], resuming: bool, decisions: dict, key: str
) -> _NodePlan:
    record = GraphNodeRecord(saved)
    if record.present and record.container and record.input_state != state:
        raise GraphExecutionError(GraphDecisionContract.CONTAINER_INPUT)
    if record.present and record.status == CheckpointStatus.COMPLETED:
        if record.input_state != state:
            raise GraphExecutionError(GraphDecisionContract.NODE_INPUT)
        return _NodePlan(GraphNodeAction.REUSE)
    if WorkflowNode(definition).pause_before and not (resuming and record.present):
        return _NodePlan(GraphNodeAction.PAUSE)
    decision = decisions.get(key, record.decision_or_empty)
    if GraphDecision(decision).rejected:
        raise GraphExecutionError(GraphDecisionContract.REJECTED)
    return _NodePlan(GraphNodeAction.EXECUTE, deepcopy(decision))
