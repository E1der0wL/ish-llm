"""Hub response protocol through llm's public completion-processing contract."""

from llm.core.model_protocol import ModelRole
from llm.core.schema import SchemaKeyword
from llm.components.processing import CompletionSession
from .input_context import HubInputContext
from .followup import INSTRUCTION, split_followup
from ..tools.advice import INSTRUCTION as RISK_INSTRUCTION, RiskAssessment
from .completion_review import TaskCompletion, ConversationalInput
from .working_context import WorkingContext
from ..tools.application import HubApplicationTools
from ...asset.guides import WORK_CYCLE
from ...config.defaults import HubDefaults


class FollowupSession(CompletionSession):
    def __init__(self, context, postprocess=False, tool_context_chars=HubDefaults.TOOL_CONTEXT_CHARS):
        self.context = context
        self.postprocess = postprocess
        self.tool_context_chars = tool_context_chars
        self.request_id = context.run.input_message_id if context is not None else None
        self.conversational = bool(context is not None and context.checkpoint is None
            and context.output_step_id is None and any(
                message.id == self.request_id and ConversationalInput.matches(message.content)
                for message in context.messages))

    async def prepare(self, request):
        if self.conversational and any(
                message.continuation and message.source_id is not None
                and message.source_id != self.request_id
                and message.message.role == ModelRole.USER for message in request.messages):
            self.conversational = False
        for index, message in enumerate(request.messages):
            if message.message.role == ModelRole.ASSISTANT and isinstance(
                message.message.content, str
            ):
                request.messages[index] = message.with_text(
                    split_followup(message.message.text).content
                )
        # Extra model context is not a Conversation Message and has no persisted
        # source ID. Checkpoints/approvals can therefore resume unchanged.
        if self.conversational:
            HubInputContext.insert(request, ConversationalInput.INSTRUCTION)
        else:
            working = any(item.message.calls for item in request.messages)
            instructions = [TaskCompletion.ROUTING, INSTRUCTION]
            if self.postprocess:
                instructions.append(TaskCompletion.BACKGROUND)
            if working:
                if any(RiskAssessment.FIELD in tool.parameters.get(SchemaKeyword.PROPERTIES, {}) for tool in request.tools):
                    instructions.append(RISK_INSTRUCTION)
                if any(tool.name == HubApplicationTools.HELP for tool in request.tools):
                    instructions.append(HubApplicationTools.DISCOVERY_INSTRUCTION)
                if any(tool.name == TaskCompletion.NAME for tool in request.tools):
                    instructions.extend((TaskCompletion.INSTRUCTION, WORK_CYCLE))
            HubInputContext.insert(request, HubInputContext.SEPARATOR.join(instructions))
        async for event in WorkingContext.prepare(self.context, request,
                                                  max_tool_chars=self.tool_context_chars):
            yield event

    async def review_final(self, observation):
        return TaskCompletion.review(observation)


class FollowupProcessor:
    name = "hub.followup"
    priority = 1000
    close_timeout = None
    review_revision = TaskCompletion.REVISION

    def __init__(self, *, postprocess=False, tool_context_chars=HubDefaults.TOOL_CONTEXT_CHARS):
        self.postprocess = postprocess
        self.tool_context_chars = tool_context_chars

    @classmethod
    def for_project(cls, project, *, postprocess=False, tool_context_chars=HubDefaults.TOOL_CONTEXT_CHARS):
        from llm.components.memory import MemoryComponent
        from llm.components.memory.configuration import MemoryProcessingConfig

        processor = cls(postprocess=postprocess, tool_context_chars=tool_context_chars)
        if MemoryComponent.name in project.components:
            settings = project.config.parameter_targets.component(MemoryComponent.name)
            priority = MemoryProcessingConfig.from_config_section(settings.to_dict()).priority_or(None)
            if priority is not None:
                processor.priority = max(cls.priority, priority + 1)
        return processor

    def session(self, context):
        return FollowupSession(context, self.postprocess, self.tool_context_chars)
