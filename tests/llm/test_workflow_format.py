"""Current Workflow format, nested readers and non-destructive authoring boundaries."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.components.workflows.graph import validate_graph, workflow_schema
from llm.llm import LargeLanguageModel


def current_graph():
    return {'schema_version': 1, 'entry': 'end', 'nodes': {'end': {'type': 'end'}}, 'edges': []}


def nested_graph():
    return (WorkflowGraph(entry='loop').node('loop', 'loop', body=current_graph(), max_iterations=1, on_limit='continue')
            .node('end', 'end').connect('loop', 'end').to_dict())


def invalid_graphs():
    for nested in (False, True):
        for value in (True, False, 1.0, '1', None, 0, 2, [], {}):
            graph = nested_graph() if nested else current_graph()
            target = graph['nodes']['loop']['body'] if nested else graph
            target['schema_version'] = value
            yield graph
        graph = nested_graph() if nested else current_graph()
        target = graph['nodes']['loop']['body'] if nested else graph
        del target['schema_version']
        yield graph


class WorkflowFormatTests(unittest.TestCase):
    def test_current_roundtrip_and_managed_constructor_fields(self):
        graph = WorkflowGraph(entry='end').node('end', 'end')
        self.assertEqual(graph.to_dict(), current_graph())
        self.assertEqual(WorkflowGraph.from_dict(current_graph()).to_dict(), current_graph())
        schema = workflow_schema()
        self.assertEqual(set(schema['required']), {'schema_version', 'entry', 'nodes', 'edges'})
        self.assertEqual(schema['properties']['schema_version'], {'const': 1})
        for key, value in (('schema_version', 1), ('nodes', {}), ('edges', [])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                WorkflowGraph(entry='end', **{key: value})
        value = current_graph()
        restored = WorkflowGraph.from_dict(value)
        value['nodes']['end']['type'] = 'unrelated'
        self.assertEqual(restored.to_dict(), current_graph())

    def test_top_level_and_nested_readers_reject_versions_without_mutating_input(self):
        for graph in invalid_graphs():
            before = deepcopy(graph)
            for name, read in (('validator', validate_graph), ('builder', WorkflowGraph.from_dict)):
                # A missing nested required field is rejected by the enclosing
                # closed-node Schema before the recursive version guard runs.
                with self.subTest(graph=graph, boundary=name), self.assertRaisesRegex(ValueError, 'schema_version|nodes/loop'):
                    read(graph)
                self.assertEqual(graph, before)


class WorkflowFormatStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_create_and_save_preserve_all_existing_project_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            async with LargeLanguageModel(Path(directory), components=[WorkflowComponent()], engines={}) as app:
                project = await app.projects.acreate(components=['workflows'])
                workflows = await project.components.aget('workflows')
                await workflows.acreate(current_graph(), identifier='flow')
                self.assertEqual(await workflows.aload('flow'), current_graph())
                before = {str(p.relative_to(project.paths.root)): p.read_bytes()
                          for p in project.paths.root.rglob('*') if p.is_file()}
                for graph in invalid_graphs():
                    for operation in ('create', 'save'):
                        with self.subTest(graph=graph, operation=operation), self.assertRaises(ValueError):
                            if operation == 'create':
                                await workflows.acreate(graph, identifier='invalid')
                            else:
                                await workflows.asave('flow', graph)
                        self.assertEqual({str(p.relative_to(project.paths.root)): p.read_bytes()
                                          for p in project.paths.root.rglob('*') if p.is_file()}, before)
