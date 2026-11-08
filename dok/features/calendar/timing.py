"""One-time date and whole-hour defaults for new events."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


class EventTiming:
    GAP = timedelta(hours=1)

    @classmethod
    def end_after(cls, start: datetime) -> datetime:
        return start + cls.GAP

    @classmethod
    def initial(cls, day: date, timezone: str | None, now: datetime | None = None) -> tuple[datetime, datetime]:
        current = now if now is not None else datetime.now(ZoneInfo(timezone) if timezone else None)
        base = datetime.combine(day, current.time().replace(tzinfo=None, minute=0, second=0, microsecond=0))
        try:
            start = base + cls.GAP
            return start, cls.end_after(start)
        except OverflowError:
            end = datetime.max.replace(minute=0, second=0, microsecond=0)
            return end - cls.GAP, end
