"""Schema-based node properties with explicit, local draft application."""

from llm.core.schema import SchemaKeyword
from llm.core.schema import SchemaType
from llm.components.workflows.contracts import WorkflowDocument
from llm.components.workflows.contracts import WorkflowNode

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, DynamicContainer, Window
from prompt_toolkit.layout.layout import walk
from prompt_toolkit.layout.containers import to_container
from prompt_toolkit.widgets import Label

from ...locales.keys import MessageKey as K
from ...config.theme_style import ThemeSelector
from ..controls import Button, TextArea, ScrollablePane
from ..section import Heading
from ..notices import Notice, NoticeText
from ..forms.settings import SchemaForm, SettingField, MISSING
from ..actions import ActionBar
from ..dialog import PopupGeometry
from ...ui.input.registry import ShortcutRegistry
from ...ui.input.keys import KeyName, ShortcutLabel, ShortcutAction
from ...ui.input.json_document import JsonDocument


class WorkflowInspector:
    DESCRIPTIONS = {
        'agent': K.WORKFLOW_DESC_AGENT, 'emit_text': K.WORKFLOW_DESC_EMIT_TEXT,
        'output_format': K.WORKFLOW_DESC_OUTPUT_FORMAT, WorkflowNode.PAUSE_BEFORE: K.WORKFLOW_DESC_PAUSE,
        WorkflowNode.TIMEOUT_SECONDS: K.WORKFLOW_DESC_TIMEOUT, 'tool': K.WORKFLOW_DESC_TOOL,
        'arguments': K.WORKFLOW_DESC_ARGUMENTS, WorkflowNode.DEFAULT: K.WORKFLOW_DESC_DEFAULT,
        WorkflowNode.JOIN: K.WORKFLOW_DESC_JOIN, WorkflowNode.MAX_ITERATIONS: K.WORKFLOW_DESC_LIMIT,
    }

    def __init__(self, editor):
        self.editor = editor
        self.form = None
        self.name = None
        self.title = None
        self.title_field = None
        self.special = []
        self.last_control = None
        self.body = Window()
        self.pane = ScrollablePane(DynamicContainer(lambda: self.body))
        t = editor.view.t
        self.apply_button = Button(t(K.WORKFLOW_APPLY), handler=editor.apply_properties)
        self.cancel_button = Button(t(K.CANCEL), handler=editor.cancel_properties)
        self.actions = ActionBar([self.apply_button, self.cancel_button],
                                 lambda: editor.INSPECTOR_WIDTH - PopupGeometry.FRAME_COLUMNS, equal_width=True)
        self.container = HSplit([self.pane, Window(height=1), self.actions, Window(height=1)])

    def fields(self):
        return ([self.title_field] if self.title_field else []) + (list(self.form.fields.values()) if self.form else [])

    @property
    def editing(self):
        return any(field.editing for field in self.fields())

    @property
    def dirty(self):
        return any(field.text != field.initial for field in self.fields())

    @property
    def actions_focused(self):
        return get_app().layout.current_control in (self.apply_button.control, self.cancel_button.control)

    @property
    def focused(self):
        current = get_app().layout.current_control
        return any(isinstance(item, Window) and item.content is current for item in walk(to_container(self.container)))

    def finish(self):
        for field in self.fields():
            field.finish()

    def show(self, name):
        self.finish()
        self.name, self.form, self.title, self.title_field = name, None, None, None
        self.special = []
        self.last_control = None
        e, t = self.editor, self.editor.view.t
        node = e.document.graph()[WorkflowDocument.NODES].get(name)
        if node is None:
            self.body = Notice(t(K.WORKFLOW_SELECT_NODE))
            return
        if not isinstance(node, dict) or e.catalog is None:
            self.body = HSplit([Notice(t(K.WORKFLOW_UNVERIFIED)),
                                Button(t(K.WORKFLOW_JSON), handler=e.advanced_json)])
            return
        schema = e.catalog.node_schema(node.get(WorkflowNode.TYPE), e.engine)
        for key, spec in schema.get(SchemaKeyword.PROPERTIES, {}).items():
            if key in self.DESCRIPTIONS and isinstance(spec, dict) and not spec.get(SchemaKeyword.DESCRIPTION):
                spec[SchemaKeyword.DESCRIPTION] = t(self.DESCRIPTIONS[key])
        self.form = SchemaForm(schema, node, t)
        metadata = node.get(WorkflowNode.METADATA, {})
        ui = metadata.get(e.document.UI_KEY, {}) if isinstance(metadata, dict) else {}
        title = ui.get(e.document.UI_TITLE, MISSING) if isinstance(ui, dict) else MISSING
        self.title_field = SettingField(t(K.WORKFLOW_TITLE),
            {SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.DESCRIPTION: t(K.WORKFLOW_TITLE_NOTE)}, title, t)
        self.title = self.title_field.input
        special = []
        if e.catalog.field_schema(node.get(WorkflowNode.TYPE), WorkflowNode.CASES):
            special.append(Button(t(K.WORKFLOW_CASES), handler=e.dialogs.cases))
        special.append(Button(t(K.WORKFLOW_BINDINGS), handler=e.dialogs.bindings))
        if e.catalog.field_schema(node.get(WorkflowNode.TYPE), WorkflowNode.BODY):
            special.append(Button(t(K.WORKFLOW_BODY), handler=e.enter_body))
        if e.catalog.field_schema(node.get(WorkflowNode.TYPE), WorkflowNode.WORKFLOW):
            special.append(Label(t(K.WORKFLOW_REFERENCE_NOTE), style=ThemeSelector.MUTED.style))
        special.append(Button(t(K.WORKFLOW_ENTRY), handler=e.set_entry))
        self.special = [item for item in special if isinstance(item, Button)]
        self.body = HSplit([
            Heading(str(name)), Label(str(node.get(WorkflowNode.TYPE, ''))),
            self.title_field.container,
            self.form.container, *special,
        ], padding=0)
        for field in self.fields():
            field.enabled = lambda: not e.busy
            field.on_edit = lambda item: get_app().create_background_task(self.edit_field(item))
            field.on_change += e.update_dirty
            self.bind_navigation(field.input.control, lambda f=field: not f.editing)
        for button in self.special:
            self.bind_navigation(button.control, lambda: True)
        self.pane.vertical_scroll = 0
        e.update_dirty()

    def bind_navigation(self, control, available):
        registry = ShortcutRegistry.require(control)
        registry.add([KeyName.UP, KeyName.DOWN], ShortcutLabel.VERTICAL, ShortcutAction.SELECT,
                     lambda event: self.move(-1 if event.key_sequence[-1].key == KeyName.UP else 1), when=available)
        registry.add([KeyName.LEFT], ShortcutLabel.LEFT, ShortcutAction.MAIN,
                     lambda event: self.editor.navigation.focus(self.editor.canvas), when=available)
        registry.add([KeyName.RIGHT], ShortcutLabel.HIDDEN, ShortcutAction.NONE, lambda event: None, when=available)

    def move(self, delta):
        controls = [field.input for field in self.fields()] + self.special
        current = get_app().layout.current_control
        index = next((i for i, item in enumerate(controls) if item.control is current), 0)
        self.finish()
        if controls:
            get_app().layout.focus(controls[max(0, min(len(controls) - 1, index + delta))])

    async def edit_field(self, field):
        e = self.editor
        if e.busy or not e.manager.edit_text or field.readonly or not field.external_editable:
            return
        e.busy = True
        try:
            field.text = await e.manager.edit_text(field.text, e.view.general, suffix=JsonDocument.SUFFIX)
        except Exception as error:
            e.status = NoticeText.error(str(error), e.view.t).inline()
        finally:
            e.busy = False
            field.finish()

    def apply(self):
        if not self.form or self.name not in self.editor.document.graph()[WorkflowDocument.NODES]:
            return
        from copy import deepcopy
        current = self.editor.document.graph()[WorkflowDocument.NODES][self.name]
        node = deepcopy(current)
        values = self.form.values()
        # A canvas drag may have changed metadata while this form was open.
        # Apply only edited form properties, never its stale whole-node snapshot.
        missing = object()
        for key in self.form.original.keys() | values.keys():
            if not JsonDocument.same(self.form.original.get(key, missing), values.get(key, missing), missing=missing):
                if key in values:
                    node[key] = values[key]
                else:
                    node.pop(key, None)
        if self.title_field.text != self.title_field.initial:
            title = self.title_field.value()
            metadata = self.editor.document.metadata(node)
            if title is MISSING:
                metadata.pop(self.editor.document.UI_TITLE, None)
            else:
                metadata[self.editor.document.UI_TITLE] = title
        self.editor.document.put_node(self.name, node)

    def focus(self):
        controls = [field.input.control for field in self.fields()] + [button.control for button in self.special]
        if self.last_control in controls:
            get_app().layout.focus(self.last_control)
        else:
            get_app().layout.focus(self.title if self.title else self.editor.canvas)
