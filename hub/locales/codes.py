"""Canonical language identities, automatic selection and final fallback."""


class LanguageCode:
    AUTO = ""
    KOREAN = "ko"
    ENGLISH = "en"
    BUILTIN = (KOREAN, ENGLISH)
    DEFAULT = ENGLISH
