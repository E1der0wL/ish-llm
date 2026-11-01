"""Draft inspection never executes or prepares a handler, Tool or model."""

from copy import deepcopy
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import Mock

from llm.components.workflows import WorkflowGraph, inspect_graph
from llm.core.resource_references import ResourceTarget
from llm.engines.graph.agent import AgentNode
from llm.engines.graph.tool import ToolNode
from llm.engines.registry import EngineRegistry


class WorkflowDataInspectionTests(IsolatedAsyncioTestCase):
    async def test_public_project_api_reads_declarations_without_running_tools_or_creating_runs(self):
        from pathlib import Path
        import tempfile
        from llm.llm import LargeLanguageModel
        from llm.components.workflows import WorkflowComponent
        from llm.components.tools import Tool, ToolRegistry
        from tests.llm.support.runtime_tools import TEST_CONTRACT

        with tempfile.TemporaryDirectory() as directory:
            backend = LargeLanguageModel(Path(directory), components=[WorkflowComponent()], engines={})
            try:
                project = await backend.projects.acreate('inspect', components=['workflows'])
                data = await project.components.aget('workflows')
                effect = Mock(side_effect=AssertionError('Inspection must never execute a Tool'))
                registry = ToolRegistry([Tool('declared', 'test', {'type': 'object'}, effect, contract=TEST_CONTRACT)])
                draft = WorkflowGraph(entry='work').node('work', 'tool', tool='declared').node('end', 'end').connect('work', 'end').to_dict()
                report = await data.ainspect(draft, handlers={'tool': ToolNode()}, declared_tools=registry)
                self.assertTrue(report.valid, report.to_dict())
                self.assertIn('runtime_authorization', report.unverified)
                self.assertEqual(await data.alist(), {})
                self.assertEqual(await project.sessions.alist(), [])
                effect.assert_not_called()
                draft['nodes']['work']['tool'] = 'missing'
                report = await data.ainspect(draft, handlers={'tool': ToolNode()}, declared_tools=registry)
                self.assertFalse(report.valid)
                self.assertEqual(report.diagnostics[0].details['node_id'], 'work')
                nested = WorkflowGraph(entry='end').node('end', 'end').to_dict()
                await data.acreate(nested, identifier='child')
                parent = WorkflowGraph(entry='child').node('child', 'workflow', workflow='child').node('end', 'end').connect('child', 'end').to_dict()
                first = await data.ainspect(parent, handlers={})
                self.assertTrue(first.valid)
                self.assertEqual(first.references[0].version, (await data.asnapshot('child'))['version'])
                nested['metadata'] = {'title': 'changed'}
                await data.asave('child', nested)
                second = await data.ainspect(parent, handlers={})
                self.assertNotEqual(first.references[0].version, second.references[0].version)
            finally:
                await backend.shutdown()


class WorkflowInspectionTests(TestCase):
    def test_structural_paths_identify_edges_and_nodes(self):
        graph = self.graph()
        graph['edges'][0]['target'] = 'missing'
        report = inspect_graph(graph, handlers={})
        error = next(item for item in report.diagnostics if item.code == 'workflow_structure_invalid')
        self.assertEqual(error.details['path'], '/edges/0')
        self.assertEqual(error.details['edge_index'], 0)
        missing = next(item for item in report.diagnostics if item.code == 'workflow_handler_missing')
        self.assertEqual(missing.details['node_id'], 'action')

    def graph(self, **options):
        return WorkflowGraph(entry='action').node('action', 'custom', **options).node('end', 'end').connect('action', 'end').to_dict()

    def test_selected_owner_validates_unknown_to_parent_options_without_execution(self):
        class Handler:
            @staticmethod
            def describe_config():
                return {'type': 'object', 'properties': {'custom_option': {'type': 'integer', 'minimum': 2}},
                        'required': ['custom_option'], 'additionalProperties': False}
            @staticmethod
            def definition_references(node):
                return ()
            def validate(self, *args):
                raise AssertionError('Runtime validator must not run in the editor')
            def __call__(self, *args):
                raise AssertionError('Handler must not execute')
        value = self.graph(custom_option=3)
        original = deepcopy(value)
        self.assertTrue(inspect_graph(value, handlers={'custom': Handler()}).valid)
        self.assertEqual(value, original)
        value['nodes']['action']['custom_option'] = 1
        issue, = inspect_graph(value, handlers={'custom': Handler()}).diagnostics
        self.assertEqual(issue.details['path'], '/nodes/action/custom_option')
        self.assertEqual(issue.details['owner'], 'custom')

    def test_schema_failures_and_unknown_handlers_are_not_success(self):
        value = self.graph()
        value['unexpected'] = True
        self.assertFalse(inspect_graph(value, handlers={}).valid)
        issue, = inspect_graph(self.graph(), handlers={}).diagnostics
        self.assertEqual(issue.code, 'workflow_handler_missing')
        self.assertEqual(issue.details['path'], '/nodes/action')

    def test_missing_schema_and_runtime_checks_are_explicitly_unverified(self):
        report = inspect_graph(self.graph(), handlers={'custom': object()})
        self.assertTrue(report.valid)
        self.assertIn('/nodes/action: handler schema', report.unverified)
        self.assertIn('runtime_authorization', report.unverified)

    def test_tool_and_agent_reference_owners_are_used_without_runtime_hooks(self):
        for kind, handler, options, target in (
            ('tool', ToolNode(), {'tool': 'file_read', 'arguments': {'path': 'x'}}, ResourceTarget('tools', 'file_read')),
            ('agent', AgentNode(engines=EngineRegistry()), {'agent': 'reviewer'}, ResourceTarget('agents', 'reviewer')),
        ):
            with self.subTest(kind=kind):
                value = WorkflowGraph(entry='action').node('action', kind, **options).node('end', 'end').connect('action', 'end').to_dict()
                from llm.components.workflows import InspectedDefinition
                read = Mock(return_value=InspectedDefinition({}, 'reference-revision'))
                handler.validate = Mock(side_effect=AssertionError('Do not prepare runtime'))
                report = inspect_graph(value, handlers={kind: handler}, read_reference=read)
                self.assertTrue(report.valid, report.diagnostics)
                self.assertEqual(report.references[0].version, 'reference-revision')
                read.assert_called_once_with(target)
                handler.validate.assert_not_called()
                read.side_effect = FileNotFoundError('missing')
                report = inspect_graph(value, handlers={kind: handler}, read_reference=read)
                self.assertEqual(report.diagnostics[0].code, 'workflow_reference_invalid')

    def test_nested_reference_cycle_and_structural_cycle_fail_without_execution(self):
        value = WorkflowGraph(entry='child').node('child', 'workflow', workflow='same').node('end', 'end').connect('child', 'end').to_dict()
        from llm.components.workflows import InspectedDefinition
        report = inspect_graph(value, handlers={}, read_reference=lambda target: InspectedDefinition(value, 'revision'))
        self.assertFalse(report.valid)
        self.assertEqual(report.diagnostics[0].code, 'workflow_reference_cycle')
        self.assertIn('/nodes/child/workflow/nodes/child', report.diagnostics[0].details['path'])

    def test_changed_handler_schema_is_checked_again(self):
        handler = Mock()
        handler.describe_config.return_value = {'type': 'object'}
        handler.definition_references.return_value = ()
        self.assertTrue(inspect_graph(self.graph(), handlers={'custom': handler}).valid)
        handler.describe_config.return_value = {'type': 'object', 'required': ['new_field']}
        self.assertFalse(inspect_graph(self.graph(), handlers={'custom': handler}).valid)
