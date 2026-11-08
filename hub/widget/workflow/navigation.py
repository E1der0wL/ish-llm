"""Workflow focus regions, field/action navigation and editor-local shortcuts."""

from enum import StrEnum
from prompt_toolkit.application.current import get_app

from ...ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from ...ui.input.registry import ShortcutRegistry
from ..mouse import window_scroll


class WorkflowRegion(StrEnum):
    NAME = 'name'
    NODES = 'nodes'
    WORKSPACE = 'workspace'
    ACTIONS = 'actions'


class WorkflowNavigation:
    def __init__(self, editor):
        self.editor = editor
        self.registry = ShortcutRegistry()
        for key, redo in ((KeyName.CONTROL_Z, False), (KeyName.CONTROL_Y, True)):
            self.registry.add([key], ShortcutLabel.HIDDEN, ShortcutAction.NONE,
                              lambda event, value=redo: editor.undo(redo=value),
                              when=lambda: (editor.view._dialog is editor and not editor.inspector.editing
                                            and get_app().layout.current_control is not editor.identifier.control),
                              global_=True)

    def region(self):
        e, current = self.editor, get_app().layout.current_control
        if e.view._dialog is not e:
            return None
        if current is e.identifier.control:
            return WorkflowRegion.NAME
        if current is e.sidebar:
            return WorkflowRegion.NODES
        if current in [button.control for button in e.buttons]:
            return WorkflowRegion.ACTIONS
        return WorkflowRegion.WORKSPACE

    def focus(self, target):
        self.editor.inspector.finish()
        get_app().layout.focus(target)
        get_app().invalidate()

    def toolbar(self, lower=False):
        bar = self.editor.lower_toolbar if lower else self.editor.toolbar
        self.focus(bar.buttons[0])

    @property
    def escape_action(self):
        region = self.region()
        if region == WorkflowRegion.ACTIONS:
            return ShortcutAction.CLOSE
        if get_app().layout.current_control is self.editor.canvas:
            return ShortcutAction.SIDEBAR
        return ShortcutAction.MAIN

    def escape(self):
        e = self.editor
        choice = next((field for field in e.inspector.fields() if field.menu_open), None)
        if choice is not None:
            choice.input.close()
        elif e.inspector.editing:
            e.inspector.finish()
        elif self.region() == WorkflowRegion.ACTIONS:
            e.close()
        elif self.region() in (WorkflowRegion.NODES, WorkflowRegion.NAME):
            self.focus(e.buttons[0])
        elif get_app().layout.current_control is e.canvas:
            self.focus(e.sidebar)
        else:
            self.focus(e.canvas)

    def cycle(self, reverse=False):
        e, current = self.editor, get_app().layout.current_control
        if any(field.menu_open for field in e.inspector.fields()):
            return
        if e.inspector.focused:
            if e.inspector.actions_focused:
                e.inspector.focus()
            else:
                e.inspector.last_control = current
                e.inspector.finish()
                self.focus(e.inspector.apply_button)
            return
        if current is e.canvas and not reverse:
            e.inspector.focus()
            return
        if self.region() == WorkflowRegion.WORKSPACE and current is not e.canvas:
            self.focus(e.canvas)
            return
        targets = [e.identifier, e.sidebar, e.canvas, e.buttons[0]]
        regions = list(WorkflowRegion)
        index = regions.index(self.region() or WorkflowRegion.NAME)
        self.focus(targets[(index + (-1 if reverse else 1)) % len(targets)])

    def scroll(self, key):
        if self.editor.inspector.focused:
            self.editor.inspector.pane.scroll(key)
        elif self.region() == WorkflowRegion.NODES:
            window = self.editor.sidebar_window
            if window.render_info is not None:
                window_scroll(window).scroll(key)
        else:
            self.editor.canvas.scroll(key)
        get_app().invalidate()
