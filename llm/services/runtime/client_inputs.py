"""Caller-key receipts on the existing conversation, not a second execution ledger.

Owners persist this metadata in the same record transaction as admission/response.
It proves acceptance only: it cannot grant approval, replay an effect or resume a Run.
File/memory durability is exactly that of the selected conversation store.
"""

from dataclasses import dataclass
from typing import ClassVar

from llm.core.contracts import JsonValue
from llm.core.json_data import JsonObject
from llm.services.infrastructure.storage import revision_token


@dataclass(frozen=True, slots=True)
class InputReceipt(JsonValue):
    METADATA: ClassVar[str] = 'client_input_receipts'
    key: str
    project_id: str
    session_id: str
    kind: str
    fingerprint: str
    message_id: str
    run_id: str | None
    interaction_id: str | None
    interaction_revision: int | None

    def __post_init__(self):
        super(InputReceipt, self).__post_init__()
        self.validate_key(self.key)
        if self.kind not in {'request', 'instruction', 'reservation', 'interaction', 'redirect', 'replacement'}:
            raise ValueError('Unknown client input receipt kind')
        if any(not isinstance(value, str) or not value for value in (
            self.project_id, self.session_id, self.message_id, self.fingerprint
        )):
            raise ValueError('Client input receipt requires its persisted identity')
        if self.kind == 'request':
            if self.run_id is not None:
                raise ValueError('Request acceptance does not select a Run')
        elif not isinstance(self.run_id, str) or not self.run_id:
            raise ValueError('Client input receipt requires its Run')
        if self.kind == 'interaction':
            if not self.interaction_id or type(self.interaction_revision) is not int or self.interaction_revision < 1:
                raise ValueError('Interaction acceptance requires the exact request revision')
        elif self.interaction_id is not None or self.interaction_revision is not None:
            raise ValueError('Only an interaction receipt may bind a decision')

    @staticmethod
    def validate_key(key):
        if not isinstance(key, str) or not key.strip():
            raise ValueError('Client request key must be a nonempty string')

    @classmethod
    def token(cls, session, key):
        cls.validate_key(key)
        return revision_token({'project_id': session.project_id, 'session_id': session.id, 'key': key})

    @staticmethod
    def bind(kind, payload):
        JsonObject.validate(payload)
        return revision_token({'kind': kind, 'payload': payload})

    def require(self, kind, payload):
        if self.kind != kind or self.fingerprint != self.bind(kind, payload):
            raise ValueError('Client request key was already accepted with different content or targets')

    @classmethod
    def find(cls, store, session, key):
        token = cls.token(session, key)
        matches = []
        # This opt-in lookup uses the store's projection (including deleted turns).
        # No key/TTL is invented for ordinary submissions, and clones cannot reuse
        # another Session's receipts. There is no separate durable index authority.
        for message in store.list():
            values = message.metadata.get(cls.METADATA, {})
            if not isinstance(values, dict):
                raise ValueError('Invalid client input receipt metadata')
            if token in values:
                item = cls.from_dict(values[token])
                if (item.project_id, item.session_id, item.key, item.message_id) != (
                    session.project_id, session.id, key, message.id
                ):
                    raise ValueError('Client input receipt ownership mismatch')
                matches.append(item)
        if len(matches) > 1:
            raise ValueError('Duplicate client input receipt')
        return matches[0] if matches else None

    @classmethod
    def record(cls, store, session, message_id, key, kind, payload, *, run_id=None,
               interaction_id=None, interaction_revision=None):
        token = cls.token(session, key)
        item = cls(key, session.project_id, session.id, kind, cls.bind(kind, payload),
                   message_id, run_id, interaction_id, interaction_revision)
        message = store.get(message_id)
        values = dict(message.metadata.get(cls.METADATA, {}))
        if token in values:
            raise ValueError('Client input receipt already exists')
        values[token] = item.to_dict()
        store.update_metadata(message_id, {cls.METADATA: values})
        return item


def response_payload(run_id, response):
    value = response.to_dict()
    # Receipt creation time is not the user's decision. Repeated equivalent
    # responses still bind the exact request revision/fingerprint and choice.
    value.pop('created_at')
    return {'run_id': run_id, 'response': value}
