"""Component managers and help share one small slash-command entry point."""


from ...backend.project.component_commands import parse_arguments
from ...components import component_spec, ManagerKind
from .. import host
from ..input.commands import CommandSyntax


class ComponentCommands:
    NO_PROGRESS = None
    CONSUME_COMMAND = True

    def __init__(self, controller):
        self.controller, self.view = controller, controller.view

    def open(self, command: str, argument: str, *, consume_input=CONSUME_COMMAND, edit_identifier=None):
        view = self.view
        try:
            parse_arguments(argument, view.t)
        except ValueError as error:
            self.controller._error(error)
            return
        name = view.component_commands[command]
        spec = component_spec(name)
        project_id = view.project_id
        if consume_input:
            view.composer.text = CommandSyntax.EMPTY
        from ...widget.forms.tool_manager import ToolManager
        from ...widget.forms.record_manager import RecordManager
        from ...widget.forms.rag_manager import RAGManager
        from ...widget.forms.definition_manager import DefinitionManager

        def request(operation, argument, version, done, *, progress=self.NO_PROGRESS):
            target = (
                (project_id,)
                if spec.manager == ManagerKind.TOOLS
                else (project_id, name)
            )
            self.controller._call(
                spec.operation,
                *target,
                operation,
                argument,
                version,
                completed=done,
                progress=progress
            )

        record_manager = (
            RAGManager
            if spec.manager == ManagerKind.DOCUMENT
            else DefinitionManager if spec.definition_editor else RecordManager
        )
        popup = (
            ToolManager(
                view, request, open_directory=lambda path: host.open_directory(path)
            )
            if spec.manager == ManagerKind.TOOLS
            else record_manager(
                view,
                request,
                name=name,
                edit_text=lambda *args, **kwargs: host.edit_text(*args, **kwargs),
            )
        )
        view.dialogs.show(popup, popup.path)
        if edit_identifier is not None and spec.manager == ManagerKind.AGENT:
            popup.selected = edit_identifier
            popup.open_item()
        else:
            popup.reload()
