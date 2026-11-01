"""Retained record commits and coalesced checkpoints, under workspace ownership.

The journal owns prepared bytes and durable prefixes until checkpoint publication.
Canonical files project only synchronized prefixes; live queries also see prepared
ranges. No Engine, Tool or domain record is interpreted. Memory views expire with
OS ownership; a new owner validates and synchronizes surviving complete frames.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import struct
import warnings
import io
from types import SimpleNamespace
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
    PROJECTION_WARNING = 'Derived record projection could not be updated'

    def __init__(self, manager):
        self.manager = manager
        self.reset()

    def reset(self):
        self.ready = False
        self.sequence = 0
        self.checkpoint_sequence = 0
        self.files = set()
        self.directories = set()
        self.projection_files = set()
        self.projection_ends = {}
        self.projection_directories = set()
        self.frames = []
        self.log = None
        self.log_size = 0
        self.log_published = False
        self.prepared = []
        self.prepared_records = {}
        self.prepared_directories = set()
        self.prepared_projections = []
        self.prepared_stamps = {}

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
        directories, records, projections = self._plan(frames)
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
        self._project(projections)
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
        """Flush a complete frame; synchronization separately permits materialization."""
        from .record_transactions import RecordTransaction
        if not self.ready or sequence != self.sequence + 1:
            raise ValueError('Invalid record commit sequence')
        created = self.log is None
        if created:
            self.manager._ensure_journal()
            self.log = self.manager.journal / f'records-{sequence}-{uuid4().hex}'
            self.log_size = 0
            self.log_published = False
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
        frame = RecordFrame(sequence, self.log, self.log_size + len(header), size)
        self.log_size = frame.offset + frame.size
        return frame

    def _plan(self, frames, *, prepared=False):
        from .record_transactions import RecordTransaction
        directories, records, projections = set(), {}, []
        for frame in frames:
            with _checked(frame.path).open('rb') as source:
                declared, values, derived = RecordTransaction._manifest(self.manager, frame, source)
            projections.extend(derived)
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
                if (not parent.exists() and parent not in directories
                        and not (prepared and parent in self.prepared_directories)):
                    raise ValueError('Undeclared record transaction directory')
        for directory in directories:
            if directory.exists() and not directory.is_dir():
                raise ValueError('Record destination is not a directory')
        for path, (prefix, ranges) in records.items():
            if path.exists() and not path.is_file():
                raise ValueError('Record destination is not a file')
            # A zero prefix is a complete reconstruction, including an append
            # to a new file. No old bytes are needed in that case.
            actual = (self.stat(path).st_size if self.exists(path) else 0) if prepared else (
                path.stat().st_size if path.exists() else 0)
            if prefix and not prefix <= actual <= prefix + sum(r[2] for r in ranges):
                raise ValueError('Record append source is missing or changed')
        if any(path in records or path in directories or any(p in records for p in path.parents)
               for path, _, _ in projections):
            raise ValueError('Derived append conflicts with authoritative storage')
        return directories, records, projections

    @classmethod
    def _projection_warning(cls):
        try:
            warnings.warn(cls.PROJECTION_WARNING, RuntimeWarning)
        except Exception:
            pass  # A logging/observer failure cannot invalidate committed execution.

    def _project(self, projections):
        for destination, prefix, data in projections:
            self.projection_ends[destination] = prefix + len(data)
            try:
                destination = _checked(destination)
                existed = destination.exists()
                if existed and not destination.is_file():
                    raise ValueError('Invalid derived append destination')
                missing = []
                parent = destination.parent
                while not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                if missing:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                # A zero prefix may be a new name materialized before a crash;
                # recovery must publish that directory entry too. An append to
                # an established nonempty file changes no directory entries.
                if not existed or prefix == 0:
                    self.projection_directories.update((*missing, *(p.parent for p in missing), destination.parent))
                with destination.open('r+b' if existed else 'x+b') as stream:
                    size = stream.seek(0, os.SEEK_END)
                    if size < prefix:
                        raise ValueError('Derived append prefix is missing')
                    stream.seek(prefix)
                    present = stream.read(len(data))
                    if not data.startswith(present):
                        raise ValueError('Derived append prefix changed')
                    # Complete a torn append or retain already-applied bytes,
                    # including later frames. Never duplicate/truncate the tail.
                    stream.seek(prefix + len(present))
                    stream.write(data[len(present):])
                self.projection_files.add(destination)
            except Exception:
                self._projection_warning()

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

    def apply(self, frame, *, durable=True):
        if frame.sequence != self.sequence + 1:
            raise ValueError('Invalid record commit sequence')
        directories, records, projections = self._plan([frame], prepared=True)
        stamp = frame.path.stat()
        for path, (prefix, ranges) in records.items():
            if prefix and path in self.prepared_records:
                base, previous = self.prepared_records[path]
                if prefix != self.prepared_stamps[path].st_size:
                    raise ValueError('Discontinuous prepared append')
                previous.extend(ranges)
            else:
                self.prepared_records[path] = prefix, ranges
            self.prepared_stamps[path] = SimpleNamespace(
                st_dev=stamp.st_dev, st_ino=stamp.st_ino,
                st_size=prefix + sum(r[2] for r in ranges),
                st_mtime_ns=stamp.st_mtime_ns + frame.sequence)
        self.prepared_directories.update(directories)
        self.prepared_projections.extend(projections)
        for path, prefix, data in projections:
            self.projection_ends[path] = prefix + len(data)
        self.prepared.append(frame)
        self.sequence = frame.sequence
        self.frames.append(frame)
        if durable:
            self.synchronize()

    def synchronize(self):
        """One durable prefix before any canonical projection or execution ACK."""
        if not self.prepared:
            return
        try:
            with _checked(self.log).open('rb') as stream:
                if os.fstat(stream.fileno()).st_size != self.log_size:
                    raise ValueError('Record journal changed during ownership')
                os.fsync(stream.fileno())
            if not self.log_published:
                _sync(self.manager.journal)
                self.log_published = True
            self._apply(self.prepared_directories, self.prepared_records)
            self._project(self.prepared_projections)
            self.prepared.clear()
            self.prepared_records.clear()
            self.prepared_directories.clear()
            self.prepared_projections.clear()
            self.prepared_stamps.clear()
        except BaseException:
            self.reset()
            raise

    def exists(self, path):
        path = Path(path).absolute()
        return path in self.prepared_records or path in self.prepared_directories or path.exists()

    def is_directory(self, path):
        path = Path(path).absolute()
        return path in self.prepared_directories or path.is_dir()

    def children(self, path):
        path = Path(path).absolute()
        actual = set(path.iterdir()) if path.exists() else set()
        if not self.exists(path):
            raise FileNotFoundError(path)
        return iter(sorted(actual | {p for p in (*self.prepared_records, *self.prepared_directories)
                                     if p.parent == path}))

    def segments(self, path, length):
        path = Path(path).absolute()
        value = self.prepared_records.get(path)
        source = [(path, 0, length)] if value is None else (
            ([(path, 0, value[0])] if value[0] else []) + value[1])
        result = []
        for file, offset, size in source:
            count = min(length, size)
            if count:
                result.append((file, offset, count))
                length -= count
            if not length:
                break
        if length:
            raise ValueError('Prepared record prefix is missing')
        return result

    def stat(self, path):
        path = Path(path).absolute()
        return self.prepared_stamps[path] if path in self.prepared_stamps else path.stat()

    def open(self, path):
        from .record_transactions import RecordReader
        path = Path(path).absolute()
        if path not in self.prepared_records:
            return path.open('rb')
        return io.BufferedReader(RecordReader(self.segments(path, self.stat(path).st_size)))

    def checkpoint(self, *, prepare_next=False):
        if not self.ready:
            return
        self.synchronize()
        if self.sequence == self.checkpoint_sequence:
            if prepare_next and self.log is None:
                self.manager._ensure_journal()
                path = self.manager.journal / f'records-{self.sequence + 1}-{uuid4().hex}'
                try:
                    with _checked(path).open('xb'):
                        pass
                    _sync(self.manager.journal)
                except BaseException:
                    self.reset()
                    raise
                self.log, self.log_size = path, 0
                self.log_published = True
            elif not prepare_next and self.log is not None and not self.log_size:
                self.log.unlink(missing_ok=True)
                self.log = None
            return
        # The current file projections are synchronized once, regardless of
        # how many retained transactions touched them. Owner-declared settlement,
        # ordinary undo mutation and final ownership release are barriers, not timers.
        for path in sorted(self.files):
            with _checked(path).open('rb') as stream:
                os.fsync(stream.fileno())
        for directory in sorted(self.directories, key=lambda p: (-len(p.parts), str(p))):
            _sync(_checked(directory))
        # Best-effort projections have durable source bytes in these same frames.
        # Their own I/O failures never become failures of the domain transaction.
        for path in sorted(self.projection_files - self.files):
            try:
                with _checked(path).open('rb') as stream:
                    os.fsync(stream.fileno())
            except Exception:
                self._projection_warning()
        for directory in sorted(self.projection_directories - self.directories,
                                key=lambda p: (-len(p.parts), str(p))):
            try:
                _sync(_checked(directory))
            except Exception:
                self._projection_warning()
        value = {self.SEQUENCE: self.sequence,
                 self.CHECKSUM: hashlib.sha256(str(self.sequence).encode()).hexdigest()}
        fd, name = tempfile.mkstemp(prefix=self.TEMPORARY, dir=self.manager.journal)
        temporary = Path(name)
        next_log = None
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(json.dumps(value, sort_keys=True).encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            if prepare_next:
                next_log = self.manager.journal / f'records-{self.sequence + 1}-{uuid4().hex}'
                with _checked(next_log).open('xb'):
                    pass
            os.replace(temporary, self.manager.journal / self.CHECKPOINT)
            # Publish the empty next segment name with the same directory ACK.
            # A prepared observation may precede its fsync; execution may not.
            _sync(self.manager.journal)
        except BaseException:
            # An uncertain watermark/name publication must be recovered before
            # another segment is allocated or a cached proof is reused.
            self.reset()
            raise
        finally:
            temporary.unlink(missing_ok=True)
        # Never delete individual commits before the durable watermark. A
        # crash may resurrect any subset of deleted names; all are now ignored.
        self.checkpoint_sequence = self.sequence
        self.files.clear()
        self.directories.clear()
        self.projection_files.clear()
        self.projection_ends.clear()
        self.projection_directories.clear()
        segments = {frame.path for frame in self.frames}
        self.frames = []
        self.log = next_log
        self.log_size = 0
        self.log_published = next_log is not None
        for path in segments:
            path.unlink(missing_ok=True)
