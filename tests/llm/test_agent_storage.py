"""Independent Agent inventory, CAS and actual transaction interruption evidence."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from llm.core.agents import AgentCatalog, AgentDefinition, AgentRecord
from llm.core.models import Project, new_id
from llm.core.paths import ProjectPaths
from llm.services.infrastructure.locking import WorkspaceOwnership
from llm.services.lifecycle.agents import AgentRepository


class AgentDefinitionTests(unittest.TestCase):
    def test_engine_only_and_explicit_presence_are_preserved(self):
        for definition in ({'engine': 'base'}, {'engine': 'base', 'system_prompt': None},
                           {'engine': 'base', 'system_prompt': '', 'completion': {},
                            'tools': [], 'resources': {'rag': False}, 'metadata': {'zero': 0}}):
            with self.subTest(definition=definition):
                original = deepcopy(definition)
                value = AgentRecord.create('a' * 32, 'worker', definition).to_dict()
                result = AgentRecord.read(value, project_id='a' * 32, identifier='worker')
                self.assertEqual(result.definition, original)
                self.assertEqual(definition, original)
                result.definition['engine'] = 'changed'
                self.assertEqual(value['definition'], original)

    def test_invalid_definitions_cannot_be_normalized(self):
        invalid = [None, [], {}, {'engine': ''}, {'engine': None}, {'engine': 'base', 'purpose': ''},
                   {'engine': 'base', 'loop': {}}, {'engine': 'base', 'run_id': 'a' * 32},
                   {'engine': 'base', 'policy': {'max_tool_calls': True}},
                   {'engine': 'base', 'policy': {'max_tool_calls': 0}},
                   {'engine': 'base', 'policy': {'require_tool': True}},
                   {'engine': 'base', 'completion': {'temperature': float('nan')}},
                   {'engine': 'base', 'input_schema': {'$ref': 'https://invalid/schema'}}]
        for definition in invalid:
            with self.subTest(definition=definition):
                with self.assertRaises((ValueError, TypeError)):
                    AgentDefinition.validate('worker', definition)

    def test_record_all_fields_current_version_and_owner_are_required(self):
        original = AgentRecord.create('a' * 32, 'worker', {'engine': 'base'}).to_dict()
        invalid = [{k: v for k, v in original.items() if k != key} for key in original]
        invalid += [{**original, 'storage_version': v} for v in (None, True, 1.0, '1', 0, 2)]
        invalid += [{**original, key: value} for key, value in (
            ('id', 'other'), ('project_id', 'b' * 32), ('revision', '0' * 64), ('definition', {}))]
        invalid += [{'engine': 'base'}, {**original, 'unknown': 1}]
        for value in invalid:
            with self.subTest(value=value):
                before = deepcopy(value)
                with self.assertRaises((ValueError, TypeError)):
                    AgentRecord.read(value, project_id='a' * 32, identifier='worker')
                self.assertEqual(value, before)

    def test_identifiers_cannot_escape_the_project(self):
        for identifier in ('', '../worker', 'a/b', '.', 1, None, 'x' * 65):
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValueError):
                    AgentDefinition.validate(identifier, {'engine': 'base'})


class AgentStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name) / 'projects'
        self.owner = WorkspaceOwnership(self.workspace)
        self.repository = AgentRepository(self.owner)
        self.project = self.new_project()
        self.repository.initialize_new(self.project)
        self.root = self.repository.root(self.project)

    def new_project(self):
        identifier = new_id()
        return Project(identifier, 'storage test', ProjectPaths(self.workspace / identifier))

    def snapshot(self, project=None):
        root = self.repository.root(self.project if project is None else project)
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob('*') if p.is_file() and not p.is_symlink()}

    def create(self):
        self.repository.create(self.project, {'engine': 'base', 'system_prompt': None}, identifier='worker')
        return self.repository.snapshot(self.project, 'worker')

    def assert_rejected_without_changes(self, operation):
        before = self.snapshot()
        with self.assertRaises((ValueError, TypeError, OSError)):
            operation()
        self.assertEqual(self.snapshot(), before)

    def operations(self, revision):
        return (lambda: self.repository.list(self.project),
                lambda: self.repository.load(self.project, 'worker'),
                lambda: self.repository.create(self.project, {'engine': 'new'}, identifier='new'),
                lambda: self.repository.save(self.project, 'worker', {'engine': 'new'}, expected_revision=revision),
                lambda: self.repository.delete(self.project, 'worker', expected_revision=revision),
                lambda: self.repository.validate_backup(self.project),
                lambda: self.repository.initialize_new(self.project))

    def test_crud_and_reopen_use_one_authoritative_catalogue(self):
        self.assertEqual(self.repository.list(self.project), {})
        original = self.create()
        value = {'engine': 'base', 'system_prompt': '', 'metadata': {'enabled': False}}
        self.repository.save(self.project, 'worker', value, expected_revision=original['revision'])
        reopened = AgentRepository(WorkspaceOwnership(self.workspace))
        self.assertEqual(reopened.load(self.project, 'worker'), value)
        current = reopened.snapshot(self.project, 'worker')
        self.assertNotEqual(current['revision'], original['revision'])
        self.assert_rejected_without_changes(lambda: reopened.save(
            self.project, 'worker', {'engine': 'other'}, expected_revision=original['revision']))
        reopened.delete(self.project, 'worker', expected_revision=current['revision'])
        self.assertEqual(reopened.list(self.project), {})
        self.assertEqual(json.loads((self.root / self.repository.MANIFEST).read_bytes())['records'], {})

    def test_concurrent_cas_has_one_winner_and_preserves_the_winning_definition(self):
        revision = self.create()['revision']
        def save(name):
            try:
                self.repository.save(self.project, 'worker', {'engine': name}, expected_revision=revision)
                return name
            except ValueError as error:
                self.assertIn('edit_conflict', str(error))
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, ('first', 'second')))
        winners = [name for name in results if name is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(self.repository.load(self.project, 'worker'), {'engine': winners[0]})

    def test_missing_record_cannot_be_recreated_or_omitted(self):
        revision = self.create()['revision']
        path = self.root / 'records/worker.json'
        path.unlink()
        for operation in (*self.operations(revision), lambda: self.repository.create(
                self.project, {'engine': 'replacement'}, identifier='worker')):
            self.assert_rejected_without_changes(operation)
        self.assertFalse(path.exists())

    def test_missing_catalog_directory_or_whole_store_never_reinitializes(self):
        revision = self.create()['revision']
        original = self.snapshot()
        for lost in (self.root / self.repository.MANIFEST, self.root / 'records', self.root):
            with self.subTest(lost=lost.name):
                if lost.is_dir():
                    shutil.rmtree(lost)
                else:
                    lost.unlink()
                for operation in self.operations(revision):
                    self.assert_rejected_without_changes(operation)
                self.assertFalse(lost.exists())
                for name, raw in original.items():
                    path = self.root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)

    def test_catalog_types_owner_and_inventory_are_required(self):
        revision = self.create()['revision']
        path = self.root / self.repository.MANIFEST
        original = json.loads(path.read_bytes())
        invalid = [{k: v for k, v in original.items() if k != field} for field in original]
        invalid += [{**original, 'schema_version': v} for v in (None, True, 1.0, '1', 2)]
        invalid += [{**original, key: value} for key, value in (
            ('project_id', 'b' * 32), ('records', {}), ('records', None),
            ('records', {'worker': '0' * 64}), ('records', {'../worker': '0' * 64}),
            ('records', {'worker': None}))]
        for value in invalid:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                for operation in self.operations(revision):
                    self.assert_rejected_without_changes(operation)

    def test_record_owner_revision_and_old_format_reject_even_with_matching_digest(self):
        revision = self.create()['revision']
        path = self.root / 'records/worker.json'
        original = json.loads(path.read_bytes())
        invalid = [{k: v for k, v in original.items() if k != field} for field in original]
        invalid += [{**original, 'storage_version': v} for v in (None, True, 1.0, '1', 2)]
        invalid += [{**original, key: value} for key, value in (
            ('id', 'other'), ('project_id', 'b' * 32), ('revision', '0' * 64))]
        invalid += [{'engine': 'base'}, {**original, 'unknown': 1}]
        for value in invalid:
            with self.subTest(value=value):
                raw = json.dumps(value).encode()
                path.write_bytes(raw)
                manifest = AgentCatalog.empty(self.project.id).to_dict()
                manifest['records']['worker'] = hashlib.sha256(raw).hexdigest()
                (self.root / self.repository.MANIFEST).write_text(json.dumps(manifest))
                for operation in self.operations(revision):
                    self.assert_rejected_without_changes(operation)

    def test_tampered_or_undeclared_bytes_are_preserved(self):
        revision = self.create()['revision']
        original = self.snapshot()
        for name in ('records/worker.json', 'records/orphan.json', 'component.json'):
            with self.subTest(name=name):
                path = self.root / name
                path.write_bytes(b'{"tampered":true}')
                for operation in self.operations(revision):
                    self.assert_rejected_without_changes(operation)
                if name in original:
                    path.write_bytes(original[name])
                else:
                    path.unlink()

    def test_linked_record_or_root_cannot_read_or_overwrite_the_target(self):
        revision = self.create()['revision']
        target = self.workspace / 'outside.json'
        target.write_bytes(b'original external bytes')
        path = self.root / 'records/worker.json'
        path.unlink()
        path.symlink_to(target)
        for operation in self.operations(revision):
            self.assert_rejected_without_changes(operation)
        self.assertEqual(target.read_bytes(), b'original external bytes')
        fresh = self.new_project()
        fresh.paths.root.symlink_to(self.project.paths.root)
        with self.assertRaises(ValueError):
            self.repository.initialize_new(fresh)
        self.assertEqual(target.read_bytes(), b'original external bytes')

    def test_record_and_catalog_failure_rolls_back_create_save_and_delete(self):
        revision = self.create()['revision']
        before = self.snapshot()
        from llm.services.lifecycle import agents
        write = agents.atomic_json
        def fail(path, value):
            write(path, value)
            raise OSError('catalog commit failed')
        for operation in (
                lambda: self.repository.create(self.project, {'engine': 'new'}, identifier='new'),
                lambda: self.repository.save(self.project, 'worker', {'engine': 'new'}, expected_revision=revision),
                lambda: self.repository.delete(self.project, 'worker', expected_revision=revision)):
            with patch.object(agents, 'atomic_json', fail), self.assertRaisesRegex(OSError, 'catalog commit'):
                operation()
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.repository.load(self.project, 'worker')['engine'], 'base')

    def test_initialization_failure_leaves_no_partial_project(self):
        project = self.new_project()
        with patch('llm.services.lifecycle.agents.atomic_json', side_effect=OSError('full')):
            with self.assertRaises(OSError):
                self.repository.initialize_new(project)
        self.assertFalse(project.paths.root.exists())

    def test_clone_rebinds_owner_and_failed_clone_preserves_both_stores(self):
        self.create()
        self.repository.create(self.project, {'engine': 'second'}, identifier='second')
        original = self.snapshot()
        destination = self.new_project()
        self.repository.initialize_new(destination)
        empty = self.snapshot(destination)
        from llm.services.lifecycle import agents
        write = agents.atomic_bytes
        def fail(path, value):
            write(path, value)
            if path.name == 'worker.json':
                raise OSError('second record failed')
        with patch.object(agents, 'atomic_bytes', fail), self.assertRaises(OSError):
            self.repository.clone(self.project, destination)
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(self.snapshot(destination), empty)
        self.repository.clone(self.project, destination)
        self.assertEqual(self.repository.list(destination), self.repository.list(self.project))
        for path in (self.repository.root(destination) / 'records').iterdir():
            self.assertEqual(json.loads(path.read_bytes())['project_id'], destination.id)
        self.assertEqual(self.snapshot(), original)

    def test_real_process_death_at_record_catalog_and_commit_boundaries(self):
        for operation in ('save', 'delete', 'create'):
            for boundary in ('record', 'catalog', 'commit'):
                with self.subTest(operation=operation, boundary=boundary):
                    project = self.new_project()
                    self.repository.initialize_new(project)
                    self.repository.create(project, {'engine': 'old'}, identifier='worker')
                    before = self.snapshot(project)
                    code = '''
import os, sys
from pathlib import Path
from llm.core.models import Project
from llm.core.paths import ProjectPaths
from llm.services.infrastructure.locking import WorkspaceOwnership
from llm.services.lifecycle import agents
root, identifier, operation, boundary = sys.argv[1:]
owner = WorkspaceOwnership(Path(root))
repository = agents.AgentRepository(owner)
project = Project(identifier, 'test', ProjectPaths(Path(root) / identifier))
revision = repository.snapshot(project, 'worker')['revision']
def interrupt(function):
    def wrapped(*args, **kwargs):
        function(*args, **kwargs)
        os._exit(77)
    return wrapped
if boundary == 'record':
    name = 'unlink_file' if operation == 'delete' else 'atomic_bytes'
    setattr(agents, name, interrupt(getattr(agents, name)))
elif boundary == 'catalog':
    agents.atomic_json = interrupt(agents.atomic_json)
else:
    owner.transactions._retire = lambda path: os._exit(77)
if operation == 'save':
    repository.save(project, 'worker', {'engine': 'new'}, expected_revision=revision)
elif operation == 'delete':
    repository.delete(project, 'worker', expected_revision=revision)
else:
    repository.create(project, {'engine': 'new'}, identifier='new')
'''
                    result = subprocess.run([sys.executable, '-c', textwrap.dedent(code), str(self.workspace),
                                             project.id, operation, boundary], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 77, result.stderr)
                    reopened = AgentRepository(WorkspaceOwnership(self.workspace))
                    values = reopened.list(project)
                    if boundary != 'commit':
                        self.assertEqual(values, {'worker': {'engine': 'old'}})
                        self.assertEqual(self.snapshot(project), before)
                    else:
                        expected = {'save': {'worker': {'engine': 'new'}}, 'delete': {},
                                    'create': {'worker': {'engine': 'old'}, 'new': {'engine': 'new'}}}
                        self.assertEqual(values, expected[operation])
                    self.assertEqual(reopened.list(project), values)
