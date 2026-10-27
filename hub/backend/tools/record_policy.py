"""Hub approval choices for reversible internal records; never external effects."""

from dataclasses import replace
from llm.components.tools import ToolRegistry
from llm.components.goals.tool_values import GoalToolContract
from .resource_contracts import GoalAuthoring


class InternalRecords:
    REVISION = 'hub-internal-records-v1'
    GOALS = frozenset((GoalAuthoring.CREATE_NAME,GoalToolContract.PROGRESS_NAME,
                      GoalToolContract.LINK_NAME,GoalAuthoring.COMPLETE_NAME))
    PROGRESS = 'Update progress/next actions for an existing Goal using its current version. Internal bookkeeping does not require approval.'

    @classmethod
    def select(cls, registry, names):
        return ToolRegistry(replace(tool,
            description=cls.PROGRESS if tool.name == GoalToolContract.PROGRESS_NAME else tool.description,
            contract=replace(tool.contract,
            revision=cls.REVISION, approval_required=False)) if tool.name in names else tool
            for tool in (registry.get(name) for name in registry.names()))
