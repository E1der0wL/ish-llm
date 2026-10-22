"""Strict recipient admission and atomic dispatch evidence for one User request.

This owner validates evidence; it never fills missing records, selects an Engine,
creates a Run or replays an effect. Runtime integration supplies the transaction.
"""

from copy import deepcopy
from pathlib import Path

from llm.core.agents import AgentBinding, AgentDefinition
from llm.core.models import Message, MessageRole, MessageStatus, ProjectConfig
from llm.core.steering import is_instruction
from llm.services.infrastructure.storage import child, record, revision_token


class RequestAdmission:
    SCHEMA_VERSION = 1

    @staticmethod
    def identifiers(values, *, empty=False):
        if not isinstance(values, (list, tuple)) or (not values and not empty):
            raise ValueError("Recipients require a nonempty collection of Agent IDs")
        for identifier in values:
            AgentDefinition.identifier(identifier)
        if len(set(values)) != len(values):
            raise ValueError("Duplicate Agent recipients")
        return sorted(values)

    @classmethod
    def select(cls, recipients, participants):
        """Only explicit 'all' expands the current Session membership snapshot."""
        members = cls.identifiers(participants, empty=True)
        selected = cls.identifiers(members if recipients == "all" else recipients)
        if not set(selected) <= set(members):
            raise ValueError("Recipient is not a Session participant")
        return selected

    @classmethod
    def create(cls, *, project_id, session_id, message_id, bindings):
        if not isinstance(bindings, dict):
            raise ValueError("Request bindings require an object")
        return cls.validate({
            "schema_version": cls.SCHEMA_VERSION,
            "project_id": project_id, "session_id": session_id, "message_id": message_id,
            "recipients": cls.identifiers(list(bindings)), "bindings": deepcopy(bindings),
            "dispatch": None,
        })

    @classmethod
    def validate(cls, value):
        fields = {"schema_version", "project_id", "session_id", "message_id",
                  "recipients", "bindings", "dispatch"}
        if not isinstance(value, dict) or value.keys() != fields:
            raise ValueError("Request admission requires all current fields")
        ProjectConfig.validate_json(value)
        if type(value["schema_version"]) is not int or value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("Unsupported request admission schema_version")
        for name in ("project_id", "session_id", "message_id"):
            if not isinstance(value[name], str):
                raise ValueError("Invalid request admission identity")
            child(Path(), value[name])
        recipients = cls.identifiers(value["recipients"])
        if value["recipients"] != recipients:
            raise ValueError("Stored recipients must be canonical")
        bindings = value["bindings"]
        if not isinstance(bindings, dict) or bindings.keys() != set(recipients):
            raise ValueError("Every recipient requires exactly one binding")
        for identifier, binding in bindings.items():
            binding = AgentBinding.read(binding)
            if binding["agent_id"] != identifier or binding["project_id"] != value["project_id"]:
                raise ValueError("Recipient binding ownership mismatch")
        dispatch = value["dispatch"]
        if dispatch is not None:
            cls._validate_dispatch(value, dispatch)
        return deepcopy(value)

    @classmethod
    def _validate_dispatch(cls, admission, dispatch):
        if not isinstance(dispatch, dict) or dispatch.keys() != {"run_ids", "history", "history_revision"}:
            raise ValueError("Dispatch requires the complete Run map and history evidence")
        identifiers = dispatch["run_ids"]
        if not isinstance(identifiers, dict) or identifiers.keys() != set(admission["recipients"]):
            raise ValueError("Dispatch must cover every recipient")
        for identifier in identifiers.values():
            if not isinstance(identifier, str):
                raise ValueError("Invalid dispatched Run identity")
            child(Path(), identifier)
        if len(set(identifiers.values())) != len(identifiers):
            raise ValueError("Recipient Runs must be distinct")
        history = dispatch["history"]
        if not isinstance(history, list) or not history:
            raise ValueError("Dispatch requires its original history snapshot")
        messages = [Message.from_record(item) for item in history]
        if len({message.id for message in messages}) != len(messages):
            raise ValueError("Duplicate message in dispatch history")
        if any("admission" in message.metadata for message in messages):
            raise ValueError("Model history cannot recursively embed admission records")
        current = messages[-1]
        if (current.id != admission["message_id"] or current.role != MessageRole.USER
                or is_instruction(current) or current.run_id is not None
                or current.status != MessageStatus.COMMITTED):
            raise ValueError("Dispatch history must end with its committed User input")
        if dispatch["history_revision"] != revision_token(history):
            raise ValueError("Dispatch history revision mismatch")

    @classmethod
    def dispatch(cls, admission, *, run_ids, history):
        value = cls.validate(admission)
        if value["dispatch"] is not None:
            raise ValueError("Request has already been dispatched")
        # Only this owner's execution envelope is removed from model history.
        # Original messages and all other request metadata remain untouched.
        snapshot = [deepcopy(record(message)) for message in history]
        for message in snapshot:
            message["metadata"].pop("admission", None)
        value["dispatch"] = {"run_ids": deepcopy(run_ids), "history": snapshot,
                             "history_revision": revision_token(snapshot)}
        return cls.validate(value)

    @classmethod
    def read(cls, message, *, project_id, session_id):
        if message.role != MessageRole.USER or is_instruction(message) or message.run_id is not None:
            raise ValueError("Admission belongs to an executable User with independent recipient Runs")
        value = cls.validate(message.metadata.get("admission"))
        if (value["project_id"], value["session_id"], value["message_id"]) != (
                project_id, session_id, message.id):
            raise ValueError("Request admission ownership mismatch")
        allowed = ((MessageStatus.QUEUED, MessageStatus.CANCELLED) if value["dispatch"] is None
                   else (MessageStatus.COMMITTED,))
        if message.status not in allowed:
            raise ValueError("Request status disagrees with dispatch evidence")
        return value

    @classmethod
    def runs(cls, message, records, *, project_id, session_id):
        """Check both witnesses before recovery or a query may return an empty group."""
        admission = cls.read(message, project_id=project_id, session_id=session_id)
        selected = [run for run in records if run.input_message_id == message.id]
        dispatch = admission["dispatch"]
        if dispatch is None:
            if selected:
                raise ValueError("Undispatched request already has Run evidence")
            return []
        by_agent = {run.agent_id: run for run in selected}
        if (len(by_agent) != len(selected) or set(by_agent) != set(admission["recipients"])
                or dispatch["run_ids"] != {agent: run.id for agent, run in by_agent.items()}):
            raise ValueError("Incomplete or duplicate recipient Run group")
        if len({run.assistant_message_id for run in selected}) != len(selected):
            raise ValueError("Recipient Assistants must be distinct")
        for identifier, run in by_agent.items():
            binding = admission["bindings"][identifier]
            if (run.session_id != session_id or run.engine != binding["engine"]
                    or run.metadata.get("agent_binding") != binding):
                raise ValueError("Run differs from its admitted owner or Agent binding")
            if (("request_context" in run.metadata) != ("request_context" in message.metadata)
                    or run.metadata.get("request_context") != message.metadata.get("request_context")):
                raise ValueError("Run differs from its original request context")
        return [by_agent[identifier] for identifier in admission["recipients"]]

    @classmethod
    def run_ids(cls, message):
        """Read history links without granting authority from a self-declared owner."""
        if message.role != MessageRole.USER or is_instruction(message):
            return (message.run_id,) if message.run_id is not None else ()
        if "admission" not in message.metadata:
            if message.run_id is not None or message.status == MessageStatus.QUEUED:
                raise ValueError("Executable User requires its original recipient admission")
            return ()  # Explicitly imported/cloned observations carry no execution link.
        value = cls.validate(message.metadata["admission"])
        value = cls.read(message, project_id=value["project_id"], session_id=value["session_id"])
        return tuple(value["dispatch"]["run_ids"].values()) if value["dispatch"] is not None else ()

    @classmethod
    def history(cls, messages, records, *, project_id, session_id, persistent):
        """Validate complete dispatch witnesses before recovery or deletion writes."""
        by_id = {message.id: message for message in messages}
        for message in messages:
            if "admission" in message.metadata or (message.role == MessageRole.USER
                    and not is_instruction(message) and message.status == MessageStatus.QUEUED):
                cls.runs(message, records, project_id=project_id, session_id=session_id)
        for run in records:
            source = by_id.get(run.input_message_id)
            if source is None:
                if persistent:
                    raise ValueError("Run requires its original User admission")
                continue  # Memory lifetime loss permits interruption, never replay.
            cls.runs(source, records, project_id=project_id, session_id=session_id)
            assistant = by_id.get(run.assistant_message_id)
            if (assistant is None or assistant.role != MessageRole.ASSISTANT
                    or assistant.run_id != run.id):
                raise ValueError("Dispatched Run requires its original Assistant")

    @classmethod
    def clone_message(cls, message):
        """Detach copied observations, retaining source identity without executable evidence."""
        value = cls.validate(message.metadata["admission"])
        result = deepcopy(message)
        result.metadata.pop("admission")
        result.metadata["cloned_request"] = {name: deepcopy(value[name]) for name in (
            "project_id", "session_id", "message_id", "recipients")}
        return result
