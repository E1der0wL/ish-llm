"""Tool approval action projections; full Interaction validation keeps its Owner."""

from copy import deepcopy
from dataclasses import asdict, dataclass, replace

from llm.core.checkpoint_records import CheckpointStep
from llm.core.checkpoints import CheckpointStatus
from llm.core.interactions import InteractionBinding, InteractionOption, InteractionRequest


@dataclass(frozen=True, slots=True)
class ToolApprovalSource:
    project_id: str
    session_id: str
    run_id: str
    step_id: str

    @classmethod
    def from_call(cls, call):
        return asdict(cls(call.project_id, call.session_id, call.run_id, call.step_id))


class ToolApprovalSourceView:
    """Optional observation of a Tool source; full ownership checks stay elsewhere."""

    def __init__(self, value):
        self._value = value

    @property
    def step_id(self):
        return self._value.get(ToolApprovalSource.step_id.__name__)


@dataclass(frozen=True, slots=True)
class ToolApprovalAction:
    tool: str
    arguments: dict
    contract: dict
    policy: dict
    classification: dict

    @classmethod
    def from_call(cls, call, policy):
        return asdict(cls(call.name, call.arguments, call.contract, policy, call.classification))

    @classmethod
    def bind(cls, request, call, policy):
        return replace(
            request,
            **call.classification,
            source=ToolApprovalSource.from_call(call),
            action=cls.from_call(call, policy),
        )


class ToolApprovalActionView:
    def __init__(self, value):
        self._value = value

    @property
    def tool(self):
        return self._value.get(ToolApprovalAction.tool.__name__)

    @property
    def arguments(self):
        return self._value.get(ToolApprovalAction.arguments.__name__)

    @property
    def classification(self):
        return self._value.get(ToolApprovalAction.classification.__name__)

    @property
    def policy(self):
        return self._value.get(ToolApprovalAction.policy.__name__)

    @property
    def policy_or_empty(self):
        return self._value.get(ToolApprovalAction.policy.__name__, {})

    @property
    def contract(self):
        return self._value.get(ToolApprovalAction.contract.__name__)


class ToolApprovalBindingView:
    def __init__(self, value):
        self._value = value

    @property
    def key(self):
        return self._value.get(InteractionBinding.Field.KEY.value)


class ToolApprovalRecord:
    """Shallow matching projection for mixed Engine records, never a second Reader."""

    ABSENT = None

    def __init__(self, value):
        self._value = value

    @property
    def interaction(self):
        return self._value.get(CheckpointStep.Field.INTERACTION.value, {})

    @property
    def action(self):
        return ToolApprovalActionView(self.interaction.get(InteractionRequest.action.__name__, {}))

    @property
    def is_approval(self):
        interaction = self.interaction
        return (
            self._value.get(CheckpointStep.Field.STATUS.value) == CheckpointStatus.WAITING.value
            and interaction.get(InteractionRequest.kind.__name__) == InteractionRequest.KIND_APPROVAL
            and ToolApprovalAction.tool.__name__ in interaction.get(InteractionRequest.action.__name__, {})
            and ToolApprovalAction.arguments.__name__
            in interaction.get(InteractionRequest.action.__name__, {})
        )

    @property
    def binding(self):
        return ToolApprovalBindingView(self.interaction.get(InteractionRequest.binding.__name__, {}))

    def select(self, decision):
        selected, _ = InteractionRequest.from_dict(self.interaction).select_decision(decision)
        return (
            selected.effect == InteractionOption.EFFECT_APPROVE
            if selected.effect in (InteractionOption.EFFECT_APPROVE, InteractionOption.EFFECT_DENY)
            else self.ABSENT
        )
