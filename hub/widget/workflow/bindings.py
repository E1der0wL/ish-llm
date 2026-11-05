"""One modal draft for the selected node's input and output bindings."""

from llm.components.workflows.bindings import WorkflowBindings as BindingFields
from llm.components.workflows.contracts import WorkflowDocument

from copy import deepcopy
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label

from ...locales.keys import MessageKey as K
from ...config.theme_style import ThemeSelector
from ...ui.input.keys import KeyName
from ..controls import Button, TextArea, ScrollbarMargin
from ..section import Heading
from ..sidebar import SidebarList, SidebarItem
from ..actions import ActionBar
from ..tab_page import TabPage


class WorkflowBindings:
    DIRECTIONS = (BindingFields.INPUTS, BindingFields.OUTPUTS)
    BODY_WIDTH = 62
    LIST_WIDTH = 25
    LIST_MINIMUM = 14

    def __init__(self, editor):
        self.editor = editor
        self.node_name = editor.selected
        node = editor.document.graph()[WorkflowDocument.NODES][self.node_name]
        self.values = {key: deepcopy(node.get(key, {})) for key in self.DIRECTIONS}
        if any(not isinstance(value, dict) for value in self.values.values()):
            raise ValueError(editor.view.t(K.WORKFLOW_JSON))
        self.changed = set()
        self.direction = BindingFields.INPUTS
        self.selected = None
        self.initial = ('', '')
        self.name = TextArea(multiline=False, height=1)
        self.path = TextArea(multiline=False, height=1)
        keys = KeyBindings()
        keys.add(KeyName.UP)(lambda event: self.list.move(-1))
        keys.add(KeyName.DOWN)(lambda event: self.list.move(1))
        for key in (KeyName.RIGHT, KeyName.ENTER, KeyName.SPACE):
            keys.add(key)(lambda event: event.app.layout.focus(self.name))
        self.list = SidebarList(self.items, lambda: self.selected, self.select,
                                key_bindings=keys, on_activate=lambda: get_app().layout.focus(self.name))
        t = editor.view.t
        self.actions = ActionBar([
            Button(t(K.COMPONENT_CREATE), handler=self.new),
            Button(t(K.WORKFLOW_APPLY), handler=self.store_action),
            Button(t(K.SETTINGS_DELETE), handler=self.remove),
        ], lambda: self.BODY_WIDTH, equal_width=True)
        content = HSplit([
            Window(height=1),
            VSplit([
                HSplit([Heading(lambda: t(K.WORKFLOW_INPUTS if self.direction == BindingFields.INPUTS else K.WORKFLOW_OUTPUTS)),
                        Window(self.list, right_margins=[ScrollbarMargin()],
                               style=ThemeSelector.POPUP_LIST.style)], width=Dimension(preferred=self.LIST_WIDTH, min=self.LIST_MINIMUM)),
                HSplit([Heading(t(K.WORKFLOW_BINDING_NAME)), self.name, Window(height=1),
                        Heading(t(K.WORKFLOW_BINDING_PATH)), self.path, Window(height=1),
                        Label(t(K.WORKFLOW_BINDING_NOTE), style=ThemeSelector.MUTED.style), Window()]),
            ], padding=1),
            Window(height=1), self.actions,
        ])
        self.tabs = TabPage([
            (BindingFields.INPUTS, t(K.WORKFLOW_INPUTS), content),
            (BindingFields.OUTPUTS, t(K.WORKFLOW_OUTPUTS), content),
        ], width=lambda: self.BODY_WIDTH, on_change=self.switch, on_enter=lambda: get_app().layout.focus(self.list))
        self.load(next(iter(self.values[self.direction]), None))

    def items(self):
        return [SidebarItem(key, key, str(value)) for key, value in self.values[self.direction].items()]

    def load(self, key):
        self.selected = key
        self.name.text = key or ''
        self.path.text = str(self.values[self.direction].get(key, ''))
        self.initial = self.name.text, self.path.text
        get_app().invalidate()

    def store(self):
        if (self.name.text, self.path.text) == self.initial:
            return
        name = self.name.text
        if not name.strip():
            raise ValueError(self.editor.view.t(K.WORKFLOW_BINDING_NAME))
        values = self.values[self.direction]
        if name != self.selected and name in values:
            raise ValueError(self.editor.view.t(K.WORKFLOW_DUPLICATE_NODE))
        value = (values[self.selected] if self.selected in values and self.path.text == self.initial[1]
                 else self.path.text)
        if self.selected is not None:
            values.pop(self.selected)
        values[name] = value
        self.changed.add(self.direction)
        self.load(name)

    def store_action(self):
        try:
            self.store()
        except ValueError as error:
            self.editor.view.notice = str(error)
            return False
        self.editor.view.notice = ''
        return True

    def select(self, key):
        if key != self.selected and self.store_action():
            self.load(key)

    def switch(self, key):
        if not self.store_action():
            self.tabs.selected = self.direction
            return
        self.direction = key
        self.load(next(iter(self.values[key]), None))

    def new(self):
        if self.store_action():
            self.load(None)
            get_app().layout.focus(self.name)

    def remove(self):
        if self.selected is not None:
            self.values[self.direction].pop(self.selected)
            self.changed.add(self.direction)
            self.load(next(iter(self.values[self.direction]), None))

    def apply(self):
        self.store()
        def edit(graph):
            node = graph[WorkflowDocument.NODES][self.node_name]
            for direction in self.changed:
                node[direction] = deepcopy(self.values[direction])
        self.editor.document.change(edit)
        self.editor.changed(rebuild=True)
