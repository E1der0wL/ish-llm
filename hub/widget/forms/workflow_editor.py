"""Workflow draft operations and explicit, non-executing backend inspection."""

from copy import deepcopy
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.containers import to_container

from hub.locales.keys import MessageKey
from hub.backend.project.management_values import ManagementAction
from hub.ui.input.json_document import JsonDocument
from .definition_manager import DefinitionEditor
from .manager_values import DefinitionDraft
from .settings import SchemaForm
from ..actions import ActionBar
from ..controls import Button, RadioList, TextArea


class WorkflowDraft:
    """UI transformations preserve raw fields; the backend retains validation/CAS."""
    def __init__(self, value):
        self.value = deepcopy(value)

    def put_node(self, name, node):
        if not isinstance(name, str) or not name.strip() or not isinstance(node, dict):
            raise ValueError('A node needs a nonempty ID and JSON object')
        self.value['nodes'][name] = deepcopy(node)

    def delete_node(self, name):
        del self.value['nodes'][name]
        # Keep edges and entry visible as invalid drafts until explicitly corrected.
        # Deleting a node must not silently rewrite the user's control flow.

    def add_edge(self, source, target, port):
        if source not in self.value['nodes'] or target not in self.value['nodes']:
            raise ValueError('Select existing endpoint nodes')
        edge = {'source': source, 'target': target}
        if port:
            edge['port'] = port
        if edge in self.value['edges']:
            raise ValueError('Duplicate edge')
        self.value['edges'].append(edge)


class WorkflowEditor(DefinitionEditor):
    def __init__(self, manager, name='', record=None):
        self._closed = False
        self._inspection = 0
        self._report = None
        super().__init__(manager, name, record)
        actions = [
            Button(self.view.t(MessageKey.WORKFLOW_NODE_EDIT), handler=self.edit_node),
            Button(self.view.t(MessageKey.WORKFLOW_NODE_DELETE), handler=self.delete_node),
            Button(self.view.t(MessageKey.WORKFLOW_EDGE_ADD), handler=self.add_edge),
            Button(self.view.t(MessageKey.WORKFLOW_EDGE_DELETE), handler=self.delete_edge),
            Button(self.view.t(MessageKey.WORKFLOW_INSPECT), handler=self.inspect),
            Button(self.view.t(MessageKey.WORKFLOW_LOCATION), handler=self.locate),
        ]
        # Preserve the common title/identifier and final status row. Reuse the
        # wrapping action bar so narrow terminals retain every edit operation.
        self.body.children.insert(self.body.children.index(to_container(self.identifier)) + 1,
                                  ActionBar(actions, lambda: max(1, self._width().preferred - 2)))

    def draft(self):
        value = DefinitionDraft.parse_object(self.input.text, self.view.t)
        if not isinstance(value.get('nodes'), dict) or not isinstance(value.get('edges'), list):
            raise ValueError('Workflow draft requires nodes and edges')
        return WorkflowDraft(value)

    def read_draft(self):
        try:
            return self.draft()
        except (ValueError, TypeError) as error:
            self.status = str(error)
            return None

    def install(self, draft):
        self.input.text = JsonDocument.render(draft.value, allow_non_finite=False)
        self.view.dialogs.show(self, self.input)
        self.refresh_preview()

    def choose(self, values, accept):
        if not values:
            return
        choices = RadioList(values)
        self.view.open_dialog(self.view.t(MessageKey.CHOOSE_TARGET), choices,
                              lambda: accept(choices.current_value), choices,
                              on_cancel=lambda: self.view.dialogs.show(self, self.input))

    def edit_node(self):
        try:
            draft = self.draft()
        except ValueError as error:
            self.status = str(error)
            return
        # Node IDs are entered explicitly; an existing ID edits only that node.
        identifier = TextArea(multiline=False)
        body = TextArea(text='{}', multiline=True)

        def load(_):
            node = draft.value['nodes'].get(identifier.text)
            if node is not None:
                body.text = JsonDocument.render(node, allow_non_finite=False)
        identifier.buffer.on_text_changed += load

        def apply():
            try:
                draft.put_node(identifier.text, DefinitionDraft.parse_object(body.text, self.view.t))
            except (ValueError, KeyError, TypeError) as error:
                self.view.notice = str(error)
                return False
            return True

        def options():
            try:
                node = DefinitionDraft.parse_object(body.text, self.view.t)
                kind = node['type']
            except (ValueError, KeyError) as error:
                self.view.notice = str(error)
                return
            text, parent = body.text, self.view._dialog

            def restore():
                self.view.dialogs.show(parent, identifier)

            def catalog(value):
                if value is None or self._closed or body.text != text or self.view._dialog is not parent:
                    return
                available = {name: kinds[kind] for name, kinds in value['engines'].items()
                             if kinds.get(kind) is not None}
                if not available:
                    self.view.notice = self.view.t(MessageKey.WORKFLOW_UNVERIFIED)
                    return
                engines = RadioList([(name, name) for name in available])

                def selected():
                    common = set(value['common_fields'])
                    form = SchemaForm(available[engines.current_value],
                                      {key: item for key, item in node.items() if key not in common}, self.view.t)
                    updated = {}

                    def validate():
                        try:
                            values = form.values()
                            if common.intersection(values):
                                raise ValueError('Handler options cannot replace Graph fields')
                            updated.update({key: item for key, item in node.items() if key in common})
                            updated.update(values)
                            return True
                        except ValueError as error:
                            self.view.notice = str(error)
                            return False

                    def accept():
                        body.text = JsonDocument.render(updated, allow_non_finite=False)
                        restore()
                    self.view.open_dialog(self.view.t(MessageKey.WORKFLOW_NODE_OPTIONS), form.container,
                                          accept, validate=validate, on_cancel=restore)
                self.view.open_dialog(self.view.t(MessageKey.CHOOSE_TARGET), engines, selected,
                                      engines, on_cancel=restore)
            self.manager.request(ManagementAction.DESCRIBE, '', None, catalog)

        self.view.open_dialog(self.view.t(MessageKey.WORKFLOW_NODE_EDIT), HSplit([
            identifier, Button(self.view.t(MessageKey.WORKFLOW_NODE_OPTIONS), handler=options), body]),
                              lambda: self.install(draft), identifier, validate=apply,
                              on_cancel=lambda: self.view.dialogs.show(self, self.input))

    def delete_node(self):
        draft = self.read_draft()
        if draft is None:
            return
        def remove(name):
            draft.delete_node(name)
            self.install(draft)
        self.choose([(name, name) for name in draft.value['nodes']], remove)

    def add_edge(self):
        draft = self.read_draft()
        if draft is None:
            return
        values = [(name, name) for name in draft.value['nodes']]
        if not values:
            return
        source, target, port = RadioList(values), RadioList(values), TextArea(multiline=False)
        def validate():
            try:
                draft.add_edge(source.current_value, target.current_value, port.text)
            except ValueError as error:
                self.view.notice = str(error)
                return False
            return True
        self.view.open_dialog(self.view.t(MessageKey.WORKFLOW_EDGE_ADD), HSplit([source, target, port]),
                              lambda: self.install(draft), source, validate=validate,
                              on_cancel=lambda: self.view.dialogs.show(self, self.input))

    def delete_edge(self):
        draft = self.read_draft()
        if draft is None:
            return
        def remove(index):
            del draft.value['edges'][index]
            self.install(draft)
        self.choose([(index, JsonDocument.render(edge, allow_non_finite=False)) for index, edge in enumerate(draft.value['edges'])], remove)

    def inspect(self):
        if self.busy:
            return
        try:
            definition = self.draft().value
        except ValueError as error:
            self.status = str(error)
            return
        self._inspection += 1
        generation, text = self._inspection, self.input.text

        def current():
            return not self._closed and generation == self._inspection and self.input.text == text

        def result(report):
            if report is None or not current() or self.view._dialog is not self:
                return
            self._report = (text, report)
            lines = [f"{item['details'].get('path', '')}: {item['message']}" for item in report['diagnostics']]
            lines.extend(f"{item['component']}/{item['id']} @ {item['version']}" for item in report.get('references', []))
            if report['unverified']:
                lines.append(self.view.t(MessageKey.WORKFLOW_UNVERIFIED) + ': ' + ', '.join(report['unverified']))
            self.status = '\n'.join(lines) if lines else self.view.t(MessageKey.DEFINITION_VALIDATION)
            get_app().invalidate()

        def selected(engine):
            self.view.dialogs.show(self, self.input)
            if current():
                self.manager.request(ManagementAction.INSPECT, {'definition': definition, 'engine': engine}, None, result)

        def catalog(value):
            if value is None or not current() or self.view._dialog is not self:
                return
            if not value['engines']:
                self.status = self.view.t(MessageKey.NO_TARGETS)
                return
            self.choose([(name, name) for name in value['engines']], selected)

        self.manager.request(ManagementAction.DESCRIBE, '', None, catalog)

    def locate(self):
        if self._report is None or self._report[0] != self.input.text:
            self.status = self.view.t(MessageKey.WORKFLOW_INSPECT)
            return
        text, report = self._report
        def selected(index):
            if self._closed or self.input.text != text:
                return
            issue = report['diagnostics'][index]
            self.view.dialogs.show(self, self.input)
            self.input.buffer.cursor_position = JsonDocument.locate(text, issue['details']['path'])
            self.status = issue['details']['path'] + ': ' + issue['message']
        self.choose([(index, item['details']['path'] + ': ' + item['message'])
                     for index, item in enumerate(report['diagnostics'])], selected)

    def close(self):
        if not self.busy:
            self._closed = True
        super().close()
