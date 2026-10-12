"""Hub response protocol through llm's public completion-processing contract."""

from dataclasses import replace
from llm.components.processing import CompletionMessage, CompletionSession
from .followup import INSTRUCTION, split_followup
from ..tools.advice import INSTRUCTION as RISK_INSTRUCTION


class FollowupSession(CompletionSession):
    async def prepare(self, request):
        for index, message in enumerate(request.messages):
            if message.value.get("role") == "assistant" and isinstance(message.value.get("content"), str):
                request.messages[index] = replace(message, value={**message.value,
                    "content": split_followup(message.value["content"])[0]})
        # Extra model context is not a Conversation Message and has no persisted
        # source ID. Checkpoints/approvals can therefore resume unchanged.
        index = 1 if request.messages and request.messages[0].value.get("role") == "system" else 0
        request.messages.insert(index, CompletionMessage({"role": "system", "content": INSTRUCTION + "\n\n" + RISK_INSTRUCTION}))
        if False:
            yield


class FollowupProcessor:
    name = "hub.followup"
    priority = 1000
    close_timeout = None

    def session(self, context):
        return FollowupSession()
