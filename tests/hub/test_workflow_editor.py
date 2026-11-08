"""Graph authoring preserves backend ownership, lossless drafts and revisions."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput, create_pipe_input
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType, MouseButton
from prompt_toolkit.data_structures import Point

from examples.hub.preview import create_preview
from hub.widget.forms.workflow_editor import WorkflowEditor
from hub.widget.workflow.draft import WorkflowDraft
from hub.widget.workflow.catalog import WorkflowCatalog
from hub.widget.tab_page import TabPage
from hub.backend.project.management_values import EditorRecord, ManagementAction
from hub.widget.controls import TextArea
from llm.components.workflows.graph import workflow_schema, validate_graph
from llm.components.workflows.inspection import WorkflowInspection
from llm.core.contracts import Diagnostic, ResourceRef
from hub.backend.project.workflow_contracts import WorkflowInspectionResponse
from tests.hub.support import SizedOutput, eventually, render_container


def definition():
    return {'schema_version': 1, 'entry': 'finish', 'nodes': {'finish': {'type': 'end'}}, 'edges': [],
            'metadata': {'ui': {'position': {'finish': [5, 8]}}}}


def catalog():
    return {'schema': workflow_schema(), 'engines': {'graph': {'agent': {
        'type': 'object', 'properties': {'agent_id': {'type': 'string', 'description': 'Agent ID'}},
        'required': ['agent_id']}}}, 'common_fields': []}


class WorkflowDraftTests(TestCase):
    def test_edit_preserves_source_and_metadata_and_delete_exposes_invalid_edges(self):
        source = definition()
        original = deepcopy(source)
        draft = WorkflowDraft(source)
        draft.put_node('a', {'type': 'custom', 'private_option': 'retained'})
        draft.add_edge('a', 'finish', '')
        draft.delete_node('a')
        self.assertEqual(draft.value['edges'], [{'source': 'a', 'target': 'finish'}])
        self.assertEqual(draft.value['metadata'], source['metadata'])
        self.assertEqual(source, original)

    def test_layout_and_history_never_change_execution_fields(self):
        draft = WorkflowDraft(definition())
        draft.arrange()
        self.assertEqual(draft.value['entry'], 'finish')
        validate_graph(draft.value)
        draft.move('finish', 60, 40)
        self.assertEqual(draft.positions()['finish'].x, 60)
        self.assertTrue(draft.history())
        self.assertEqual(draft.positions()['finish'].x, 2)
        self.assertTrue(draft.history(redo=True))
        self.assertEqual(draft.positions()['finish'].x, 60)

    def test_nested_edits_undo_restore_scope_and_keep_reference_workflows(self):
        draft = WorkflowDraft(definition())
        draft.put_node('repeat', {'type': 'loop', 'body': definition(), 'max_iterations': 2})
        draft.put_node('ref', {'type': 'workflow', 'workflow': 'external'})
        draft.enter('repeat')
        draft.move('finish', 20, 5)
        self.assertEqual(draft.path, ('repeat',))
        draft.leave()
        self.assertTrue(draft.history())
        self.assertEqual(draft.path, ('repeat',))
        draft.leave()
        self.assertEqual(draft.graph()['nodes']['ref']['workflow'], 'external')

    def test_failed_edit_is_atomic_and_unknown_metadata_is_not_replaced(self):
        draft = WorkflowDraft(definition())
        draft.put_node('finish', {'type': 'end', 'metadata': {'hub_graph': 'foreign'}})
        before = deepcopy(draft.value)
        with self.assertRaises(ValueError):
            draft.move('finish', 1, 2)
        self.assertEqual(draft.value, before)

    def test_catalog_uses_backend_controls_and_handler_fields(self):
        value = WorkflowCatalog(catalog())
        self.assertIn('loop', value.kinds('graph'))
        self.assertIn('agent', value.kinds('graph'))
        self.assertIn('agent_id', value.node_schema('agent', 'graph')['properties'])
        self.assertEqual(value.field_schema('loop', 'max_iterations')['minimum'], 1)


class WorkflowEditorTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app, self.view, _ = create_preview(input=DummyInput(), output=SizedOutput())
        self.app.loop = asyncio.get_running_loop()
        self.enterContext(set_app(self.app))
        self.view.visible = True
        self.manager = SimpleNamespace(name='workflows', view=self.view, request=Mock(), list=TextArea())
        self.editor = WorkflowEditor(self.manager, 'test', EditorRecord.create(EditorRecord.render(definition()), 'revision'))
        self.view.dialogs.show(self.editor, self.editor.canvas)
        self.editor.load()
        self.manager.request.call_args.args[-1](catalog())
        self.manager.request.reset_mock()

    async def asyncTearDown(self):
        self.view.close()

    def confirm(self):
        self.view._dialog._buttons[0].handler()

    def install(self, value):
        self.editor.document.replace(value)
        self.editor.changed(rebuild=True)

    async def test_inspection_is_nonexecuting_and_discards_stale_results(self):
        self.editor.inspect()
        args = self.manager.request.call_args.args
        self.assertEqual(args[:2], (ManagementAction.INSPECT, {'definition': definition(), 'engine': 'graph'}))
        self.editor.input.text = '{}'
        args[-1]({'valid': False, 'diagnostics': [{'message': 'stale', 'details': {'path': '/entry'}}], 'unverified': []})
        self.assertNotIn('stale', self.editor.status)
        self.assertIsNone(self.editor._report)
        self.assertEqual(self.editor.version, 'revision')
        self.assertFalse(self.editor.busy)

    async def test_disposed_editor_ignores_late_catalog_without_replacing_popup(self):
        from hub.widget.dialog import Dialog
        self.editor.catalog = None
        self.editor.load()
        reply = self.manager.request.call_args.args[-1]
        replacement = TextArea(text='next popup')
        self.view.dialogs.show(Dialog(body=replacement), replacement)
        popup = self.view._dialog
        reply(catalog())
        self.assertTrue(self.editor._closed)
        self.assertIsNone(self.editor.catalog)
        self.assertIs(self.view._dialog, popup)

    async def test_disposed_editor_ignores_late_inspection(self):
        from hub.widget.dialog import Dialog
        self.editor.inspect()
        reply = self.manager.request.call_args.args[-1]
        replacement = TextArea(text='next popup')
        self.view.dialogs.show(Dialog(body=replacement), replacement)
        reply(WorkflowInspectionResponse.write(WorkflowInspection([], [], [])))
        self.assertTrue(self.editor._closed)
        self.assertIsNone(self.editor._report)
        self.assertIs(self.app.layout.current_control, replacement.control)

    async def test_invalid_advanced_json_stays_open_and_cancel_preserves_graph(self):
        self.editor.advanced_json()
        popup = self.view._dialog
        popup.body.text = '{'
        self.confirm()
        self.assertIs(self.view._dialog, popup)
        self.assertEqual(self.editor.document.value, definition())
        popup._buttons[1].handler()
        self.assertIs(self.view._dialog, self.editor)
        self.manager.request.assert_not_called()

    async def test_advanced_json_preserves_unknown_fields_and_saves_original_revision(self):
        self.editor.advanced_json()
        value = definition()
        value['metadata']['external_ui'] = {'anything': 17}
        self.view._dialog.body.text = EditorRecord.render(value)
        self.confirm()
        self.assertIs(self.view._dialog, self.editor)
        self.assertEqual(self.editor.document.value, value)
        self.editor.save()
        action, argument, version, done = self.manager.request.call_args.args
        self.assertEqual(action, ManagementAction.SAVE)
        self.assertEqual(version, 'revision')
        self.assertIn('external_ui', argument['text'])
        done(None)
        self.assertIs(self.view._dialog, self.editor)
        self.assertEqual(self.editor.document.value, value)

    async def test_invalid_graph_values_remain_editable_and_inspectable_without_paint_crashes(self):
        self.editor.advanced_json()
        value = definition()
        value['entry'] = []
        value['nodes']['finish']['type'] = {'invalid': True}
        value['edges'] = [{'source': [], 'target': 'finish'}, {'source': 'finish', 'target': {}}]
        self.view._dialog.body.text = EditorRecord.render(value)
        self.confirm()
        self.assertIs(self.view._dialog, self.editor)
        content = self.editor.canvas.create_content(75, 25)
        self.assertGreater(content.line_count, 0)
        self.assertEqual(self.editor.document.value, value)
        self.editor.inspect()
        self.assertEqual(self.manager.request.call_args.args[1]['definition'], value)
        self.manager.request.call_args.args[-1](WorkflowInspectionResponse.write(WorkflowInspection([
            Diagnostic('workflow_invalid', 'entry must be a string', details={'path': '/entry'})], [], [])))
        self.assertFalse(self.editor.busy)
        self.assertEqual(self.editor.canvas.errors, frozenset())
        self.editor.arrange()
        self.assertEqual(self.editor.document.value['edges'], value['edges'])
        self.assertEqual(self.editor.document.value['entry'], [])

    async def test_property_edit_does_not_revert_canvas_position_or_custom_options(self):
        value = definition()
        value['nodes']['finish'] = {'type': 'agent', 'agent_id': 'assistant', 'custom': 7}
        self.install(value)
        self.editor.move_node('finish', 40, 9)
        self.editor.inspector.form.fields[('agent_id',)].text = 'planner'
        self.editor.apply_properties()
        node = self.editor.document.value['nodes']['finish']
        self.assertEqual(node['agent_id'], 'planner')
        self.assertEqual(node['custom'], 7)
        self.assertEqual(node['metadata']['hub_graph']['position'], [40, 9])
        self.editor.undo()
        self.assertEqual(self.editor.document.value['nodes']['finish']['agent_id'], 'assistant')

    async def test_node_add_and_edge_editor_return_to_parent_and_are_local(self):
        self.editor.add_node()
        popup = self.view._dialog
        name = popup.body.children[1].content.buffer
        name.text = 'new'
        self.confirm()
        self.assertEqual(self.editor.selected, 'new')
        self.editor.add_edge()
        self.confirm()
        self.assertEqual(len(self.editor.document.graph()['edges']), 1)
        self.editor.dialogs.edges()
        self.confirm()
        self.assertIsNot(self.view._dialog, self.editor)
        self.confirm()
        self.assertIs(self.view._dialog, self.editor)
        self.manager.request.assert_not_called()

    async def test_branch_condition_and_binding_popups_keep_parent_draft(self):
        value = definition()
        value['nodes']['branch'] = {'type': 'branch', 'cases': []}
        self.install(value)
        self.editor.select('branch')
        self.editor.dialogs.cases()
        self.confirm()
        self.assertIsNot(self.view._dialog, self.editor)
        self.view._dialog._buttons[1].handler()
        self.assertIs(self.view._dialog, self.editor)
        self.editor.dialogs.bindings()
        popup = self.view._dialog
        bindings = popup.body
        # The combined popup exposes the real input/output draft, not a second editor.
        self.assertIsInstance(bindings, TabPage)
        owner = bindings.on_change.__self__
        owner.name.text = 'question'
        owner.path.text = '/input'
        owner.switch('outputs')
        owner.name.text = 'answer'
        owner.path.text = '/result'
        self.confirm()
        self.assertEqual(self.editor.document.graph()['nodes']['branch']['inputs'], {'question': '/input'})
        self.assertEqual(self.editor.document.graph()['nodes']['branch']['outputs'], {'answer': '/result'})
        self.assertIs(self.view._dialog, self.editor)
        self.manager.request.assert_not_called()

    async def test_diagnostics_select_node_and_nested_scope_without_rewriting(self):
        value = definition()
        value['nodes']['loop'] = {'type': 'loop', 'body': definition()}
        self.install(value)
        text = self.editor.input.text
        self.editor._report = (text, WorkflowInspection([Diagnostic('workflow_invalid', 'example',
            details={'path': '/nodes/loop/body/nodes/finish/type'})], [], []))
        self.editor.highlight_report()
        self.assertEqual(self.editor.canvas.errors, {'loop'})
        self.editor.locate()
        self.confirm()
        self.assertEqual(self.editor.input.text, text)
        self.assertEqual(self.editor.document.path, ('loop',))
        self.assertEqual(self.editor.selected, 'finish')
        self.assertEqual(self.editor.canvas.errors, {'finish'})

    async def test_condition_form_updates_one_case_and_preserves_other_branches(self):
        from hub.widget.forms.settings import SchemaForm
        value = definition()
        old = {'port': 'old', 'when': {'path': '/flag', 'op': 'eq', 'value': True}}
        value['nodes']['route'] = {'type': 'branch', 'cases': [old], 'default': 'fallback'}
        self.install(value)
        self.editor.select('route')
        forms = []
        def form(*args, **kwargs):
            result = SchemaForm(*args, **kwargs)
            forms.append(result)
            return result
        with patch('hub.widget.workflow.dialogs.SchemaForm', side_effect=form):
            self.editor.dialogs.cases()
            self.view._dialog.body.current_value = 0
            self.confirm()
        selected = forms[-1]
        selected.fields[('port',)].text = 'yes'
        selected.fields[('when', 'value')].text = 'false'
        self.confirm()
        node = self.editor.document.graph()['nodes']['route']
        self.assertEqual(node['cases'], [{'port': 'yes', 'when': {'path': '/flag', 'op': 'eq', 'value': False}}])
        self.assertEqual(node['default'], 'fallback')

    async def test_edge_diagnostic_opens_connection_form_without_changing_draft(self):
        value = definition()
        value['edges'] = [{'source': 'finish', 'target': 'missing'}]
        self.install(value)
        self.editor._report = (self.editor.input.text, WorkflowInspection([
            Diagnostic('workflow_invalid', 'missing target', details={'path': '/edges/0/target'})], [], []))
        self.editor.highlight_report()
        self.assertEqual(self.editor.canvas.errors, {'finish'})
        self.editor.locate()
        self.confirm()
        self.assertIsNot(self.view._dialog, self.editor)
        self.assertEqual(self.view._dialog.title, self.view.t('workflow_edges'))
        self.assertEqual(self.editor.document.value, value)
        self.view._dialog._buttons[1].handler()
        self.assertIs(self.view._dialog, self.editor)

    async def test_invalid_property_prevents_selection_and_keeps_typed_value(self):
        value = definition()
        value['nodes']['other'] = {'type': 'end'}
        self.install(value)
        field = self.editor.inspector.form.fields[('pause_before',)]
        field.text = '{ invalid'
        self.assertTrue(self.editor.select('finish'))
        self.assertIs(self.editor.inspector.form.fields[('pause_before',)], field)
        self.assertFalse(self.editor.select('other'))
        self.assertEqual(self.editor.selected, 'finish')
        self.assertEqual(field.text, '{ invalid')
        self.assertNotIn('pause_before', self.editor.document.graph()['nodes']['finish'])

    async def test_toolbar_and_history_shortcuts_replace_removed_graph_commands(self):
        control = self.editor.canvas
        from prompt_toolkit.keys import Keys
        for key in (Keys.ShiftRight, Keys.PageUp, Keys.PageDown, Keys.Home, Keys.End,
                    Keys.Enter, ' ', 'd', 'c', 'e', 'l', 'u', 'y', 'j', 'v', 'i', 'o', 'b'):
            self.assertEqual(control.get_key_bindings().get_bindings_for_keys((key,)), [])
        for key, bar in (('a', self.editor.toolbar), ('s', self.editor.lower_toolbar)):
            binding = control.get_key_bindings().get_bindings_for_keys((key,))[-1]
            binding.handler(SimpleNamespace())
            self.assertIs(self.app.layout.current_control, bar.buttons[0].control)
        self.assertNotIn('Shift+←↓↑→', control.help_text(self.view.t))

    async def test_binding_cancel_discards_local_changes_and_retains_parent(self):
        self.editor.dialogs.bindings()
        owner = self.view._dialog.body.on_change.__self__
        owner.name.text, owner.path.text = ' input ', '/question'
        owner.store_action()
        self.view.dialogs.close()
        self.assertEqual(self.editor.document.value, definition())
        self.assertIs(self.view._dialog, self.editor)
        self.assertFalse(self.editor._closed)
        self.manager.request.assert_not_called()

    async def test_reference_and_unverified_details_are_available_without_errors(self):
        self.editor._report = (self.editor.input.text, WorkflowInspection([], ['runtime_authorization'],
            [ResourceRef('definition', 'assistant', component='agents', version='revision')]))
        self.editor.locate()
        self.assertIn('agents/assistant @ revision', self.view._dialog.body.text)
        self.assertIn('runtime_authorization', self.view._dialog.body.text)
        self.confirm()
        self.assertIs(self.view._dialog, self.editor)

    async def test_mouse_drag_records_one_undo_and_empty_click_only_focuses(self):
        canvas = self.editor.canvas
        canvas.create_content(70, 20)
        original = self.editor.document.positions()['finish']
        def mouse(kind, x, y):
            canvas.mouse_handler(MouseEvent(Point(x, y), kind, MouseButton.LEFT, frozenset()))
        mouse(MouseEventType.MOUSE_DOWN, 4, 2)
        mouse(MouseEventType.MOUSE_MOVE, 10, 6)
        mouse(MouseEventType.MOUSE_MOVE, 15, 8)
        mouse(MouseEventType.MOUSE_UP, 15, 8)
        self.assertEqual(len(self.editor.document.undo_stack), 1)
        self.assertNotEqual(self.editor.document.positions()['finish'], original)
        mouse(MouseEventType.MOUSE_DOWN, 69, 19)
        mouse(MouseEventType.MOUSE_UP, 69, 19)
        self.assertEqual(self.editor.selected, 'finish')
        self.editor.undo()
        self.assertEqual(self.editor.document.positions()['finish'], original)

    async def test_canvas_cache_viewport_cjk_and_no_backend_work_on_paint(self):
        node = {'type': 'end', 'metadata': {'hub_graph': {'title': '한글 노드 😀'}}}
        self.editor.document.put_node('finish', node)
        self.editor.changed(rebuild=True)
        canvas = self.editor.canvas
        canvas.create_content(70, 20)
        lines = canvas._lines
        canvas.create_content(70, 20)
        self.assertIs(canvas._lines, lines)
        self.assertIn('한글', ''.join(text for line in lines for _, text in line))
        self.editor.move_node('finish', 10000000, 10000000)
        canvas.create_content(50, 10)
        self.assertEqual(len(canvas._lines), 10)
        self.manager.request.assert_not_called()

    async def test_busy_inspection_blocks_graph_mutations(self):
        self.editor.inspect()
        self.editor.move_node('finish', 40, 10)
        self.editor.delete_node()
        self.assertEqual(self.editor.document.value, definition())


class WorkflowKeyboardTests(IsolatedAsyncioTestCase):
    async def test_real_keys_graph_properties_json_and_resizing(self):
        with create_pipe_input() as pipe:
            output = SizedOutput(140, 44)
            app, view, _ = create_preview(input=pipe, output=output)
            from hub.widget.forms.definition_manager import DefinitionManager
            def request(action, argument, version, done):
                done(catalog() if action == ManagementAction.DESCRIBE else None)
            manager = DefinitionManager(view, request, name='workflows', edit_text=None)
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                with set_app(app):
                    editor = WorkflowEditor(manager, 'test', EditorRecord.create(EditorRecord.render(definition()), 'v'))
                    view.dialogs.show(editor, editor.canvas)
                    editor.load()
                pipe.send_text('a')
                await eventually(lambda: app.layout.current_control is editor.toolbar.buttons[0].control)
                pipe.send_text('\r')
                await eventually(lambda: view._dialog is not editor)
                pipe.send_text('next')
                await eventually(lambda: app.current_buffer.text == 'next')
                with set_app(app):
                    view._dialog._buttons[0].handler()
                    app.layout.focus(editor.canvas)
                pipe.send_text('\t')
                await eventually(lambda: app.layout.current_control is editor.inspector.title.control)
                pipe.send_text('\t')
                await eventually(lambda: app.layout.current_control is editor.inspector.apply_button.control)
                pipe.send_text('\t')
                await eventually(lambda: app.layout.current_control is editor.inspector.title.control)
                pipe.send_text('\x1b')
                await eventually(lambda: app.layout.current_control is editor.canvas)
                pipe.send_text('a')
                await eventually(lambda: app.layout.current_control is editor.toolbar.buttons[0].control)
                for index in range(1, len(editor.toolbar.buttons)):
                    pipe.send_text('\x1b[C')
                    await eventually(lambda: app.layout.current_control is editor.toolbar.buttons[index].control)
                pipe.send_text('\r')
                await eventually(lambda: view._dialog is not editor)
                pipe.send_text('\x1b')
                await eventually(lambda: view._dialog is editor)
                with set_app(app):
                    output.columns, output.rows = 80, 30
                    app.invalidate()
                await asyncio.sleep(0.15)
                self.assertIsNotNone(app.renderer._last_screen)
                with set_app(app):
                    app.layout.focus(editor.canvas)
                pipe.send_text('\x1b')
                await eventually(lambda: app.layout.current_control is editor.sidebar)
                pipe.send_text('\x1b')
                await eventually(lambda: app.layout.current_control is editor.buttons[0].control)
                pipe.send_text('\x1b')
                await eventually(lambda: view._dialog is manager)
            finally:
                app.exit()
                await task
                view.close()
