"""Public snapshot shapes, immutable observations and real stale-write effects."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from llm.components.base import Component
from llm.core.agent_views import AgentSnapshot
from llm.core.agents import AgentDefinition, AgentRecord
from llm.core.editable_snapshot import EditableSnapshot
from llm.llm import LargeLanguageModel


class SnapshotValueTests(unittest.TestCase):
    def test_component_fields_are_required_opaque_and_detached(self):
        wire = {'data': {'extension': [None, False, 0, {'version': 'payload'}]}, 'version': 'opaque-cas'}
        before = deepcopy(wire)
        value = EditableSnapshot.read(wire)
        wire['data']['extension'].clear()
        value.data['extension'].clear()
        value.to_dict()['data']['extension'].clear()
        self.assertEqual(value.to_dict(), before)
        self.assertEqual(value.version, 'opaque-cas')
        # No hash calculation, policy interpretation or invented version default.
        self.assertEqual(EditableSnapshot({}, '').to_dict(), {'data': {}, 'version': ''})
        invalid = [{key: item for key, item in before.items() if key != missing} for missing in before]
        invalid += [{**before, 'extra': 1}, {**before, 'data': []}, {**before, 'version': None},
                    {**before, 'version': 1}, {**before, 'data': {'bad': float('nan')}}]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises((TypeError, ValueError)):
                EditableSnapshot.read(item)

    def test_agent_projection_checks_definition_and_revision_without_defaults(self):
        definition = {'engine': 'base', 'system_prompt': None, 'metadata': {'nested': []}}
        record = AgentRecord.create('a' * 32, 'worker', definition)
        wire = {'agent_id': 'worker', 'definition': definition, 'revision': AgentDefinition.revision(definition)}
        before = deepcopy(wire)
        value = AgentSnapshot.from_record(record)
        self.assertEqual(value.to_dict(), before)
        self.assertEqual(AgentSnapshot.read(wire).to_dict(), before)
        definition['metadata']['nested'].append('caller mutation')
        value.definition['metadata']['nested'].append('view mutation')
        value.to_dict()['definition']['metadata']['nested'].append('wire mutation')
        self.assertEqual(value.to_dict(), before)
        invalid = [{key: item for key, item in before.items() if key != missing} for missing in before]
        invalid += [{**before, 'extra': 1}, {**before, 'agent_id': '../worker'},
                    {**before, 'revision': '0' * 64}, {**before, 'definition': {'engine': 'changed'}},
                    {**before, 'definition': {}}]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises((TypeError, ValueError)):
                AgentSnapshot.read(item)


class Notes(Component):
    name = directory = 'notes'


class SnapshotStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_modified_observations_and_stale_versions_cannot_change_current_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            async with LargeLanguageModel(temporary, components=[Notes()]) as app:
                project = await app.projects.acreate(components=['notes'])
                data = project.components.notes
                await data.acreate({'nested': {'value': 'original'}}, identifier='record')
                wire = await data.asnapshot('record')
                snapshot = EditableSnapshot.read(wire)
                wire['data']['nested']['value'] = 'public edit'
                snapshot.data['nested']['value'] = 'view edit'
                self.assertEqual(await data.aload('record'), {'nested': {'value': 'original'}})
                await data.asave('record', {'nested': {'value': 'current'}}, expected_version=snapshot.version)
                root = Path(project.paths.root)
                before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                with self.assertRaisesRegex(ValueError, 'edit_conflict'):
                    await data.asave('record', wire['data'], expected_version=snapshot.version)
                with self.assertRaisesRegex(ValueError, 'edit_conflict'):
                    await data.adelete('record', expected_version=snapshot.version)
                self.assertEqual({p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}, before)
                self.assertEqual(await data.aload('record'), {'nested': {'value': 'current'}})
