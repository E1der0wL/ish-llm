"""UI language packs; model content and backend diagnostics are not translated."""

from . import en, ko
from ..asset.icon import VALUES, NERD
import re
from string import Formatter


def validate_packs(packs):
    if not isinstance(packs, dict):
        raise ValueError("language_packs must be a JSON object")
    formatter = Formatter()
    for code, messages in packs.items():
        if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,15}", code) or code in ("ko", "en"):
            raise ValueError("Use a custom language code; built-in ko/en cannot be replaced")
        if not isinstance(messages, dict) or not messages:
            raise ValueError("A language pack must contain JSON message translations")
        for key, value in messages.items():
            if key not in en.MESSAGES or not isinstance(value, str):
                raise ValueError(f"Unknown or invalid translation: {key}")
            allowed = {field for _, field, _, _ in formatter.parse(en.MESSAGES[key]) if field} | set(VALUES)
            for _, name, spec, conversion in formatter.parse(value):
                if name is not None and (name not in allowed or "{" in spec or conversion not in (None, "s", "r", "a")):
                    raise ValueError(f"Invalid translation placeholder: {key}")


class Language:
    def __init__(self, code: str = "ko", custom_packs=None, *, icons=None) -> None:
        custom_packs = {} if custom_packs is None else custom_packs
        validate_packs(custom_packs)
        packs = {"ko": ko.MESSAGES, "en": en.MESSAGES, **custom_packs}
        if code not in packs:
            raise ValueError(f"Unsupported Hub language: {code}")
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
        return self("status_" + str(value)) if "status_" + str(value) in self.messages else str(value)

    def elapsed(self, seconds: float | None) -> str:
        if seconds is None:
            return self("elapsed_unknown")
        tenths = max(0, round(seconds * 10))
        hours, remainder = divmod(tenths, 36000)
        minutes, remainder = divmod(remainder, 600)
        duration = self("duration_seconds", seconds=remainder / 10)
        if minutes or hours:
            duration = self("duration_minutes", minutes=minutes) + " " + duration
        if hours:
            duration = self("duration_hours", hours=hours) + " " + duration
        return self("elapsed", duration=duration)
