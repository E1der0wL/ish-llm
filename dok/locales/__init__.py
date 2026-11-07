"""Language selection, fallback and built-in text formatting, without host imports."""
from dataclasses import dataclass
from enum import StrEnum
from . import ko
from .messages import Message, MONTHS, WEEKDAYS


class Language(StrEnum):
    ENGLISH = "en"
    KOREAN = "ko"


@dataclass(frozen=True, slots=True)
class Locale:
    language: Language = Language.ENGLISH

    @classmethod
    def resolve(cls, language=None):
        name = str(language or "").lower().replace("_", "-").split("-", 1)[0].split(".", 1)[0]
        return cls(Language.KOREAN if name == Language.KOREAN else Language.ENGLISH)

    def text(self, message: Message, /, **values) -> str:
        text = ko.TEXT.get(message, message.value) if self.language == Language.KOREAN else message.value
        return text.format(**values) if values else text

    def custom(self, value: str | None, message: Message) -> str:
        return self.text(message) if value is None else value

    def month(self, month: int) -> str:
        return (ko.MONTHS if self.language == Language.KOREAN else MONTHS)[month - 1]

    def weekday(self, weekday: int, *, abbreviated=False) -> str:
        name = (ko.WEEKDAYS if self.language == Language.KOREAN else WEEKDAYS)[weekday]
        return name[:1 if self.language == Language.KOREAN else 3] if abbreviated else name

    def date(self, day) -> str:
        return self.text(Message.DATE, weekday=self.weekday(day.weekday(), abbreviated=True),
                         day=day.day, month=self.month(day.month), year=day.year)
