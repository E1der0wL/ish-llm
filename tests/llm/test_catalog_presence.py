"""Catalog injection and cache sizing preserve authoritative files and explicit values."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from llm.services.infrastructure.storage import DOMAIN_STORAGE_VERSION, atomic_json
from llm.services.lifecycle.steps import StepRepository
from llm.services.query import FileCatalog
from llm.services.runtime.runs import RunRepository


class FalseyProjection:
    def __init__(self, effects):
        self.effects = effects

    def __bool__(self):
        return False

    def __call__(self, record):
        with self.effects.open('a') as stream:
            stream.write(record['id'] + '\n')
        return SimpleNamespace(id=record['id'], status='projected', created_at=record['created_at'])


class CatalogPresenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.effects = self.root / 'projection-calls'

    def write_record(self, identifier, **changes):
        path = self.root / identifier / 'record.json'
        atomic_json(path, dict(storage_version=DOMAIN_STORAGE_VERSION, id=identifier,
            status='completed', created_at='2026-01-01', **changes))
        return path

    def test_falsey_projection_is_called_once_for_cached_actual_file(self):
        path = self.write_record('one')
        before = path.read_bytes()
        projection = FalseyProjection(self.effects)
        catalog = FileCatalog(projection=projection)
        first = catalog.read(path)
        second = catalog.read(path)
        self.assertEqual(first.status, 'projected')
        self.assertIs(first, second)
        self.assertEqual(self.effects.read_text(), 'one\n')
        self.assertIs(catalog.projection, projection)
        self.assertEqual(path.read_bytes(), before)

    def test_invalid_projection_rejected_before_reading_or_effect(self):
        path = self.write_record('one')
        before = path.read_bytes()
        for value in (False, 0, '', [], {}, object()):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(TypeError, 'projection'):
                    FileCatalog(projection=value)
                self.assertFalse(self.effects.exists())
                self.assertEqual(path.read_bytes(), before)
        self.assertEqual(FileCatalog(projection=None).read(path).status, 'completed')

    def test_zero_and_bounded_cache_preserve_files_and_repository_choices(self):
        paths = [self.write_record(identifier) for identifier in ('one', 'two')]
        before = {path: path.read_bytes() for path in paths}
        for capacity in (0, 1):
            with self.subTest(capacity=capacity):
                self.effects.unlink(missing_ok=True)
                catalog = FileCatalog(capacity, projection=FalseyProjection(self.effects))
                for path in [*paths, paths[0]]:
                    catalog.read(path)
                self.assertEqual(self.effects.read_text(), 'one\ntwo\none\n')
                self.assertEqual(len(catalog.entries), capacity)
                self.assertEqual({path: path.read_bytes() for path in paths}, before)
                self.assertEqual(StepRepository(catalog_size=capacity).catalog.max_entries, capacity)
                runs = RunRepository(catalog_size=capacity)
                self.assertEqual([c.max_entries for c in
                    (runs.catalog, runs.result_catalog, runs.usage_catalog)], [capacity] * 3)
        for capacity in (None, False, True, -1, 1.0, '1'):
            with self.subTest(invalid=repr(capacity)):
                with self.assertRaises(ValueError):
                    FileCatalog(capacity)
        self.assertEqual(FileCatalog().max_entries, 4096)
        self.assertEqual(StepRepository().catalog.max_entries, 4096)
        self.assertEqual(RunRepository().catalog.max_entries, 4096)

    def test_corrupt_version_or_identity_never_reaches_projection(self):
        path = self.write_record('one')
        cases = [dict(storage_version=DOMAIN_STORAGE_VERSION, id='different', status='completed', created_at='now'),
                 dict(id='one', status='completed', created_at='now'),
                 dict(storage_version=True, id='one', status='completed', created_at='now')]
        for data in cases:
            with self.subTest(data=data):
                atomic_json(path, data)
                before = path.read_bytes()
                catalog = FileCatalog(projection=FalseyProjection(self.effects))
                with self.assertRaises(ValueError):
                    catalog.read(path)
                self.assertFalse(self.effects.exists())
                self.assertEqual(len(catalog.entries), 0)
                self.assertEqual(path.read_bytes(), before)
