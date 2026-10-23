"""Transient Memory processing observations; no stored record or execution authority."""


class MemoryProcessingSnapshot:
    class Field:
        IDENTITY = "identity"
        CONFIGURATION = "configuration"
        HAS_SEARCH = "has_search"
        RECORDS = "records"
        SUMMARY = "summary"
        GOAL_IDS = "goal_ids"
        GOAL_REFERENCES = "goal_references"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, *, identity, configuration, has_search, records, summary):
        return {cls.Field.IDENTITY: identity, cls.Field.CONFIGURATION: configuration,
                cls.Field.HAS_SEARCH: has_search, cls.Field.RECORDS: records, cls.Field.SUMMARY: summary}

    @property
    def identity(self):
        return self._value[self.Field.IDENTITY]

    @property
    def configuration(self):
        return self._value[self.Field.CONFIGURATION]

    @property
    def has_search(self):
        return self._value[self.Field.HAS_SEARCH]

    @property
    def records(self):
        return self._value[self.Field.RECORDS]

    @property
    def summary(self):
        return self._value[self.Field.SUMMARY]

    @summary.setter
    def summary(self, value):
        self._value[self.Field.SUMMARY] = value

    @property
    def has_goal_references(self):
        return self.Field.GOAL_REFERENCES in self._value

    @property
    def goal_ids(self):
        return self._value[self.Field.GOAL_IDS]

    @property
    def goal_references(self):
        return self._value[self.Field.GOAL_REFERENCES]

    def select_goals(self, identifiers, references):
        self._value.update({self.Field.GOAL_IDS: identifiers, self.Field.GOAL_REFERENCES: references})
