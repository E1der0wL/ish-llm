"""Deadline planning and admission. Calendar storage persists the returned claims."""
from dataclasses import dataclass
from datetime import datetime, timezone

from .commands import CommandOccurrence
from .models import CalendarEvent, ReminderOccurrence


@dataclass(frozen=True, slots=True)
class DeliveryPlan:
    reminders: tuple[ReminderOccurrence, ...] = ()
    commands: tuple[CommandOccurrence, ...] = ()
    next_at: datetime | None = None


class CalendarScheduler:
    """Schedules local recurrence and already-expanded provider occurrences equally.

    Receipts are claimed before callers perform effects. A fresh command watch
    never executes an elapsed occurrence, including after process restart.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._watches = {}
        self._plan = None

    def plan(self, events, now, receipts, command_receipts, *, deliver, commands):
        now = now.astimezone(timezone.utc)
        command_events = tuple(event for event in events if commands and event.command.strip())
        self._watches = {event.command_key: self._watches.get(event.command_key, now) for event in command_events}

        def key():
            return (events, tuple(sorted(receipts.items())), tuple(sorted(command_receipts.items())),
                    tuple(sorted(self._watches.items())), CalendarEvent.recurrence_context(), deliver, commands)

        cached = self._plan
        if cached is not None and cached[0] == key() and now >= cached[1] and (cached[2] is None or now < cached[2]):
            return DeliveryPlan(next_at=cached[2])
        due, future, command_due = [], [], []
        if deliver:
            for event in events:
                for alarm in event.alarms:
                    occurrence = event.latest_due(now, alarm)
                    previous = receipts.get(event.reminder_key(alarm))
                    pending = previous is None if event.delivery_once else occurrence is not None and occurrence.stamp > (previous or "")
                    if occurrence and pending:
                        receipts[event.reminder_key(alarm)] = occurrence.stamp
                        due.append(occurrence)
                    following = event.next_due(now, alarm)
                    if following is not None:
                        future.append(following)
        for event in command_events:
            occurrence = CommandOccurrence.latest(event, now)
            previous = command_receipts.get(event.command_key)
            pending = previous is None if event.delivery_once else occurrence is not None and occurrence.stamp > (previous or "")
            if occurrence and pending and occurrence.at > self._watches[event.command_key]:
                command_receipts[event.command_key] = occurrence.stamp
                command_due.append(occurrence)
            following = CommandOccurrence.next_at(event, now)
            if following is not None:
                future.append(following)
        next_at = min(future) if future else None
        self._plan = key(), now, next_at
        return DeliveryPlan(tuple(sorted(due, key=lambda item: item.at)),
                            tuple(sorted(command_due, key=lambda item: item.at)), next_at)
