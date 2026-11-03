"""Native durable input requests project into the same question card as ask_user."""

from llm.core.interactions import InteractionRequest
from ...model import TraceItem
from ...locales.keys import MessageKey
from .tool_details import ValuePreview, TextPreview


class QuestionTrace:
    PREFIX = 'question:'

    @classmethod
    def identifier(cls, request_id):
        return cls.PREFIX + request_id

    @classmethod
    def project(cls, views):
        result = []
        for view in views:
            request = view.request
            if request.kind == InteractionRequest.KIND_APPROVAL or request.action:
                continue
            details = [(MessageKey.QUESTION_HEADING, ValuePreview.render(request.title))]
            if request.description:
                details.append((MessageKey.CARD_DESCRIPTION, ValuePreview.render(request.description)))
            if view.response is not None:
                details.append((MessageKey.CARD_ANSWER, TextPreview.render(
                    view.response.decision(request), TextPreview.OUTPUT_KEYS)))
            result.append(TraceItem(cls.identifier(request.id), TraceItem.QUESTION, request.title,
                'answered' if view.response is not None else 'waiting_input' if view.can_respond else view.status,
                request.created_at, details=tuple(details)))
        return tuple(result)
