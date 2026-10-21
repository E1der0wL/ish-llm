"""Tool package loss must not become absent dependencies or permission to execute."""
from pathlib import Path
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from llm.components.tools import ToolComponent, ToolPaths
from llm.components.tools.packages import load_tool, read_source_package
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository


class ToolPackageLossTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.projects = ProjectManager(ProjectRepository(self.root / 'workspace'), components=[ToolComponent()])
        self.project = self.projects.create('probe', components=['tools'])
        self.data = self.projects.component(self.project, 'tools')
        self.paths = ToolPaths.for_project(self.project)
        self.data.create({'source':"raise AssertionError('never import')", 'requirements':'retained-dependency>=1'},
                         identifier='saved')

    def test_explicit_external_import_preserves_absent_or_empty_requirements_without_execution(self):
        directory = self.root / 'external'
        directory.mkdir()
        source = b"raise AssertionError('must not execute imports')\n"
        (directory / 'external.py').write_bytes(source)
        self.assertEqual(read_source_package(directory), {'source': source.decode()})
        requirements = directory / 'requirements.txt'
        requirements.write_bytes(b'')
        imported = read_source_package(directory)
        self.assertEqual(imported, {'source': source.decode(), 'requirements': ''})
        self.data.create(imported, identifier='imported')
        self.assertEqual(self.data.load('imported'), imported)
        for content in ('-r secret.txt', 'package @ https://example.invalid/package.whl'):
            requirements.write_text(content)
            with self.assertRaises(ValueError):
                read_source_package(directory)
            self.assertEqual(requirements.read_text(), content)
        self.assertEqual((directory / 'external.py').read_bytes(), source)

    def test_external_import_rejects_links_special_files_and_extra_contents(self):
        directory = self.root / 'external'
        directory.mkdir()
        source = directory / 'external.py'
        source.write_text('async def main(): return 1\n')
        extra = directory / 'extra.txt'
        extra.write_text('preserve')
        with self.assertRaises(ValueError):
            read_source_package(directory)
        self.assertEqual(extra.read_text(), 'preserve')
        extra.unlink()
        requirements = directory / 'requirements.txt'
        requirements.symlink_to(source)
        with self.assertRaises(ValueError):
            read_source_package(directory)
        self.assertTrue(requirements.is_symlink())
        requirements.unlink()
        os.mkfifo(requirements)
        result = subprocess.run([sys.executable, '-c',
            'import sys\nfrom llm.components.tools.packages import read_source_package\n'
            'try:\n read_source_package(sys.argv[1])\n'
            'except ValueError:\n sys.exit(0)\nraise SystemExit(1)\n', str(directory)],
            timeout=10, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(requirements.exists())

    def test_lost_requirements_are_not_an_explicit_dependency_free_package(self):
        path = self.paths.requirements('saved')
        path.unlink()
        with self.assertRaises((ValueError, OSError)):
            self.data.load('saved')
        self.assertFalse(path.exists())

    def test_lost_package_is_not_an_empty_catalog(self):
        path = self.paths.package('saved')
        shutil.rmtree(path)
        with self.assertRaises((ValueError, OSError)):
            self.data.list()
        self.assertFalse(path.exists())

    def test_lost_explicit_empty_requirements_blocks_another_real_effect(self):
        effect = self.root / 'effects.txt'
        source = '''from pathlib import Path
from llm.components.tools import tool, ToolContract, ToolEffect, ToolIsolation
@tool(ToolContract(revision='probe', effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE,
                   approval_required=False, operation_key_required=False, retry_fields=()))
async def main():
    """Write the independent local probe effect."""
    with Path(EFFECT_PATH).open('a') as stream:
        stream.write('called\\n')
    return 'done'
'''.replace('EFFECT_PATH', repr(str(effect)))
        self.data.save('saved', {'source':source, 'requirements':''})
        tool = load_tool(self.project, 'saved')
        asyncio.run(tool.handler({}))
        self.paths.requirements('saved').unlink()
        rejected = False
        try:
            asyncio.run(tool.handler({}))
        except Exception:
            rejected = True
        self.assertEqual((rejected, effect.read_text()), (True, 'called\n'))

    def snapshot(self):
        return {path.relative_to(self.paths.root).as_posix():path.read_bytes()
                for path in self.paths.root.rglob('*') if path.is_file()}

    def test_absent_and_explicit_empty_requirements_are_distinct_valid_states(self):
        for value in ({'source':'# no imports'}, {'source':'# no imports', 'requirements':''}):
            self.data.save('saved', value)
            self.assertEqual(self.data.load('saved'), value)
            manifest = json.loads(self.paths.manifest.read_bytes())
            self.assertEqual(manifest['packages']['saved']['requirements'] is None, 'requirements' not in value)
        self.data.delete('saved')
        self.assertEqual(self.data.list(), {})
        self.assertEqual(json.loads(self.paths.manifest.read_bytes())['packages'], {})
        self.data.create({'source':'# explicitly recreated'}, identifier='saved')
        self.assertEqual(self.data.load('saved'), {'source':'# explicitly recreated'})

    def test_source_manifest_or_package_loss_is_preserved_by_all_storage_operations(self):
        for lost in ('source', 'manifest', 'package'):
            with self.subTest(lost=lost):
                project = self.projects.create('loss', components=['tools'])
                data = self.projects.component(project, 'tools')
                paths = ToolPaths.for_project(project)
                data.create({'source':'# protected', 'requirements':''}, identifier='saved')
                if lost == 'package':
                    shutil.rmtree(paths.package('saved'))
                else:
                    (paths.source('saved') if lost == 'source' else paths.manifest).unlink()
                before = {p.relative_to(paths.root).as_posix():p.read_bytes()
                          for p in paths.root.rglob('*') if p.is_file()}
                projects = [p.id for p in self.projects.list()]
                for operation in (data.list, lambda:data.load('saved'),
                                  lambda:data.save('saved', {'source':'# replacement'}),
                                  lambda:data.delete('saved'), lambda:self.projects.clone(project),
                                  lambda:self.projects.backup(project, self.root / 'backup')):
                    with self.assertRaises((ValueError, OSError)):
                        operation()
                    self.assertEqual({p.relative_to(paths.root).as_posix():p.read_bytes()
                                      for p in paths.root.rglob('*') if p.is_file()}, before)
                self.assertFalse((self.root / 'backup').exists())
                self.assertEqual([p.id for p in self.projects.list()], projects)
                self.projects.remove_component(project, 'tools')
                for operation in (lambda:self.projects.set_components(project, ('tools',)),
                                  lambda:self.projects.remove_component(project, 'tools', permanent=True)):
                    with self.assertRaises((ValueError, OSError)):
                        operation()
                self.assertEqual(self.projects.load(project.id).initialized_components, ('tools',))

    def test_lost_package_cannot_be_recreated_through_create(self):
        shutil.rmtree(self.paths.package('saved'))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.data.create({'source':'# replacement'}, identifier='saved')
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.paths.package('saved').exists())

    def test_manifest_requires_current_fields_types_owner_and_content_declarations(self):
        original = json.loads(self.paths.manifest.read_bytes())
        invalid = [{key:value for key,value in original.items() if key != field} for field in original]
        invalid += [{**original,'schema_version':value} for value in (None, True, 1.0, '1', 2)]
        invalid += [{**original,key:value} for key,value in (
            ('project_id','another'), ('component','another'), ('packages',None), ('packages',{}))]
        entry = original['packages']['saved']
        entries = [{key:value for key,value in entry.items() if key != field} for field in entry]
        entries += [{**entry,key:value} for key,value in (
            ('source',None), ('source','0'*64), ('requirements',True), ('requirements','invalid'), ('requirements',None))]
        invalid += [{**original,'packages':{'saved':value}} for value in entries]
        invalid += [{**original,'packages':{'../saved':entry}}]
        for value in invalid:
            with self.subTest(value=value):
                self.paths.manifest.write_text(json.dumps(value))
                before = self.snapshot()
                with self.assertRaises((ValueError, OSError)):
                    self.data.list()
                self.assertEqual(self.snapshot(), before)

    def test_changed_and_undeclared_files_are_not_adopted(self):
        source = self.paths.source('saved')
        original = source.read_bytes()
        for path in (source, self.paths.package('saved') / 'extra.py'):
            path.write_text('# changed')
            before = self.snapshot()
            for operation in (self.data.list, lambda:self.data.save('saved', {'source':'# overwrite'})):
                with self.assertRaises(ValueError):
                    operation()
                self.assertEqual(self.snapshot(), before)
            path.unlink()
            source.write_bytes(original)
        orphan = self.paths.package('orphan')
        orphan.mkdir()
        (orphan / 'orphan.py').write_text('# orphan')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'undeclared'):
            self.data.list()
        self.assertEqual(self.snapshot(), before)

    def test_package_semantics_are_checked_even_when_manifest_hashes_match(self):
        for value in ({'source':'# valid', 'requirements':'--index-url https://invalid.example'},
                      {'source':'  ', 'requirements':''}):
            self.paths.source('saved').write_text(value['source'])
            self.paths.requirements('saved').write_text(value['requirements'])
            manifest = json.loads(self.paths.manifest.read_bytes())
            manifest['packages']['saved'] = ToolPaths.fingerprints(value)
            self.paths.manifest.write_text(json.dumps(manifest))
            before = self.snapshot()
            with self.assertRaises(ValueError):
                self.data.load('saved')
            self.assertEqual(self.snapshot(), before)

    def test_regular_manifest_is_required_without_blocking_on_a_fifo(self):
        self.paths.manifest.unlink()
        os.mkfifo(self.paths.manifest)
        code = textwrap.dedent('''
            from pathlib import Path
            import sys
            from llm.components.tools import ToolPaths
            paths = ToolPaths(Path(sys.argv[1]), sys.argv[2])
            try:
                paths.inventory()
            except ValueError:
                pass
            else:
                raise AssertionError('FIFO manifest was accepted')
        ''')
        child = subprocess.run([sys.executable, '-c', code, str(self.paths.root), self.project.id],
                               capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr.decode(errors='replace'))
        self.assertTrue(self.paths.manifest.is_fifo())

    def test_resolved_worker_rejects_changed_project_owner_before_effect(self):
        from tests.llm.test_tool_workers import source
        from llm.services.runtime.tools import ToolExecutionError
        effect = self.root / 'must-not-exist'
        text = source(f'Path({str(effect)!r}).write_text("effect")\nreturn "done"', 'from pathlib import Path')
        self.data.save('saved', {'source':text})
        tool = load_tool(self.project, 'saved')
        manifest = json.loads(self.paths.manifest.read_bytes())
        manifest['project_id'] = 'another'
        self.paths.manifest.write_text(json.dumps(manifest))
        before = self.snapshot()
        with self.assertRaises(ToolExecutionError):
            asyncio.run(tool.handler({}))
        self.assertFalse(effect.exists())
        self.assertEqual(self.snapshot(), before)

    def test_manifest_write_failure_restores_source_requirements_and_inventory(self):
        from llm.components.tools import packages
        for operation in ('create', 'save', 'delete'):
            with self.subTest(operation=operation):
                identifier = 'new' if operation == 'create' else 'saved'
                before = self.snapshot()
                original = packages.atomic_json
                def fail(path, value):
                    if path == self.paths.manifest:
                        raise OSError('manifest failed')
                    return original(path, value)
                with patch.object(packages, 'atomic_json', side_effect=fail), self.assertRaises(OSError):
                    if operation == 'create':
                        self.data.create({'source':'# replacement', 'requirements':''}, identifier=identifier)
                    elif operation == 'save':
                        self.data.save(identifier, {'source':'# replacement', 'requirements':''})
                    else:
                        self.data.delete(identifier)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(set(self.data.list()), {'saved'})

    def test_process_death_preserves_package_and_manifest_commit_boundaries(self):
        code = textwrap.dedent('''
            import os, sys
            from pathlib import Path
            from llm.components.tools import ToolComponent, ToolPaths, packages
            from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
            root, operation, boundary = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
            projects = ProjectManager(ProjectRepository(root), components=[ToolComponent()])
            project = projects.create('child', components=['tools'])
            data = projects.component(project, 'tools')
            if operation != 'create':
                data.create({'source':'# original', 'requirements':'# original'}, identifier='saved')
            write, manifest, remove = packages._write_text, packages.atomic_json, packages.remove_named_tree
            def crash_write(path, value):
                write(path, value)
                if ((path.suffix == '.py' and boundary == 'source')
                        or (path.name == 'requirements.txt' and boundary == 'requirements')):
                    os._exit(77)
            def crash_manifest(path, value):
                manifest(path, value)
                if path.name == ToolPaths.MANIFEST and boundary == 'manifest':
                    os._exit(77)
            def crash_remove(*args):
                remove(*args)
                if boundary == 'package':
                    os._exit(77)
            packages._write_text, packages.atomic_json, packages.remove_named_tree = crash_write, crash_manifest, crash_remove
            changed = {'source':'# changed', 'requirements':'# changed'}
            if operation == 'create':
                data.create(changed, identifier='saved')
            elif operation == 'save':
                data.save('saved', changed)
            else:
                data.delete('saved')
            os._exit(77)
        ''')
        for operation in ('create', 'save', 'delete'):
            boundaries = ('package', 'manifest', 'committed') if operation == 'delete' else (
                'source', 'requirements', 'manifest', 'committed')
            for boundary in boundaries:
                with self.subTest(operation=operation, boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                    child = subprocess.run([sys.executable, '-c', code, temporary, operation, boundary],
                                           capture_output=True, timeout=15)
                    self.assertEqual(child.returncode, 77, child.stderr.decode(errors='replace'))
                    projects = ProjectManager(ProjectRepository(Path(temporary)), components=[ToolComponent()])
                    project, = projects.list()
                    expected = {} if operation == 'create' else {
                        'saved':{'source':'# original', 'requirements':'# original'}}
                    if boundary == 'committed':
                        expected = {} if operation == 'delete' else {
                            'saved':{'source':'# changed', 'requirements':'# changed'}}
                    self.assertEqual(projects.component(project, 'tools').list(), expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
