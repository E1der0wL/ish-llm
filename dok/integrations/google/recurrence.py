"""Google recurrence wire mapping. Unsupported rules remain opaque and unchanged."""
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from ...features.calendar.contracts import ExternalRecurrence
from ...features.calendar.recurrence import Recurrence, RepeatEnd, RepeatUnit


class GoogleRecurrence:
    FIELD = "recurrence"
    PREFIX = "RRULE:"
    FREQ, INTERVAL, COUNT, UNTIL = "FREQ", "INTERVAL", "COUNT", "UNTIL"
    MONTH, DAY, SETPOS = "BYMONTH", "BYMONTHDAY", "BYSETPOS"
    FREQUENCIES = {RepeatUnit.DAYS: "DAILY", RepeatUnit.WEEKS: "WEEKLY",
                   RepeatUnit.MONTHS: "MONTHLY", RepeatUnit.YEARS: "YEARLY"}

    @classmethod
    def encode(cls, spec, start, zone):
        if spec.rule is None:
            return list(spec.encoded)
        rule = spec.rule
        if rule.unit == RepeatUnit.NONE:
            return []
        origin = start.date() if isinstance(start, datetime) else start
        rule.validate_start(origin)
        values = {cls.FREQ: cls.FREQUENCIES[rule.unit], cls.INTERVAL: str(rule.interval)}
        if rule.unit in (RepeatUnit.MONTHS, RepeatUnit.YEARS) and origin.day > 28:
            # Match dok's shorter-month clamping instead of RFC's skip-invalid-day.
            values[cls.DAY] = ",".join(str(day) for day in range(28, origin.day + 1))
            values[cls.SETPOS] = "-1"
        if rule.unit == RepeatUnit.YEARS:
            values[cls.MONTH] = str(origin.month)
        if rule.end == RepeatEnd.DATE:
            if isinstance(start, datetime):
                if not zone:
                    raise ValueError("A recurring timed event requires an IANA timezone")
                until = datetime.combine(rule.until, time(23, 59, 59), ZoneInfo(zone)).astimezone(timezone.utc)
                values[cls.UNTIL] = until.strftime("%Y%m%dT%H%M%SZ")
            else:
                values[cls.UNTIL] = rule.until.strftime("%Y%m%d")
        elif rule.count:
            values[cls.COUNT] = str(rule.count)
        return [cls.PREFIX + ";".join(f"{key}={value}" for key, value in values.items())]

    @classmethod
    def decode(cls, rows, start, zone):
        if not isinstance(rows, (list, tuple)) or any(not isinstance(row, str) for row in rows):
            raise ValueError("Invalid Google recurrence")
        if not rows:
            return ExternalRecurrence()
        opaque = ExternalRecurrence(None, tuple(rows))
        if len(rows) != 1 or not rows[0].startswith(cls.PREFIX):
            return opaque
        try:
            pairs = [part.split("=", 1) for part in rows[0][len(cls.PREFIX):].split(";")]
            values = dict(pairs)
            if len(values) != len(pairs):
                return opaque
            unit = next(unit for unit, value in cls.FREQUENCIES.items() if value == values[cls.FREQ])
            until = values.get(cls.UNTIL)
            last = None
            if until:
                if isinstance(start, datetime):
                    last = datetime.strptime(until, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                    last = last.astimezone(ZoneInfo(zone) if zone else start.tzinfo).date()
                else:
                    last = datetime.strptime(until, "%Y%m%d").date()
            rule = Recurrence(unit, int(values.get(cls.INTERVAL, 1)), RepeatEnd.DATE if until else RepeatEnd.COUNT,
                              int(values.get(cls.COUNT, 0)), last)
            encoded = cls.encode(ExternalRecurrence(rule), start, zone)
            expected = dict(part.split("=", 1) for part in encoded[0][len(cls.PREFIX):].split(";"))
            # An omitted INTERVAL=1 / redundant BYMONTH is semantically identical.
            values.setdefault(cls.INTERVAL, "1")
            if unit == RepeatUnit.YEARS:
                values.setdefault(cls.MONTH, str(start.month))
            if values != expected:
                return opaque
            return ExternalRecurrence(rule, tuple(rows))
        except (ValueError, KeyError, StopIteration, OverflowError):
            return opaque
