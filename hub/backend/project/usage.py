"""Compact dated usage projections for the profile chart."""

from datetime import datetime
from dataclasses import dataclass, asdict
from llm.core.usage import UsageReceipt


@dataclass
class DailyUsage:
    date: str
    tokens: int = 0
    unknown: int = 0

    def include(self, receipt):
        tokens = receipt.known_tokens
        if tokens is None:
            self.unknown += 1
        else:
            self.tokens += tokens

    def to_dict(self):
        return asdict(self)


def daily_usage(calls):
    days = {}
    for call in calls:
        receipt = UsageReceipt.from_record(call)
        stamp = receipt.started_at
        if not stamp:
            continue
        day = datetime.fromisoformat(stamp).astimezone().date().isoformat()
        days.setdefault(day, DailyUsage(day)).include(receipt)
    return [days[key].to_dict() for key in sorted(days)]
