"""Bridge durable submission receipts only until the authoritative Snapshot includes them."""

from ..model import SessionIdentity
from dataclasses import replace
from llm.core.models import MessageStatus


class SubmittedMessages:
    FIELD = "_submitted_messages"
    STARTED_FIELD = "_started_requests"
    STARTED_LIMIT = 256
    ABSENT = None

    @classmethod
    def initialize(cls, view):
        setattr(view, cls.FIELD, {})
        setattr(view, cls.STARTED_FIELD, {})

    @classmethod
    def started(cls, view, observation):
        key = (observation.project_id, observation.session_id)
        store = getattr(view, cls.STARTED_FIELD)
        store[(key, observation.message_id)] = observation
        while len(store) > cls.STARTED_LIMIT:
            store.pop(next(iter(store)))

    @classmethod
    def committed(cls, view, key, message):
        if (message.status == MessageStatus.QUEUED
                and (key, message.id) in getattr(view, cls.STARTED_FIELD)):
            return replace(message, status=MessageStatus.COMMITTED.value)
        return message

    @classmethod
    def reconcile(cls, view, snapshot):
        """A late queued read cannot overwrite a persisted Run-start proof."""
        key = (snapshot.project_id, snapshot.selected_id)
        store = getattr(view, cls.STARTED_FIELD)
        if not any(identity == key for identity, _ in store):
            return snapshot
        messages = tuple(cls.committed(view, key, message) for message in snapshot.messages)
        for message in snapshot.messages:
            if message.status != MessageStatus.QUEUED:
                store.pop((key, message.id), cls.ABSENT)
        return snapshot if messages == snapshot.messages else replace(snapshot, messages=messages)

    @classmethod
    def remember(cls, view, key, message):
        getattr(view, cls.FIELD).setdefault(key, {})[message.id] = cls.committed(view, key, message)

    @classmethod
    def unobserved(cls, view, key, known):
        store = getattr(view, cls.FIELD)
        pending = store.get(key, {})
        pending = {
            identifier: cls.committed(view, key, message)
            for identifier, message in pending.items()
            if identifier not in known
        }
        if pending:
            store[key] = pending
        else:
            store.pop(key, cls.ABSENT)
        return pending

    @classmethod
    def retain(cls, view, project_id, sessions):
        store = getattr(view, cls.FIELD)
        setattr(
            view,
            cls.FIELD,
            {
                key: value
                for key, value in store.items()
                if SessionIdentity(key).project_id == project_id
                and SessionIdentity(key).session_id in sessions
            },
        )
        started = getattr(view, cls.STARTED_FIELD)
        for key in tuple(started):
            identity, _ = key
            if SessionIdentity(identity).project_id != project_id or SessionIdentity(identity).session_id not in sessions:
                del started[key]

    @classmethod
    def discard(cls, view, key, identifiers):
        submitted = getattr(view, cls.FIELD, {}).get(key, {})
        for identifier in identifiers:
            submitted.pop(identifier, cls.ABSENT)
            getattr(view, cls.STARTED_FIELD, {}).pop((key, identifier), cls.ABSENT)
