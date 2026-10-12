"""Project execution observations without presenting checkpoint reuse as work."""

from hashlib import sha256
import json

from ...model import StepSummary


def display_steps(steps) -> tuple[StepSummary, ...]:
    result = []
    for step in steps:
        metadata = getattr(step, "metadata", {})
        # Loop emits observation Steps while restoring receipts. Graph marks
        # reuse on STARTED as well, before the final `reused` flag is persisted.
        if metadata.get("reused") is True or metadata.get("reused_step_id"):
            continue
        kind, status = str(step.kind), str(step.status)
        identity = getattr(step, "id", "")
        if kind == "tool":
            call_id = metadata.get("tool_call_id")
            node_key = metadata.get("node_checkpoint_key")
            if call_id or node_key:
                # A waiting approval and its later execution have different
                # Step IDs but belong to one invocation. Scope distinguishes
                # identical calls in parallel Graph nodes and loop rounds.
                key = [step.name, metadata.get("checkpoint_name"), node_key,
                       metadata.get("iteration"), call_id]
                identity = "tool:" + sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()
            if status == "cancelled" and metadata.get("phase") == "waiting_approval":
                status = "waiting_approval"
        result.append(StepSummary(step.name, status, kind, identity, getattr(step, "started_at", None) or "",
                                  getattr(step, "id", "")))
    return tuple(result)
