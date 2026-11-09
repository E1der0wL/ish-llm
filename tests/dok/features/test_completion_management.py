"""Manual edits and collection policies share the persisted completion owners."""
from dataclasses import replace
import unittest

from dok import DockConfig, SettingsError
from dok.features.completion.models import Analysis, Candidate, Catalog, MergeMode, Registration


class CompletionManagementTests(unittest.TestCase):
    def test_locks_survive_merge_replace_and_storage_roundtrip(self):
        locked = Candidate('--locked', help='manual', locked=True)
        registration = Registration(('tool',), items=(locked, Candidate('--old')))
        incoming = Analysis(tuple(Candidate(name, help='new', locked=True) for name in
                                  ('--locked', '--new')))
        for mode in MergeMode:
            with self.subTest(mode=mode):
                result = registration.apply(incoming, mode, 'tool docs')
                self.assertEqual(result.item('--locked'), locked)
                self.assertFalse(result.item('--new').locked)
                self.assertEqual(result.item('--old') is not None, mode == MergeMode.MERGE)
                self.assertEqual(Registration.from_dict(result.to_dict()), result)
                prefix_locked = replace(registration, locked=True)
                self.assertIs(prefix_locked.apply(incoming, mode, 'new docs'), prefix_locked)
        self.assertEqual([item.name for item in registration.collection_items(incoming)], ['--new'])

    def test_deletion_allows_recollection_and_locks_still_allow_manual_edits(self):
        original = Registration(('tool',), items=(Candidate('--all'),))
        deleted = original.remove_item('--all')
        analysis = Analysis((Candidate('--all'),))
        restored = deleted.apply(analysis, MergeMode.MERGE, '')
        self.assertEqual(restored.items, original.items)
        # A lock protects collection, not deliberate manual editing/deletion.
        locked = replace(original, locked=True)
        changed = locked.edit_item('--all', Candidate('--renamed', help='manual'))
        self.assertEqual(changed.items, (Candidate('--renamed', help='manual'),))
        self.assertEqual(changed.remove_item('--renamed').items, ())

    def test_removed_collection_exclusion_has_no_persisted_setting_or_alias(self):
        self.assertNotIn('excluded', Registration.schema()['properties'])
        with self.assertRaises(SettingsError):
            Registration.from_dict({'prefix': ['tool'], 'excluded': ['--old']})

    def test_prefix_rename_delete_and_conflicts_are_exact_and_atomic(self):
        root = Registration(('git',), items=(Candidate('--version'),))
        child = Registration(('git', 'log'))
        catalog = Catalog(registrations=(root, child))
        renamed = replace(root, prefix=('g',))
        draft = catalog.edit_registration(root.prefix, renamed)
        self.assertEqual(draft.get(child.prefix), child)
        self.assertIsNone(draft.get(root.prefix))
        draft = draft.remove_registration(renamed.prefix)
        self.assertEqual(draft.registrations, (child,))
        saved = catalog.replace_contents(draft)
        self.assertEqual(saved.revision, catalog.revision + 1)
        with self.assertRaises(ValueError):
            catalog.edit_registration(root.prefix, replace(root, prefix=child.prefix))
        with self.assertRaises(ValueError):
            root.edit_item(None, Candidate('--version'))
        self.assertEqual(catalog.registrations, (root, child))

    def test_manager_default_key_and_collision_validation_use_key_owner(self):
        config = DockConfig.initial()
        self.assertEqual(config.completion.manage.key.to_list(), ['escape', 'm'])
        self.assertEqual(config.completion.manage.key.label(), 'Alt+M')
        self.assertEqual(config.popups.calendar.key.label(), 'Alt+C')
        self.assertEqual(config.dock.keys.toggle.label(), 'Ctrl+W')
        with self.assertRaises(SettingsError):
            DockConfig(completion={'manage': {'key': ['escape', 'o']}})
        self.assertIsNone(DockConfig(completion={'manage': {'key': None}}).completion.manage.key)
