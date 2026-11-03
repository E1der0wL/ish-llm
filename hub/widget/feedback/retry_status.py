"""Local retry countdown for the assistant header; no storage reads."""

from dataclasses import dataclass, fields
from math import ceil
from time import monotonic
from hub.locales.keys import MessageKey, MessagePattern
from ...model import RetrySummary
from ..captions import Caption


class RetryLabel:
    NO_RETRY = None
    EMPTY = ""
    MINIMUM_REMAINING = 0


@dataclass(frozen=True, slots=True)
class RetryLabelArguments:
    target: str
    attempt: int
    maximum: int
    next_attempt: int

    def to_record(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


def retry_text(retry, t):
    if retry is RetryLabel.NO_RETRY:
        return RetryLabel.EMPTY
    if retry.kind == RetrySummary.RECOVERY:
        return t.status(
            retry.phase, key=MessagePattern.recovery(retry.phase),
            attempt=retry.attempt,
            maximum=retry.maximum,
            progress=(
                (Caption.SEPARATOR + retry.name)
                if retry.name
                else RetryLabel.EMPTY
            ),
        )
    target = (
        t(MessageKey.RETRY_TOOL, name=retry.name)
        if retry.kind == RetrySummary.TOOL
        else t(MessageKey.RETRY_PROVIDER)
    )
    values = RetryLabelArguments(
        target, retry.attempt, retry.maximum, RetrySummary.next_attempt(retry)
    ).to_record()
    if retry.phase == RetrySummary.REPAIRING:
        return t.status(retry.phase, key=MessageKey.RETRY_REPAIRING, **values)
    if retry.retry_at is not RetrySummary.NO_RETRY_AT:
        return t.status(
            RetrySummary.WAITING, key=MessageKey.RETRY_WAIT,
            **values,
            seconds=max(
                RetryLabel.MINIMUM_REMAINING, ceil(retry.retry_at - monotonic())
            )
        )
    return t.status(RetrySummary.RETRYING, key=MessageKey.RETRY_ACTIVE, **values)
