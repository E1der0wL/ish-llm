"""Reference observations preserve wire evidence and block actual unsafe deletion."""

from copy import deepcopy
from itertools import product
from pathlib import Path
import tempfile
import unittest

from llm.components.base import Component
from llm.core.resource_references import ResourceTarget, StoredResourceReference, AgentUse
from llm.llm import LargeLanguageModel


class ResourceReferenceTests(unittest.TestCase):
    def test_required_identity_and_opaque_evidence_survive_without_aliasing(self):
        for optional in ({}, {'path': None}, {'path': 'guide.md'}):
            original = {'component': 'data', 'identifier': 'manual', 'source': 'guide',
                        'revision': 'pinned', 'metadata': {'nested': [False]}, **optional}
            before = deepcopy(original)
            reference = StoredResourceReference.read(original)
            original['metadata']['nested'].clear()
            reference.to_dict()['metadata']['nested'].clear()
            self.assertEqual(reference.to_dict(), before)
            self.assertEqual(reference.dependent('skills').to_dict(), {'component': 'skills', 'identifier': 'guide'})
            for field in ('component', 'identifier', 'source'):
                broken = {key: value for key, value in before.items() if key != field}
                with self.subTest(field=field), self.assertRaises((TypeError, ValueError)):
                    StoredResourceReference.read(broken)
                self.assertNotIn(field, broken)

    def test_matching_preserves_current_component_record_and_path_semantics(self):
        for component, identifier, optional, wanted_component, wanted_identifier, wanted_path in product(
                ('data', 'tools'), (None, 'a', 'b'), ({}, {'path': None}, {'path': 'x'}, {'path': 'y'}),
                ('data', 'tools'), (None, 'a', 'b'), (None, 'x', 'y')):
            raw = {'component': component, 'identifier': identifier, 'source': 'owner', **optional}
            expected = (raw['component'] == wanted_component
                        and (wanted_identifier is None or raw['identifier'] == wanted_identifier)
                        and (wanted_path is None or raw.get('path') in (None, wanted_path)))
            self.assertEqual(StoredResourceReference.read(raw).matches(
                wanted_component, wanted_identifier, path=wanted_path), expected)

    def test_engine_requires_one_exact_record_target_and_workflow_use_has_no_defaults(self):
        self.assertEqual(ResourceTarget.from_engine({'component': 'workflows', 'identifier': 'flow'}).to_dict(),
                         {'component': 'workflows', 'identifier': 'flow'})
        for value in (None, {}, {'component': 'workflows', 'identifier': None},
                      {'component': '', 'identifier': 'flow'}, {'component': 'workflows', 'identifier': ''},
                      {'component': 'workflows', 'identifier': 'flow', 'extra': 1}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Engine Agent references'):
                ResourceTarget.from_engine(value)
        self.assertEqual(AgentUse.from_dict({'identifier': 'worker', 'source': 'flow'}).to_dict(),
                         {'identifier': 'worker', 'source': 'flow'})
        for value in ({'identifier': 'worker'}, {'source': 'flow'}, {'identifier': None, 'source': 'flow'}):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                AgentUse.from_dict(value)


class Notes(Component):
    name = directory = 'notes'


class Watcher(Component):
    name = directory = 'watcher'

    def references(self, project):
        return list(self.list(project).values())


class ReferenceDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_evidence_and_disabled_owner_cannot_allow_record_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            async with LargeLanguageModel(temporary, components=[Notes(), Watcher()]) as app:
                project = await app.projects.acreate(components=['notes', 'watcher'])
                notes, watcher = project.components.notes, project.components.watcher
                await notes.acreate({'protected': True}, identifier='record')
                await watcher.acreate({'component': 'notes', 'identifier': 'record'}, identifier='guard')
                root = Path(project.paths.root)

                def files():
                    return {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}

                before = files()
                with self.assertRaises((TypeError, ValueError)):
                    await notes.adelete('record')
                self.assertEqual(files(), before)
                await watcher.asave('guard', {'component': 'notes', 'identifier': 'record', 'source': 'guard'})
                await project.components.aremove('watcher')
                before = files()
                with self.assertRaisesRegex(ValueError, 'referenced'):
                    await notes.adelete('record')
                self.assertEqual(files(), before)
                self.assertEqual(await notes.aload('record'), {'protected': True})
                await project.components.aselect(['notes', 'watcher'])
                await project.components.watcher.adelete('guard')
                await notes.adelete('record')
                self.assertEqual(await notes.alist(), {})
