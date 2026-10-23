"""Agent-owned usage and delegation selection inside the shared checkpoint lifecycle."""

from copy import deepcopy
import json

from llm.core.checkpoint_records import CheckpointStep
from llm.core.checkpoints import CheckpointEnvelope, CheckpointLifecycle, CheckpointStatus, unwrap_record
from llm.core.steering import InstructionCheckpoint
from llm.core.tool_usage_records import ToolUsageError, ToolUsageRecord


class AgentCheckpoint:
    USAGE_FIELD = "agent_usage"
    BINDING_FIELD = "agent_binding"
    CONTAINER_KIND = "agent_container"
    KEY_KIND = "agent"
    KEY_SEPARATORS = (",", ":")
    MISSING_USAGE = "Tool usage missing from Agent checkpoint: {owner}"
    ABSENT = None

    @classmethod
    def delegation_key(cls, request_key, identifier):
        return json.dumps([request_key, cls.KEY_KIND, identifier], separators=cls.KEY_SEPARATORS)

    @classmethod
    def usage(cls, record, owner):
        usage = record.get(cls.USAGE_FIELD)
        if not isinstance(usage, dict) or owner not in usage:
            raise ToolUsageError(cls.MISSING_USAGE.format(owner=owner))
        return usage[owner]

    @classmethod
    def required_usage(cls, record, owner):
        return record[cls.USAGE_FIELD][owner]

    @classmethod
    def with_usage(cls, record, owner, usage):
        value = deepcopy(record)
        value.setdefault(cls.USAGE_FIELD, {})[owner] = deepcopy(usage)
        return value

    @classmethod
    def selected_usage(cls, records, owner, name):
        values = []
        selected = [record for record in records.values() if CheckpointEnvelope.belongs_to(record, owner, name)]
        for record in selected:
            payload, _ = unwrap_record(record)
            if InstructionCheckpoint.is_record(payload):
                InstructionCheckpoint.read(payload)
                continue
            values.append(cls.usage(record, owner))
        marker = records.get(owner)
        if marker is not cls.ABSENT:
            if (cls.USAGE_FIELD in marker or marker.get(CheckpointEnvelope.KIND_FIELD) == cls.CONTAINER_KIND
                    or CheckpointStep(marker).required_status == CheckpointStatus.STARTED):
                values.append(cls.usage(marker, owner))
        ToolUsageRecord.read_many(values)
        return values

    @classmethod
    def delegation(cls, owner, binding, usage):
        status, container = CheckpointStatus.STARTED, CheckpointLifecycle.CONTAINER
        return {
            CheckpointEnvelope.KIND_FIELD: cls.CONTAINER_KIND, CheckpointStep.Field.STATUS.value: status,
            CheckpointLifecycle.container.__name__: container,
            CheckpointStep.Field.REQUIRES_RETRY.value: CheckpointEnvelope.retry_required(status, container),
            cls.BINDING_FIELD: binding, cls.USAGE_FIELD: {owner: usage},
        }

    @classmethod
    def selected_binding(cls, record):
        return record.get(cls.BINDING_FIELD)

    @classmethod
    def bindings(cls, record):
        return (record[cls.BINDING_FIELD],) if cls.BINDING_FIELD in record else ()
