"""Hub-owned live questions; the Tool invocation stays active until answered."""

import asyncio
from dataclasses import dataclass

from llm.core.interactions import InteractionOption, InteractionRequest
from llm.services.runtime.tools import current_tool_call
from ...model import QuestionPresentation, SessionSummary
from .question_values import QuestionArguments, QuestionTool, QuestionReply


async def pending_requests(handle):
    """Read unanswered input through public APIs under one ownership scope.

    A durable response no longer needs an input form, even while its resume
    request is queued. Unanswered requests still use the backend's lifecycle,
    expiry and cancellation checks; this observation never authorizes execution.
    """
    def read():
        with handle.app.project_manager.ownership.scope():
            requests = handle.interactions()
            if not requests:
                return ()
            answered = {response.request_id for response in handle.interaction_responses()}
            if all(request.id in answered for request in requests):
                return ()
            return tuple(handle.interactions(pending_only=True))

    return await asyncio.to_thread(read)


def waiting_status(questions):
    return (
        SessionSummary.WAITING_APPROVAL
        if any(
            QuestionPresentation.observe(item).request.kind
            == InteractionRequest.KIND_APPROVAL
            for item in questions
        )
        else SessionSummary.WAITING_INPUT
    )


@dataclass(slots=True)
class WaitingQuestion:
    project_id: str
    session_id: str
    run_id: str
    request: InteractionRequest
    result: asyncio.Future


class QuestionBroker:
    NO_CALL = None
    NO_PENDING = None
    ACTIVE_RUN_REQUIRED = "ask_user requires an active Run"
    NOT_WAITING = "Question is no longer waiting"
    ALREADY_ANSWERED = "Question already answered"

    def __init__(self):
        self.pending = {}
        self.changed = lambda: None

    async def ask(self, arguments):
        call = current_tool_call()
        if call is self.NO_CALL or not call.session_id:
            raise RuntimeError(self.ACTIVE_RUN_REQUIRED)
        values = QuestionArguments(arguments)
        request = InteractionRequest(
            values.question,
            (InteractionOption(QuestionTool.REPLY_ID, QuestionTool.REPLY_LABEL),),
            kind=InteractionRequest.KIND_INPUT,
            category=QuestionTool.CATEGORY,
            description=values.description,
            input_schema=QuestionTool.input_schema(),
            source=QuestionTool.source(values),
        )
        future = asyncio.get_running_loop().create_future()
        self.pending[request.id] = WaitingQuestion(
            call.project_id, call.session_id, call.run_id, request, future
        )
        self.changed()
        try:
            return QuestionReply(await future).to_record()
        finally:
            self.pending.pop(request.id, self.NO_PENDING)
            self.changed()

    def views(self, project_id, session_id):
        return tuple(
            QuestionPresentation.live_request(item.run_id, item.request)
            for item in self.pending.values()
            if (item.project_id, item.session_id) == (project_id, session_id)
        )

    def answer(self, project_id, session_id, run_id, request, option_id, value):
        item = self.pending.get(request.id)
        if item is self.NO_PENDING or (
            item.project_id,
            item.session_id,
            item.run_id,
        ) != (project_id, session_id, run_id):
            raise ValueError(self.NOT_WAITING)
        if item.result.done():
            raise ValueError(self.ALREADY_ANSWERED)
        response = request.respond(option_id, value=value)
        answer = response.decision(item.request)
        item.result.set_result(answer)
        self.pending.pop(request.id, self.NO_PENDING)
        self.changed()

    def close(self):
        for item in tuple(self.pending.values()):
            item.result.cancel()
