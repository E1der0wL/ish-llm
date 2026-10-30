"""Commit batching preserves durable undo, failure and observer boundaries."""

import os
from contextlib import chdir
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llm.services.infrastructure.locking import WorkspaceOwnership
from llm.services.infrastructure.storage import atomic_bytes, append_bytes, remove_named_tree
from llm.services.infrastructure.transactions import after_commit, current_transaction


class StorageBatchingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.owner = WorkspaceOwnership(self.root)

    def capture(self, seen):
        original = os.fsync

        def sync(fd):
            seen.append(Path(os.readlink(f'/proc/self/fd/{fd}')))
            return original(fd)
        return patch.object(os, 'fsync', sync)

    def test_repeated_append_has_one_undo_and_one_data_flush_before_observer(self):
        target = self.root / 'history'
        atomic_bytes(target, b'old\n')
        synced, observed = [], []
        with self.capture(synced):
            with self.owner.scope():
                for text in (b'a\n', b'b\n', b'c\n'):
                    append_bytes(target, text)
                self.assertEqual(len(current_transaction().entries), 1)
                self.assertNotIn(target, synced)
                after_commit(lambda: observed.append(target in synced))
                self.assertEqual(observed, [])
        self.assertEqual(synced.count(target), 1)
        self.assertEqual(observed, [True])
        self.assertEqual(target.read_bytes(), b'old\na\nb\nc\n')

    def test_repeated_replacement_keeps_original_and_flushes_final_value_once(self):
        target = self.root / 'value'
        atomic_bytes(target, b'old')
        synced = []
        with self.capture(synced):
            with self.owner.scope():
                for value in (b'first', b'second', b'final'):
                    atomic_bytes(target, value)
                self.assertEqual(len(current_transaction().entries), 1)
                self.assertEqual(target.read_bytes(), b'final')
                self.assertNotIn(target, synced)
        self.assertEqual(synced.count(target), 1)
        self.assertEqual(synced.count(target.parent), 2)  # journal admission + final rename

    def test_abort_repeated_and_interleaved_mutations_restores_exact_bytes(self):
        target, other = self.root / 'value', self.root / 'other'
        for operations in ('aarrr', 'rraarr', 'arara', 'rrara'):
            with self.subTest(operations=operations):
                atomic_bytes(target, b'original\n')
                atomic_bytes(other, b'other')
                with self.assertRaisesRegex(RuntimeError, 'abort'):
                    with self.owner.scope():
                        for i, operation in enumerate(operations):
                            if operation == 'a':
                                append_bytes(target, b'append\n')
                            else:
                                atomic_bytes(target, str(i).encode())
                            atomic_bytes(other, str(i).encode())
                        raise RuntimeError('abort')
                self.assertEqual(target.read_bytes(), b'original\n')
                self.assertEqual(other.read_bytes(), b'other')

    def test_final_data_sync_failure_rolls_back_and_never_notifies(self):
        target = self.root / 'value'
        atomic_bytes(target, b'original')
        observed = []
        original = os.fsync
        failed = False

        def sync(fd):
            nonlocal failed
            if not failed and Path(os.readlink(f'/proc/self/fd/{fd}')) == target:
                failed = True
                raise OSError('commit data flush failed')
            return original(fd)

        with patch.object(os, 'fsync', sync), self.assertRaisesRegex(OSError, 'flush failed'):
            with self.owner.scope():
                atomic_bytes(target, b'new')
                after_commit(lambda: observed.append('published'))
        self.assertTrue(failed)
        self.assertEqual(observed, [])
        with self.owner.scope():
            self.assertEqual(target.read_bytes(), b'original')

    def test_existing_path_is_not_resynced_on_read_only_transaction(self):
        with self.owner.scope():
            atomic_bytes(self.root/'value', b'1')
        synced = []
        with self.capture(synced), self.owner.scope():
            self.assertEqual((self.root/'value').read_bytes(), b'1')
        self.assertEqual(synced, [])

    def test_atomic_writes_outside_transaction_remain_immediately_durable(self):
        target = self.root/'value'
        synced = []
        with self.capture(synced):
            atomic_bytes(target, b'new')
            append_bytes(target, b'\n')
        self.assertEqual(len(synced), 3)  # atomic data+directory, append data
        self.assertEqual(target.read_bytes(), b'new\n')

    def test_new_tree_includes_untracked_component_copy_at_commit(self):
        synced = []
        with self.capture(synced), self.owner.scope():
            transaction = current_transaction()
            tree = self.root/'tree'
            transaction.mkdir(tree)
            (tree/'copied').write_bytes(b'copy')
            atomic_bytes(tree/'metadata', b'{}')
        self.assertEqual(synced.count(tree/'copied'), 1)
        self.assertEqual(synced.count(tree/'metadata'), 1)

    def test_new_directory_entries_flush_at_commit_not_at_each_mkdir(self):
        synced, observed = [], []
        tree = self.root / 'tree'
        with self.capture(synced):
            with self.owner.scope():
                transaction = current_transaction()
                transaction.mkdir(tree / 'one' / 'two')
                self.assertNotIn(tree, synced)
                self.assertNotIn(tree / 'one', synced)
                atomic_bytes(tree / 'one' / 'two' / 'value', b'complete')
                after_commit(lambda: observed.append(tuple(synced)))
            self.assertEqual(synced.count(tree), 1)
            self.assertEqual(synced.count(tree / 'one'), 1)
            self.assertEqual(synced.count(tree / 'one' / 'two'), 1)
        self.assertEqual(len(observed), 1)
        self.assertIn(tree / 'one' / 'two' / 'value', observed[0])

    def test_new_restore_parent_is_durable_before_dependent_remove_undo(self):
        from llm.services.infrastructure.transaction_records import UndoKind, UndoOperation
        synced = []
        tree = self.root / 'tree'
        with self.capture(synced), self.assertRaisesRegex(RuntimeError, 'abort'):
            with self.owner.scope():
                transaction = current_transaction()
                transaction.mkdir(tree)
                # Component copies may create descendants without using mkdir.
                (tree / 'nested' / 'child').mkdir(parents=True)
                original = transaction._entry
                def entry(value):
                    if value[UndoOperation.KIND] == UndoKind.REMOVE:
                        self.assertIn(tree / 'nested', synced)
                        self.assertIn(tree, synced)
                        self.assertIn(self.root, synced)
                    original(value)
                with patch.object(transaction, '_entry', entry):
                    transaction.remove(tree / 'nested' / 'child')
                raise RuntimeError('abort')
        self.assertFalse(tree.exists())

    def test_intermediate_replacement_is_durable_before_later_backup(self):
        target = self.root/'value'
        atomic_bytes(target, b'old')
        synced = []
        with self.capture(synced), self.owner.scope():
            atomic_bytes(target, b'intermediate')
            append_bytes(target, b'append')
            self.assertEqual(synced.count(target), 1)
            self.assertIn(target.parent, synced)
            atomic_bytes(target, b'final')
            self.assertEqual(synced.count(target), 2)
        self.assertEqual(synced.count(target), 3)

    def test_removed_parent_does_not_flush_stale_children_or_reuse_undo(self):
        tree = self.root/'tree'
        with self.owner.scope():
            atomic_bytes(tree/'value', b'original')
        with self.assertRaisesRegex(RuntimeError, 'abort'):
            with self.owner.scope():
                atomic_bytes(tree/'value', b'new')
                remove_named_tree(self.root, tree, 'tree')
                atomic_bytes(tree/'value', b'recreated')
                atomic_bytes(tree/'value', b'last')
                raise RuntimeError('abort')
        self.assertEqual((tree/'value').read_bytes(), b'original')
        with self.owner.scope():
            atomic_bytes(tree/'value', b'new')
            remove_named_tree(self.root, tree, 'tree')
            atomic_bytes(tree, b'now a file')
        self.assertEqual(tree.read_bytes(), b'now a file')

    def test_relative_path_and_absolute_path_share_flush_and_undo_identity(self):
        target = self.root/'value'
        atomic_bytes(target, b'original')
        synced = []
        with chdir(self.root), self.capture(synced), self.owner.scope():
            atomic_bytes(Path('value'), b'first')
            append_bytes(target, b'append')
            atomic_bytes(target, b'final')
            self.assertEqual(synced.count(target), 2)
        self.assertEqual(synced.count(target), 3)

    def test_append_undo_references_durable_replacement_before_recording_length(self):
        from llm.services.infrastructure.transactions import Transaction
        target = self.root/'value'
        atomic_bytes(target, b'old')
        synced = []
        original = Transaction._entry

        def entry(transaction, value):
            if value['kind'] == 'append':
                self.assertIn(target, synced)
                self.assertIn(target.parent, synced)
            return original(transaction, value)

        with self.capture(synced), patch.object(Transaction, '_entry', entry):
            with self.owner.scope():
                atomic_bytes(target, b'longer intermediate')
                synced.clear()
                append_bytes(target, b'appended')

    def test_data_and_directory_flushes_precede_commit_marker(self):
        from llm.services.infrastructure import transactions
        target = self.root/'value'
        atomic_bytes(target, b'old')
        synced, markers = [], []
        original = transactions._write

        def write(path, data):
            if path.name == 'COMMITTED':
                self.assertIn(target, synced)
                self.assertIn(target.parent, synced)
                self.assertLess(synced.index(target), len(synced)-1)
                markers.append(path)
            return original(path, data)

        with self.capture(synced), patch.object(transactions, '_write', write):
            with self.owner.scope():
                atomic_bytes(target, b'new')
        self.assertEqual(len(markers), 1)
