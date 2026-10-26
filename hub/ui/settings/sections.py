"""Section identities shared by settings navigation, pages and JSON drafts."""


class SettingsSection:
    DEBUGGING = "debugging"
    GENERAL = "project:general"
    ENGINE = "engine"
    COMPONENT = "component"
    SEPARATOR = ":"
    ENGINE_PREFIX = ENGINE + SEPARATOR
    COMPONENT_PREFIX = COMPONENT + SEPARATOR
    NAMED_KINDS = (ENGINE, COMPONENT)

    @classmethod
    def engine(cls, name):
        return cls.ENGINE_PREFIX + name

    @classmethod
    def component(cls, name):
        return cls.COMPONENT_PREFIX + name

    @classmethod
    def is_engine(cls, section):
        return section.startswith(cls.ENGINE_PREFIX)

    @classmethod
    def is_component(cls, section):
        return section.startswith(cls.COMPONENT_PREFIX)

    @classmethod
    def parts(cls, section):
        kind, _, name = section.partition(cls.SEPARATOR)
        return kind, name

    @classmethod
    def name(cls, section):
        _, _, name = section.partition(cls.SEPARATOR)
        return name

    @classmethod
    def choices(cls, engines, components):
        return [
            cls.GENERAL,
            *(cls.engine(name) for name in engines),
            *(cls.component(name) for name in components),
        ]
