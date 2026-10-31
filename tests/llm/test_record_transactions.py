"""Record batches preserve durable admission while amortizing journal fsync."""

import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.services.infrastructure.locking import WorkspaceOwnership
from llm.services.infrastructure.record_transactions import RecordTransaction
from llm.services.infrastructure.record_journal import RecordJournal
from llm.services.infrastructure.storage import (
    StorageIO, atomic_bytes, atomic_json, append_bytes, read_json, open_record,
    record_exists, record_children, make_directory, prepare_create,
)
from llm.services.infrastructure.transactions import after_commit


class RecordTransactionTests(unittest.TestCase):
    def test_lexical_routing_preserves_descendants_and_checked_targets(self):
        from llm.services.infrastructure.transactions import Transaction, _relative

        transaction = Transaction(self.owner.transactions)
        root, journal = self.owner.transactions.root, self.owner.transactions.journal
        paths = (root, root / 'one', root / '한글 /deep/file', root.parent,
                 root.with_name(root.name + '-other') / 'file', journal,
                 journal / 'nested/file', root / 'one/../two')
        for path in paths:
            with self.subTest(path=path):
                expected = root in path.absolute().parents and journal not in path.absolute().parents
                self.assertEqual(transaction.owns(path), expected)
                try:
                    relative = path.relative_to(root)
                except ValueError:
                    with self.assertRaises(ValueError):
                        _relative(path, root)
                else:
                    self.assertEqual(_relative(path, root), relative)
        with self.assertRaises(ValueError):
            transaction._target(root / 'one/../two')
        linked = root / 'linked'
        linked.symlink_to(root / 'missing')
        with self.assertRaises(ValueError):
            transaction._target(linked / 'file')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.owner = WorkspaceOwnership(self.root)

    def test_prepared_next_segment_still_fsyncs_first_frame_before_ack(self):
        path = self.root / 'value'
        self.owner.retain()
        try:
            with self.owner.scope(records=True):
                atomic_bytes(path, b'first')
            with self.owner.scope():
                self.owner.transactions.records.checkpoint(prepare_next=True)
            log = self.owner.transactions.records.log
            self.assertEqual(log.stat().st_size, 0)
            calls, seen, original = [], [], os.fsync
            def sync(fd):
                calls.append(Path(os.readlink(f'/proc/self/fd/{fd}')))
                return original(fd)
            with patch.object(os, 'fsync', sync), self.owner.scope(records=True):
                atomic_bytes(path, b'second')
                after_commit(lambda: seen.append(list(calls)))
            self.assertEqual(calls, [log])
            self.assertEqual(seen, [[log]])
        finally:
            self.owner.release()

    def test_failed_preparation_publication_requires_recovery_before_next_commit(self):
        import llm.services.infrastructure.record_journal as journal_module

        path = self.root / 'value'
        self.owner.retain()
        try:
            with self.owner.scope(records=True):
                atomic_bytes(path, b'first')
            original = journal_module._sync
            def sync(target):
                if target == self.owner.transactions.journal:
                    raise OSError('directory publication')
                return original(target)
            with self.assertRaisesRegex(OSError, 'publication'), patch.object(journal_module, '_sync', sync):
                with self.owner.scope():
                    self.owner.transactions.records.checkpoint(prepare_next=True)
            self.assertFalse(self.owner.transactions.records.ready)
            with self.owner.scope(records=True):
                self.assertEqual(path.read_bytes(), b'first')
                atomic_bytes(path, b'second')
            self.assertEqual(path.read_bytes(), b'second')
        finally:
            self.owner.release()

    def test_prepared_segment_recovers_after_crash_with_or_without_new_commit(self):
        for committed in (False, True):
            with self.subTest(committed=committed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / 'value'
                pid = os.fork()
                if pid == 0:
                    owner = WorkspaceOwnership(root)
                    owner.retain()
                    with owner.scope(records=True):
                        atomic_bytes(path, b'first')
                    with owner.scope():
                        owner.transactions.records.checkpoint(prepare_next=True)
                    if committed:
                        with owner.scope(records=True):
                            atomic_bytes(path, b'second')
                        path.write_bytes(b'first')  # Loss of an unsynchronized projection.
                    os._exit(73)
                _, status = os.waitpid(pid, 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 73)
                with WorkspaceOwnership(root).scope():
                    self.assertEqual(path.read_bytes(), b'second' if committed else b'first')

    def test_existing_segment_uses_one_fsync_per_commit_before_observer(self):
        path = self.root / 'value'
        self.owner.retain()
        try:
            with self.owner.scope(records=True):
                atomic_bytes(path, b'first')
            log = self.owner.transactions.records.log
            calls, seen = [], []
            original = os.fsync
            def sync(fd):
                calls.append(Path(os.readlink(f'/proc/self/fd/{fd}')))
                return original(fd)
            with patch.object(os, 'fsync', sync):
                for index in range(3):
                    with self.owner.scope(records=True):
                        append_bytes(path, b'x')
                        after_commit(lambda: seen.append(len(calls)))
                        self.assertEqual(len(seen), index)
            self.assertEqual(calls, [log] * 3)
            self.assertEqual(seen, [1, 2, 3])
            self.assertEqual(path.read_bytes(), b'firstxxx')
        finally:
            self.owner.release()

    def test_fsync_failure_is_uncertain_and_recovery_durably_resolves_it(self):
        path, seen = self.root / 'value', []
        path.write_bytes(b'old')
        original = os.fsync
        def sync(fd):
            name = Path(os.readlink(f'/proc/self/fd/{fd}')).name
            if RecordJournal.BUNDLE.fullmatch(name):
                raise OSError('journal fsync failed')
            return original(fd)
        with patch.object(os, 'fsync', sync), self.assertRaisesRegex(OSError, 'fsync failed'):
            with self.owner.scope(records=True):
                atomic_bytes(path, b'complete frame')
                after_commit(lambda: seen.append('ack'))
        self.assertEqual(seen, [])
        self.assertEqual(path.read_bytes(), b'old')
        # The complete but unacknowledged frame is made durable before observation.
        with WorkspaceOwnership(self.root).scope():
            self.assertEqual(path.read_bytes(), b'complete frame')
        with WorkspaceOwnership(self.root).scope():
            self.assertEqual(path.read_bytes(), b'complete frame')

    def test_payload_copy_uses_bounded_reads_and_preserves_bytes(self):
        payload = b'payload\n' * RecordTransaction.BLOCK
        path, reads = self.root / 'large', []
        original = RecordJournal.append
        def append(journal, sequence, source):
            read = source.read
            def bounded(size):
                self.assertGreater(size, 0)
                self.assertLessEqual(size, RecordTransaction.BLOCK)
                reads.append(size)
                return read(size)
            with patch.object(source, 'read', bounded):
                return original(journal, sequence, source)
        with patch.object(RecordJournal, 'append', append):
            with self.owner.scope(records=True):
                atomic_bytes(path, payload)
        self.assertGreater(len(reads), 1)
        self.assertEqual(path.read_bytes(), payload)

    def test_failed_append_discards_only_incomplete_tail(self):
        path = self.root / 'value'
        path.write_bytes(b'old')
        original = RecordJournal.append
        def append(journal, sequence, source):
            read = source.read
            calls = 0
            def partial(size):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError('disk full during append')
                return read(min(size, 5))
            with patch.object(source, 'read', partial):
                return original(journal, sequence, source)
        with patch.object(RecordJournal, 'append', append), self.assertRaisesRegex(OSError, 'disk full'):
            with self.owner.scope(records=True):
                atomic_bytes(path, b'not committed')
        with WorkspaceOwnership(self.root).scope():
            self.assertEqual(path.read_bytes(), b'old')

    def test_crash_in_final_header_or_payload_preserves_prior_commits(self):
        import hashlib
        for phase in ('header', 'payload'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / 'value'
                path.write_bytes(b'old')
                pid = os.fork()
                if pid == 0:
                    owner = WorkspaceOwnership(root)
                    owner.retain()
                    with owner.scope(records=True):
                        atomic_bytes(path, b'committed')
                    original = RecordJournal.append
                    def interrupted(journal, sequence, source):
                        size = source.seek(0, os.SEEK_END)
                        source.seek(0)
                        prefix = journal.PREFIX.pack(journal.MAGIC, sequence, size)
                        header = prefix + hashlib.sha256(prefix).digest()
                        with journal.log.open('ab') as stream:
                            stream.write(header[:5] if phase == 'header' else header + source.read(5))
                            stream.flush()
                            os.fsync(stream.fileno())
                        path.write_bytes(b'lost projection')
                        os._exit(74)
                    RecordJournal.append = interrupted
                    with owner.scope(records=True):
                        atomic_bytes(path, b'never acknowledged')
                    os._exit(99)
                _, status = os.waitpid(pid, 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 74)
                for _ in range(2):
                    with WorkspaceOwnership(root).scope():
                        self.assertEqual(path.read_bytes(), b'committed')

    def test_corrupt_complete_later_frame_preserves_all_targets_and_evidence(self):
        paths = [self.root / 'one', self.root / 'two']
        self.owner.retain()
        for path in paths:
            with self.owner.scope(records=True):
                atomic_bytes(path, b'committed')
        frame = self.owner.transactions.records.frames[-1]
        self.owner.transactions.records.reset()
        self.owner.release()
        for path in paths:
            path.write_bytes(b'unchanged')
        with frame.path.open('r+b') as stream:
            stream.seek(frame.offset)
            stream.write(b'X')
        evidence = frame.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'checksum'), WorkspaceOwnership(self.root).scope():
            pass
        self.assertEqual([path.read_bytes() for path in paths], [b'unchanged'] * 2)
        self.assertEqual(frame.path.read_bytes(), evidence)

    def test_unframed_retained_journal_is_rejected_without_conversion(self):
        self.owner.transactions.journal.mkdir()
        path = self.owner.transactions.journal / ('records-1-' + 'a' * 32)
        path.write_bytes(b'old unframed record payload')
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'frame'), self.owner.scope():
            pass
        self.assertEqual(path.read_bytes(), before)

    def test_staged_reads_replacement_append_and_directories(self):
        path = self.root / 'nested' / 'value'
        with self.owner.scope(records=True):
            atomic_bytes(path, b'one')
            append_bytes(path, b'two\n')
            self.assertFalse(path.exists())
            self.assertTrue(record_exists(path))
            self.assertEqual(list(record_children(path.parent)), [path])
            with open_record(path) as stream:
                self.assertEqual(stream.read(), b'onetwo\n')
                stream.seek(-4, os.SEEK_END)
                self.assertEqual(stream.readline(), b'two\n')
            atomic_json(path, {'final': True})
            self.assertEqual(read_json(path), {'final': True})
        self.assertEqual(read_json(path), {'final': True})
        self.assertEqual([p.name for p in self.owner.transactions.journal.iterdir()], [RecordJournal.CHECKPOINT])

    def test_append_reads_ranges_without_copying_existing_history(self):
        path = self.root / 'history'
        path.write_bytes(b'a' * (1024 * 1024) + b'\n')
        with self.owner.scope(records=True):
            append_bytes(path, b'new\n')
            append_bytes(path, b'last\n')
            from llm.services.infrastructure.transactions import current_transaction
            batch = current_transaction()
            with open_record(path) as stream:
                stream.seek(-9, os.SEEK_END)
                self.assertEqual(stream.read(), b'new\nlast\n')
            self.assertEqual(batch.pending.stat().st_size, 9)
        self.assertEqual(path.stat().st_size, 1024 * 1024 + 10)

    def test_abort_and_nested_failure_preserve_original(self):
        path = self.root / 'value'
        path.write_bytes(b'original')
        observed = []
        with self.assertRaisesRegex(RuntimeError, 'Nested storage'):
            with self.owner.scope(records=True):
                atomic_bytes(path, b'new')
                try:
                    with self.owner.scope():
                        append_bytes(path, b'changed')
                        raise ValueError('nested failure')
                except ValueError:
                    pass
                after_commit(lambda: observed.append(True))
        self.assertEqual(path.read_bytes(), b'original')
        self.assertEqual(observed, [])

    def test_general_mutation_is_rejected_before_touching_destination(self):
        with self.assertRaisesRegex(RuntimeError, 'raw filesystem'):
            with self.owner.scope(records=True):
                atomic_json(self.root / 'value', {'new': True})
                prepare_create(self.root / 'raw-copy')
        self.assertFalse((self.root / 'value').exists())

    def test_failed_pending_cleanup_still_rolls_back_participants(self):
        from llm.services.infrastructure.transactions import current_transaction
        path, restored = self.root / 'value', []
        path.write_bytes(b'old')
        original = Path.unlink
        def unlink(target, *args, **kwargs):
            if target.name.startswith(RecordTransaction.PENDING):
                raise OSError('cleanup denied')
            return original(target, *args, **kwargs)
        with patch.object(Path, 'unlink', unlink), self.assertRaisesRegex(OSError, 'cleanup denied'):
            with self.owner.scope(records=True):
                current_transaction().on_rollback('projection', lambda: restored.append(True))
                atomic_bytes(path, b'new')
                raise RuntimeError('abort before publication')
        self.assertEqual(restored, [True])
        with WorkspaceOwnership(self.root).scope():
            self.assertEqual(path.read_bytes(), b'old')

    def test_staging_failure_before_publication_rolls_back_without_observers(self):
        path, seen = self.root / 'value', []
        path.write_bytes(b'old')
        original = RecordTransaction.commit
        def commit(transaction):
            with patch.object(transaction.spool, 'flush', side_effect=OSError('disk full')):
                return original(transaction)
        with patch.object(RecordTransaction, 'commit', commit), self.assertRaisesRegex(OSError, 'disk full'):
            with self.owner.scope(records=True):
                atomic_bytes(path, b'new')
                after_commit(lambda: seen.append(True))
        self.assertEqual(path.read_bytes(), b'old')
        self.assertEqual(seen, [])

    def test_published_bundle_recovers_all_records_before_next_reader(self):
        paths = [self.root / 'one', self.root / 'two']
        for path in paths:
            path.write_bytes(b'old')
        seen = []
        with patch.object(RecordTransaction, 'materialize', side_effect=OSError('lost device')):
            with self.assertRaisesRegex(OSError, 'lost device'):
                with self.owner.scope(records=True):
                    for path in paths:
                        atomic_bytes(path, b'new')
                    after_commit(lambda: seen.append(True))
        self.assertEqual(seen, [])
        with WorkspaceOwnership(self.root).scope():
            self.assertEqual([p.read_bytes() for p in paths], [b'new', b'new'])

    def test_crash_before_and_after_publication(self):
        for published in (False, True):
            with self.subTest(published=published):
                path = self.root / 'value'
                path.write_bytes(b'old')
                pid = os.fork()
                if pid == 0:
                    owner = WorkspaceOwnership(self.root)
                    if published:
                        RecordTransaction.materialize = classmethod(lambda *args: os._exit(71))
                    with owner.scope(records=True):
                        atomic_bytes(path, b'new')
                        if not published:
                            os._exit(70)
                    os._exit(99)
                _, status = os.waitpid(pid, 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 71 if published else 70)
                with WorkspaceOwnership(self.root).scope():
                    self.assertEqual(path.read_bytes(), b'new' if published else b'old')

    def test_corrupt_bundle_fails_closed_before_any_materialization(self):
        path = self.root / 'value'
        path.write_bytes(b'old')
        with patch.object(RecordTransaction, 'materialize', side_effect=OSError('stop')):
            with self.assertRaises(OSError), self.owner.scope(records=True):
                atomic_bytes(path, b'new')
        bundle = next(self.owner.transactions.journal.glob('records-*'))
        with bundle.open('r+b') as stream:
            stream.write(b'X')
        with self.assertRaisesRegex(ValueError, 'checksum'), WorkspaceOwnership(self.root).scope():
            pass
        self.assertEqual(path.read_bytes(), b'old')

    def test_partial_append_recovery_is_idempotent(self):
        path = self.root / 'history'
        path.write_bytes(b'old\n')
        with patch.object(RecordTransaction, 'materialize', side_effect=OSError('stop')):
            with self.assertRaises(OSError), self.owner.scope(records=True):
                append_bytes(path, b'new\nlast\n')
        with path.open('ab') as stream:
            stream.write(b'ne')
        # Repeated owned recovery must not duplicate the committed suffix.
        with WorkspaceOwnership(self.root).scope():
            self.assertEqual(path.read_bytes(), b'old\nnew\nlast\n')
        with WorkspaceOwnership(self.root).scope():
            self.assertEqual(path.read_bytes(), b'old\nnew\nlast\n')

    def test_conversation_projection_survives_commit_without_history_replay(self):
        from llm.services.history.conversation import ConversationStore
        from llm.core.models import MessageRole, MessageStatus
        store = ConversationStore(self.root / 'messages.jsonl')
        with self.owner.scope(records=True):
            message = store.create(MessageRole.USER, 'current request', MessageStatus.QUEUED)
            atomic_json(self.root / 'unrelated', {'value': 1})
            with patch.object(store, '_clear', side_effect=AssertionError('unrelated write invalidated history')):
                self.assertEqual(store.get(message.id).content, 'current request')
        with self.owner.scope(), patch.object(store, '_clear', side_effect=AssertionError('commit replayed history')):
            self.assertEqual(store.get(message.id).content, 'current request')

    def test_directory_only_batch_and_postcommit_read(self):
        directory = self.root / 'one' / 'two'
        seen = []
        with self.owner.scope(records=True):
            make_directory(directory)
            after_commit(lambda: seen.append(directory.is_dir()))
            self.assertFalse(directory.exists())
        self.assertEqual(seen, [True])

    def test_json_reader_keeps_utf8_storage_contract(self):
        path = self.root / 'value'
        for encoding in ('utf-16', 'utf-32'):
            with self.subTest(encoding=encoding):
                path.write_bytes('{"value": 1}'.encode(encoding))
                with self.assertRaises(UnicodeError):
                    read_json(path)
                with self.owner.scope(records=True):
                    atomic_bytes(path, '{"value": 2}'.encode(encoding))
                    with self.assertRaises(UnicodeError):
                        read_json(path)

    def test_path_escape_links_and_reserved_targets_are_rejected(self):
        link = self.root / 'link'
        link.symlink_to(self.root, target_is_directory=True)
        targets = [link / 'value', self.root / 'x' / '..' / 'value',
                   self.root / '.transactions' / 'value', self.root / '.ish.lock']
        for path in targets:
            with self.subTest(path=path):
                # Reserved/unowned targets are not permitted record destinations.
                with self.owner.scope(records=True):
                    from llm.services.infrastructure.transactions import current_transaction
                    with self.assertRaises(ValueError):
                        current_transaction().write(path, b'bad', append=False)

    def test_crash_during_materialization_recovers_remaining_records(self):
        paths = [self.root / 'one', self.root / 'two']
        for path in paths:
            path.write_bytes(b'old')
        pid = os.fork()
        if pid == 0:
            original = os.replace
            def replace(source, destination):
                result = original(source, destination)
                if Path(destination) == paths[0]:
                    os._exit(72)
                return result
            with patch.object(os, 'replace', replace), WorkspaceOwnership(self.root).scope(records=True):
                for path in paths:
                    atomic_bytes(path, b'new')
            os._exit(99)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 72)
        with WorkspaceOwnership(self.root).scope():
            self.assertEqual([p.read_bytes() for p in paths], [b'new', b'new'])

    def test_garbage_never_replays_over_later_committed_bytes(self):
        path = self.root / 'value'
        path.write_bytes(b'old')
        self.owner.retain()
        try:
            with self.owner.scope(records=True):
                atomic_bytes(path, b'first')
            garbage = self.owner.transactions.records.frames[0].path
            saved = garbage.read_bytes()
        finally:
            self.owner.release()
        with self.owner.scope(records=True):
            atomic_bytes(path, b'latest')
        # Simulate resurrection of the already-retired file after power loss.
        garbage.write_bytes(saved)
        with WorkspaceOwnership(self.root).scope():
            self.assertEqual(path.read_bytes(), b'latest')

    def test_recovery_validates_manifest_paths_before_mutating_any_file(self):
        import hashlib
        import json
        path = self.root / 'value'
        path.write_bytes(b'old')
        with patch.object(RecordTransaction, 'materialize', side_effect=OSError('stop')):
            with self.assertRaises(OSError), self.owner.scope(records=True):
                atomic_bytes(path, b'new')
        bundle = next(self.owner.transactions.journal.glob('records-*'))
        data = bundle.read_bytes()[RecordJournal.HEADER.size:]
        footer = RecordTransaction.FOOTER
        length, _, magic = footer.unpack(data[-footer.size:])
        boundary = len(data) - footer.size - length
        manifest = json.loads(data[boundary:-footer.size])
        manifest['records'][0]['path'] = '../outside'
        raw = json.dumps(manifest).encode()
        body = data[:boundary] + raw + footer.pack(len(raw), hashlib.sha256(raw).digest(), magic)
        prefix = RecordJournal.PREFIX.pack(RecordJournal.MAGIC, manifest['sequence'], len(body))
        bundle.write_bytes(prefix + hashlib.sha256(prefix).digest() + body)
        with self.assertRaises(ValueError), WorkspaceOwnership(self.root).scope():
            pass
        self.assertEqual(path.read_bytes(), b'old')

    def test_fsync_reduction_and_observer_after_journal_durability(self):
        counts = []
        for records in (False, True):
            for i in range(20):
                (self.root / str(i)).write_bytes(b'old')
            calls, observed = [], []
            original = os.fsync
            def sync(fd):
                calls.append(os.readlink(f'/proc/self/fd/{fd}'))
                return original(fd)
            self.owner.retain()
            try:
                with patch.object(os, 'fsync', sync), self.owner.scope(records=records):
                    for i in range(20):
                        atomic_bytes(self.root / str(i), b'new')
                    after_commit(lambda: observed.append(len(calls)))
                    self.assertEqual(observed, [])
                counts.append(len(calls))
                self.assertEqual(observed, [len(calls)])
                if records:
                    self.assertTrue(any(RecordJournal.BUNDLE.fullmatch(Path(p).name) for p in calls))
                    self.assertFalse(any('records-pending-' in p for p in calls))
                    self.assertFalse(any(Path(p).name.isdigit() for p in calls))
            finally:
                self.owner.release()
        self.assertLess(counts[1], counts[0] * .5, counts)

    def test_retained_commits_coalesce_sync_at_undo_boundary(self):
        path = self.root / 'value'
        self.owner.retain()
        try:
            for data in (b'a', b'b', b'c'):
                with self.owner.scope(records=True):
                    append_bytes(path, data)
            self.assertEqual(len(self.owner.transactions.records.frames), 3)
            calls, original = [], os.fsync
            def sync(fd):
                calls.append(os.readlink(f'/proc/self/fd/{fd}'))
                return original(fd)
            with patch.object(os, 'fsync', sync):
                with self.owner.scope():
                    self.assertEqual(path.read_bytes(), b'abc')
                self.assertEqual(calls, [])  # A read is not a flush barrier.
                with self.assertRaisesRegex(RuntimeError, 'abort'), self.owner.scope():
                    atomic_bytes(path, b'undo mutation')
                    self.assertEqual(self.owner.transactions.records.frames, [])
                    raise RuntimeError('abort')
            self.assertEqual(path.read_bytes(), b'abc')
            self.assertEqual(calls.count(str(path)), 1)
        finally:
            self.owner.release()

    def test_crash_chain_recovers_latest_replace_and_append(self):
        path = self.root / 'value'
        path.write_bytes(b'base')
        pid = os.fork()
        if pid == 0:
            owner = WorkspaceOwnership(self.root)
            owner.retain()
            for data, append in ((b'-1', True), (b'-2', True), (b'new', False), (b'-3', True)):
                with owner.scope(records=True):
                    (append_bytes if append else atomic_bytes)(path, data)
            # Simulate target projection loss; the complete journal is durable.
            path.write_bytes(b'base')
            os._exit(73)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
        with WorkspaceOwnership(self.root).scope():
            self.assertEqual(path.read_bytes(), b'new-3')

    def test_missing_commit_in_chain_fails_before_replay(self):
        path = self.root / 'value'
        self.owner.retain()
        for data in (b'a', b'b', b'c'):
            with self.owner.scope(records=True):
                append_bytes(path, data)
        first, middle, last = self.owner.transactions.records.frames
        data = first.path.read_bytes()
        first.path.write_bytes(data[:middle.offset - RecordJournal.HEADER.size]
                               + data[last.offset - RecordJournal.HEADER.size:])
        # End the proof lifetime without checkpointing, as a process crash does.
        self.owner.transactions.records.reset()
        self.owner.release()
        path.write_bytes(b'unchanged')
        with self.assertRaisesRegex(ValueError, 'sequence'), WorkspaceOwnership(self.root).scope():
            pass
        self.assertEqual(path.read_bytes(), b'unchanged')

    def test_checkpoint_failure_retains_recovery_source_and_releases_owner(self):
        path = self.root / 'value'
        self.owner.retain()
        with self.owner.scope(records=True):
            atomic_bytes(path, b'committed')
        original = os.fsync
        def sync(fd):
            if os.readlink(f'/proc/self/fd/{fd}') == str(path):
                raise OSError('checkpoint failed')
            return original(fd)
        with patch.object(os, 'fsync', sync), self.assertRaisesRegex(OSError, 'checkpoint failed'):
            self.owner.release()
        self.assertTrue(list(self.owner.transactions.journal.glob('records-*')))
        path.write_bytes(b'lost projection')
        with WorkspaceOwnership(self.root).scope():
            self.assertEqual(path.read_bytes(), b'committed')

    def test_record_operation_declaration_preserves_shared_storage_runner(self):
        from llm.services.infrastructure.locking import workspace_locked
        class Handle:
            ownership = self.owner
            @workspace_locked(records=True)
            def save(handle):
                from llm.services.infrastructure.transactions import current_transaction
                self.assertTrue(current_transaction().record_batch)
                atomic_bytes(self.root / 'value', b'written')
        asyncio.run(StorageIO(self.owner).run(Handle().save))
        self.assertEqual((self.root / 'value').read_bytes(), b'written')

    def test_failed_watermark_publication_does_not_delete_commits(self):
        self.owner.retain()
        path = self.root / 'value'
        with self.owner.scope(records=True):
            atomic_bytes(path, b'durable')
        bundle = self.owner.transactions.records.frames[0].path
        from llm.services.infrastructure.record_journal import _sync
        def sync(directory):
            if directory == self.owner.transactions.journal:
                self.assertTrue((directory / RecordJournal.CHECKPOINT).is_file())
                raise OSError('sync failed')
            return _sync(directory)
        with patch('llm.services.infrastructure.record_journal._sync', side_effect=sync):
            with self.assertRaises(OSError):
                self.owner.release()
        self.assertTrue(bundle.exists())
        # Simulate loss of the unconfirmed checkpoint rename. Recovery must
        # still have the source journal and must not need an old target value.
        (self.owner.transactions.journal / RecordJournal.CHECKPOINT).unlink()
        path.write_bytes(b'lost')
        with WorkspaceOwnership(self.root).scope():
            self.assertEqual(path.read_bytes(), b'durable')

    def test_recovery_coalesces_appends_with_missing_created_directories(self):
        import shutil
        directory = self.root / 'created'
        path = directory / 'history'
        self.owner.retain()
        for data in (b'one\n', b'two\n', b'three\n'):
            with self.owner.scope(records=True):
                append_bytes(path, data)
        self.owner.transactions.records.reset()
        self.owner.release()
        shutil.rmtree(directory)  # Simulated loss of unsynced target projection.
        with WorkspaceOwnership(self.root).scope():
            self.assertEqual(path.read_bytes(), b'one\ntwo\nthree\n')

    def test_backup_reads_complete_materialized_records_under_lock(self):
        from llm.services.infrastructure.backups import DirectoryBackups
        directory = self.root / 'project'
        destination = self.root / 'backup'
        self.owner.retain()
        try:
            with self.owner.scope(records=True):
                atomic_bytes(directory / 'first', b'one')
                atomic_bytes(directory / 'second', b'two')
            with self.owner.scope():
                backups = DirectoryBackups()
                backups.create(directory, destination, metadata={})
                backups.verify(destination)
                restored = backups.project_root(destination)
                self.assertEqual((restored / 'first').read_bytes(), b'one')
                self.assertEqual((restored / 'second').read_bytes(), b'two')
        finally:
            self.owner.release()


class RecordCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_drains_accepted_bundle(self):
        import threading
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entered, release = threading.Event(), threading.Event()
            def persist():
                atomic_bytes(root / 'value', b'durable')
                entered.set()
                if not release.wait(10):
                    raise RuntimeError('Test release was not signalled')
            pending = asyncio.create_task(StorageIO(WorkspaceOwnership(root)).run_records(persist))
            if not await asyncio.to_thread(entered.wait, 10):
                await pending
                self.fail('Record operation did not start')
            pending.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            self.assertEqual((root / 'value').read_bytes(), b'durable')
