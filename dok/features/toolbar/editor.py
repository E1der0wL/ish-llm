"""Typed prompt status and bounded decimal aggregation; no host dependency."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
from enum import StrEnum
import re
from ...locales import Locale, Message as M


class InputField(StrEnum):
    LENGTH = "length"
    LINES = "lines"
    LINE = "line"
    COLUMN = "column"
    POSITION = "position"
    MODE = "mode"
    ENCODING = "encoding"

    def label(self, locale=Locale()):
        return locale.text({self.LENGTH: M.LENGTH, self.LINES: M.LINES, self.LINE: M.LINE,
            self.COLUMN: M.COLUMN, self.POSITION: M.POSITION, self.MODE: M.MODE, self.ENCODING: M.ENCODING}[self])


class NumberScope(StrEnum):
    SELECTION = "selection"
    INPUT = "input"


@dataclass(frozen=True, slots=True)
class EditorSnapshot:
    text: str = ""
    length: int = 0
    lines: int = 1
    line: int = 1
    column: int = 1
    position: int = 1
    mode: str = "Emacs"
    encoding: str = ""
    selection: tuple[tuple[int, int], ...] = ()

    def value(self, field: InputField) -> str:
        return str(getattr(self, InputField(field).value))

    def ranges(self, scope: NumberScope):
        return ((0, len(self.text)),) if scope == NumberScope.INPUT else self.selection


@dataclass(frozen=True, slots=True)
class NumberSummary:
    count: int = 0
    total: Decimal = Decimal(0)
    average: Decimal = Decimal(0)
    limited: bool = False


class NumberCalculator:
    """Parse plain signed decimals and grouped thousands, never expressions.

    One cache entry, at most MAX_CHARACTERS examined, no partial totals on limit.
    Malformed runs (IP addresses, 12,34, scientific notation) are skipped whole.
    Numbers may be adjacent to ordinary labels, e.g. 'apple100'.
    """
    MAX_CHARACTERS = 100_000
    MAX_NUMBERS = 10_000
    MAX_DIGITS = 256
    PRECISION = MAX_DIGITS * 2 + 16
    UNITS = ("", "K", "M", "B", "T")
    CANDIDATE = re.compile(r"[+-]?(?:[0-9][0-9,.]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
    NUMBER = re.compile(r"[+-]?(?:(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?|\.[0-9]+)")

    def __init__(self):
        self._key = None
        self._value = NumberSummary()

    def calculate(self, snapshot: EditorSnapshot, scope: NumberScope) -> NumberSummary:
        ranges = snapshot.ranges(scope)
        key = (snapshot.text, ranges)
        if key == self._key:
            return self._value
        self._key = key
        self._value = self._calculate(snapshot.text, ranges)
        return self._value

    @classmethod
    def _calculate(cls, text, ranges):
        if sum(end - start for start, end in ranges) > cls.MAX_CHARACTERS:
            return NumberSummary(limited=True)
        with localcontext() as context:
            context.prec = cls.PRECISION
            count, total = 0, Decimal(0)
            for start, end in ranges:
                for match in cls.CANDIDATE.finditer(text[start:end]):
                    token = match.group().rstrip(",.")
                    if cls.NUMBER.fullmatch(token) is None:
                        continue
                    if sum(char.isdigit() for char in token) > cls.MAX_DIGITS or count >= cls.MAX_NUMBERS:
                        return NumberSummary(limited=True)
                    total += Decimal(token.replace(",", ""))
                    count += 1
            return NumberSummary(count, total, total / count if count else Decimal(0))

    @classmethod
    def format(cls, value: Decimal, decimals: int | None) -> str:
        with localcontext() as context:
            context.prec = cls.PRECISION
            if decimals is not None and -value.as_tuple().exponent > decimals:
                value = value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
            text = format(value if value else Decimal(0), ",f")
            return text.rstrip("0").rstrip(".") if "." in text else text

    @staticmethod
    def _digits(text: str) -> int:
        return sum(char.isdigit() for char in text)

    @classmethod
    def _forms(cls, value: Decimal, full: str, decimals: int | None, digits: int | None):
        limited = digits is not None and cls._digits(full) > digits
        if not limited:
            yield full
        with localcontext() as context:
            context.prec = cls.PRECISION
            exponent = value.adjusted()
            if exponent < 3:
                return
            tier = min(exponent // 3, len(cls.UNITS) - 1)
            power, suffix = 3 * tier, cls.UNITS[tier]
            if digits is None and exponent >= 3 * len(cls.UNITS):
                power, suffix = exponent, f"e{exponent}"
            scaled = value.scaleb(-power)
            places = max(0, digits - scaled.adjusted() - 1) if limited else 2
            if decimals is not None:
                places = min(places, decimals)
            for precision in dict.fromkeys((places, min(2, places), min(1, places), 0)):
                text = cls.format(scaled, precision).replace(",", "")
                unit = suffix
                if text.lstrip("-") == "1000" and tier + 1 < len(cls.UNITS):
                    text = "-1" if value < 0 else "1"
                    unit = cls.UNITS[tier + 1]
                if digits is None or cls._digits(text) <= digits:
                    yield text + unit

    @staticmethod
    def _prefix(full: str, digits: int | None, cells: int | None) -> str:
        # A one-cell ellipsis leaves room for a digit in very narrow fields.
        if cells == 0:
            return ""
        marker = "..." if cells is None or cells >= 4 + full.startswith("-") else "…"
        prefix = ""
        count = 0
        for char in full.replace(",", ""):
            if char.isdigit():
                if digits is not None and count >= digits:
                    break
                count += 1
            if cells is not None and len(prefix) + len(marker) >= cells:
                break
            prefix += char
        return prefix.rstrip(".-") + marker

    @classmethod
    def compact(cls, value: Decimal, full: str, cells: int | None, *, digits: int | None = None,
                decimals: int | None = None) -> str:
        for form in cls._forms(value, full, decimals, digits):
            if cells is None or len(form) <= cells:
                return form
        return cls._prefix(full, digits, cells)

    @classmethod
    def format_pair(cls, summary: NumberSummary, decimals: int | None, cells: int | None,
                    *, digits: int | None = None) -> str:
        total = cls.format(summary.total, decimals)
        mean = cls.format(summary.average, decimals)
        room = None if cells is None else cells - 2
        if room is not None and room < 2:
            return "…"
        if digits is None and (room is None or len(total) + len(mean) <= room):
            return f"{total}({mean})"

        def forms(value, full):
            options = tuple(dict.fromkeys(cls._forms(value, full, decimals, digits)))
            return options or (cls._prefix(full, digits, None),)

        totals = forms(summary.total, total)
        means = forms(summary.average, mean)
        if room is None:
            return f"{totals[0]}({means[0]})"
        choices = ((max(i, j), i + j, f"{t}({m})")
                   for i, t in enumerate(totals) for j, m in enumerate(means) if len(t) + len(m) <= room)
        best = min(choices, default=None)
        if best is not None:
            return best[-1]

        # Preserve a short operand and share the remaining cells when both are long.
        total_room = min(len(totals[0]), max((room + 1) // 2, room - len(means[0])))
        t = cls.compact(summary.total, total, total_room, digits=digits, decimals=decimals)
        m = cls.compact(summary.average, mean, room - len(t), digits=digits, decimals=decimals)
        return f"{t}({m})"
