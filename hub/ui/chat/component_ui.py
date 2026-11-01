"""Component managers and help share one small slash-command entry point."""


from ...backend.project.component_commands import parse_arguments
from ...components import component_spec, ManagerKind
from ...backend.project.management_values import ManagementAction
from ...locales.keys import MessageKey
from prompt_toolkit.application.current import get_app
from .. import host
from ..input.commands import CommandSyntax


class ComponentCommands:
    NO_PROGRESS = None
    CONSUME_COMMAND = True

    def __init__(self, controller):
        self.controller, self.view = controller, controller.view

    def open(self, command: str, argument: str, *, consume_input=CONSUME_COMMAND,
             edit_identifier=None, completed=None):
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
        if edit_identifier is not None and spec.manager == ManagerKind.AGENT:
            self.edit_agent(popup, edit_identifier, completed=completed)
        else:
            view.dialogs.show(popup, popup.path)
            popup.reload()

    def edit_agent(self, manager, identifier, *, completed=None):
        """Read the same revisioned record without displaying its manager first."""
        from ...widget.forms.agent_editor import AgentEditor

        view = self.view
        parent, focus = view._dialog, get_app().layout.current_control
        token = view.progress.start(view.t(MessageKey.COMPONENT_LOADING))

        def restore():
            if parent is not None:
                view.dialogs.show(parent, focus)
            else:
                # Remove the editor's return hook before closing the root popup.
                view._dialog.on_close = None
                view.close_dialog()

        def ready(record):
            view.progress.finish(token)
            if completed:
                completed(record)
            if record is not None and view._dialog is parent:
                editor = AgentEditor(manager, identifier, record, on_return=restore)
                view.dialogs.show(editor, editor.workspace.sidebar)
                editor.load()

        try:
            manager.request(ManagementAction.READ, identifier, None,
                            view.progress.callback(token, ready), progress=token)
        except Exception:
            view.progress.finish(token)
            if completed:
                completed(None)
            raise
