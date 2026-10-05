"""Component command dialogs on the prompt-toolkit thread."""

import json

from prompt_toolkit.widgets import Label

from ...backend.component_commands import parse_arguments
from ...widget.reader import ReadOnlyDialog


class ComponentCommands:
    def __init__(self, controller):
        self.controller = controller
        self.view = controller.view
        self.busy = False

    def open(self, command: str, argument: str):
        view = self.view
        if self.busy:
            view.notice = view.t("component_busy")
            return
        try:
            action, identifier, _ = parse_arguments(argument, view.t)
        except (ValueError, TypeError) as error:
            self.controller._error(error)
            return
        project_id = view.project_id
        session_id = view.sessions[view.selected].id
        name = view.component_commands[command]
        original = view.composer.text

        def execute():
            self.busy = True
            background = name == "rag" and action in ("create", "update")
            progress = view.progress.start(view.t("progress_component", command=f"/{command} {action}"))
            if not background:
                view.open_dialog("/" + command, Label(view.t("component_loading")))
            pending = view._dialog

            def completed(result):
                self.busy = False
                view.progress.finish(progress)
                present = not background and view._dialog is pending
                if present:
                    view.close_dialog()
                if result is None:
                    return
                if view._drafts.get(session_id) == original:
                    view._drafts[session_id] = ""
                same = (project_id, session_id) == (view.project_id, view.sessions[view.selected].id)
                if same and view.composer.text == original:
                    view.composer.text = ""
                if background:
                    view.toasts.alert("/" + command, view.t("progress_component_done", identifier=identifier), "success")
                    return
                if not same or not present or not view.visible:
                    return
                if action == "settings":
                    screen = view.settings
                    view.settings_open = True
                    screen.loaded_catalog(result["catalog"])
                    cached = screen.pages.get(project_id)
                    if cached is not None and name in cached.components:
                        screen.selected = screen._left_key = project_id
                        screen.show_page(screen.pages[project_id])
                    else:
                        screen.loaded_project(result["project"])
                    page = screen.page
                    form = page.component_forms.get(name)
                    widgets = page.body_widgets()
                    target = (next(iter(form.fields.values())).input if form and form.fields
                              else widgets[0] if widgets else page.save)
                    if target is not None:
                        screen._main_zone = 0 if widgets else 1
                        self.controller.app.layout.focus(target)
                else:
                    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
                    popup = ReadOnlyDialog(f"/{command} {action}", text, view._rows, view._columns,
                                           view.close_dialog, view.t)
                    view.dialogs.show(popup, popup.receiver)
                self.controller.app.invalidate()

            self.controller._call("component_command", project_id, command, argument, completed=completed)

        if action == "delete":
            view.open_dialog(view.t("component_delete"),
                Label(view.t("component_delete_confirm", name=name, identifier=identifier)), execute)
        else:
            execute()
