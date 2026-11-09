"""Read the host's message locale without changing process locale or preferences."""

import os
from collections.abc import Iterable

from .codes import LanguageCode


class LanguageSelection:
    LOCALE_VARIABLES = ("LC_ALL", "LC_MESSAGES", "LANG")
    PREFERENCE_VARIABLE = "LANGUAGE"
    NEUTRAL_LOCALES = ("c", "posix")
    PREFERENCE_SEPARATOR = ":"
    REGION_SEPARATOR = "-"
    ENCODING_SEPARATOR = "."
    MODIFIER_SEPARATOR = "@"

    @classmethod
    def _normalize(cls, value: str) -> str:
        return (value.strip().split(cls.ENCODING_SEPARATOR)[0]
                .split(cls.MODIFIER_SEPARATOR)[0].replace("_", cls.REGION_SEPARATOR).lower())

    @classmethod
    def system_language(cls, available: Iterable[str]) -> str:
        locale_name = next((os.environ[name] for name in cls.LOCALE_VARIABLES
                            if os.environ.get(name)), LanguageCode.AUTO)
        locale_code = cls._normalize(locale_name)
        # C/POSIX explicitly requests untranslated messages, including when a
        # desktop has also exported a LANGUAGE preference list.
        if not locale_code or locale_code in cls.NEUTRAL_LOCALES:
            return LanguageCode.DEFAULT
        packs = {cls._normalize(code): code for code in available}
        preferences = os.environ.get(cls.PREFERENCE_VARIABLE, LanguageCode.AUTO)
        for candidate in (*preferences.split(cls.PREFERENCE_SEPARATOR), locale_name):
            normalized = cls._normalize(candidate)
            if normalized in cls.NEUTRAL_LOCALES:
                return LanguageCode.DEFAULT
            for code in (normalized, normalized.split(cls.REGION_SEPARATOR)[0]):
                if code in packs:
                    return packs[code]
        return LanguageCode.DEFAULT
