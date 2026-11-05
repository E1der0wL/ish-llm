"""Typed prompt status and bounded decimal aggregation; no host dependency."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
from enum import StrEnum
import re


class InputField(StrEnum):
    LENGTH = "length"
    LINES = "lines"
    LINE = "line"
    COLUMN = "column"
    POSITION = "position"
    MODE = "mode"
    ENCODING = "encoding"


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
    def format(cls, value: Decimal, decimal_places: int) -> str:
        with localcontext() as context:
            context.prec = cls.PRECISION
            rounded = value.quantize(Decimal(1).scaleb(-decimal_places), rounding=ROUND_HALF_UP)
            if not rounded:
                rounded = abs(rounded)
            text = f"{rounded:,.{decimal_places}f}"
            return text.rstrip("0").rstrip(".") if decimal_places else text
