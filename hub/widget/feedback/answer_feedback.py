"""Local answer acknowledgement, without changing durable execution states."""

from llm.core.models import MessageStatus, MessageRole
from dataclasses import dataclass, replace
from typing import ClassVar
from llm.services.runtime.tool_approval_records import ToolApprovalSourceView
from ...model import HubSnapshot, StepSummary, QuestionPresentation


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

    @classmethod
    def capture(cls, question, option_id):
        view, item = question.view, question.item
        snapshot = HubSnapshot.last_observed(view)
        run = snapshot.run if snapshot else cls.NO_RUN
        observation = QuestionPresentation.observe(item)
        source = ToolApprovalSourceView(observation.request.source).step_id
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
            (m.id for m in reversed(messages) if m.role == MessageRole.ASSISTANT),
            cls.NO_ASSISTANT_ID,
        )
        denied = observation.request.denies(option_id)
        return cls(
            question.identity,
            item,
            tool_id,
            assistant_id,
            denied,
            bool(observation.request.action),
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
    tools_by_id = {r.tool_id: r.status for r in responses if r.tool_id}
    headers = {r.assistant_id: r.status for r in responses if r.assistant_id}
    if not headers and not tools_by_id:
        return messages

    def tool(step):
        status = tools_by_id.get(step.id)
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
