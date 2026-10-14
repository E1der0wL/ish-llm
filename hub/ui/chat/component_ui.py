"""Component managers and help share one small slash-command entry point."""

from ...backend.project.component_commands import parse_arguments
from ...widget.output.reader import ReadOnlyDialog
from ...components import component_spec
from .. import host


class ComponentCommands:
    def __init__(self, controller):
        self.controller, self.view = controller, controller.view

    def open(self, command: str, argument: str):
        view = self.view
        try:
            action = parse_arguments(argument, view.t)
        except ValueError as error:
            self.controller._error(error)
            return
        name = view.component_commands[command]
        spec = component_spec(name)
        project_id = view.project_id
        view.composer.text = ""
        if action == "help":
            popup = ReadOnlyDialog(f"/{command} help", view.t("component_help", command=command),
                                   view._rows, view._columns, view.close_dialog, view.t,
                                   render_worker=view.render_worker)
            view.dialogs.show(popup, popup.receiver)
            return
        from ...widget.forms.tool_manager import ToolManager
        from ...widget.forms.record_manager import RecordManager

        def request(operation, argument, version, done):
            target = (project_id,) if spec.manager == "tools" else (project_id, name)
            self.controller._call(spec.operation, *target, operation, argument, version, completed=done)

        popup = (ToolManager(view, request, open_directory=lambda path: host.open_directory(path)) if spec.manager == "tools" else
                 RecordManager(view, request, name=name, edit_text=lambda *args, **kwargs: host.edit_text(*args, **kwargs)))
        view.dialogs.show(popup, popup.path)
        popup.reload()
