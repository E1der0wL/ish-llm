"""Hub review aggregation over authoritative Backend turn-deletion plans."""

import hashlib
import json
from llm.core.plans import TurnDeletionPlan
from llm.core.contracts import Diagnostic, ResourceRef


class TurnDeletionItem:
    ID = "id"
    QUEUED = "queued"
    DEFERRED = "deferred"
    PLAN = "plan"
    YES = True
    NO = False

    def __init__(self, value):
        self._value = value

    @classmethod
    def cancellation(cls, identifier):
        return {cls.ID: identifier, cls.QUEUED: cls.YES}

    @classmethod
    def deferred(cls, identifier):
        return {cls.ID: identifier, cls.QUEUED: cls.NO, cls.DEFERRED: cls.YES}

    @classmethod
    def planned(cls, identifier, plan):
        return {cls.ID: identifier, cls.QUEUED: cls.NO, cls.PLAN: plan}

    @property
    def id(self):
        return self._value[self.ID]

    @property
    def queued(self):
        return self._value[self.QUEUED]


class TurnDeletionReview:
    ITEMS = "items"
    BLOCKERS = TurnDeletionPlan.blockers.__name__
    REVISION = TurnDeletionPlan.revision.__name__
    SORT_KEYS = True
    ENCODING = "utf-8"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, items, blockers):
        revision = hashlib.sha256(
            json.dumps(items, sort_keys=cls.SORT_KEYS).encode(cls.ENCODING)
        ).hexdigest()
        return {
            cls.ITEMS: items,
            cls.BLOCKERS: list(blockers.values()),
            cls.REVISION: revision,
        }

    @staticmethod
    def blocker_run_id(blocker):
        return blocker[Diagnostic.source.__name__][ResourceRef.run_id.__name__]

    def optional_items(self):
        return self._value.get(self.ITEMS, ())

    @property
    def items(self):
        return self._value[self.ITEMS]

    @property
    def blockers(self):
        return self._value[self.BLOCKERS]

    @property
    def revision(self):
        return self._value[self.REVISION]


class TurnDeletionResult:
    REMOVED = "removed"
    ERRORS = "errors"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, removed, errors):
        return {cls.REMOVED: removed, cls.ERRORS: errors}

    @property
    def removed(self):
        return self._value[self.REMOVED]

    @property
    def errors(self):
        return self._value[self.ERRORS]
