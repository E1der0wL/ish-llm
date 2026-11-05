"""Instruction receipt wording is presentation, never a delivery decision."""

from llm.core.steering import InstructionStatus
from llm.core.steering_events import SteeringReason
from ...locales.keys import MessageKey
from ...backend.execution.tool_details import ValuePreview
from ..captions import Caption


REASON_MESSAGES = {SteeringReason.COMPLETED: MessageKey.INSTRUCTION_ENDED,
                  SteeringReason.NODE_NOT_REACHED: MessageKey.INSTRUCTION_NOT_REACHED,
                  SteeringReason.WITHDRAWN: MessageKey.INSTRUCTION_WITHDRAWN,
                  SteeringReason.SUPERSEDED: MessageKey.INSTRUCTION_SUPERSEDED}
REASON_CHARACTERS = 300
REASON_SEPARATOR = ': '


def reason_caption(reason, t):
    return t(REASON_MESSAGES[reason]) if reason in REASON_MESSAGES else ValuePreview.literal(str(reason), REASON_CHARACTERS)


def instruction_caption(item, t):
    if len(item.targets) > 1:
        return ''
    return t.status(item.status,
        key=MessageKey.INSTRUCTION_PENDING if item.status == InstructionStatus.PENDING else None)


def target_captions(item, t):
    if len(item.targets) > 1:
        return tuple(Caption(target.name, t.status(target.status,
            key=MessageKey.INSTRUCTION_PENDING if target.status == InstructionStatus.PENDING else None),
            (reason_caption(target.reason, t),)).render() for target in item.targets)
    reason = item.targets[0].reason if item.targets else item.reason
    return (t(MessageKey.INSTRUCTION_REASON) + REASON_SEPARATOR + reason_caption(reason, t),) if reason else ()
