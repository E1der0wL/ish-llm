"""Engine-specific request selection; widgets and drafts stay on the UI loop."""

from prompt_toolkit.layout import ConditionalContainer, DynamicContainer, HSplit
from prompt_toolkit.filters import Condition
from prompt_toolkit.widgets import Label
from .controls import RadioList


class ExecutionChooser:
    def __init__(self, view, catalog: dict, initial: str = "") -> None:
        self.view, self.catalog = view, catalog
        self.error = ""
        self.engines = RadioList([(name, name) for name in catalog["engines"]],
                                 default=initial or view.engine, select_on_focus=True)
        self.engines.window.height = lambda: min(4 if view._rows() >= 24 else 2, len(catalog["engines"]))
        records = catalog["workflows"]
        self._workflows = {}
        for engine, spec in catalog["engines"].items():
            if spec["kind"] != "graph":
                continue
            choices = RadioList([(name, name) for name in sorted(records)] or
                                [("", view.t("execution_no_workflows"))],
                                default=view.execution_options_for(engine).get("workflow"), select_on_focus=True)
            choices.window.height = lambda: min(4 if view._rows() >= 24 else 2, max(1, len(records)))
            self._workflows[engine] = choices
        graph = Condition(self.is_graph)
        self.body = HSplit([
            Label(view.t("execution_engine")), self.engines,
            ConditionalContainer(HSplit([
                Label(view.t("execution_workflow")),
                DynamicContainer(lambda: self.workflows if self.is_graph() else Label("")),
                ConditionalContainer(Label(self.description), Condition(lambda: view._rows() >= 24)),
            ]), graph),
            ConditionalContainer(Label(view.t("execution_plain")), ~graph),
            ConditionalContainer(Label(lambda: self.error, style="class:hub.notice"), Condition(lambda: bool(self.error))),
        ])

    def is_graph(self) -> bool:
        return self.catalog["engines"][self.engines.current_value]["kind"] == "graph"

    @property
    def workflows(self) -> RadioList:
        return self._workflows[self.engines.current_value]

    def description(self) -> str:
        if not self.is_graph():
            return ""
        record = self.catalog["workflows"].get(self.workflows.current_value)
        if record is None:
            return self.view.t("execution_workflows_hint")
        description = record.get("description", "")
        summary = self.view.t("execution_summary", entry=record["entry"], count=len(record["nodes"]))
        return (description.splitlines()[0][:120] + "\n" if isinstance(description, str) and description else "") + summary

    def validate(self) -> bool:
        if self.is_graph() and not self.workflows.current_value:
            self.error = self.view.t("execution_workflows_hint")
            return False
        return True

    def apply(self) -> None:
        options = {"workflow": self.workflows.current_value} if self.is_graph() else {}
        self.view.set_execution(self.engines.current_value, options)

    def open(self) -> None:
        self.view.open_dialog(self.view.t("execution_title"), self.body, self.apply,
                              self.engines, validate=self.validate)
