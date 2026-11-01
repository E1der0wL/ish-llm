"""Explicit record batches: atomically prepare, then durably authorize/materialize.

Only atomic byte replacement, append and directory creation participate. General
tree mutations retain the undo transaction. No Engine/Tool execution is replayed.
Prepared frames are read through the owned view without changing canonical files.
Only a strong commit fsyncs the dependency prefix and permits effects. A failure
after publication is uncertain and requires recovery, never guessed rollback.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace

from .transactions import Transaction, _checked
from .record_journal import RecordJournal
from llm.core.paths import WorkspacePaths


class RecordReader(io.RawIOBase):
    """Seek across an existing prefix and staged ranges without copying history."""

    def __init__(self, segments):
        self.segments = segments
        self.position = 0
        self.size = sum(length for _, _, length in segments)
        self.streams = {}

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=os.SEEK_SET):
        position = offset + (self.position if whence == os.SEEK_CUR else
                             self.size if whence == os.SEEK_END else 0)
        if whence not in (os.SEEK_SET, os.SEEK_CUR, os.SEEK_END) or position < 0:
            raise ValueError("Invalid record seek")
        self.position = position
        return position

    def readinto(self, buffer):
        remaining, written, start = len(buffer), 0, 0
        for path, offset, length in self.segments:
            end = start + length
            if start <= self.position < end and remaining:
                stream = self.streams.get(path)
                if stream is None:
                    stream = self.streams[path] = path.open('rb')
                count = min(remaining, end - self.position)
                stream.seek(offset + self.position - start)
                data = stream.read(count)
                if len(data) != count:
                    raise ValueError("Record source shortened during transaction")
                buffer[written:written + count] = data
                self.position += count
                written += count
                remaining -= count
            start = end
        return written

    def close(self):
        for stream in self.streams.values():
            stream.close()
        super().close()


class RecordTransaction(Transaction):
    record_batch = True
    DIRECTORIES = 'directories'
    RECORDS = 'records'
    PROJECTIONS = 'projections'
    PATH = 'path'
    PREFIX = 'prefix'
    APPEND = 'append'
    RANGES = 'ranges'
    PENDING = 'records-pending-'
    SEQUENCE = RecordJournal.SEQUENCE
    MAGIC = b'ISHRECORD1\n'
    # Framing/checksum and copy block size are storage implementation constants,
    # not execution policies, retention bounds or user configuration defaults.
    FOOTER = struct.Struct('>Q32s11s')
    BLOCK = 65536

    def __init__(self, manager, *, durable=True):
        super().__init__(manager)
        self.durable = durable
        self.pending = self.path.with_name(self.PENDING + self.path.name)
        self.sequence = manager.records.sequence + 1
        self._publication_started = False
        self.records = {}
        self.projections = []
        self._projection_ends = {}
        self.directories = set()
        self.spool = None
        self._stamps = {}

    def _ensure(self):
        if self.spool is None:
            self.manager._ensure_journal()
            self.spool = self.pending.open('x+b')

    def mkdir(self, path):
        if Path(path).absolute() == self.manager.root:
            return
        path, _ = self._target(path)
        cursor = path
        while cursor != self.manager.root:
            if cursor in self.records or (self.manager.records.exists(cursor)
                                         and not self.manager.records.is_directory(cursor)):
                raise NotADirectoryError(cursor)
            if self.manager.records.exists(cursor) or cursor in self.directories:
                break
            self.directories.add(cursor)
            self.changed()
            cursor = cursor.parent

    def write(self, path, data, *, append):
        path, name = self._target(path)
        if path in self.manager.records.projection_ends or path in self._projection_ends:
            raise ValueError('A derived projection cannot also be an authoritative record')
        if not isinstance(data, bytes):
            raise TypeError('Record storage requires bytes')
        if (path in self.directories or self.manager.records.is_directory(path)
                or path.exists() and not path.is_file()):
            raise ValueError('Record storage requires a regular file')
        self.mkdir(path.parent)
        self._ensure()
        record = self.records.get(path)
        if not append or record is None:
            prefix = self.manager.records.stat(path).st_size if append and self.manager.records.exists(path) else 0
            record = self.records[path] = {
                self.PATH: name, self.PREFIX: prefix, self.APPEND: append, self.RANGES: [],
            }
        self.spool.seek(0, os.SEEK_END)
        offset = self.spool.tell()
        self.spool.write(data)
        record[RecordTransaction.RANGES].append([offset, len(data), hashlib.sha256(data).hexdigest()])
        self.changed()
        self._stamps.pop(path, None)

    def project_append(self, path, data):
        """Declare a non-authoritative append; the frame owns its durable bytes."""
        path, name = self._target(path)
        if (not isinstance(data, bytes) or path in self.records or path in self.directories
                or path in self.manager.records.prepared_directories
                or path in self.manager.records.files or path in self.manager.records.prepared_records):
            raise ValueError('Invalid derived append')
        if path.exists() and not path.is_file():
            raise ValueError('Derived append requires a regular file')
        if path in self._projection_ends:
            prefix = self._projection_ends[path]
        else:
            prefix = path.stat().st_size if path.exists() else 0
            # Failed projections still own their slots in retained source frames.
            prefix = max(prefix, self.manager.records.projection_ends.get(path, prefix))
        self.projections.append({self.PATH: name, self.PREFIX: prefix, 'data': data.hex()})
        self._projection_ends[path] = prefix + len(data)
        self.changed()

    def exists(self, path):
        path, _ = self._target(path)
        return path in self.records or path in self.directories or self.manager.records.exists(path)

    def children(self, path):
        path, _ = self._target(path)
        actual = set(self.manager.records.children(path)) if self.manager.records.exists(path) else set()
        if not self.manager.records.exists(path) and path not in self.directories:
            raise FileNotFoundError(path)
        return iter(sorted(actual | {p for p in (*self.records, *self.directories) if p.parent == path}))

    def open(self, path):
        path, _ = self._target(path)
        record = self.records.get(path)
        if record is None:
            return self.manager.records.open(path)
        self.spool.flush()
        segments = self.manager.records.segments(path, record[RecordTransaction.PREFIX])
        segments.extend((self.pending, offset, length) for offset, length, _ in record[RecordTransaction.RANGES])
        return io.BufferedReader(RecordReader(segments))

    def stat(self, path):
        path, _ = self._target(path)
        # After-commit consumers observe the published view, which may still
        # live in the journal. The private staging file has already closed.
        if self.spool is not None and self.spool.closed:
            return self.manager.records.stat(path)
        record = self.records.get(path)
        if record is None:
            return self.manager.records.stat(path)
        self.spool.flush()
        if path not in self._stamps:
            stamp = os.fstat(self.spool.fileno())
            self._stamps[path] = SimpleNamespace(
                st_dev=stamp.st_dev, st_ino=stamp.st_ino,
                st_size=record[RecordTransaction.PREFIX] + sum(r[1] for r in record[RecordTransaction.RANGES]),
                st_mtime_ns=stamp.st_mtime_ns + self.writes)
        return self._stamps[path]

    def _unsupported(self, *args, **kwargs):
        raise RuntimeError('Record batch does not support raw filesystem mutation')

    prepare_create = before_replace = before_append = before_truncate = _unsupported
    remove = staging_directory = temporary_file = defer_sync = _unsupported

    def commit(self):
        if not self.records and not self.directories and not self.projections:
            if self.durable:
                self.manager.records.synchronize()
            return
        self._ensure()
        manifest = json.dumps({
            self.SEQUENCE: self.sequence,
            RecordTransaction.DIRECTORIES: sorted(p.relative_to(self.manager.root).as_posix() for p in self.directories),
            RecordTransaction.RECORDS: list(self.records.values()),
            self.PROJECTIONS: self.projections,
        }, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        self.spool.seek(0, os.SEEK_END)
        self.spool.write(manifest)
        self.spool.write(self.FOOTER.pack(len(manifest), hashlib.sha256(manifest).digest(), self.MAGIC))
        self.spool.flush()
        try:
            # A failure after beginning the append is uncertain, even when its
            # complete frame is visible. Recovery decides before any new access.
            self._publication_started = True
            frame = self.manager.records.append(self.sequence, self.spool)
            if self.durable:
                self.materialize(self.manager, frame)
            else:
                self.manager.records.apply(frame, durable=False)
        except BaseException:
            # Applied projections are no longer proven complete. The next
            # owned access validates/reconstructs the retained chain first.
            self.manager.records.reset()
            raise

    def is_committed(self):
        return self._publication_started

    def discard(self):
        try:
            self.close()
            self.pending.unlink(missing_ok=True)
        finally:
            # Cleanup failure cannot make an uncommitted in-memory projection
            # authoritative. The next owner can still discard a pending file.
            for callback in reversed(tuple(self._rollback.values())):
                callback()

    def close(self):
        if self.spool is not None:
            self.spool.close()
        if self._publication_started:
            self.pending.unlink(missing_ok=True)

    def finish(self):
        # Retained until a checkpoint proves all target files durable.
        pass

    @classmethod
    def _manifest(cls, manager, frame, stream):
        size = frame.size
        if size < cls.FOOTER.size:
            raise ValueError('Incomplete record transaction')
        stream.seek(frame.offset + size - cls.FOOTER.size)
        length, checksum, magic = cls.FOOTER.unpack(stream.read(cls.FOOTER.size))
        end = size - cls.FOOTER.size - length
        if magic != cls.MAGIC or end < 0:
            raise ValueError('Invalid record transaction framing')
        stream.seek(frame.offset + end)
        raw = stream.read(length)
        if hashlib.sha256(raw).digest() != checksum:
            raise ValueError('Record transaction manifest checksum mismatch')
        manifest = json.loads(raw.decode('utf-8'))
        if not isinstance(manifest, dict) or set(manifest) != {cls.SEQUENCE, cls.DIRECTORIES, cls.RECORDS, cls.PROJECTIONS}:
            raise ValueError('Invalid record transaction manifest')
        if (type(manifest[cls.SEQUENCE]) is not int
                or manifest[cls.SEQUENCE] != frame.sequence):
            raise ValueError('Invalid record transaction sequence')

        def target(name, *, physical=True):
            if not isinstance(name, str):
                raise ValueError('Invalid record transaction target')
            relative = Path(name)
            value = manager.root / relative
            if (relative.is_absolute() or not relative.parts or '..' in relative.parts
                    or relative.as_posix() != name or WorkspacePaths.reserved(relative)):
                raise ValueError('Invalid record transaction target')
            # A nonempty relative path with no parent traversal joined to root
            # is already a strict child; _checked above still rejects symlinks.
            return _checked(value) if physical else value

        if not isinstance(manifest[RecordTransaction.DIRECTORIES], list) or not isinstance(manifest[RecordTransaction.RECORDS], list):
            raise ValueError('Invalid record transaction entries')
        directories = [target(name) for name in manifest[RecordTransaction.DIRECTORIES]]
        if len(set(directories)) != len(directories):
            raise ValueError('Duplicate record transaction directory')
        records, seen = [], set(directories)
        for record in manifest[RecordTransaction.RECORDS]:
            if not isinstance(record, dict) or set(record) != {RecordTransaction.PATH, RecordTransaction.PREFIX, RecordTransaction.APPEND, RecordTransaction.RANGES}:
                raise ValueError('Invalid record transaction record')
            destination = target(record[RecordTransaction.PATH])
            prefix = record[RecordTransaction.PREFIX]
            if (destination in seen or type(record[RecordTransaction.APPEND]) is not bool
                    or type(prefix) is not int or prefix < 0
                    or (not record[RecordTransaction.APPEND] and prefix != 0)
                    or not isinstance(record[RecordTransaction.RANGES], list) or not record[RecordTransaction.RANGES]):
                raise ValueError('Invalid record transaction record')
            seen.add(destination)
            for item in record[RecordTransaction.RANGES]:
                if (not isinstance(item, list) or len(item) != 3
                        or any(type(v) is not int or v < 0 for v in item[:2])
                        or item[0] + item[1] > end):
                    raise ValueError('Invalid record transaction range')
                stream.seek(frame.offset + item[0])
                remaining, digest = item[1], hashlib.sha256()
                while remaining:
                    chunk = stream.read(min(remaining, cls.BLOCK))
                    if not chunk:
                        raise ValueError('Incomplete record transaction payload')
                    digest.update(chunk)
                    remaining -= len(chunk)
                if digest.hexdigest() != item[2]:
                    raise ValueError('Record transaction payload checksum mismatch')
            if destination.exists() and not destination.is_file():
                raise ValueError('Record transaction destination is not a file')
            records.append((destination, record))
        files = {p for p, _ in records}
        for destination in (*directories, *files):
            if any(parent in files for parent in destination.parents):
                raise ValueError('Record transaction file/directory conflict')
            for parent in destination.parents:
                if parent.exists() and not parent.is_dir():
                    raise NotADirectoryError(parent)
                # Missing parents may be declared by an earlier retained
                # commit. The journal validates the complete coalesced plan.
        for directory in directories:
            if directory.exists() and not directory.is_dir():
                raise NotADirectoryError(directory)
        projections = []
        if not isinstance(manifest[cls.PROJECTIONS], list):
            raise ValueError('Invalid derived append list')
        for item in manifest[cls.PROJECTIONS]:
            if (not isinstance(item, dict) or set(item) != {cls.PATH, cls.PREFIX, 'data'}
                    or type(item[cls.PREFIX]) is not int or item[cls.PREFIX] < 0
                    or not isinstance(item['data'], str)):
                raise ValueError('Invalid derived append')
            destination = target(item[cls.PATH], physical=False)
            data = bytes.fromhex(item['data'])
            if data.hex() != item['data'] or destination in seen:
                raise ValueError('Invalid derived append')
            if any(p in files for p in destination.parents):
                raise ValueError('Derived append conflicts with authoritative storage')
            projections.append((destination, item[cls.PREFIX], data))
        return directories, records, projections

    @classmethod
    def materialize(cls, manager, frame):
        manager.records.apply(frame)
