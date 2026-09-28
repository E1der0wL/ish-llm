"""지정한 작업 루트 안의 텍스트 파일 탐색과 원자적 변경을 제공한다."""

import hashlib
import os
from pathlib import Path
import tempfile
import threading
from uuid import uuid4



class FileTools:
    """실행 루트는 개발자가 지정한다. 심볼릭 링크과 상위 경로는 따라가지 않는다."""

    def __init__(self, root: Path, max_bytes: int = 1_000_000):
        self.root = Path(root).absolute()
        self.max_bytes = max_bytes
        self.lock = threading.RLock()
        self.path(".")
        if not self.root.is_dir():
            raise ValueError("Tool working root must be an existing directory")

    def path(self, value: str, *, internal=False) -> Path:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError("Expected a relative path")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Path must stay inside the working root")
        if not internal and any(part.startswith(".llm-") for part in relative.parts):
            raise ValueError("Path is reserved for Tool storage")
        target = self.root / relative
        for entry in (target, *target.parents):
            if entry.is_symlink():
                raise ValueError("Tool paths cannot follow symlinks")
        target.resolve().relative_to(self.root.resolve())
        return target

    @staticmethod
    def digest(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    def read_bytes(self, path: Path) -> bytes:
        with path.open("rb") as stream:
            value = stream.read(self.max_bytes + 1)
        if len(value) > self.max_bytes:
            raise ValueError("File exceeds Tool byte limit")
        return value

    def check_version(self, value: bytes, expected: str) -> None:
        if self.digest(value) != expected:
            raise ValueError("File changed; read it again before editing")

    def write(self, path: Path, content: bytes) -> None:
        if len(content) > self.max_bytes:
            raise ValueError("File exceeds Tool byte limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = path.stat().st_mode if path.exists() else None
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".llm-write-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if mode is not None:
                os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def file_read(self, args):
        with self.lock:
            value = self.read_bytes(self.path(args["path"]))
            lines = value.decode("utf-8").splitlines(keepends=True)
            start, count = args.get("start_line", 1), args.get("max_lines", 300)
            return {"path": args["path"], "text": "".join(lines[start - 1:start - 1 + count]),
                    "sha256": self.digest(value), "total_lines": len(lines),
                    "start_line": start, "truncated": start > 1 or len(lines) > count}

    def entries(self, args):
        root = self.path(args.get("path", "."))
        if not root.is_dir():
            raise ValueError("Expected a directory")
        pending = [root]
        while pending:
            directory = pending.pop()
            for path in sorted(directory.iterdir()):
                if path.name.startswith(".llm-") or path.is_symlink():
                    continue
                yield path
                if args.get("recursive", False) and path.is_dir():
                    pending.append(path)

    def file_list(self, args):
        with self.lock:
            limit = args.get("limit", 500)
            values = []
            for path in self.entries(args):
                if len(values) == limit:
                    return {"entries": values, "truncated": True}
                values.append({"path": path.relative_to(self.root).as_posix(),
                               "type": "directory" if path.is_dir() else "file"})
            return {"entries": values, "truncated": False}

    def file_search(self, args):
        with self.lock:
            query, values, skipped = args["query"], [], 0
            insensitive = not args.get("case_sensitive", False)
            needle = query.casefold() if insensitive else query
            scanned = 0
            for path in self.entries({**args, "recursive": args.get("recursive", True)}):
                scanned += 1
                if scanned > 5000:
                    return {"matches": values, "skipped": skipped, "truncated": True}
                if not path.is_file():
                    continue
                try:
                    text = self.read_bytes(path).decode("utf-8")
                except (ValueError, UnicodeError):
                    skipped += 1
                    continue
                for line_number, line in enumerate(text.splitlines(), 1):
                    if needle in (line.casefold() if insensitive else line):
                        values.append({"path": path.relative_to(self.root).as_posix(),
                                       "line": line_number, "text": line[:2000]})
                        if len(values) >= args.get("limit", 100):
                            return {"matches": values, "skipped": skipped, "truncated": True}
            return {"matches": values, "skipped": skipped, "truncated": False}

    def file_create(self, args):
        with self.lock:
            path = self.path(args["path"])
            if path.exists():
                raise FileExistsError("File already exists")
            value = args["content"].encode("utf-8")
            self.write(path, value)
            return {"path": args["path"], "sha256": self.digest(value), "bytes": len(value)}

    def file_patch(self, args):
        with self.lock:
            path = self.path(args["path"])
            original = self.read_bytes(path)
            self.check_version(original, args["expected_sha256"])
            text = original.decode("utf-8")
            if text.count(args["old_text"]) != 1:
                raise ValueError("Patch text must match exactly once")
            value = text.replace(args["old_text"], args["new_text"], 1).encode("utf-8")
            self.write(path, value)
            return {"path": args["path"], "before_sha256": self.digest(original), "sha256": self.digest(value)}

    def file_delete(self, args):
        """삭제는 휴지통 이동으로 처리한다. 디렉토리 재귀 삭제는 제공하지 않는다."""
        with self.lock:
            source = self.path(args["path"])
            original = self.read_bytes(source)
            self.check_version(original, args["expected_sha256"])
            identifier = uuid4().hex
            destination = self.path(f".llm-trash/{identifier}/content", internal=True)
            destination.parent.mkdir(parents=True)
            source.rename(destination)
            return {"path": args["path"], "trash_id": identifier, "sha256": self.digest(original)}

    def file_restore(self, args):
        with self.lock:
            identifier = args["trash_id"]
            if len(identifier) != 32 or any(char not in "0123456789abcdef" for char in identifier):
                raise ValueError("Invalid trash ID")
            source = self.path(f".llm-trash/{identifier}/content", internal=True)
            destination = self.path(args["path"])
            if destination.exists():
                raise FileExistsError("Restore destination already exists")
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            return {"path": args["path"], "restored": True}

    def file_move(self, args):
        with self.lock:
            source, destination = self.path(args["path"]), self.path(args["destination"])
            original = self.read_bytes(source)
            self.check_version(original, args["expected_sha256"])
            if destination.exists():
                raise FileExistsError("Move destination already exists")
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            return {"path": args["destination"], "sha256": self.digest(original)}
