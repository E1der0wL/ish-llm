"""Graph-first authoring; public inspection and CAS remain backend-owned."""

from llm.components.workflows.contracts import WorkflowDocument
from llm.components.workflows.contracts import WorkflowEdge
from llm.components.workflows.contracts import WorkflowNode

from prompt_toolkit.application.current import get_app
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.widgets import Frame, Label
from prompt_toolkit.layout.dimension import Dimension

from hub.locales.keys import MessageKey as K
from hub.backend.project.management_values import ManagementAction
from hub.backend.project.workflow_contracts import WorkflowInspectionRequest, WorkflowInspectionResponse
from llm.core.json_data import JsonPointer
from hub.ui.input.json_document import JsonDocument
from hub.ui.input.keys import KeyName
from hub.config.theme_style import ThemeSelector
from .definition_manager import DefinitionEditor
from .manager_values import DefinitionDraft
from ..actions import ActionBar
from ..controls import Button, ScrollbarMargin, TextArea
from ..sidebar import SidebarList, SidebarItem
from ..workflow.draft import WorkflowDraft
from ..workflow.catalog import WorkflowCatalog
from ..workflow.canvas import GraphCanvas
from ..workflow.inspector import WorkflowInspector
from ..workflow.dialogs import WorkflowDialogs
from ..workflow.navigation import WorkflowNavigation, WorkflowRegion
from ..focus_region import FocusRegion
from ..section import Heading
from prompt_toolkit.utils import get_cwidth


class WorkflowEditor(DefinitionEditor):
    BUTTON_INSET = 2
    PATH_SEPARATOR = ' / '
    UNTITLED = 'Workflow'
    UNKNOWN_ENGINE = '—'
    LOCATION = '{scope}  •  {engine}  •  x:{x} y:{y}'
    DIRTY_PREFIX = '* '
    SEPARATOR = ' • '
    REFERENCE = '{component}/{identifier} @ {version}'
    DETAIL_SEPARATOR = ': '
    LIST_SEPARATOR = ', '
    NEWLINE = '\n'
    INSPECTOR_ATTRIBUTE = 'inspector'
    CANVAS_ATTRIBUTE = 'canvas'
    LIST_WIDTH = 21
    INSPECTOR_WIDTH = 35
    LIST_MIN_COLUMNS = 115
    EMPTY_STATUS = ''
    UPPER_ACTION_KEY = 'a'
    HELP_KEY = '?'
    NARROW_LIST_WIDTH = 14
    NARROW_INSPECTOR_WIDTH = 25

    def __init__(self, manager, name='', record=None):
        self._closed = False
        self._inspection = 0
        self._report = None
        self.catalog = None
        self.engine = None
        self._loading_catalog = False
        super().__init__(manager, name, record)
        self.document = WorkflowDraft(DefinitionDraft.parse_object(self.input.text, self.view.t))
        self.document.graph()
        self.selected = next(iter(self.document.graph()[WorkflowDocument.NODES]), None)
        self.saved_text = self.input.text
        self._synced_revision = self.document.revision
        self.dialogs = WorkflowDialogs(self)
        self.canvas = GraphCanvas(self.document, lambda: self.selected, self.select,
                                  self.move_node, {
            self.UPPER_ACTION_KEY: (lambda: self.navigation.toolbar(), K.WORKFLOW_UPPER_ACTIONS),
            KeyName.S: (lambda: self.navigation.toolbar(lower=True), K.WORKFLOW_LOWER_ACTIONS),
            KeyName.CONTROL_Z: (self.undo, K.WORKFLOW_UNDO),
            KeyName.CONTROL_Y: (lambda: self.undo(redo=True), K.WORKFLOW_REDO),
            self.HELP_KEY: (self.help, K.WORKFLOW_HELP),
        })
        self.inspector = WorkflowInspector(self)
        self.navigation = WorkflowNavigation(self)
        self.scroll_target = self.navigation
        keys = KeyBindings()
        keys.add(KeyName.UP)(lambda e: self.sidebar.move(-1))
        keys.add(KeyName.DOWN)(lambda e: self.sidebar.move(1))
        for key in (KeyName.ENTER, KeyName.SPACE, KeyName.RIGHT):
            keys.add(key)(lambda e: self.navigation.focus(self.canvas))
        self.sidebar = SidebarList(self.items, lambda: self.selected, self.select,
                                   key_bindings=keys, on_activate=lambda: self.navigation.focus(self.canvas))
        self.sidebar_window = Window(self.sidebar, right_margins=[ScrollbarMargin()])
        t = self.view.t
        self.toolbar = ActionBar([
            Button(t(K.WORKFLOW_ADD), handler=self.add_node),
            Button(t(K.WORKFLOW_NODE_DELETE), handler=self.delete_node),
            Button(t(K.WORKFLOW_EDGES), handler=self.dialogs.edges),
            Button(t(K.WORKFLOW_EDGE_ADD), handler=self.add_edge),
            Button(t(K.WORKFLOW_ARRANGE), handler=self.arrange),
            Button(t(K.WORKFLOW_UNDO), handler=self.undo),
            Button(t(K.WORKFLOW_REDO), handler=lambda: self.undo(redo=True)),
            Button(t(K.WORKFLOW_JSON), handler=self.advanced_json),
        ], self.toolbar_width)
        self.lower_toolbar = ActionBar([
            Button(t(K.WORKFLOW_GRAPH_OPTIONS), handler=self.dialogs.graph_options),
            Button(t(K.WORKFLOW_ENGINE), handler=self.choose_engine),
            Button(t(K.WORKFLOW_INSPECT), handler=self.inspect),
            Button(t(K.WORKFLOW_LOCATION), handler=self.locate),
            Button(t(K.WORKFLOW_BACK), handler=self.leave_body),
            Button(t(K.WORKFLOW_HELP), handler=self.help),
        ], self.toolbar_width)
        for button in [*self.toolbar.buttons, *self.lower_toolbar.buttons]:
            button.width = max(button.width, get_cwidth(button.text) + self.BUTTON_INSET)
            button.window.width = Dimension.exact(button.width)
        name = HSplit([Heading(t(K.WORKFLOW_NAME)), self.identifier])

        def workspace_region(content):
            return FocusRegion(content,
                               active=lambda: self.navigation.region() == WorkflowRegion.WORKSPACE,
                               on_click=lambda: self.navigation.focus(self.canvas))

        self.body = HSplit([
            FocusRegion(name, active=lambda: self.navigation.region() == WorkflowRegion.NAME,
                        on_click=lambda: self.navigation.focus(self.identifier)),
            workspace_region(HSplit([Window(height=1), self.toolbar, Window(height=1)])),
            VSplit([
                FocusRegion(Frame(self.sidebar_window, title=t(K.WORKFLOW_NODES),
                                  width=lambda: Dimension.exact(self.sidebar_width())),
                            active=lambda: self.navigation.region() == WorkflowRegion.NODES,
                            on_click=lambda: self.navigation.focus(self.sidebar)),
                workspace_region(VSplit([
                    Frame(self.canvas, title=t(K.WORKFLOW_CANVAS)),
                    Frame(self.inspector.container, title=t(K.WORKFLOW_PROPERTIES),
                          width=lambda: Dimension.exact(self.INSPECTOR_WIDTH if self.view._columns() >= self.LIST_MIN_COLUMNS else self.NARROW_INSPECTOR_WIDTH)),
                ], padding=1)),
            ], padding=1),
            workspace_region(HSplit([
                self.lower_toolbar, Window(height=1),
                Label(lambda: self.breadcrumb(), style=ThemeSelector.MUTED.style),
            ])),
        ], key_bindings=self.navigation.registry.bindings)
        self.inspector.show(self.selected)
        self.identifier.buffer.on_text_changed += self.update_dirty

    def sidebar_width(self):
        return self.LIST_WIDTH if self.view._columns() >= self.LIST_MIN_COLUMNS else self.NARROW_LIST_WIDTH

    def toolbar_width(self):
        from ..dialog import PopupGeometry
        return max(1, self._width().preferred - PopupGeometry.FRAME_COLUMNS - 2 * self._content_padding)

    def actions_active(self):
        return self.navigation.region() == WorkflowRegion.ACTIONS

    def actions_focus(self):
        self.navigation.focus(self.buttons[0])

    @property
    def focus_target(self):
        return self.canvas

    def refresh_preview(self):
        self._preview_timer = None

    def schedule_preview(self, _=None):
        # Graph paint consumes the draft; never parse JSON or query services on paint.
        pass

    def load(self):
        if self._loading_catalog or self.catalog is not None:
            return
        self._loading_catalog = True
        token = self.view.progress.start(self.view.t(K.COMPONENT_LOADING))
        def ready(value):
            self._loading_catalog = False
            self.view.progress.finish(token)
            if self._closed:
                return
            if value is None:
                self.status = self.view.t(K.WORKFLOW_UNVERIFIED)
            else:
                self.catalog = WorkflowCatalog(value)
                self.engine = next(iter(self.catalog.engines), None)
                self.inspector.show(self.selected)
            get_app().invalidate()
        self.manager.request(ManagementAction.DESCRIBE, '', None, self.view.progress.callback(token, ready))

    def require_catalog(self):
        if self.catalog is None:
            self.status = self.view.t(K.COMPONENT_LOADING if self._loading_catalog else K.WORKFLOW_UNVERIFIED)
            self.load()
            return False
        return True

    def breadcrumb(self):
        scope = self.PATH_SEPARATOR.join((self.identifier.text or self.UNTITLED, *self.document.path))
        return self.LOCATION.format(scope=scope, engine=self.engine or self.UNKNOWN_ENGINE,
                                    x=self.canvas.offset_x, y=self.canvas.offset_y)

    def items(self):
        return [SidebarItem(name, self.document.title(name)) for name in self.document.graph()[WorkflowDocument.NODES]]

    def flush(self):
        if self.busy or self._closed:
            return False
        try:
            self.inspector.apply()
        except (ValueError, TypeError, KeyError) as error:
            self.status = str(error)
            return False
        self.changed()
        return True

    def changed(self, *, rebuild=False):
        if self._synced_revision != self.document.revision:
            self.input.text = JsonDocument.render(self.document.value, allow_non_finite=False)
            self._synced_revision = self.document.revision
        if self._report is not None and self._report[0] != self.input.text:
            self._report = None
            self.canvas.errors = frozenset()
            self.status = self.EMPTY_STATUS
        if self.selected not in self.document.graph()[WorkflowDocument.NODES]:
            self.selected = next(iter(self.document.graph()[WorkflowDocument.NODES]), None)
            rebuild = True
        if rebuild:
            self.inspector.show(self.selected)
        self.highlight_report()
        self.update_dirty()
        get_app().invalidate()

    def update_dirty(self, _=None):
        pending = self.inspector.dirty
        dirty = (pending or self.input.text != self.saved_text
                 or self.identifier.text != self.original_name)
        self.buttons[0].text = (self.DIRTY_PREFIX if dirty else '') + self.view.t(K.SETTINGS_SAVE)
        self.inspector.apply_button.text = (self.DIRTY_PREFIX if pending else '') + self.view.t(K.WORKFLOW_APPLY)
        get_app().invalidate()

    def select(self, name):
        if name not in self.document.graph()[WorkflowDocument.NODES] or not self.flush():
            return False
        if name != self.selected:
            self.selected = name
            self.inspector.show(name)
        self.canvas.reveal()
        get_app().invalidate()
        return True

    def focus_properties(self):
        self.inspector.focus()

    def apply_properties(self):
        if self.flush():
            self.inspector.show(self.selected)
            self.inspector.focus()

    def cancel_properties(self):
        if not self.busy:
            self.inspector.show(self.selected)
            self.update_dirty()
            self.inspector.focus()

    def restore(self):
        if self.view._dialog is not self:
            self.view.dialogs.show(self, self.canvas)
        self.canvas.reveal()

    def add_node(self):
        self.dialogs.add_node()

    def delete_node(self):
        self.dialogs.delete_node()

    def add_edge(self):
        self.dialogs.edge()

    def advanced_json(self):
        self.dialogs.json()

    def help(self):
        body = TextArea(text=self.canvas.help_text(self.view.t), read_only=True, scrollbar=True)
        self.view.open_dialog(self.view.t(K.WORKFLOW_HELP), body, self.restore, body,
                              on_cancel=self.restore, parent=self)

    def arrange(self):
        if self.flush():
            try:
                self.document.arrange()
            except ValueError as error:
                self.status = str(error)
                return
            self.changed(rebuild=True)
            self.canvas.reveal()

    def move_node(self, name, x, y):
        if self.flush():
            try:
                self.document.move(name, x, y)
            except ValueError as error:
                self.status = str(error)
                return
            self.changed()

    def undo(self, redo=False):
        if not self.flush():
            return
        if self.document.history(redo):
            self.changed(rebuild=True)
            self.canvas.reveal()

    def set_entry(self):
        if self.selected is not None and self.flush():
            self.document.set_entry(self.selected)
            self.changed()

    def enter_body(self):
        if self.selected is None or not self.flush() or not self.require_catalog():
            return
        node = self.document.graph()[WorkflowDocument.NODES][self.selected]
        if not isinstance(node, dict) or not self.catalog.field_schema(node.get(WorkflowNode.TYPE), WorkflowNode.BODY):
            return
        try:
            if WorkflowNode.BODY not in node:
                self.document.change(lambda graph: graph[WorkflowDocument.NODES][self.selected].__setitem__(
                    WorkflowNode.BODY, DefinitionDraft.create(WorkflowDocument.COMPONENT)))
            self.document.enter(self.selected)
        except ValueError as error:
            self.status = str(error)
            return
        self.selected = None
        self.changed(rebuild=True)
        self.restore()

    def leave_body(self):
        if self.document.path and self.flush():
            self.selected = self.document.path[-1]
            self.document.leave()
            self.changed(rebuild=True)
            self.restore()

    def choose_engine(self):
        if not self.flush() or not self.require_catalog():
            return
        def choose(name):
            self.engine = name
            self.inspector.show(self.selected)
            self._report = None
            self.canvas.errors = frozenset()
        self.dialogs.choose(self.view.t(K.WORKFLOW_ENGINE), [(name, name) for name in self.catalog.engines], choose)

    def inspect(self):
        if not self.flush() or not self.require_catalog() or self.engine is None:
            return
        self._inspection += 1
        generation, text, engine = self._inspection, self.input.text, self.engine
        self.busy = True
        token = self.view.progress.start(self.view.t(K.WORKFLOW_INSPECT))
        def result(report):
            self.busy = False
            self.view.progress.finish(token)
            if self._closed or generation != self._inspection or self.input.text != text or self.engine != engine:
                return
            if report is not None:
                report = WorkflowInspectionResponse.read(report)
                self._report = (text, report)
                self.highlight_report()
                self.status = self.view.t(K.WORKFLOW_VALIDATION_RESULT, count=len(report.diagnostics))
                if report.unverified:
                    self.status += self.SEPARATOR + self.view.t(K.WORKFLOW_UNVERIFIED)
            get_app().invalidate()
        self.manager.request(ManagementAction.INSPECT,
                             WorkflowInspectionRequest(self.document.value, engine).to_dict(), None,
                             self.view.progress.callback(token, result))

    @staticmethod
    def pointer_parts(path):
        return list(JsonPointer.parts(path))

    def highlight_report(self):
        prefix = [part for name in self.document.path for part in (WorkflowDocument.NODES, name, WorkflowNode.BODY)]
        errors = set()
        if self._report:
            for issue in self._report[1].diagnostics:
                parts = self.pointer_parts(WorkflowInspectionResponse.location(issue))
                if parts[:len(prefix)] != prefix:
                    continue
                tail = parts[len(prefix):]
                if len(tail) >= 2 and tail[0] == WorkflowDocument.NODES:
                    errors.add(tail[1])
                elif len(tail) >= 2 and tail[0] == WorkflowDocument.EDGES:
                    try:
                        edge = self.document.graph()[WorkflowDocument.EDGES][int(tail[1])]
                    except (ValueError, IndexError):
                        continue
                    if isinstance(edge, dict):
                        errors.update(name for name in (edge.get(WorkflowEdge.SOURCE), edge.get(WorkflowEdge.TARGET))
                                      if isinstance(name, str) and name in self.document.graph()[WorkflowDocument.NODES])
                elif tail == [WorkflowDocument.ENTRY]:
                    entry = self.document.graph().get(WorkflowDocument.ENTRY)
                    if isinstance(entry, str):
                        errors.add(entry)
        self.canvas.errors = frozenset(errors)

    def locate(self):
        if not self.flush() or self._report is None:
            self.status = self.view.t(K.WORKFLOW_INSPECT)
            return
        text, report = self._report
        if not report.diagnostics:
            lines = [self.status]
            lines.extend(self.REFERENCE.format(component=item.component, identifier=item.id, version=item.version) for item in report.references)
            if report.unverified:
                lines.append(self.view.t(K.WORKFLOW_UNVERIFIED) + self.DETAIL_SEPARATOR + self.LIST_SEPARATOR.join(report.unverified))
            body = TextArea(text=self.NEWLINE.join(lines), read_only=True, scrollbar=True)
            self.view.open_dialog(self.view.t(K.WORKFLOW_INSPECT), body,
                                  self.restore, body, on_cancel=self.restore, parent=self)
            return
        def selected(index):
            if self.input.text != text:
                return
            issue = report.diagnostics[index]
            path = WorkflowInspectionResponse.location(issue)
            parts = self.pointer_parts(path)
            self.document.path = ()
            self.selected = None
            while len(parts) >= 2 and parts[0] == WorkflowDocument.NODES and parts[1] in self.document.graph()[WorkflowDocument.NODES]:
                self.selected = parts[1]
                parts = parts[2:]
                if parts and parts[0] == WorkflowNode.BODY:
                    try:
                        self.document.enter(self.selected)
                    except ValueError:
                        break
                    parts = parts[1:]
                else:
                    break
            self.inspector.show(self.selected)
            self.highlight_report()
            self.status = path + self.DETAIL_SEPARATOR + issue.message
            if len(parts) >= 2 and parts[0] == WorkflowDocument.EDGES:
                try:
                    index = int(parts[1])
                    edge = self.document.graph()[WorkflowDocument.EDGES][index]
                except (ValueError, IndexError):
                    return
                if isinstance(edge, dict):
                    self.dialogs.edge(index)
            elif parts == [WorkflowDocument.ENTRY]:
                self.dialogs.graph_options()
        self.dialogs.choose(self.view.t(K.WORKFLOW_LOCATION),
                            [(index, WorkflowInspectionResponse.location(item) + self.DETAIL_SEPARATOR + item.message)
                             for index, item in enumerate(report.diagnostics)], selected)

    def save(self):
        if self.flush():
            super().save()

    def close(self):
        if not self.busy:
            self._closed = True
            self.inspector.finish()
        super().close()

    def dispose(self):
        self._closed = True
        super().dispose()
        if hasattr(self, self.INSPECTOR_ATTRIBUTE):
            self.inspector.finish()
        if hasattr(self, self.CANVAS_ATTRIBUTE):
            self.canvas.drag = self.canvas.drag_position = None
            self.canvas.pan_anchor = None
