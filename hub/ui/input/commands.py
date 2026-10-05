"""Slash commands route to the same screen actions as keyboard input."""


class HubCommands:
    def __init__(self, view):
        self.view = view

    def execute(self, text):
        view = self.view
        if not text.startswith("/"):
            return False
        command, _, argument = text.partition(" ")
        if command[1:] in view.component_commands:
            if view.on_component:
                view.on_component(command[1:], argument.strip())
            return True
        actions = {"/help": view.help_dialog, "/engine": lambda: view.engine_dialog(argument.strip()),
                   "/new": lambda: view.session_dialog("new", argument.strip()),
                   "/clone": lambda: view.session_dialog("clone", argument.strip()),
                   "/preview": lambda: setattr(view, "show_preview", not view.show_preview),
                   "/details": view.toggle_details,
                   "/stop": lambda: view.on_interrupt and view.on_interrupt(view.sessions[view.selected].id)}
        if command not in actions:
            view.notice = view.t("unknown_command", command=command)
            return True
        view.composer.text = ""
        actions[command]()
        return True

