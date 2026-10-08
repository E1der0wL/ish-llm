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
        state = self.runs.setdefault(run.id, {"seen": {}, "text": "", "phase": None})
        self.runs.move_to_end(run.id)
        while len(self.runs) > 128:
            self.runs.popitem(last=False)
        completion = getattr(event, "completion", None)
        if completion is not None:
            text = reasoning_text(completion.reasoning_content)
            previous = state["seen"].get(completion.id, "")
            state["seen"][completion.id] = text
            if text != previous and text:
                added = text[len(previous):] if text.startswith(previous) else text
                state["text"] = (state["text"] if state["phase"] == completion.id else "") + added
                state["phase"] = completion.id
        delta = getattr(event, "delta", None)
        output = getattr(event, "output", None)
        if any(value is not None and value.visibility == "user" and value.text for value in (delta, output)):
            state["text"], state["phase"] = "", None

    def visible(self, run_id, fallback, *, active, content):
        if not active:
            self.runs.pop(run_id, None)
            return ""
        state = self.runs.get(run_id)
        return state["text"] if state is not None else fallback if not content else ""
