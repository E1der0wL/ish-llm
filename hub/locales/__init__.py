"""UI language packs; model content and backend diagnostics are not translated."""

from . import en, ko
from .codes import LanguageCode
from .keys import MessageKey, MessagePattern
from ..asset.icon import VALUES, NERD
import re
from string import Formatter


class TranslationPack:
    CODE_PATTERN = r"[a-z][a-z0-9_-]{1,15}"
    NESTED_FORMAT = "{"
    CONVERSIONS = (None, "s", "r", "a")
    INVALID_OBJECT = "language_packs must be a JSON object"
    RESERVED_CODE = "Use a custom language code; built-in ko/en cannot be replaced"
    EMPTY_PACK = "A language pack must contain JSON message translations"
    INVALID_MESSAGE = "Unknown or invalid translation: {key}"
    INVALID_PLACEHOLDER = "Invalid translation placeholder: {key}"


def validate_packs(packs):
    if not isinstance(packs, dict):
        raise ValueError(TranslationPack.INVALID_OBJECT)
    formatter = Formatter()
    for code, messages in packs.items():
        if not isinstance(code, str) or not re.fullmatch(TranslationPack.CODE_PATTERN, code) or code in LanguageCode.BUILTIN:
            raise ValueError(TranslationPack.RESERVED_CODE)
        if not isinstance(messages, dict) or not messages:
            raise ValueError(TranslationPack.EMPTY_PACK)
        for key, value in messages.items():
            if key not in en.MESSAGES or not isinstance(value, str):
                raise ValueError(TranslationPack.INVALID_MESSAGE.format(key=key))
            allowed = {field for _, field, _, _ in formatter.parse(en.MESSAGES[key]) if field} | set(VALUES)
            for _, name, spec, conversion in formatter.parse(value):
                if name is not None and (name not in allowed or TranslationPack.NESTED_FORMAT in spec or conversion not in TranslationPack.CONVERSIONS):
                    raise ValueError(TranslationPack.INVALID_PLACEHOLDER.format(key=key))


class Language:
    UNSUPPORTED = "Unsupported Hub language: {code}"
    MINIMUM_SECONDS = 0
    SECONDS_PER_HOUR = 3600
    SECONDS_PER_MINUTE = 60
    DURATION_SEPARATOR = " "

    def __init__(self, code: str = LanguageCode.DEFAULT, custom_packs=None, *, icons=None) -> None:
        custom_packs = {} if custom_packs is None else custom_packs
        validate_packs(custom_packs)
        packs = {LanguageCode.KOREAN: ko.MESSAGES, LanguageCode.ENGLISH: en.MESSAGES, **custom_packs}
        if code not in packs:
            raise ValueError(self.UNSUPPORTED.format(code=code))
        self.code = code
        self.messages = {**en.MESSAGES, **packs[code]}
        self.icon_source = icons or NERD

    @property
    def icons(self):
        return self.icon_source() if callable(self.icon_source) else self.icon_source

    def __call__(self, key: str, **values) -> str:
        try:
            return self.messages.get(key, en.MESSAGES.get(key, key)).format(**{**self.icons.values, **values})
        except (KeyError, ValueError, TypeError, IndexError):
            return en.MESSAGES.get(key, key).format(**{**self.icons.values, **values})

    def status(self, value) -> str:
        return self(MessagePattern.status(str(value))) if MessagePattern.status(str(value)) in self.messages else str(value)

    def elapsed(self, seconds: float | None) -> str:
        if seconds is None:
            return self(MessageKey.ELAPSED_UNKNOWN)
        whole_seconds = max(self.MINIMUM_SECONDS, int(seconds))
        hours, remainder = divmod(whole_seconds, self.SECONDS_PER_HOUR)
        minutes, remainder = divmod(remainder, self.SECONDS_PER_MINUTE)
        duration = self(MessageKey.DURATION_SECONDS, seconds=remainder)
        if minutes or hours:
            duration = self(MessageKey.DURATION_MINUTES, minutes=minutes) + self.DURATION_SEPARATOR + duration
        if hours:
            duration = self(MessageKey.DURATION_HOURS, hours=hours) + self.DURATION_SEPARATOR + duration
        return self(MessageKey.ELAPSED, duration=duration)
