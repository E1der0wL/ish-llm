"""Hub response protocol through llm's public completion-processing contract."""

from llm.core.model_protocol import ModelRole
from llm.components.processing import CompletionSession
from .input_context import HubInputContext
from .followup import INSTRUCTION, split_followup
from ..tools.advice import INSTRUCTION as RISK_INSTRUCTION
from .completion_review import TaskCompletion
from ..tools.application import HubApplicationTools


class FollowupSession(CompletionSession):
    async def prepare(self, request):
        for index, message in enumerate(request.messages):
            if message.message.role == ModelRole.ASSISTANT and isinstance(
                message.message.content, str
            ):
                request.messages[index] = message.with_text(
                    split_followup(message.message.text).content
                )
        # Extra model context is not a Conversation Message and has no persisted
        # source ID. Checkpoints/approvals can therefore resume unchanged.
        HubInputContext.insert(
            request, INSTRUCTION + HubInputContext.SEPARATOR + RISK_INSTRUCTION
        )
        HubInputContext.insert(request, HubApplicationTools.DISCOVERY_INSTRUCTION)
        if any(tool.name == TaskCompletion.NAME for tool in request.tools):
            HubInputContext.insert(request, TaskCompletion.INSTRUCTION)
        if False:
            yield

    async def review_final(self, observation):
        return TaskCompletion.review(observation)


class FollowupProcessor:
    name = "hub.followup"
    priority = 1000
    close_timeout = None
    review_revision = TaskCompletion.REVISION

    def session(self, context):
        return FollowupSession()
