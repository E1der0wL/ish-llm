"""UI draft changes are local; saving and execution retain backend ownership."""

from copy import deepcopy
import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput
from examples.hub.preview import create_preview
from hub.widget.forms.workflow_editor import WorkflowDraft, WorkflowEditor
from hub.backend.project.management_values import EditorRecord, ManagementAction
from tests.hub.support import SizedOutput


def definition():
    return {'schema_version': 1, 'entry': 'finish', 'nodes': {'finish': {'type': 'end'}}, 'edges': [],
            'metadata': {'ui': {'position': {'finish': [5, 8]}}}}


class WorkflowDraftTests(TestCase):
    def test_diagnostic_pointer_uses_json_tokens_without_rewriting_source(self):
        from hub.ui.input.json_document import JsonDocument
        source = ' {"a/b": [{"quoted": "\\\"x"}], "duplicate": 1, "duplicate": 2}'
        for path, expected in (('/a~1b/0/quoted', '"\\\"x"'), ('/duplicate', '2'),
                               ('/a~1b/0/missing', '{"quoted"')):
            position = JsonDocument.locate(source, path)
            self.assertTrue(source[position:].startswith(expected), source[position:])

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


class WorkflowEditorTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app, self.view, _ = create_preview(input=DummyInput(), output=SizedOutput())
        self.app.loop = asyncio.get_running_loop()
        self.enterContext(set_app(self.app))
        self.view.visible = True
        self.manager = SimpleNamespace(name='workflows', view=self.view, request=Mock())
        self.editor = WorkflowEditor(self.manager, 'test', EditorRecord.create(EditorRecord.render(definition()), 'revision'))
        self.view.dialogs.show(self.editor, self.editor.input)

    async def asyncTearDown(self):
        self.view.close()

    async def test_inspection_requires_engine_selection_and_discards_stale_results(self):
        self.editor.inspect()
        args = self.manager.request.call_args.args
        self.assertEqual(args[0], ManagementAction.DESCRIBE)
        args[-1]({'engines': {'one': {}, 'two': {}}, 'schema': {}})
        popup = self.view._dialog
        popup.body.current_value = 'two'
        popup._buttons[0].handler()
        args = self.manager.request.call_args.args
        self.assertEqual(args[:2], (ManagementAction.INSPECT, {'definition': definition(), 'engine': 'two'}))
        self.editor.input.text = EditorRecord.render({**definition(), 'metadata': {'ui': 'changed'}})
        args[-1]({'valid': False, 'diagnostics': [{'message': 'stale', 'details': {'path': '/entry'}}], 'unverified': []})
        self.assertNotIn('stale', self.editor.status)
        self.assertEqual(self.editor.version, 'revision')

    async def test_malformed_json_is_kept_when_edit_operations_fail(self):
        self.editor.input.text = '{'
        for operation in (self.editor.edit_node, self.editor.delete_node, self.editor.add_edge, self.editor.delete_edge):
            operation()
            self.assertEqual(self.editor.input.text, '{')
        self.manager.request.assert_not_called()

    async def test_node_and_edge_actions_remain_local_until_cas_save(self):
        from hub.widget.controls import TextArea, RadioList
        inputs, choices = [], []
        def input_field(*args, **kwargs):
            value = TextArea(*args, **kwargs)
            inputs.append(value)
            return value
        def choice_field(*args, **kwargs):
            value = RadioList(*args, **kwargs)
            choices.append(value)
            return value
        with patch('hub.widget.forms.workflow_editor.TextArea', side_effect=input_field):
            self.editor.edit_node()
        popup = self.view._dialog
        inputs[0].text = 'new'
        inputs[1].text = '{"type":"custom","private_field":7}'
        popup._buttons[0].handler()
        self.assertEqual(self.editor.draft().value['nodes']['new']['private_field'], 7)
        with patch('hub.widget.forms.workflow_editor.RadioList', side_effect=choice_field):
            self.editor.add_edge()
        popup = self.view._dialog
        choices[0].current_value, choices[1].current_value = 'new', 'finish'
        popup._buttons[0].handler()
        self.assertEqual(self.editor.draft().value['edges'], [{'source': 'new', 'target': 'finish'}])
        self.editor.delete_edge()
        self.view._dialog._buttons[0].handler()
        self.assertEqual(self.editor.draft().value['edges'], [])
        self.assertEqual(self.editor.version, 'revision')
        self.manager.request.assert_not_called()

    async def test_diagnostic_selection_moves_cursor_without_rewriting_draft(self):
        text = self.editor.input.text
        self.editor._report = (text, {'diagnostics': [{'message': 'example',
            'details': {'path': '/nodes/finish/type'}}]})
        self.editor.locate()
        self.view._dialog._buttons[0].handler()
        self.assertEqual(self.editor.input.text, text)
        self.assertTrue(text[self.editor.input.buffer.cursor_position:].startswith('"end"'))
