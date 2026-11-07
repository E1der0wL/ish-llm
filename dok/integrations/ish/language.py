"""Read the host's selected language without loading or modifying its catalogs."""


class HostLanguage:
    @staticmethod
    def current():
        try:
            from ish.lang import i18n
        except ImportError:
            return None
        return i18n.current_lang
