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
import struct
from dataclasses import dataclass
from uuid import uuid4

from .transactions import _checked, _sync


@dataclass(frozen=True, slots=True)
class RecordFrame:
    sequence: int
    path: Path
    offset: int
    size: int


class RecordJournal:
    CHECKPOINT = 'record-checkpoint'
    TEMPORARY = '.record-'
    SEQUENCE = 'sequence'
    CHECKSUM = 'checksum'
    BUNDLE = re.compile(r'records-([1-9][0-9]*)-([0-9a-f]{32})')
    PENDING = re.compile(r'records-pending-[0-9a-f]{32}')
    # A checksummed frame length distinguishes an unfinished final append from
    # a complete but corrupt record. No domain data or execution is interpreted.
    MAGIC = b'ISHFRAME1\n'
    PREFIX = struct.Struct('>10sQQ')
    HEADER = struct.Struct('>10sQQ32s')

    def __init__(self, manager):
        self.manager = manager
        self.reset()

    def reset(self):
        self.ready = False
        self.sequence = 0
        self.checkpoint_sequence = 0
        self.files = set()
        self.directories = set()
        self.frames = []
        self.log = None
        self.log_size = 0

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
        segments, obsolete = [], []
        for path in journal.iterdir():
            if not self.owns(path):
                continue
            if not _checked(path).is_file():
                raise ValueError('Invalid record journal file')
            match = self.BUNDLE.fullmatch(path.name)
            if match and int(match[1]) > watermark:
                segments.append((int(match[1]), path))
            elif path.name != self.CHECKPOINT:
                obsolete.append(path)
        segments.sort()
        expected = watermark + 1
        frames, tails = [], []
        for index, (sequence, path) in enumerate(segments):
            if sequence != expected:
                raise ValueError('Invalid record journal sequence')
            found, end = self._scan(path, sequence)
            frames.extend(found)
            expected += len(found)
            if end != path.stat().st_size:
                if index != len(segments) - 1:
                    raise ValueError('Incomplete nonfinal record segment')
                tails.append((path, end))
            if not found:
                obsolete.append(path)
        # Validate the entire retained chain before changing any target. Later
        # replacements supersede earlier ranges; appends must join exactly.
        directories, records = self._plan(frames)
        # Prove every complete frame first. Only a framed, unfinished final
        # append can be truncated; corrupt complete frames remain untouched.
        for path, end in tails:
            with path.open('r+b') as stream:
                stream.truncate(end)
        if segments:
            # A complete write whose fsync failed is uncertain to its caller.
            # Recovery makes its journal durable before applying/observing it.
            for _, path in segments:
                with path.open('rb') as stream:
                    os.fsync(stream.fileno())
            _sync(journal)
        self.sequence = frames[-1].sequence if frames else watermark
        self.checkpoint_sequence = watermark
        self.frames = frames
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

    def _scan(self, path, sequence):
        """Return complete frames and the last complete byte boundary."""
        from .record_transactions import RecordTransaction
        frames, cursor = [], 0
        with _checked(path).open('rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            while cursor < size:
                stream.seek(cursor)
                raw = stream.read(self.HEADER.size)
                if len(raw) < self.HEADER.size:
                    if not self.MAGIC.startswith(raw[:len(self.MAGIC)]):
                        raise ValueError('Invalid record frame prefix')
                    break
                magic, current, length, checksum = self.HEADER.unpack(raw)
                if magic != self.MAGIC or hashlib.sha256(raw[:self.PREFIX.size]).digest() != checksum:
                    raise ValueError('Record frame header checksum mismatch')
                if current != sequence:
                    raise ValueError('Invalid record frame sequence')
                if length < RecordTransaction.FOOTER.size:
                    raise ValueError('Invalid record frame length')
                if size - cursor - self.HEADER.size < length:
                    break
                frames.append(RecordFrame(current, path, cursor + self.HEADER.size, length))
                cursor += self.HEADER.size + length
                sequence += 1
        return frames, cursor

    def append(self, sequence, source):
        """Append one complete transaction; ACK requires its actual fsync."""
        from .record_transactions import RecordTransaction
        if not self.ready or sequence != self.sequence + 1:
            raise ValueError('Invalid record commit sequence')
        created = self.log is None
        if created:
            self.manager._ensure_journal()
            self.log = self.manager.journal / f'records-{sequence}-{uuid4().hex}'
            self.log_size = 0
        size = source.seek(0, os.SEEK_END)
        source.seek(0)
        prefix = self.PREFIX.pack(self.MAGIC, sequence, size)
        header = prefix + hashlib.sha256(prefix).digest()
        with _checked(self.log).open('xb' if created else 'r+b') as stream:
            if stream.seek(0, os.SEEK_END) != self.log_size:
                raise ValueError('Record journal changed during ownership')
            stream.write(header)
            remaining = size
            while remaining:
                chunk = source.read(min(remaining, RecordTransaction.BLOCK))
                if not chunk:
                    raise ValueError('Incomplete staged record payload')
                stream.write(chunk)
                remaining -= len(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if created:
            _sync(self.manager.journal)
        frame = RecordFrame(sequence, self.log, self.log_size + len(header), size)
        self.log_size = frame.offset + frame.size
        return frame

    def _plan(self, frames):
        from .record_transactions import RecordTransaction
        directories, records = set(), {}
        for frame in frames:
            with _checked(frame.path).open('rb') as source:
                declared, values = RecordTransaction._manifest(self.manager, frame, source)
            directories.update(declared)
            for path, value in values:
                prefix = value[RecordTransaction.PREFIX]
                ranges = [(frame.path, frame.offset + offset, length)
                          for offset, length, _ in value[RecordTransaction.RANGES]]
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

    def apply(self, frame):
        if frame.sequence != self.sequence + 1:
            raise ValueError('Invalid record commit sequence')
        directories, records = self._plan([frame])
        self._apply(directories, records)
        self.sequence = frame.sequence
        self.frames.append(frame)

    def checkpoint(self):
        if not self.ready or self.sequence == self.checkpoint_sequence:
            return
        # The current file projections are synchronized once, regardless of
        # how many retained transactions touched them. Owner-declared settlement,
        # ordinary undo mutation and final ownership release are barriers, not timers.
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
        segments = {frame.path for frame in self.frames}
        self.frames = []
        self.log = None
        self.log_size = 0
        for path in segments:
            path.unlink(missing_ok=True)
