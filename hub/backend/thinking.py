"""Transient provider-exposed reasoning phases; never a second transcript."""

from collections import OrderedDict


def reasoning_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return reasoning_text(value.get("thinking", ""))
    return ""


class ThinkingPhases:
    def __init__(self):
        self.runs = OrderedDict()

    def observe(self, run, event):
        if str(run.status) not in ("pending", "running"):
            self.runs.pop(run.id, None)
            return
        state = self.runs.setdefault(run.id, {"seen": {}, "text": "", "phase": None,
                                             "completion": None, "activity": ""})
        self.runs.move_to_end(run.id)
        while len(self.runs) > 128:
            self.runs.popitem(last=False)
        completion = getattr(event, "completion", None)
        if completion is not None:
            if completion.id != state["completion"]:
                state["completion"] = completion.id
                state["activity"] = "thinking"
            text = reasoning_text(completion.reasoning_content)
            previous = state["seen"].get(completion.id, "")
            state["seen"][completion.id] = text
            if text != previous and text:
                added = text[len(previous):] if text.startswith(previous) else text
                state["text"] += ("\n\n" if state["text"] and not previous else "") + added
                state["phase"] = completion.id
                state["activity"] = "thinking"
            if str(getattr(completion, "status", "running")) not in ("pending", "running"):
                state["activity"] = "running"
        delta = getattr(event, "delta", None)
        output = getattr(event, "output", None)
        if any(value is not None and value.visibility == "user" and value.text for value in (delta, output)):
            state["activity"] = "streaming"

    def activity(self, run) -> str:
        """Execution observation, independent of whether thought text is exposed."""
        if run is None or str(run.status) not in ("pending", "running"):
            return ""
        state = self.runs.get(run.id)
        if state and state["activity"]:
            return state["activity"]
        completions = run.metadata.get("completions", [])
        if completions:
            return "thinking" if completions[-1].get("status") in ("pending", "running") else "running"
        return "preparing"

    def visible(self, run_id, fallback, *, active, content):
        if not active:
            self.runs.pop(run_id, None)
            return ""
        state = self.runs.get(run_id)
        text = state["text"] if state is not None else ""
        return fallback if len(fallback) >= len(text) else text
