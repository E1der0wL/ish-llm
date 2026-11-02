"""Small authoring dialogs share the standard Hub popup and form contracts."""

from llm.core.schema import SchemaKeyword
from llm.components.workflows.contracts import WorkflowCase
from llm.components.workflows.contracts import WorkflowDocument
from llm.components.workflows.contracts import WorkflowEdge
from llm.components.workflows.contracts import WorkflowNode

from copy import deepcopy
from llm.components.workflows.contracts import WorkflowCondition
from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.widgets import Label

from ...locales.keys import MessageKey as K
from ...ui.input.json_document import JsonDocument
from ..controls import Button, TextArea, RadioList, ScrollablePane
from ..dialog import PopupSize
from ..section import Heading
from ..forms.manager_values import DefinitionDraft
from ..forms.settings import SchemaForm


class WorkflowDialogs:
    def __init__(self, editor):
        self.editor = editor

    def open(self, title, body, apply, focus=None, *, size=PopupSize.MEDIUM):
        e = self.editor
        def validate():
            try:
                apply()
                return True
            except (ValueError, KeyError, TypeError) as error:
                e.view.notice = str(error)
                return False
        e.view.notice = ''
        e.view.open_dialog(title, body, e.restore, focus,
                           validate=validate, on_cancel=e.restore, size=size, parent=e)

    def choose(self, title, values, accept):
        if not values:
            self.editor.status = self.editor.view.t(K.NO_TARGETS)
            return
        choices = RadioList(values)
        def selected():
            accept(choices.current_value)
            if self.editor.view._dialog is None:
                self.editor.restore()
        self.editor.view.open_dialog(title, choices, selected, choices,
                                     on_cancel=self.editor.restore, parent=self.editor)

    def add_node(self):
        e, t = self.editor, self.editor.view.t
        if not e.flush() or not e.require_catalog():
            return
        name = TextArea(multiline=False, height=1)
        types = RadioList([(kind, kind) for kind in e.catalog.kinds(e.engine)])
        def apply():
            key = name.text.strip()
            if key in e.document.graph()[WorkflowDocument.NODES]:
                raise ValueError(t(K.WORKFLOW_DUPLICATE_NODE))
            e.document.put_node(key, {WorkflowNode.TYPE: types.current_value})
            e.selected = key
            e.changed(rebuild=True)
        self.open(t(K.WORKFLOW_ADD), HSplit([Heading(t(K.DEFINITION_NAME)), name,
                  Window(height=1), Heading(t(K.WORKFLOW_NODE_TYPE)), types]), apply, name)

    def delete_node(self):
        e, t = self.editor, self.editor.view.t
        if not e.flush() or e.selected is None:
            return
        name = e.selected
        def apply():
            e.document.delete_node(name)
            e.selected = None
            e.changed(rebuild=True)
        self.open(t(K.WORKFLOW_NODE_DELETE), Label(t(K.WORKFLOW_DELETE_NOTE, name=name)), apply)

    def edges(self):
        e, t = self.editor, self.editor.view.t
        if not e.flush():
            return
        values = [(index, f"{edge.get(WorkflowEdge.SOURCE, '?')} → {edge.get(WorkflowEdge.TARGET, '?')}  {edge.get(WorkflowEdge.PORT, '')}")
                  for index, edge in enumerate(e.document.graph()[WorkflowDocument.EDGES]) if isinstance(edge, dict)]
        self.choose(t(K.WORKFLOW_EDGES), values, self.edge)

    def edge(self, index=None):
        e, t = self.editor, self.editor.view.t
        if not e.flush():
            return
        graph = e.document.graph()
        values = [(name, e.document.title(name)) for name in graph[WorkflowDocument.NODES]]
        if not values:
            return
        source, target = RadioList(values), RadioList(values)
        original = deepcopy(graph[WorkflowDocument.EDGES][index]) if index is not None else {}
        source.current_value = original.get(WorkflowEdge.SOURCE, e.selected or values[0][0])
        target.current_value = original.get(WorkflowEdge.TARGET, values[-1][0])
        port = TextArea(text=str(original.get(WorkflowEdge.PORT, '')), multiline=False, height=1)
        def apply():
            if index is None:
                e.document.add_edge(source.current_value, target.current_value, port.text.strip())
            else:
                updated = {**original, WorkflowEdge.SOURCE: source.current_value, WorkflowEdge.TARGET: target.current_value}
                if port.text:
                    updated[WorkflowEdge.PORT] = port.text
                else:
                    updated.pop(WorkflowEdge.PORT, None)
                e.document.change(lambda value: value[WorkflowDocument.EDGES].__setitem__(index, updated))
            e.changed()
        children = [VSplit([HSplit([Heading(t(K.WORKFLOW_SOURCE)), source]),
                            HSplit([Heading(t(K.WORKFLOW_TARGET)), target])], padding=1),
                    Heading(t(K.WORKFLOW_PORT)), port]
        if index is not None:
            def remove():
                e.document.delete_edge(index)
                e.changed()
                e.restore()
            children.append(Button(t(K.WORKFLOW_EDGE_DELETE), handler=remove))
        self.open(t(K.WORKFLOW_EDGE_ADD if index is None else K.WORKFLOW_EDGES), HSplit(children), apply, source)

    def cases(self):
        e, t = self.editor, self.editor.view.t
        if not e.flush() or not e.require_catalog():
            return
        node = e.document.graph()[WorkflowDocument.NODES][e.selected]
        values = node.get(WorkflowNode.CASES, [])
        schema = e.catalog.field_schema(node[WorkflowNode.TYPE], WorkflowNode.CASES).get(SchemaKeyword.ITEMS, {})
        descriptions = {
            (WorkflowEdge.PORT,): K.WORKFLOW_DESC_PORT,
            (WorkflowCase.WHEN, WorkflowCondition.PATH): K.WORKFLOW_DESC_CONDITION_PATH,
            (WorkflowCase.WHEN, WorkflowCondition.OPERATOR): K.WORKFLOW_DESC_CONDITION_OP,
            (WorkflowCase.WHEN, WorkflowCondition.VALUE): K.WORKFLOW_DESC_CONDITION_VALUE,
        }
        for path, message in descriptions.items():
            field = schema
            for part in path:
                field = field.get(SchemaKeyword.PROPERTIES, {}).get(part, {})
            if field and not field.get(SchemaKeyword.DESCRIPTION):
                field[SchemaKeyword.DESCRIPTION] = t(message)
        if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
            e.status = t(K.WORKFLOW_JSON)
            return
        def select(index):
            value = deepcopy(values[index]) if index is not None else {}
            form = SchemaForm(schema, value, t)
            def apply():
                updated = form.values()
                def edit(graph):
                    items = graph[WorkflowDocument.NODES][e.selected].setdefault(WorkflowNode.CASES, [])
                    if index is None:
                        items.append(updated)
                    else:
                        items[index] = updated
                e.document.change(edit)
                e.changed(rebuild=True)
            children = [form.container]
            if index is not None:
                def remove():
                    e.document.change(lambda graph: graph[WorkflowDocument.NODES][e.selected][WorkflowNode.CASES].pop(index))
                    e.changed(rebuild=True)
                    e.restore()
                children.append(Button(t(K.SETTINGS_DELETE), handler=remove))
            self.open(t(K.WORKFLOW_CASES), ScrollablePane(HSplit(children)), apply,
                      next(iter(form.fields.values())).input if form.fields else None)
        self.choose(t(K.WORKFLOW_CASES), [(None, t(K.WORKFLOW_ADD)),
                    *((i, str(item.get(WorkflowEdge.PORT, i))) for i, item in enumerate(values))],
                    select)

    def bindings(self):
        from .bindings import WorkflowBindings
        e = self.editor
        if not e.flush() or e.selected is None:
            return
        try:
            popup = WorkflowBindings(e)
        except ValueError as error:
            e.status = str(error)
            return
        self.open(e.view.t(K.WORKFLOW_BINDINGS), popup.tabs, popup.apply, popup.tabs.buttons[0])

    def graph_options(self):
        e = self.editor
        if not e.flush() or not e.require_catalog():
            return
        form = SchemaForm(e.catalog.graph_schema(), e.document.graph(), e.view.t)
        def apply():
            value = form.values()
            def edit(graph):
                graph.clear()
                graph.update(value)
            e.document.change(edit)
            e.changed(rebuild=True)
        self.open(e.view.t(K.WORKFLOW_GRAPH_OPTIONS), ScrollablePane(form.container), apply,
                  next(iter(form.fields.values())).input if form.fields else None)

    def json(self):
        e = self.editor
        if not e.flush():
            return
        text = TextArea(text=e.input.text, multiline=True, scrollbar=True, wrap_lines=False)
        def apply():
            DefinitionDraft.require_size(text.text, e.view.t)
            value = DefinitionDraft.parse_object(text.text, e.view.t)
            from .draft import WorkflowDraft
            WorkflowDraft(value).graph()
            e.document.replace(value)
            e.changed(rebuild=True)
        self.open(e.view.t(K.WORKFLOW_JSON), text, apply, text, size=PopupSize.LARGE)
