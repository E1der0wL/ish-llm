"""Versioned recipient admission inside the original User Message journal."""

from copy import deepcopy
from llm.core.agents import AgentDefinition
from llm.core.models import MessageRole, ProjectConfig
from llm.core.steering import is_instruction
from llm.services.runtime.agents import RunAgentResolver
from llm.services.infrastructure.storage import revision_token, record


class RequestAdmission:
    version = 2

    @staticmethod
    def identity(message):
        value = deepcopy(record(message))
        if "admission" in value["metadata"]:
            value["metadata"]["admission"].pop("pending_agents", None)
        return value

    @staticmethod
    def identifiers(values):
        if not isinstance(values, (list, tuple)) or not values:
            raise ValueError("Recipients require a nonempty list or tuple of Agent IDs")
        for value in values:
            AgentDefinition.identifier(value)
        if len(set(values)) != len(values):
            raise ValueError("Duplicate Agent recipients are not allowed")
        return sorted(values)

    @classmethod
    def create(cls, bindings, *, exclusive=False):
        value = {"storage_version": cls.version, "recipients": sorted(bindings),
                 "bindings": deepcopy(bindings), "exclusive": exclusive}
        return cls.validate(value)

    @classmethod
    def validate(cls, value):
        required = {"storage_version", "recipients", "bindings", "exclusive"}
        if (not isinstance(value, dict) or not required <= value.keys()
                or value.keys() - required - {"run_ids", "pending_agents", "context", "context_revision"}
                or type(value["storage_version"]) is not int or value["storage_version"] != cls.version
                or type(value["exclusive"]) is not bool):
            raise ValueError("Unsupported User request admission")
        identifiers = cls.identifiers(value["recipients"])
        if identifiers != value["recipients"] or not isinstance(value["bindings"], dict):
            raise ValueError("Recipient snapshot must be canonical")
        if set(value["bindings"]) != set(identifiers):
            raise ValueError("Every recipient requires exactly one Agent binding")
        for identifier, binding in value["bindings"].items():
            if RunAgentResolver.validate(binding)["agent_id"] != identifier:
                raise ValueError("Recipient Agent binding mismatch")
        if "run_ids" in value:
            rows = value["run_ids"]
            if not isinstance(rows, dict) or set(rows) != set(identifiers):
                raise ValueError("Admitted Run projection must cover every recipient")
            for identifier in rows.values():
                AgentDefinition.identifier(identifier)
            if len(set(rows.values())) != len(rows):
                raise ValueError("Recipient Runs must be distinct")
        if "context" in value and not isinstance(value["context"], list):
            raise ValueError("Request context snapshot requires a JSON array")
        if "context" in value or "context_revision" in value:
            if "context" not in value or value.get("context_revision") != revision_token(value["context"]):
                raise ValueError("Request context snapshot revision mismatch")
        if "pending_agents" in value:
            pending = value["pending_agents"]
            if ("run_ids" not in value or not isinstance(pending, list)
                    or any(not isinstance(v, str) for v in pending)
                    or pending != sorted(set(pending)) or not set(pending).issubset(identifiers)):
                raise ValueError("Invalid pending recipient projection")
        ProjectConfig.validate_json(value)
        return deepcopy(value)

    @classmethod
    def read(cls, message):
        value = message.metadata.get("admission")
        if value is None:
            return None  # Explicit pre-recipient Message decoder.
        if message.role != MessageRole.USER or is_instruction(message):
            raise ValueError("Only executable User messages can own admission")
        return cls.validate(value)

    @classmethod
    def runs(cls, message, records, *, check_pending=True):
        """RunRepository remains authoritative; the journal projection is checked."""
        admission = cls.read(message)
        selected = [run for run in records if run.input_message_id == message.id]
        if admission is None:
            if (len(selected) > 1 or selected and message.run_id not in (None, selected[0].id)
                    or message.run_id is not None and not selected):
                raise ValueError("Legacy request and Run ownership mismatch")
            return selected
        if not selected:
            if "run_ids" in admission:
                raise ValueError("Admitted request is missing its Runs")
            return []
        by_agent = {run.agent_id: run for run in selected}
        if (len(by_agent) != len(selected) or set(by_agent) != set(admission["recipients"])
                or admission.get("run_ids") != {agent: run.id for agent, run in by_agent.items()}):
            raise ValueError("Incomplete or duplicate recipient Run group")
        for agent, run in by_agent.items():
            if run.metadata.get("agent_binding") != admission["bindings"][agent]:
                raise ValueError("Run differs from its accepted Agent binding")
            if run.metadata.get("transfer") != message.metadata.get("transfer"):
                raise ValueError("Run differs from its accepted transfer origin")
        if check_pending and "pending_agents" in admission and admission["pending_agents"] != sorted(
                run.agent_id for run in selected if run.status == "pending"):
            raise ValueError("Pending recipient projection differs from its Runs")
        return [by_agent[agent] for agent in admission["recipients"]]
