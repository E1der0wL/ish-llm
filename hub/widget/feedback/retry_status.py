"""Local retry countdown for the assistant header; no storage reads."""

from hub.locales.keys import MessageKey, MessagePattern

from math import ceil
from time import monotonic
from ...model import RetrySummary


def retry_text(retry, t):
    if retry is None:
        return ""
    if retry.kind == RetrySummary.RECOVERY:
        return t(MessagePattern.recovery(retry.phase), attempt=retry.attempt, maximum=retry.maximum,
                 progress=(" · " + retry.name) if retry.name else "")
    target = t(MessageKey.RETRY_TOOL, name=retry.name) if retry.kind == RetrySummary.TOOL else t(MessageKey.RETRY_PROVIDER)
    values = dict(target=target, attempt=retry.attempt, maximum=retry.maximum, next_attempt=retry.attempt + 1)
    if retry.phase == RetrySummary.REPAIRING:
        return t(MessageKey.RETRY_REPAIRING, **values)
    if retry.retry_at is not None:
        return t(MessageKey.RETRY_WAIT, **values, seconds=max(0, ceil(retry.retry_at - monotonic())))
    return t(MessageKey.RETRY_ACTIVE, **values)
