"""Hub-owned live questions; the Tool invocation stays active until answered."""

import asyncio
from dataclasses import dataclass

from llm.core.interactions import InteractionOption, InteractionRequest
from llm.services.runtime.tools import current_tool_call


@dataclass(slots=True)
class WaitingQuestion:
    project_id: str
    session_id: str
    run_id: str
    request: InteractionRequest
    result: asyncio.Future


class QuestionBroker:
    def __init__(self):
        self.pending = {}
        self.changed = lambda: None

    async def ask(self, arguments):
        call = current_tool_call()
        if call is None or not call.session_id:
            raise RuntimeError("ask_user requires an active Run")
        request = InteractionRequest(arguments["question"],
            (InteractionOption("reply", "Reply"),), kind="input", category="hub.question",
            description=arguments.get("description", ""), input_schema={"type": "string", "minLength": 1},
            source={"choices": arguments.get("choices", [])})
        future = asyncio.get_running_loop().create_future()
        self.pending[request.id] = WaitingQuestion(call.project_id, call.session_id, call.run_id, request, future)
        self.changed()
        try:
            return {"answer": await future}
        finally:
            self.pending.pop(request.id, None)
            self.changed()

    def views(self, project_id, session_id):
        return tuple({"run_id": item.run_id, "live": True, "request": item.request.to_dict()}
                     for item in self.pending.values()
                     if (item.project_id, item.session_id) == (project_id, session_id))

    def answer(self, project_id, session_id, run_id, request, option_id, value):
        item = self.pending.get(request.id)
        if item is None or (item.project_id, item.session_id, item.run_id) != (project_id, session_id, run_id):
            raise ValueError("Question is no longer waiting")
        if item.result.done():
            raise ValueError("Question already answered")
        response = request.respond(option_id, value=value)
        answer = response.decision(item.request)
        item.result.set_result(answer)
        self.pending.pop(request.id, None)
        self.changed()

    def close(self):
        for item in tuple(self.pending.values()):
            item.result.cancel()

