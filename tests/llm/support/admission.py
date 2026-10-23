"""Explicit current request evidence for storage and history fixtures."""
from llm.core.models import Message, MessageRole, MessageStatus, new_id
from llm.services.runtime.requests import RequestAdmission


def stored_request(store, session, content, binding, *, run_id=None):
    """Create a single-recipient fixture, without inferring or repairing old records."""
    message = Message(new_id(), MessageRole.USER, content,
                      MessageStatus.QUEUED if run_id is None else MessageStatus.COMMITTED)
    admission = RequestAdmission.create(project_id=session.project_id, session_id=session.id,
        message_id=message.id, bindings={binding["agent_id"]: binding})
    if run_id is not None:
        admission = RequestAdmission.dispatch(admission,
            run_ids={binding["agent_id"]: run_id}, history=[message])
    return store.create(message.role, message.content, message.status, message_id=message.id,
                        metadata={"admission": admission})

from uuid import NAMESPACE_URL, uuid5
from llm.core.agents import AgentBinding

_labels = {}
def uid(label):
    identifier = uuid5(NAMESPACE_URL, label).hex
    _labels[identifier] = label
    return identifier

def admitted_message(identifier, role, status, run_id=None, metadata=None):
    message = Message(identifier, MessageRole(role), _labels.get(identifier, identifier),
                      MessageStatus(status), run_id=run_id, metadata=metadata or {})
    if role == 'user':
        message.run_id = None
        binding = AgentBinding.create('a' * 32, 'fixture', {'engine': 'test'}).to_dict()
        admission = RequestAdmission.create(project_id='a' * 32, session_id='b' * 32,
            message_id=identifier, bindings={'fixture': binding})
        if run_id is not None:
            admission = RequestAdmission.dispatch(admission, run_ids={'fixture': run_id}, history=[message])
        message.metadata['admission'] = admission
    return message

