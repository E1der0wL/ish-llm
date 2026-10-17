"""Compact dated usage projections for the profile chart."""

from datetime import datetime


def daily_usage(calls):
    days = {}
    for call in calls:
        stamp = call.get("started_at")
        if not stamp:
            continue
        day = datetime.fromisoformat(stamp).astimezone().date().isoformat()
        entry = days.setdefault(day, {"date": day, "tokens": 0, "unknown": 0})
        tokens = (call.get("usage") or {}).get("total_tokens")
        if call.get("usage_complete") and type(tokens) is int:
            entry["tokens"] += tokens
        else:
            entry["unknown"] += 1
    return [days[key] for key in sorted(days)]
