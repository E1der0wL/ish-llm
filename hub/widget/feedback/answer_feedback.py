"""Local answer acknowledgement, without changing durable execution states."""

from llm.core.models import MessageStatus
from dataclasses import dataclass, replace
from llm.core.interactions import InteractionOption


@dataclass(slots=True)
class AnswerFeedback:
    identity: tuple
    item: dict | None
    tool_id: str
    assistant_id: str
    denied: bool
    approval: bool
    acknowledged: bool = False
    pending: bool = True

    @classmethod
    def capture(cls, question, option_id):
        view, item = question.view, question.item
        snapshot = getattr(view, "_last_snapshot", None)
        run = snapshot.run if snapshot else None
        source = item["request"].get("source", {}).get("step_id")
        tool_id = next((step.id for step in run.steps if source and step.source_id == source), "") if run and run.id == item["run_id"] else ""
        messages = view.sessions[view.selected].messages
        assistant_id = next((m.id for m in reversed(messages) if m.role == "assistant"), "")
        denied = any(o["id"] == option_id and o.get("effect") == InteractionOption.EFFECT_DENY
                     for o in item["request"].get("options", ()))
        return cls(question.identity, item, tool_id, assistant_id, denied, bool(item["request"].get("action")))

    @property
    def status(self):
        if not self.acknowledged:
            return "answer_sending"
        return "denied" if self.denied else "approved" if self.approval else "answered"


def project_answers(responses, messages):
    """Single pass, retaining the cached message when only another row changes."""
    tools_by_id = {r.tool_id: r.status for r in responses if r.tool_id}
    headers = {r.assistant_id: r.status for r in responses if r.assistant_id}
    if not headers and not tools_by_id:
        return messages
    def tool(step):
        status = tools_by_id.get(step.id)
        return replace(step, status=status) if status and step.status == "waiting_approval" else step
    result = []
    for message in messages:
        tools = tuple(tool(step) for step in message.tools)
        trace = tuple(tool(step) for step in message.trace)
        phase = headers.get(message.id, message.phase) if message.status == MessageStatus.PAUSED else message.phase
        result.append(replace(message, tools=tools, trace=trace, phase=phase)
                      if (tools, trace, phase) != (message.tools, message.trace, message.phase) else message)
    return tuple(result)
