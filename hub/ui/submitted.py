"""Bridge durable submission receipts only until the authoritative Snapshot includes them."""

from ..model import SessionIdentity


class SubmittedMessages:
    FIELD = "_submitted_messages"
    ABSENT = None

    @classmethod
    def initialize(cls, view):
        setattr(view, cls.FIELD, {})

    @classmethod
    def remember(cls, view, key, message):
        getattr(view, cls.FIELD).setdefault(key, {})[message.id] = message

    @classmethod
    def unobserved(cls, view, key, known):
        store = getattr(view, cls.FIELD)
        pending = store.get(key, {})
        pending = {
            identifier: message
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

    @classmethod
    def discard(cls, view, key, identifiers):
        submitted = getattr(view, cls.FIELD, {}).get(key, {})
        for identifier in identifiers:
            submitted.pop(identifier, cls.ABSENT)
