"""Independent wire assertions and invalid/aliased input at typed Owner boundaries."""

from copy import deepcopy
import hashlib
import io
import unittest

from llm.components.file_evidence import FileEvidence
from llm.components.files import FILE_MANIFEST, FileComponent
from llm.components.skills.records import SkillFileEvidence, SkillSnapshot
from llm.components.skills.tools import skill_tools
from llm.components.skills.views import SkillListRequest, SkillReadRequest, SkillResourceRequest
from llm.components.usage import ModelUsageManifest
from tests.llm.support.skills import skill_markdown


class TypedOwnerTests(unittest.TestCase):
    def test_manifest_wire_detachment_and_validated_update(self):
        original = {'schema_version': 1, 'project_id': 'p', 'component': 'skills', 'records': {'r': 'a' * 64}}
        manifest = ModelUsageManifest.from_dict(original, project_id='p', component='skills')
        expected = deepcopy(original)
        original['records'].clear()
        self.assertEqual(manifest.to_dict(), expected)
        manifest.to_dict()['records'].clear()
        self.assertEqual(manifest.records['r'], 'a' * 64)
        with self.assertRaises(TypeError):
            manifest.records['r'] = 'b' * 64
        changed = manifest.with_record('r', 'b' * 64)
        self.assertEqual(manifest.to_dict(), expected)
        self.assertEqual(changed.to_dict(), {**expected, 'records': {'r': 'b' * 64}})
        with self.assertRaises(ValueError):
            manifest.with_record('r', 'invalid')
        self.assertEqual(manifest.to_dict(), expected)
        self.assertEqual(ModelUsageManifest.empty(project_id='p', component='skills').to_dict(),
                         {'schema_version': 1, 'project_id': 'p', 'component': 'skills', 'records': {}})

    def test_manifest_rejects_missing_null_extra_identity_and_revision(self):
        valid = {'schema_version': 1, 'project_id': 'p', 'component': 'skills', 'records': {'r': 'a' * 64}}
        invalid = [{k: v for k, v in valid.items() if k != missing} for missing in valid]
        invalid += [{**valid, key: None} for key in valid]
        invalid += [{**valid, 'schema_version': True}, {**valid, 'schema_version': 1.0},
                    {**valid, 'project_id': 'other'}, {**valid, 'component': 'other'},
                    {**valid, 'extra': {}}, {**valid, 'records': {'manifest': 'a' * 64}},
                    {**valid, 'records': {'r': 'A' * 64}}, {**valid, 'records': []}]
        for value in invalid:
            before = deepcopy(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                ModelUsageManifest.from_dict(value, project_id='p', component='skills')
            self.assertEqual(value, before)

    def test_file_integrity_wire_schema_copy_and_actual_bytes(self):
        content = b'abc\r\n'
        expected = {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}
        output = io.BytesIO()
        measured = FileEvidence.measure(io.BytesIO(content), output)
        self.assertEqual(output.getvalue(), content)
        self.assertEqual(measured.to_dict(), expected)
        self.assertEqual(FileComponent._digest(io.BytesIO(content)), expected)
        self.assertTrue(FileEvidence.from_dict(expected).matches(content))
        self.assertFalse(measured.matches(content + b'x'))
        wrong_size = FileEvidence.from_dict({**expected, 'bytes': 0})
        self.assertFalse(wrong_size.matches(content))
        self.assertEqual(FILE_MANIFEST['additionalProperties'], {
            'type': 'object', 'properties': {'sha256': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
                                            'bytes': {'type': 'integer', 'minimum': 0}},
            'required': ['sha256', 'bytes'], 'additionalProperties': False})
        native = SkillFileEvidence.from_dict({**expected, 'mode': 0o640})
        self.assertEqual(native.mode, 0o640)
        self.assertEqual(native.to_dict(), {**expected, 'mode': 0o640})
        for value in ({'sha256': expected['sha256']}, {**expected, 'bytes': -1},
                      {**expected, 'bytes': True}, {**expected, 'sha256': None}, {**expected, 'other': 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                FileEvidence.from_dict(value)
        for value in (expected, {**expected, 'mode': 0o4640}, {**expected, 'mode': None}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SkillFileEvidence.from_dict(value)

    def test_snapshot_is_validated_and_detached_without_normalizing_markdown(self):
        raw = '---\r\nname: guide\r\ndescription: Example\r\n# keep this\r\n---\r\nBody\r\n'
        value = {'markdown': raw, 'source': {'user': ['original']},
                 'files': {'notes.txt': {'sha256': 'a' * 64, 'bytes': 1, 'mode': 0o600}}}
        original = deepcopy(value)
        snapshot = SkillSnapshot('guide', value)
        value['source']['user'].clear()
        snapshot.to_dict()['source']['user'].clear()
        self.assertEqual(snapshot.to_dict(), original)
        self.assertEqual(snapshot.name, 'guide')
        self.assertEqual(snapshot.description, 'Example')
        self.assertEqual(snapshot.file('notes.txt').bytes, 1)
        with self.assertRaises(ValueError):
            snapshot.file('missing.txt')
        with self.assertRaises(ValueError):
            SkillSnapshot('other', original)
        with self.assertRaises(ValueError):
            SkillSnapshot('guide', {})

    def test_request_presence_and_schema_have_one_owner(self):
        omitted = {}
        self.assertIsNone(SkillListRequest.from_dict(omitted).limit)
        self.assertEqual(omitted, {})
        self.assertEqual(SkillListRequest.from_dict({'limit': 2}).limit, 2)
        self.assertIsNone(SkillReadRequest.from_dict({'identifier': 'guide'}).expected_revision)
        for owner, invalid in (
            (SkillListRequest, [{'limit': None}, {'limit': True}, {'limit': 1.0}, {'extra': 1}, {'query': None}]),
            (SkillReadRequest, [{}, {'identifier': None}, {'identifier': 'guide', 'expected_revision': None}]),
            (SkillResourceRequest, [{'identifier': 'guide'}, {'identifier': 'guide', 'index': None},
                                   {'identifier': 'guide', 'index': 0, 'path': 'a.txt'}])):
            for value in invalid:
                original = deepcopy(value)
                with self.subTest(owner=owner, value=value), self.assertRaises(ValueError):
                    owner.from_dict(value)
                self.assertEqual(value, original)
        self.assertEqual(SkillResourceRequest.from_dict({'identifier': 'guide', 'index': 0}).index, 0)
        self.assertEqual(SkillResourceRequest.from_dict({'identifier': 'guide', 'path': 'a.txt'}).path, 'a.txt')
        self.assertEqual(SkillReadRequest.schema(), {
            'type': 'object', 'properties': {
                'identifier': {'type': 'string', 'minLength': 1, 'maxLength': 64, 'pattern': r'^[^\W_]+(?:-[^\W_]+)*$'},
                'expected_revision': {'type': 'string', 'minLength': 1}},
            'required': ['identifier'], 'additionalProperties': False})


class TypedToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_wire_and_invalid_direct_request_preserve_callback_boundary(self):
        raw = skill_markdown('guide', 'Read carefully', 'Example')
        content = b'notes'
        info = {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': 5, 'mode': 0o600}
        definition = {'markdown': raw, 'files': {'notes.txt': info}}
        calls = []

        async def read_file(identifier, path, *, expected_revision):
            calls.append((identifier, path, expected_revision))
            return content

        async def read_resource(*args, **kwargs):
            self.fail('Path selection must never invoke the index Reader')

        registry = skill_tools({'guide': definition}, read_file=read_file, read_resource=read_resource)
        listing = await registry.get('skill_list').handler({})
        revision = listing['skills'][0]['revision']
        self.assertEqual(listing, {'skills': [{'id': 'guide', 'revision': revision, 'name': 'guide',
                                             'description': 'Example'}], 'total': 1, 'next_after': None})
        self.assertEqual(await registry.get('skill_read').handler({'identifier': 'guide'}),
                         {'id': 'guide', 'revision': revision, 'definition': definition})
        handler = registry.get('skill_read_resource').handler
        for invalid in ({'identifier': 'guide'}, {'identifier': 'guide', 'index': 0, 'path': 'notes.txt'},
                        {'identifier': 'guide', 'path': None}, {'identifier': 'guide', 'path': 'missing.txt'}):
            with self.assertRaises(ValueError):
                await handler(invalid)
        self.assertEqual(calls, [])
        self.assertEqual(await handler({'identifier': 'guide', 'path': 'notes.txt'}),
                         {'path': 'notes.txt', 'text': 'notes', **info})
        self.assertEqual(calls, [('guide', 'notes.txt', revision)])
