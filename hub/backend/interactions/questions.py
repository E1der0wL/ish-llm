"""Public question observations and explicit answer delivery; llm owns durability."""

import json

from llm.core.interactions import InteractionRequest, InteractionStatus
from ...model import QuestionPresentation, SessionSummary


async def pending_requests(handle):
    return tuple(view.request for view in await handle.ainteraction_views()
                 if view.status == InteractionStatus.PENDING and view.response is None)


def blocking_questions(questions):
    return tuple(item for item in questions if not QuestionPresentation.observe(item).nonblocking)


def waiting_status(questions):
    return (SessionSummary.WAITING_APPROVAL if any(
        QuestionPresentation.observe(item).request.kind == InteractionRequest.KIND_APPROVAL
        for item in questions) else SessionSummary.WAITING_INPUT)


class QuestionAnswers:
    """No queues, Future-based answers, approval decisions or retry engine here."""

    @staticmethod
    async def observe(run):
        result = []
        for view in await run.aquestions():
            delivery = await run.get_delivery(view.request.id) if view.response else None
            result.append(QuestionPresentation.nonblocking_request(
                run.id, view.request, response=view.response,
                delivery=delivery.to_dict() if delivery is not None else None, status=view.status))
        return tuple(result)

    @staticmethod
    async def save(run, request, option_id, value):
        return await run.aanswer_question(request.respond(option_id, value=value))

    @staticmethod
    async def deliver(run, request_id, *, mode, run_id=None, targets=None, recipients=None):
        views = await run.aquestions()
        view = next(item for item in views if item.request.id == request_id)
        if view.response is None:
            raise ValueError('Save the answer before delivering it')
        # Stable application text binds repeated clicks to the same native receipt.
        answer = view.response.decision(view.request)
        text = '\n'.join((view.request.title, view.request.description or '',
                          answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)))
        if mode == 'steer':
            await run.deliver_answer(request_id, text, mode=mode, run_id=run_id, targets=targets)
        elif mode == 'submit':
            await run.deliver_answer(request_id, text, mode=mode, recipients=recipients)
        else:
            raise ValueError('Choose an explicit answer delivery mode')
        # The two routes return different handle types. The shared persisted
        # receipt is their public observation contract, never a fabricated ack.
        receipt = await run.get_delivery(request_id)
        if receipt is None:
            raise ValueError('Answer delivery receipt is unavailable; reload the question')
        return receipt
