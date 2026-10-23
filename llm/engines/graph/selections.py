"""Persisted selected handler identity; private handler bindings remain opaque."""

from copy import deepcopy


class GraphHandlerSelection:
    FIELD = "handler_selection"
    ENVIRONMENT = "environment"
    DEFINITION = "definition"
    BINDING = "binding"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, environment, definition, binding):
        return cls({cls.ENVIRONMENT: environment, cls.DEFINITION: definition, cls.BINDING: binding})

    @property
    def environment(self):
        return self._value[self.ENVIRONMENT]

    @property
    def definition(self):
        return self._value[self.DEFINITION]

    @property
    def binding(self):
        return self._value[self.BINDING]

    def to_dict(self):
        return deepcopy(self._value)

    def matches(self, record):
        return record.get(self.FIELD) == self.to_dict()

    def attach(self, record):
        record[self.FIELD] = self.to_dict()

    @classmethod
    def pending(cls, records):
        return {key: cls(record[cls.FIELD]) for key, record in records.items() if cls.FIELD in record}

    @classmethod
    def bindings(cls, record):
        return (record[cls.FIELD][cls.BINDING],) if cls.FIELD in record else ()
