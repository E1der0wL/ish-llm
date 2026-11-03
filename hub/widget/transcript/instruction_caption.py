"""Instruction receipt wording is presentation, never a delivery decision."""

from llm.core.steering import InstructionStatus
from llm.core.steering_events import SteeringReason
from ...locales.keys import MessageKey
from ...backend.execution.tool_details import ValuePreview


def reason_caption(reason, t):
    messages = {SteeringReason.COMPLETED: MessageKey.INSTRUCTION_ENDED,
                SteeringReason.NODE_NOT_REACHED: MessageKey.INSTRUCTION_NOT_REACHED,
                'withdrawn': MessageKey.INSTRUCTION_WITHDRAWN,
                'superseded': MessageKey.INSTRUCTION_SUPERSEDED}
    return t(messages[reason]) if reason in messages else ValuePreview.literal(t.status(reason), 300)


def instruction_caption(item, t):
    if len(item.targets) > 1:
        return ''
    return (t(MessageKey.INSTRUCTION_PENDING) if item.status == InstructionStatus.PENDING
            else t.status(item.status) if item.status else '')


def target_captions(item, t):
    if len(item.targets) > 1:
        return tuple(' • '.join(part for part in (
            target.name, t(MessageKey.INSTRUCTION_PENDING) if target.status == InstructionStatus.PENDING
            else t.status(target.status), reason_caption(target.reason, t)) if part) for target in item.targets)
    reason = item.targets[0].reason if item.targets else item.reason
    return (t(MessageKey.INSTRUCTION_REASON) + ': ' + reason_caption(reason, t),) if reason else ()
