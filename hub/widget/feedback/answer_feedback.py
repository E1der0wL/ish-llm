"""Local answer acknowledgement, without changing durable execution states."""

from llm.core.models import MessageStatus, MessageRole, StepStatus
from dataclasses import dataclass, replace
from typing import ClassVar
from llm.services.runtime.tool_approval_records import ToolApprovalSourceView
from ...model import HubSnapshot, StepSummary, QuestionPresentation, TraceItem, QuestionSource, ReasoningMessage
from ...locales.keys import MessageKey
from ...backend.execution.tool_details import TextPreview


@dataclass(slots=True)
class AnswerFeedback:
    NO_ITEM: ClassVar[None] = None
    NO_RUN: ClassVar[None] = None
    NO_TOOL_ID: ClassVar[str] = ""
    NO_ASSISTANT_ID: ClassVar[str] = ""
    ACKNOWLEDGED: ClassVar[bool] = True
    UNACKNOWLEDGED: ClassVar[bool] = False
    WAITING: ClassVar[bool] = True
    SETTLED: ClassVar[bool] = False
    SENDING: ClassVar[str] = "answer_sending"
    DENIED: ClassVar[str] = "denied"
    APPROVED: ClassVar[str] = "approved"
    ANSWERED: ClassVar[str] = "answered"

    identity: tuple
    item: dict | None
    tool_id: str
    assistant_id: str
    denied: bool
    approval: bool
    acknowledged: bool = UNACKNOWLEDGED
    pending: bool = WAITING
    answer: str = ""

    @classmethod
    def capture(cls, question, option_id, value=None):
        view, item = question.view, question.item
        snapshot = HubSnapshot.last_observed(view)
        run = snapshot.run if snapshot else cls.NO_RUN
        observation = QuestionPresentation.observe(item)
        source = (ToolApprovalSourceView(observation.request.source).step_id
                  or observation.request.source.get(QuestionSource.STEP_ID, ''))
        tool_id = (
            next(
                (step.id for step in run.steps if source and step.source_id == source),
                cls.NO_TOOL_ID,
            )
            if run and run.id == observation.run_id
            else cls.NO_TOOL_ID
        )
        messages = view.sessions[view.selected].messages
        assistant_id = next(
            (m.id for m in reversed(messages) if m.role == MessageRole.ASSISTANT
             and (m.run_id == observation.run_id or not m.run_id)),
            cls.NO_ASSISTANT_ID,
        )
        if not tool_id and source:
            tool_id = next((step.id for m in messages
                if m.id == ReasoningMessage.identifier(assistant_id)
                for step in m.tools if step.source_id == source), source)
        if not observation.request.action:
            from ...backend.execution.question_trace import QuestionTrace
            tool_id = QuestionTrace.identifier(observation.request.id)
        denied = observation.request.denies(option_id)
        return cls(
            question.identity,
            item,
            tool_id,
            assistant_id,
            denied,
            bool(observation.request.action),
            answer=TextPreview.render(value, TextPreview.OUTPUT_KEYS) if value is not None else "",
        )

    def finish_pending(self):
        self.pending = self.SETTLED

    def acknowledge(self):
        self.acknowledged = self.ACKNOWLEDGED

    def release_item(self):
        self.item = self.NO_ITEM

    @property
    def status(self):
        if not self.acknowledged:
            return self.SENDING
        return (
            self.DENIED
            if self.denied
            else self.APPROVED if self.approval else self.ANSWERED
        )


def project_answers(responses, messages):
    """Single pass, retaining the cached message when only another row changes."""
    tools_by_id = {r.tool_id: r for r in responses if r.tool_id}
    headers = {r.assistant_id: r.status for r in responses if r.assistant_id}
    if not headers and not tools_by_id:
        return messages

    def tool(step):
        response = tools_by_id.get(step.id)
        status = response.status if response else None
        if (response and isinstance(step, TraceItem) and step.kind == TraceItem.QUESTION
                and not any(key == MessageKey.CARD_ANSWER for key, _ in step.details)
                and step.status not in (StepStatus.FAILED, StepStatus.INTERRUPTED, StepStatus.CANCELLED)):
            return replace(step, status=status, details=(*step.details, (MessageKey.CARD_ANSWER, response.answer)))
        return (
            replace(step, status=status)
            if status and step.status == StepSummary.WAITING_APPROVAL
            else step
        )

    result = []
    for message in messages:
        tools = tuple(tool(step) for step in message.tools)
        trace = tuple(tool(step) for step in message.trace)
        phase = (
            headers.get(message.id, message.phase)
            if message.status == MessageStatus.PAUSED
            else message.phase
        )
        result.append(
            replace(message, tools=tools, trace=trace, phase=phase)
            if (tools, trace, phase) != (message.tools, message.trace, message.phase)
            else message
        )
    return tuple(result)
