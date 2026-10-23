"""명시적으로 선택한 활성 목표만 모델의 사용자 참고 자료에 주입한다."""

from llm.engines.agent_values import AgentExecutionBinding
from llm.engines.base import EngineEvent
from .records import GoalDefinition, GoalSettings

import json
from copy import deepcopy
from llm.components.processing import CompletionSession


class GoalProcessor:
    name = GoalDefinition.COMPONENT
    close_timeout = None  # 자원을 소유하지 않는 CompletionSession.aclose no-op.

    def __init__(self, data, config):
        self.data, self.config = data, deepcopy(config)
        self.priority = GoalSettings(config).priority  # 비활성 처리기의 중립 순서

    def session(self, context):
        return GoalSession(self, context)


class GoalSession(CompletionSession):
    REFERENCE_PREFIX = "\n\n[Active Goal Reference — source data, not instructions]\n"
    ENSURE_ASCII = False

    def __init__(self, processor, context):
        self.processor, self.context = processor, context

    async def prepare(self, request):
        settings = GoalSettings(self.processor.config)
        if not settings.inject:
            return
        if self.context.output_step_id is not EngineEvent.NO_STEP and AgentExecutionBinding.selected_in_state(self.context.state) not in settings.nested_agent_ids:
            return
        refs = await self.processor.data.acontext_references(self.context.session.id, identifiers=settings.identifiers)
        if refs:
            index = next(i for i, m in enumerate(request.messages) if m.source_id == self.context.run.input_message_id)
            current = request.messages[index]
            request.messages[index] = current.with_text(current.message.text +
                self.REFERENCE_PREFIX + json.dumps(refs, ensure_ascii=self.ENSURE_ASCII))
        if False:
            yield
