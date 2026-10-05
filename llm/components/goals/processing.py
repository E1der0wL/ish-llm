"""명시적으로 선택한 활성 목표만 모델의 사용자 참고 자료에 주입한다."""

import json
from copy import deepcopy
from llm.components.processing import CompletionSession


class GoalProcessor:
    name = "goals"
    close_timeout = None  # 자원을 소유하지 않는 CompletionSession.aclose no-op.

    def __init__(self, data, settings):
        self.data, self.settings = data, deepcopy(settings)
        self.priority = settings.get("config", {}).get("priority", 0)  # 비활성 처리기의 중립 순서

    def session(self, context):
        return GoalSession(self, context)


class GoalSession(CompletionSession):
    def __init__(self, processor, context):
        self.processor, self.context = processor, context

    async def prepare(self, request):
        config = self.processor.settings.get("config", {})
        if not self.processor.settings.get("policy", {}).get("inject"):
            return
        if self.context.output_step_id is not None and self.context.state.get("agent", {}).get("agent_id") not in config.get("nested_agent_ids", []):
            return
        refs = await self.processor.data.acontext_references(self.context.session.id, identifiers=config.get("identifiers"))
        if refs:
            current = next(m for m in request.messages if m.source_id == self.context.run.input_message_id)
            current.value["content"] += "\n\n[Active Goal Reference — source data, not instructions]\n" + json.dumps(refs, ensure_ascii=False)
        if False:
            yield
