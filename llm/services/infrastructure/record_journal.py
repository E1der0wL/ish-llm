"""Retained record commits and coalesced checkpoints, under workspace ownership.

The journal is the durable authority until its checkpoint is published. Applied
files are a readable projection. No Engine, Tool or domain-specific record is
interpreted here. The in-memory proof expires with the OS ownership handle.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .transactions import _checked, _sync


class RecordJournal:
    CHECKPOINT = 'record-checkpoint'
    TEMPORARY = '.record-'
    SEQUENCE = 'sequence'
    CHECKSUM = 'checksum'
    BUNDLE = re.compile(r'records-([1-9][0-9]*)-([0-9a-f]{32})')
    PENDING = re.compile(r'records-pending-[0-9a-f]{32}')

    def __init__(self, manager):
        self.manager = manager
        self.reset()

    def reset(self):
        self.ready = False
        self.sequence = 0
        self.checkpoint_sequence = 0
        self.files = set()
        self.directories = set()
        self.bundles = []

    def _watermark(self):
        path = _checked(self.manager.journal / self.CHECKPOINT)
        if not path.exists():
            return 0
        value = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or set(value) != {self.SEQUENCE, self.CHECKSUM}
                or type(value[self.SEQUENCE]) is not int or value[self.SEQUENCE] < 1
                or hashlib.sha256(str(value[self.SEQUENCE]).encode()).hexdigest() != value[self.CHECKSUM]):
            raise ValueError('Invalid record checkpoint')
        return value[self.SEQUENCE]

    @classmethod
    def owns(cls, path):
        return (path.name == cls.CHECKPOINT or cls.BUNDLE.fullmatch(path.name)
                or cls.PENDING.fullmatch(path.name) or path.name.startswith(cls.TEMPORARY))

    def recover(self):
        if self.ready:
            return
        self.reset()
        journal = self.manager.journal
        if not journal.exists():
            self.ready = True
            return
        watermark = self._watermark()
        bundles, obsolete = [], []
        for path in journal.iterdir():
            if not self.owns(path):
                continue
            if not _checked(path).is_file():
                raise ValueError('Invalid record journal file')
            match = self.BUNDLE.fullmatch(path.name)
            if match and int(match[1]) > watermark:
                bundles.append((int(match[1]), path))
            elif path.name != self.CHECKPOINT:
                obsolete.append(path)
        bundles.sort()
        expected = watermark + 1
        for sequence, _ in bundles:
            if sequence != expected:
                raise ValueError('Invalid record journal sequence')
            expected += 1
        # Validate the entire retained chain before changing any target. Later
        # replacements supersede earlier ranges; appends must join exactly.
        directories, records = self._plan(bundles)
        if bundles:
            _sync(journal)
        self.sequence = bundles[-1][0] if bundles else watermark
        self.checkpoint_sequence = watermark
        self.bundles = [path for _, path in bundles]
        self._apply(directories, records)
        self.ready = True
        try:
            self.checkpoint()
            if obsolete:
                # A visible watermark from an uncertain rename must itself be
                # durable before obsolete commits may be discarded.
                _sync(journal)
                for path in obsolete:
                    path.unlink(missing_ok=True)
        except BaseException:
            self.reset()
            raise

    def _plan(self, bundles):
        from .record_transactions import RecordTransaction
        directories, records = set(), {}
        for sequence, bundle in bundles:
            with _checked(bundle).open('rb') as source:
                declared, values = RecordTransaction._manifest(self.manager, bundle, source)
            directories.update(declared)
            for path, value in values:
                prefix = value[RecordTransaction.PREFIX]
                ranges = [(bundle, offset, length) for offset, length, _ in value[RecordTransaction.RANGES]]
                if value[RecordTransaction.APPEND] and path in records:
                    previous_prefix, previous_ranges = records[path]
                    if prefix != previous_prefix + sum(item[2] for item in previous_ranges):
                        raise ValueError('Discontinuous record append')
                    records[path] = previous_prefix, previous_ranges + ranges
                else:
                    records[path] = prefix, ranges
        for path in (*directories, *records):
            if path in directories and path in records:
                raise ValueError('Record file/directory conflict')
            for parent in path.parents:
                if parent in records or (parent.exists() and not parent.is_dir()):
                    raise ValueError('Record file/directory conflict')
                if not parent.exists() and parent not in directories:
                    raise ValueError('Undeclared record transaction directory')
        for directory in directories:
            if directory.exists() and not directory.is_dir():
                raise ValueError('Record destination is not a directory')
        for path, (prefix, ranges) in records.items():
            if path.exists() and not path.is_file():
                raise ValueError('Record destination is not a file')
            # A zero prefix is a complete reconstruction, including an append
            # to a new file. No old bytes are needed in that case.
            actual = path.stat().st_size if path.exists() else 0
            if prefix and not prefix <= actual <= prefix + sum(r[2] for r in ranges):
                raise ValueError('Record append source is missing or changed')
        return directories, records

    def _apply(self, directories, records):
        from .record_transactions import RecordTransaction
        for directory in sorted(directories, key=lambda p: (len(p.parts), str(p))):
            directory.mkdir(exist_ok=True)
            self.directories.update((directory, directory.parent))
        for destination, (prefix, ranges) in records.items():
            temporary = None
            existed = destination.exists()
            if prefix:
                stream = destination.open('r+b')
                stream.seek(prefix)
            else:
                fd, name = tempfile.mkstemp(prefix=self.TEMPORARY, dir=self.manager.journal)
                temporary = Path(name)
                stream = os.fdopen(fd, 'wb')
            try:
                with stream:
                    for bundle, offset, length in ranges:
                        with bundle.open('rb') as source:
                            source.seek(offset)
                            remaining = length
                            while remaining:
                                chunk = source.read(min(remaining, RecordTransaction.BLOCK))
                                if not chunk:
                                    raise ValueError('Incomplete record payload')
                                stream.write(chunk)
                                remaining -= len(chunk)
                    stream.truncate()
                    # Close/flush exposes committed bytes to readers under the
                    # lock. Durability is already provided by the journal.
                if temporary is not None:
                    os.replace(temporary, destination)
                self.files.add(destination)
                if temporary is not None or not existed:
                    self.directories.add(destination.parent)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def apply(self, path):
        match = self.BUNDLE.fullmatch(path.name)
        if match is None or int(match[1]) != self.sequence + 1:
            raise ValueError('Invalid record commit sequence')
        directories, records = self._plan([(int(match[1]), path)])
        self._apply(directories, records)
        self.sequence = int(match[1])
        self.bundles.append(path)

    def checkpoint(self):
        if not self.ready or self.sequence == self.checkpoint_sequence:
            return
        # The current file projections are synchronized once, regardless of
        # how many retained transactions touched them. Ordinary undo mutation
        # and final ownership release are barriers, not configurable timers.
        for path in sorted(self.files):
            with _checked(path).open('rb') as stream:
                os.fsync(stream.fileno())
        for directory in sorted(self.directories, key=lambda p: (-len(p.parts), str(p))):
            _sync(_checked(directory))
        value = {self.SEQUENCE: self.sequence,
                 self.CHECKSUM: hashlib.sha256(str(self.sequence).encode()).hexdigest()}
        fd, name = tempfile.mkstemp(prefix=self.TEMPORARY, dir=self.manager.journal)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(json.dumps(value, sort_keys=True).encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.manager.journal / self.CHECKPOINT)
            _sync(self.manager.journal)
        finally:
            temporary.unlink(missing_ok=True)
        # Never delete individual commits before the durable watermark. A
        # crash may resurrect any subset of deleted names; all are now ignored.
        self.checkpoint_sequence = self.sequence
        self.files.clear()
        self.directories.clear()
        bundles, self.bundles = self.bundles, []
        for path in bundles:
            path.unlink(missing_ok=True)
